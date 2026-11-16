# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Prepares serialized globus search records, with helpful options for batching and validation

This is a helper module that is missing from existing globus search tooling. It facilitates
    more efficient bulk ingest. Because it prepares json files, it may be used by LLM
     scripts without requiring direct access to Globus credentials.
"""
from __future__ import annotations

import argparse
import codecs
import gzip
import json
import logging
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)

# Globus Search limits: https://docs.globus.org/api/search/limits/
MAX_PAYLOAD_BYTES = 10_000_000  # whole serialized GIngest document
MAX_ENTRY_BYTES = 10_000_000  # one GMetaEntry
MAX_FIELD_BYTES = 32_000  # one string value, UTF-8
MAX_SUBJECT_BYTES = 500  # explicitly UTF-8
MAX_VISIBLE_TO = 100
MAX_DEPTH = 20  # nesting levels

def _json_size(obj: Any) -> int:
    """
    Payload size. This matches the serialization used by the Globus SDK (`json.dumps`) when it reads, parses,
        and re-serializes the data. Even if your script used a more efficient serialization, this ensures
        the byte count per chunk will work with other globus tooling.
    """
    return len(json.dumps(obj, allow_nan=False).encode("utf-8"))


_WRAPPER_OVERHEAD = _json_size(
    {"ingest_type": "GMetaList", "ingest_data": {"gmeta": []}}
)
_SEPARATOR_BYTES = len(", ")  # what json.dumps puts between list items


def _utf8_len(text: str) -> int:
    # surrogatepass: lone surrogates are valid in parsed JSON; measure them instead of crashing.
    return len(text.encode("utf-8", errors="surrogatepass"))


@dataclass(frozen=True)
class Violation:
    subject: str
    rule: str
    path: str  # field path within `content`; blank for entry-level rules
    message: str


class ValidationError(Exception):
    """A record failed validation. Carries every violation found for that record."""
    def __init__(self, subject: str, violations: list[Violation]):
        self.subject = subject
        self.violations = violations
        detail = "; ".join(f"[{v.rule}] {v.path + ': ' if v.path else ''}{v.message}" for v in violations)
        super().__init__(f"{subject}: {detail}")


def raise_on_error(error: ValidationError) -> None:
    """``on_error`` handler: stop the stream."""
    raise error


def skip_on_error(error: ValidationError) -> None:
    """``on_error`` handler: drop the record and continue. (The violations were already logged.)"""
    return None


def _report(violations: list[Violation]) -> None:
    for v in violations:
        logger.error(
            "%s: [%s]%s %s",
            v.subject,
            v.rule,
            f" at {v.path!r}:" if v.path else ":",
            v.message,
        )


# --------------------------------------------------------------------------- paths


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def _format_path(parts: tuple[str | int, ...]) -> str:
    """``("a", 0, "b")`` -> ``"a[0].b"``. Keys are strings and list indexes are ints."""
    out = ""
    for part in parts:
        out = f"{out}[{part}]" if isinstance(part, int) else _join(out, part)
    return out


##############
# validators
##############
_Issue = tuple[str, str]


def _validate_subject(subject: str) -> list[Violation]:
    size = _utf8_len(subject)
    if size > MAX_SUBJECT_BYTES:
        return [Violation(subject, "subject", "", f"subject is {size} bytes (limit {MAX_SUBJECT_BYTES})")]
    return []


def _validate_visible_to(subject: str, visible_to: Any) -> list[Violation]:
    if not isinstance(visible_to, (list, tuple)):
        return [Violation(subject, "visible_to", "", "visible_to must be a list of strings")]
    if not visible_to:
        return [Violation(subject, "visible_to", "", "visible_to must not be empty")]
    found: list[Violation] = []
    if len(visible_to) > MAX_VISIBLE_TO:
        found.append(
            Violation(subject, "visible_to", "", f"visible_to has {len(visible_to)} items (limit {MAX_VISIBLE_TO})")
        )
    bad = [p for p in visible_to if not _is_principal(p)]
    if bad:
        found.append(
            Violation(
                subject,
                "visible_to",
                "",
                f"visible_to items must be {' or '.join(map(repr, _VISIBILITY_VALUES))}, or a principal URN "
                f"starting with {' or '.join(map(repr, _PRINCIPAL_URN_PREFIXES))}; got {bad!r}",
            )
        )
    return found


# https://docs.globus.org/api/search/overview/#principal_urns
# Iteration order (used only in error messages) is unstable across runs; accepted as unimportant.
_VISIBILITY_VALUES = frozenset({"public", "all_authenticated_users"})
_PRINCIPAL_URN_PREFIXES = ("urn:globus:auth:identity:", "urn:globus:groups:id:")


def _is_principal(value: Any) -> bool:
    """Validate visible_to entries"""
    if not isinstance(value, str):
        return False
    if value in _VISIBILITY_VALUES:
        return True
    return any(value.startswith(prefix) and len(value) > len(prefix) for prefix in _PRINCIPAL_URN_PREFIXES)


def _validate_string(value: str) -> list[_Issue]:
    """Field-level rules for one string value."""
    # UTF-8 uses at most 4 bytes per character, so shorter strings can't exceed the limit and
    # don't need encoding to check.
    if len(value) > MAX_FIELD_BYTES // 4:
        size = _utf8_len(value)
        if size > MAX_FIELD_BYTES:
            return [("field_size", f"value is {size} bytes (limit {MAX_FIELD_BYTES})")]
    return []


def _validate_content(subject: str, content: dict, *, truncated: bool = False) -> list[Violation]:
    """
    Validate the `content` record, including field-level checks

    ``truncated=True`` means the strings were already clipped by ``truncate_fields``, so the
    field length checks are guaranteed to pass and are skipped.
    """
    found: list[Violation] = []
    # (path parts, node, level). Paths are only formatted when there is something to report.
    stack: list[tuple[tuple[str | int, ...], Any, int]] = [((), content, 0)]
    while stack:
        path, node, level = stack.pop()
        if isinstance(node, str):
            if truncated:
                continue
            for rule, message in _validate_string(node):
                found.append(Violation(subject, rule, _format_path(path), message))
        elif isinstance(node, dict):
            if node and level >= MAX_DEPTH:
                found.append(
                    Violation(subject, "depth", _format_path(path), f"nesting exceeds {MAX_DEPTH} levels")
                )
                continue
            stack.extend((path + (k,), v, level + 1) for k, v in reversed(node.items()))
        elif isinstance(node, (list, tuple)):
            stack.extend((path + (i,), node[i], level) for i in range(len(node) - 1, -1, -1))
    return found


def _validate_entry_size(subject: str, entry_bytes: int) -> list[Violation]:
    """``entry_bytes`` is the serialized size of the entry alone."""
    if entry_bytes > MAX_ENTRY_BYTES:
        return [Violation(subject, "entry_size", "", f"entry is {entry_bytes} bytes (limit {MAX_ENTRY_BYTES})")]
    if _WRAPPER_OVERHEAD + entry_bytes > MAX_PAYLOAD_BYTES:
        return [
            Violation(
                subject,
                "entry_size",
                "",
                f"entry is {entry_bytes} bytes; with the {_WRAPPER_OVERHEAD}-byte wrapper it cannot fit "
                f"in one {MAX_PAYLOAD_BYTES}-byte payload",
            )
        ]
    return []


def _validate_record(entry: dict[str, Any], *, truncated: bool = False) -> tuple[list[Violation], int]:
    """
    Run every validator on an assembled entry. Returns the violations and the entry's serialized size.
    """
    label = entry["subject"]
    violations = _validate_subject(label) + _validate_visible_to(label, entry["visible_to"])
    try:
        entry_bytes = _json_size(entry)
    except (TypeError, ValueError) as exc:
        violations.append(Violation(label, "json_safe", "", f"entry is not JSON-serializable: {exc}"))
        return violations, 0
    violations += _validate_content(label, entry["content"], truncated=truncated)
    violations += _validate_entry_size(label, entry_bytes)
    return violations, entry_bytes


# --------------------------------------------------------------------------- truncation


def _clip_utf8(text: str, limit: int) -> str:
    """
    Truncate a UTF-8 string, while trying to avoid splitting a character mid-bytes
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="surrogatepass")
    # final=False causes the decoder to hold back incomplete multi-byte sequences
    return decoder.decode(text.encode("utf-8", errors="surrogatepass")[:limit], final=False)


def truncate_fields(content: Any, subject: str = "") -> Any:
    """
    Return a copy of ``content`` with every string clipped to ``MAX_FIELD_BYTES`` UTF-8 bytes,
      on a character boundary. Each clipped field is logged.
    """

    def clip(node: Any, path: str) -> Any:
        if isinstance(node, str):
            size = _utf8_len(node)
            if size <= MAX_FIELD_BYTES:
                return node
            clipped = _clip_utf8(node, MAX_FIELD_BYTES)
            logger.warning(
                "%s: field %r truncated from %d to %d bytes", subject, path, size, _utf8_len(clipped)
            )
            return clipped
        if isinstance(node, dict):
            return {k: clip(v, _join(path, k)) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [clip(v, f"{path}[{i}]") for i, v in enumerate(node)]
        return node

    return clip(content, "")


# --------------------------------------------------------------------------- entries


def _build_entry(
    record: dict | object,
    subject_field: str | Callable[[dict], str],
    visible_to: list[str] | Callable[[dict], list[str]],
    serializer: Callable[[object], dict] | None,
    truncate: bool,
) -> tuple[dict[str, Any], int]:
    """Validate and serialize one record. Returns the entry and its serialized size in bytes."""
    if isinstance(record, dict):
        content = record
    elif serializer is None:
        raise TypeError(f"record is {type(record).__name__}, not a dict, and no serializer was provided")
    else:
        content = serializer(record)
        if not isinstance(content, dict):
            raise TypeError(f"serializer returned {type(content).__name__}, not a dict")

    subject = subject_field(content) if callable(subject_field) else content.get(subject_field)
    if not isinstance(subject, str) or not subject:
        # Every entry needs a subject. Without one the input is malformed, so stop the pipeline.
        raise ValueError(f"record has no subject: expected a non-empty string, got {subject!r}")
    visible = visible_to(content) if callable(visible_to) else visible_to
    if truncate:
        # Before validation, so the content that is validated is exactly the content emitted.
        content = truncate_fields(content, subject)

    entry: dict[str, Any] = {"subject": subject, "visible_to": visible, "content": content}
    violations, entry_bytes = _validate_record(entry, truncated=truncate)
    if violations:
        _report(violations)
        raise ValidationError(subject, violations)
    entry["visible_to"] = list(visible)  # our own copy; serializes identically
    return entry, entry_bytes


def to_gmeta_entry(
    record: dict | object,
    subject_field: str | Callable[[dict], str],
    visible_to: list[str] | Callable[[dict], list[str]],
    *,
    serializer: Callable[[object], dict] | None = None,
    truncate: bool = False,
) -> dict[str, Any]:
    """
    Validate and serialize just one record to a ``GMetaEntry`` dict: ``{"subject", "visible_to", "content"}``.
    """
    entry, _ = _build_entry(record, subject_field, visible_to, serializer, truncate)
    return entry


def to_gmeta_ingest(
    records: Iterable[dict | object],
    subject_field: str | Callable[[dict], str],
    visible_to: list[str] | Callable[[dict], list[str]],
    *,
    serializer: Callable[[object], dict] | None = None,
    truncate: bool = False,
    on_error: Callable[[ValidationError], None] = raise_on_error,
) -> Iterator[dict[str, Any]]:
    """
    Stream records into batches of serialized and validated ``GIngest`` documents
    """
    batch: list[dict[str, Any]] = []
    doc_bytes = _WRAPPER_OVERHEAD  # exact size of the document if it were emitted now

    for record in records:
        try:
            entry, entry_bytes = _build_entry(record, subject_field, visible_to, serializer, truncate)
        except ValidationError as err:
            # Flexible callback can either stop the pipeline, or do something else like logging
            on_error(err)
            continue

        added = entry_bytes + (_SEPARATOR_BYTES if batch else 0)
        if batch and doc_bytes + added > MAX_PAYLOAD_BYTES:
            yield _to_gingest_wrapper(batch)
            batch = []
            doc_bytes = _WRAPPER_OVERHEAD
            added = entry_bytes
        batch.append(entry)
        doc_bytes += added

    if batch:
        yield _to_gingest_wrapper(batch)


def _to_gingest_wrapper(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ingest_type": "GMetaList", "ingest_data": {"gmeta": entries}}


# --------------------------------------------------------------------------- CLI


class InputError(Exception):
    """The CLI's input can't be parsed (not valid UTF-8 JSON, or unreadable)."""


def _parse_record(raw: bytes, source: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{source}: not a valid UTF-8 JSON record: {exc}") from None


def _read_jsonl(stream: BinaryIO) -> Iterator[Any]:
    """One JSON record per line. Blank lines are skipped."""
    for line_number, line in enumerate(stream, start=1):
        if line.strip():
            yield _parse_record(line, f"stdin line {line_number}")


def _read_dir(path: Path) -> Iterator[Any]:
    """One JSON record per ``*.json`` file, not recursive, in sorted order."""
    for file in sorted(path.glob("*.json")):
        try:
            raw = file.read_bytes()
        except OSError as exc:
            raise InputError(f"{file}: {exc}") from None
        yield _parse_record(raw, str(file))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch JSON records into Globus Search GIngest documents, within the service size limits. "
        "Reads JSONL from stdin, or one record per file from --input-dir. "
        "Writes one document per line to stdout, or gzipped chunk files to --output-dir.",
    )
    visibility = parser.add_mutually_exclusive_group(required=True)
    visibility.add_argument("--visible-to", nargs="+", metavar="PRINCIPAL", help="principal URNs (or well-known values)")
    visibility.add_argument("--public", action="store_true", help='same as --visible-to public')
    visibility.add_argument(
        "--all-authenticated", action="store_true", help="same as --visible-to all_authenticated_users"
    )
    parser.add_argument("--subject-field", required=True, help="top-level record field holding the subject")
    parser.add_argument("--input-dir", type=Path, help="read one record per *.json file (default: JSONL on stdin)")
    parser.add_argument(
        "--output-dir", type=Path, help="write gzipped chunk files here (default: one document per line on stdout)"
    )
    parser.add_argument(
        "--truncate", action="store_true", help="clip oversized string fields instead of rejecting the record"
    )
    parser.add_argument(
        "--on-error",
        choices=("raise", "skip"),
        default="raise",
        help="on an invalid record: stop the run (default), or skip the record and continue",
    )
    args = parser.parse_args(argv)

    if args.public:
        args.visible_to = ["public"]
    elif args.all_authenticated:
        args.visible_to = ["all_authenticated_users"]
    # Visibility is the same for every record in CLI mode, so check it once, up front.
    problems = _validate_visible_to("--visible-to", args.visible_to)
    if problems:
        parser.error("; ".join(v.message for v in problems))

    if args.input_dir is not None and not args.input_dir.is_dir():
        parser.error(f"--input-dir {args.input_dir} is not a directory")
    if args.output_dir is not None and args.output_dir.exists():
        if not args.output_dir.is_dir():
            parser.error(f"--output-dir {args.output_dir} is not a directory")
        if any(args.output_dir.iterdir()):
            parser.error(f"--output-dir {args.output_dir} must be empty")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # stdout may carry output documents, so all messages go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s: %(message)s")

    records = _read_dir(args.input_dir) if args.input_dir else _read_jsonl(sys.stdin.buffer)

    policy = raise_on_error if args.on_error == "raise" else skip_on_error
    failures = 0

    def on_error(error: ValidationError) -> None:
        nonlocal failures
        failures += 1
        policy(error)

    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        documents = to_gmeta_ingest(
            records, args.subject_field, args.visible_to, truncate=args.truncate, on_error=on_error
        )
        for number, document in enumerate(documents, start=1):
            text = json.dumps(document)  # the same serialization the size limit was measured on
            if args.output_dir is None:
                sys.stdout.write(text + "\n")
            else:
                # No trailing newline: the file's contents are exactly the measured payload.
                with gzip.open(args.output_dir / f"chunk{number:06d}.json.gz", "wt", encoding="utf-8") as f:
                    f.write(text)
    except ValidationError:
        return 1  # the library already logged the violations
    except (InputError, TypeError, ValueError) as exc:
        # Malformed input (unparseable, not an object, or no subject) stops the run.
        logger.error("%s", exc)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
