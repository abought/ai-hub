#!/usr/bin/env python3
"""
Format a Globus Flows definition according to style guide.

Key order, state order and blank-line placement are derived entirely from the style guide.

The incoming layout may have been randomized when stored in the DB, and is not assumed to
    have inherent meaning.

In addition to formatting, authoring conventions are checked (such as warnings of
    unreachable states, result path handling, naming rules, etc). These manifest
    as advisory warnings printed to stderr, and the script does not auto-fix.
"""

import argparse
import json
import sys

# Key groups within a state, in output order. One blank line separates each populated
# group from the next. Error handling is split from the rest of *where* because STYLE.md
# asks for a blank line between them.
STATE_GROUPS = (
    ("Type", "ActionUrl", "End", "Error", "Cause", "Comment"),  # what
    ("RunAs",),  # who
    ("Next", "ResultPath", "WaitTime", "Default", "Choices"),  # where
    ("ExceptionOnActionFailure", "Catch"),  # where: error handling
)
STATE_KEYS = {key for group in STATE_GROUPS for key in group} | {"Parameters"}

TOP_LEVEL_HEAD = ("Comment", "StartAt")
CATCH_ORDER = ("Next", "ResultPath", "ErrorEquals")
CHOICE_HEAD = ("Next", "Variable")
PRIVATE_PARAMETERS = "__Private_Parameters"


class Grouped:
    """An object whose keys are emitted in groups separated by blank lines.

    `groups` is a list of lists of (key, value) pairs; empty groups are dropped, so a
    group only contributes a blank line when it actually has keys in it.
    """

    def __init__(self, groups, blank_lines=1):
        self.groups = [group for group in groups if group]
        self.blank_lines = blank_lines


# --------------------------------------------------------------------------- parse


def reject_duplicate_keys(pairs):
    """`object_pairs_hook` that refuses documents where a key is defined twice.

    Duplicate keys are legal JSON but silently lossy: json's default hook keeps the last
    one, so the formatter would quietly delete data.
    """
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r} in the same object")
        seen[key] = value
    return seen


def load_flow(handle):
    return json.load(handle, object_pairs_hook=reject_duplicate_keys)


# ----------------------------------------------------------------------- state order


def is_terminal(state):
    return state.get("End") is True or state.get("Type") == "Fail"


def tier_of(state):
    """1: ordinary states. 2: non-terminal `Pass` message states. 3: terminal states."""
    if not isinstance(state, dict):
        return 1
    if is_terminal(state):
        return 3
    if state.get("Type") == "Pass":
        return 2
    return 1


def successors(state):
    """Outgoing edges, in the order a reader follows them."""
    if not isinstance(state, dict):
        return []
    targets = []
    if "Next" in state:
        targets.append(state["Next"])
    for choice in state.get("Choices") or []:
        if isinstance(choice, dict) and "Next" in choice:
            targets.append(choice["Next"])
    if "Default" in state:
        targets.append(state["Default"])
    for entry in state.get("Catch") or []:
        if isinstance(entry, dict) and "Next" in entry:
            targets.append(entry["Next"])
    return [name for name in targets if isinstance(name, str)]


def visit_order(states, start_at):
    """Names in depth-first execution order from `StartAt`, first visit wins."""
    visited = []
    pending = [start_at] if isinstance(start_at, str) else []
    while pending:
        name = pending.pop()
        if name in visited or name not in states:
            continue
        visited.append(name)
        # Reversed, because a stack pops the last item first.
        pending.extend(reversed(successors(states[name])))
    return visited


def terminal_sort_key(name):
    """`Success*` states first, then the rest alphabetically."""
    return (not name.startswith("Success"), name)


def state_order(states, start_at, findings):
    reached = visit_order(states, start_at)
    unreachable = sorted(name for name in states if name not in reached)
    if unreachable:
        findings.append(
            ("warning", "unreachable from StartAt: " + ", ".join(unreachable))
        )

    tiers = {1: [], 2: [], 3: []}
    # Reachable states keep execution order; unreachable ones are appended to the end of
    # their own tier alphabetically. Never dropped.
    for name in reached + unreachable:
        tiers[tier_of(states[name])].append(name)
    tiers[3].sort(key=terminal_sort_key)
    return tiers[1] + tiers[2] + tiers[3]


# ------------------------------------------------------------------- parameter order


def parameters_sort_key(key):
    """Collate on the bare parameter name; the full key only breaks ties.

    Trailing `.$` and `.=` mark a value as a reference or an expression rather than a
    literal, so they should not drive placement: `endpoint_id` sorts next to
    `endpoint_id.$`, and `DATA` lands among the *d*'s.
    """
    base = key
    for suffix in (".$", ".="):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return (base.lower(), key)


def alphabetical_parameters(obj):
    """Default ordering strategy: keys of a `Parameters` object, in output order.

    `__Private_Parameters` is handled separately by the caller, which always places it
    last. STYLE.md leaves parameter order to the action provider, so this is a tool
    default rather than a rule from the guide -- swap in another strategy (same
    signature: object in, list of keys out) when provider-specific orders are defined.
    """
    return sorted((key for key in obj if key != PRIVATE_PARAMETERS), key=parameters_sort_key)


def order_parameters(node, strategy):
    """Recursively order a `Parameters` value, including objects nested in arrays."""
    if isinstance(node, list):
        return [order_parameters(item, strategy) for item in node]
    if not isinstance(node, dict):
        return node
    body = [(key, order_parameters(node[key], strategy)) for key in strategy(node)]
    private = (
        [(PRIVATE_PARAMETERS, node[PRIVATE_PARAMETERS])]
        if PRIVATE_PARAMETERS in node
        else []
    )
    return Grouped([body, private])


# ----------------------------------------------------------------------- state bodies


def order_catch(entries):
    """`Next`, `ResultPath`, `ErrorEquals`; array order is preserved."""
    ordered = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            ordered.append(entry)
            continue
        keys = [key for key in CATCH_ORDER if key in entry]
        keys += sorted(key for key in entry if key not in CATCH_ORDER)
        ordered.append(Grouped([[(key, entry[key]) for key in keys]]))
    return ordered if isinstance(entries, list) else entries


def order_choice(node):
    """`Next` then `Variable` then the remaining predicate keys, alphabetically.

    Alphabetical rather than source order for the tail, because with shuffled input there
    is no source order worth keeping and the output has to be deterministic. Recurses
    through `And`/`Or`/`Not`. The `Choices` array itself is never reordered: its order is
    semantically significant in Amazon States Language.
    """
    if isinstance(node, list):
        return [order_choice(item) for item in node]
    if not isinstance(node, dict):
        return node
    keys = [key for key in CHOICE_HEAD if key in node]
    keys += sorted(key for key in node if key not in CHOICE_HEAD)
    return Grouped([[(key, order_choice(node[key])) for key in keys]])


def check_result_paths(name, state, findings):
    """Suggest `_private` result paths for `RunAs: Flow` states.

    Warning only: STYLE.md leaves the final choice to the author, so this never edits the
    document. A bare `$` is exempt -- there is no key there to prefix.
    """
    if state.get("RunAs") != "Flow":
        return
    candidates = [("ResultPath", state.get("ResultPath"))]
    for index, entry in enumerate(state.get("Catch") or []):
        if isinstance(entry, dict):
            candidates.append((f"Catch[{index}].ResultPath", entry.get("ResultPath")))
    for label, path in candidates:
        if not isinstance(path, str) or path == "$" or "_private" in path:
            continue
        findings.append(
            (
                "warning",
                f"{name}.{label}: {path} is on a RunAs: Flow state and stays visible in "
                "user-facing logs; consider a _private prefix",
            )
        )


def order_state(name, state, findings, strategy):
    if not isinstance(state, dict):
        return state
    check_result_paths(name, state, findings)

    transforms = {"Catch": order_catch, "Choices": order_choice}
    groups = [
        [(key, transforms.get(key, lambda value: value)(state[key])) for key in group if key in state]
        for group in STATE_GROUPS
    ]

    # Keys the style guide does not describe (Retry, TimeoutSeconds, Branches, ...) are
    # never dropped: they sit between the state body and Parameters, alphabetically.
    # Alphabetically and not in source order, because shuffled input has no source order
    # to keep and output must not depend on it.
    extra = [(key, state[key]) for key in sorted(state) if key not in STATE_KEYS]
    if extra:
        findings.append(
            (
                "info",
                f"{name}: keys not described by the style guide, kept: "
                + ", ".join(key for key, _ in extra),
            )
        )
    groups.append(extra)

    if "Parameters" in state:
        groups.append([("Parameters", order_parameters(state["Parameters"], strategy))])
    return Grouped(groups)


def order_flow(flow, findings, strategy=alphabetical_parameters):
    """Return an ordered, group-annotated copy of a flow definition."""
    if not isinstance(flow, dict):
        raise ValueError("a flow definition must be a JSON object")

    head = [(key, flow[key]) for key in TOP_LEVEL_HEAD if key in flow]
    extra = [
        (key, flow[key])
        for key in sorted(flow)
        if key not in TOP_LEVEL_HEAD and key != "States"
    ]
    if extra:
        findings.append(
            (
                "info",
                "top level: keys not described by the style guide, kept: "
                + ", ".join(key for key, _ in extra),
            )
        )

    tail = []
    if "States" in flow:
        states = flow["States"]
        if isinstance(states, dict):
            order = state_order(states, flow.get("StartAt"), findings)
            # Two blank lines between states, so the one-blank-line group breaks inside a
            # state body stay visually distinct from the break between states.
            ordered = Grouped(
                [[(name, order_state(name, states[name], findings, strategy))] for name in order],
                blank_lines=2,
            )
        else:
            ordered = states
        tail = [("States", ordered)]

    # No blank lines at the top level: a single group.
    return Grouped([head + extra + tail])


# ------------------------------------------------------------------------------ emit


def dump_scalar(value):
    """Encode one scalar. `ensure_ascii=False` keeps readable text readable: escaping a
    comment's accented characters would be a needless edit to the document."""
    return json.dumps(value, ensure_ascii=False)


def emit(node, depth=0):
    """Serialize an ordered document. Scalars go through json for correct escaping."""
    if isinstance(node, Grouped):
        return emit_object(node.groups, node.blank_lines, depth)
    if isinstance(node, dict):
        return emit_object([list(node.items())], 1, depth)
    if isinstance(node, list):
        if not node:
            return "[]"
        inner = "  " * (depth + 1)
        items = ",\n".join(inner + emit(item, depth + 1) for item in node)
        return "[\n" + items + "\n" + "  " * depth + "]"
    return dump_scalar(node)


def emit_object(groups, blank_lines, depth):
    if not groups:
        return "{}"
    inner = "  " * (depth + 1)
    chunks = [
        ",\n".join(f"{inner}{dump_scalar(key)}: {emit(value, depth + 1)}" for key, value in group)
        for group in groups
    ]
    separator = ",\n" + "\n" * blank_lines
    return "{\n" + separator.join(chunks) + "\n" + "  " * depth + "}"


def format_flow(flow, findings, strategy=alphabetical_parameters):
    return emit(order_flow(flow, findings, strategy)) + "\n"


# ------------------------------------------------------------------------------- cli


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("infile", nargs="?", help="flow definition (default: stdin)")
    parser.add_argument("--out", help="output file (default: stdout)")
    args = parser.parse_args()

    try:
        handle = open(args.infile, encoding="utf-8") if args.infile else sys.stdin
        with handle:
            flow = load_flow(handle)
        findings = []
        text = format_flow(flow, findings)
    except (OSError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    for severity, message in findings:
        print(f"{severity}: {message}", file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as out:
            out.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
