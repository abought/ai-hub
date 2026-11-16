import json
import logging

import pytest

import search_batcher as sb
from search_batcher import (
    ValidationError,
    skip_on_error,
    to_gmeta_entry,
    to_gmeta_ingest,
)

PUBLIC = ["public"]
# Example principals from https://docs.globus.org/api/search/overview/#principal_urns
IDENTITY = "urn:globus:auth:identity:46bd0f56-e24f-11e5-a510-131bef46955c"
GROUP = "urn:globus:groups:id:fdb38a24-03c1-11e3-86f7-12313809f035"


def entry(record, **kwargs):
    kwargs.setdefault("visible_to", PUBLIC)
    return to_gmeta_entry(record, "id", **kwargs)


def doc_size(doc):
    return len(json.dumps(doc).encode())


def chain(levels, leaf=1):
    """A dict with exactly `levels` nesting levels: {"k": {"k": ... leaf}}."""
    content = leaf
    for _ in range(levels):
        content = {"k": content}
    return content


def nested(levels):
    """A record (with an "id") whose content has exactly `levels` nesting levels."""
    return {"id": "s", **chain(levels)} if levels else {"id": "s"}


def rules(exc_info):
    return [v.rule for v in exc_info.value.violations]


def subjects(docs):
    return [e["subject"] for d in docs for e in d["ingest_data"]["gmeta"]]


def record_with_entry_size(subject, size):
    """A record whose serialized GMetaEntry is exactly `size` bytes."""

    def measure(content):
        return len(json.dumps({"subject": subject, "visible_to": PUBLIC, "content": content}).encode())

    chunk = 30_000
    extra = size - measure({"id": subject, "pad": [""]})
    assert extra >= 0
    pad = []
    while extra > chunk + 4:  # a full chunk adds its characters, two quotes, and a ", "
        pad.append("x" * chunk)
        extra -= chunk + 4
    pad.append("x" * extra)
    content = {"id": subject, "pad": pad}
    assert measure(content) == size
    return content


# ------------------------------------------------------------------ to_gmeta_entry


class TestEntry:
    def test_basic_entry(self):
        record = {"id": "doi:1", "title": "t"}
        assert entry(record) == {"subject": "doi:1", "visible_to": PUBLIC, "content": record}

    def test_subject_and_visibility_callables_receive_serialized_dict(self):
        seen = []
        result = to_gmeta_entry(
            {"id": "a", "owner": "u1"},
            lambda r: seen.append(r) or f"subj:{r['id']}",
            lambda r: [f"urn:globus:auth:identity:{r['owner']}"],
        )
        assert result["subject"] == "subj:a"
        assert result["visible_to"] == ["urn:globus:auth:identity:u1"]
        assert seen == [{"id": "a", "owner": "u1"}]

    def test_input_is_not_mutated(self):
        record = {"id": "a", "n": ["x" * 40_000]}
        before = json.dumps(record)
        entry(record, truncate=True)
        assert json.dumps(record) == before

    def test_serializer_converts_non_dict(self):
        class Model:
            id = "m1"

        result = entry(Model(), serializer=lambda m: {"id": m.id})
        assert result["content"] == {"id": "m1"}

    def test_non_dict_without_serializer_is_type_error(self):
        with pytest.raises(TypeError):
            entry(["not", "a", "dict"])

    def test_serializer_returning_non_dict_is_type_error(self):
        with pytest.raises(TypeError):
            entry(object(), serializer=lambda m: "nope")

    def test_visible_to_has_no_default(self):
        with pytest.raises(TypeError):
            to_gmeta_entry({"id": "a"}, "id")


class TestSubject:
    """A missing or unusable subject is malformed input: ValueError, not a per-record violation."""

    def test_missing_field(self):
        with pytest.raises(ValueError, match="no subject"):
            entry({"other": 1})

    @pytest.mark.parametrize("value", ["", None, 5])
    def test_bad_values(self, value):
        with pytest.raises(ValueError, match="no subject"):
            entry({"id": value})

    def test_bad_value_from_callable(self):
        with pytest.raises(ValueError, match="no subject"):
            to_gmeta_entry({"id": "a"}, lambda r: None, PUBLIC)

    def test_length_limit_is_a_per_record_violation_in_bytes(self):
        entry({"id": "a" * 500})
        with pytest.raises(ValidationError) as exc:
            entry({"id": "a" * 501})
        assert rules(exc) == ["subject"]
        assert exc.value.subject == "a" * 501  # an over-length subject still names its record
        with pytest.raises(ValidationError):
            entry({"id": "é" * 251})  # 251 characters, 502 bytes
        entry({"id": "é" * 250})


class TestVisibleTo:
    @pytest.mark.parametrize(
        "value",
        [PUBLIC, ["all_authenticated_users"], [IDENTITY], [GROUP], [IDENTITY, GROUP, "public"]],
    )
    def test_valid(self, value):
        assert entry({"id": "a"}, visible_to=value)["visible_to"] == value

    @pytest.mark.parametrize(
        "value",
        [
            [],
            None,
            "public",  # a bare string, not a list
            [""],
            [1],
            ["public", None],
            ["Public"],  # values are exact
            ["ALL_AUTHENTICATED_USERS"],
            ["urn:globus:auth:identity:"],  # prefix with no ID
            ["urn:globus:groups:id:"],
            ["urn:globus:groups:id"],  # missing the trailing ":"
            ["urn:example:user:1"],  # some other URN scheme
            ["46bd0f56-e24f-11e5-a510-131bef46955c"],  # an ID without its prefix
        ],
    )
    def test_invalid(self, value):
        with pytest.raises(ValidationError) as exc:
            entry({"id": "a"}, visible_to=value)
        assert set(rules(exc)) == {"visible_to"}

    def test_message_lists_the_bad_items(self):
        with pytest.raises(ValidationError) as exc:
            entry({"id": "a"}, visible_to=["public", "nobody", "urn:globus:groups:id:"])
        message = exc.value.violations[0].message
        assert "'nobody'" in message and "'urn:globus:groups:id:'" in message
        assert "got ['nobody', 'urn:globus:groups:id:']" in message

    def test_item_limit(self):
        entry({"id": "a"}, visible_to=[IDENTITY] * 100)
        with pytest.raises(ValidationError) as exc:
            entry({"id": "a"}, visible_to=[IDENTITY] * 101)
        assert rules(exc) == ["visible_to"]

    def test_callable_result_is_validated(self):
        with pytest.raises(ValidationError):
            to_gmeta_entry({"id": "a"}, "id", lambda r: [])

    def test_tuple_is_emitted_as_list(self):
        assert entry({"id": "a"}, visible_to=("public", GROUP))["visible_to"] == ["public", GROUP]

    def test_entry_has_its_own_copy(self):
        shared = ["public"]
        result = entry({"id": "a"}, visible_to=shared)
        shared.append(GROUP)
        assert result["visible_to"] == ["public"]


class TestFieldSizes:
    def test_limit_is_utf8_bytes(self):
        entry({"id": "a", "f": "x" * 32_000})
        with pytest.raises(ValidationError):
            entry({"id": "a", "f": "x" * 32_001})
        entry({"id": "a", "f": "é" * 16_000})  # 32,000 bytes
        with pytest.raises(ValidationError):
            entry({"id": "a", "f": "é" * 16_001})

    def test_four_byte_characters_at_the_shortcut_boundary(self):
        # Strings of <= 8,000 characters are not encoded (4 bytes/char max). Just above that, they are.
        assert sb._validate_string("😀" * 8_000) == []  # exactly 32,000 bytes
        assert [rule for rule, _ in sb._validate_string("😀" * 8_001)] == ["field_size"]

    def test_reports_path_including_array_elements(self):
        with pytest.raises(ValidationError) as exc:
            entry({"id": "a", "outer": {"items": ["ok", "x" * 32_001]}})
        violation = exc.value.violations[0]
        assert violation.rule == "field_size"
        assert violation.path == "outer.items[1]"
        assert violation.subject == "a"

    def test_lone_surrogate_does_not_crash(self):
        entry({"id": "a", "f": "\ud800"})

    def test_truncate_clips_on_a_character_boundary(self, caplog):
        record = {"id": "a", "f": "é" * 20_000, "nested": ["y" * 40_000]}
        with caplog.at_level(logging.WARNING, logger="search_batcher"):
            result = entry(record, truncate=True)
        content = result["content"]
        assert len(content["f"].encode()) == 32_000
        assert content["f"] == "é" * 16_000
        assert content["nested"] == ["y" * 32_000]
        assert "truncated" in caplog.text and "nested[0]" in caplog.text

    def test_truncate_odd_boundary_never_splits_a_character(self):
        # 31,999 ASCII bytes then a 2-byte character straddling the limit.
        text = "x" * 31_999 + "é" + "tail"
        result = entry({"id": "a", "f": text}, truncate=True)
        assert result["content"]["f"] == "x" * 31_999

    def test_values_in_limit_are_untouched_by_truncate(self):
        record = {"id": "a", "f": "short"}
        assert entry(record, truncate=True)["content"] == record


class TestDepth:
    def test_top_level_keys_are_level_one(self):
        assert sb._validate_content("s", {"a": 1}) == []
        assert sb._validate_content("s", chain(1)) == []

    def test_twenty_levels_allowed_twenty_one_rejected(self):
        entry(nested(20))
        with pytest.raises(ValidationError) as exc:
            entry(nested(21))
        assert rules(exc) == ["depth"]

    def test_reports_the_dict_whose_keys_are_too_deep(self):
        found = sb._validate_content("s", chain(21))
        assert [(v.rule, v.path) for v in found] == [("depth", ".".join(["k"] * 20))]

    def test_one_violation_per_over_deep_dict(self):
        too_deep = chain(20, leaf={"a": 1, "b": 2, "c": 3})  # three keys at level 21
        assert len(sb._validate_content("s", too_deep)) == 1
        two_branches = {"x": chain(20, leaf={"a": 1}), "y": chain(20, leaf={"b": 1})}
        assert [v.path.split(".")[0] for v in sb._validate_content("s", two_branches)] == ["x", "y"]

    def test_arrays_add_no_depth(self):
        deep_lists = 1
        for _ in range(30):
            deep_lists = [deep_lists]
        assert sb._validate_content("s", {"v": deep_lists}) == []

    def test_objects_inside_arrays_add_depth(self):
        assert sb._validate_content("s", {"x": [{"y": [{"z": 1}]}]}) == []  # 3 levels: x, y, z
        assert sb._validate_content("s", {"a": [chain(19)]}) == []  # 1 + 19 = 20
        found = sb._validate_content("s", {"a": [chain(20)]})  # 1 + 20 = 21
        assert [(v.rule, v.path) for v in found] == [("depth", "a[0]." + ".".join(["k"] * 19))]

    def test_dotted_keys_are_ordinary_keys(self):
        assert sb._validate_content("s", {"a.b.c.d": 1}) == []

    def test_depth_is_still_checked_when_truncated(self):
        found = sb._validate_content("s", chain(21), truncated=True)
        assert [v.rule for v in found] == ["depth"]
        with pytest.raises(ValidationError) as exc:
            entry(nested(21), truncate=True)
        assert rules(exc) == ["depth"]


class TestTruncatedFlag:
    def test_skips_string_rules(self):
        content = {"f": "x" * 40_000}
        assert [v.rule for v in sb._validate_content("s", content)] == ["field_size"]
        assert sb._validate_content("s", content, truncated=True) == []


class TestJsonSafe:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), object()])
    def test_unserializable_values(self, bad):
        with pytest.raises(ValidationError) as exc:
            entry({"id": "a", "f": bad})
        assert rules(exc) == ["json_safe"]

    def test_cycle(self):
        record = {"id": "a"}
        record["self"] = record
        with pytest.raises(ValidationError) as exc:
            entry(record)
        assert rules(exc) == ["json_safe"]


class TestEntrySize:
    def test_exact_boundary(self):
        target = sb.MAX_PAYLOAD_BYTES - sb._WRAPPER_OVERHEAD
        docs = list(to_gmeta_ingest([record_with_entry_size("s", target)], "id", PUBLIC))
        assert [doc_size(d) for d in docs] == [sb.MAX_PAYLOAD_BYTES]
        with pytest.raises(ValidationError) as exc:
            entry(record_with_entry_size("s", target + 1))
        assert rules(exc) == ["entry_size"]

    def test_oversized_entry_is_an_error_not_an_oversize_chunk(self):
        record = {"id": "a", "pad": ["x" * 32_000] * 400}  # ~12.8 MB in valid fields
        with pytest.raises(ValidationError) as exc:
            entry(record)
        assert rules(exc) == ["entry_size"]

    def test_each_branch(self):
        fits = sb.MAX_PAYLOAD_BYTES - sb._WRAPPER_OVERHEAD
        assert sb._validate_entry_size("s", fits) == []

        [payload] = sb._validate_entry_size("s", fits + 1)
        assert "cannot fit" in payload.message

        [too_big] = sb._validate_entry_size("s", sb.MAX_ENTRY_BYTES + 1)
        assert f"(limit {sb.MAX_ENTRY_BYTES})" in too_big.message


class TestTruncateBeforeValidate:
    def long_fields_record(self, subject):
        # 100 fields of 60,000 characters: ~6.0 MB as given, ~3.2 MB once each is clipped to 32,000.
        return {"id": subject, "f": ["x" * 60_000 for _ in range(100)]}

    def test_record_valid_only_after_truncation_is_accepted(self):
        with pytest.raises(ValidationError):
            entry(self.long_fields_record("a"))
        result = entry(self.long_fields_record("a"), truncate=True)
        assert all(len(v) == 32_000 for v in result["content"]["f"])

    def test_chunking_uses_the_truncated_size(self):
        # Measured before truncation (~6.0 MB each), these would need three documents.
        records = [self.long_fields_record(f"s{i}") for i in range(3)]
        docs = list(to_gmeta_ingest(records, "id", PUBLIC, truncate=True))
        assert [len(d["ingest_data"]["gmeta"]) for d in docs] == [3]
        assert doc_size(docs[0]) <= sb.MAX_PAYLOAD_BYTES


class TestReporting:
    def test_every_violation_is_logged_with_subject_and_rule(self, caplog):
        with caplog.at_level(logging.WARNING, logger="search_batcher"):
            with pytest.raises(ValidationError) as exc:
                entry({"id": "doi:bad", "a": "x" * 32_001, "b": "y" * 32_001}, visible_to=[])
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 3
        assert all("doi:bad" in r.getMessage() for r in errors)
        assert rules(exc) == ["visible_to", "field_size", "field_size"]
        assert exc.value.subject == "doi:bad"
        message = str(exc.value)  # lists every violation
        assert message.count("[visible_to]") == 1 and message.count("[field_size]") == 2


class TestFormatPath:
    @pytest.mark.parametrize(
        ("parts", "expected"),
        [((), ""), (("a",), "a"), (("a", 0, "b"), "a[0].b"), (("a", 0, 1), "a[0][1]"), (("a", "b"), "a.b")],
    )
    def test_format(self, parts, expected):
        assert sb._format_path(parts) == expected


# ------------------------------------------------------------------ to_gmeta_ingest


class TestChunker:
    def test_no_records_no_documents(self):
        assert list(to_gmeta_ingest([], "id", PUBLIC)) == []

    def test_document_shape(self):
        docs = list(to_gmeta_ingest([{"id": "a"}, {"id": "b"}], "id", PUBLIC))
        assert docs == [
            {
                "ingest_type": "GMetaList",
                "ingest_data": {
                    "gmeta": [
                        {"subject": "a", "visible_to": PUBLIC, "content": {"id": "a"}},
                        {"subject": "b", "visible_to": PUBLIC, "content": {"id": "b"}},
                    ]
                },
            }
        ]

    def test_consumes_records_lazily(self):
        pulled = []

        def source():
            for i in range(3):
                pulled.append(i)
                yield {"id": str(i)}

        stream = to_gmeta_ingest(source(), "id", PUBLIC)
        assert pulled == []
        next(stream)
        assert pulled == [0, 1, 2]

    def test_splits_and_preserves_order_within_limit(self):
        records = [record_with_entry_size(f"s{i}", 3_000_000) for i in range(7)]
        docs = list(to_gmeta_ingest(records, "id", PUBLIC))
        assert [len(d["ingest_data"]["gmeta"]) for d in docs] == [3, 3, 1]
        assert all(doc_size(d) <= sb.MAX_PAYLOAD_BYTES for d in docs)
        assert subjects(docs) == [f"s{i}" for i in range(7)]

    def test_accounting_is_exact_at_the_limit(self):
        first = record_with_entry_size("a", 4_000_000)
        # Size the second so the two-entry document is exactly the limit.
        room = sb.MAX_PAYLOAD_BYTES - sb._WRAPPER_OVERHEAD - 4_000_000 - sb._SEPARATOR_BYTES
        exact = record_with_entry_size("b", room)

        docs = list(to_gmeta_ingest([first, exact], "id", PUBLIC))
        assert len(docs) == 1
        assert doc_size(docs[0]) == sb.MAX_PAYLOAD_BYTES

        over = record_with_entry_size("b", room + 1)
        docs = list(to_gmeta_ingest([first, over], "id", PUBLIC))
        assert [len(d["ingest_data"]["gmeta"]) for d in docs] == [1, 1]

    def test_non_ascii_content_is_measured_as_serialized(self):
        # Each é is 2 bytes raw but a 6-byte escape once serialized. Four records are ~4.8 MB of raw
        # UTF-8 (one document if mis-measured) but ~14.4 MB serialized (must split).
        records = [{"id": f"s{i}", "f": ["é" * 15_000 for _ in range(40)]} for i in range(4)]
        docs = list(to_gmeta_ingest(records, "id", PUBLIC))
        assert len(docs) > 1
        assert all(doc_size(d) <= sb.MAX_PAYLOAD_BYTES for d in docs)

    def test_serializer_is_used_for_non_dicts(self):
        class Model:
            def __init__(self, id):
                self.id = id

        docs = list(
            to_gmeta_ingest([Model("m1"), {"id": "d1"}], "id", PUBLIC, serializer=lambda m: {"id": m.id})
        )
        assert subjects(docs) == ["m1", "d1"]


class TestChunkerErrors:
    BAD = {"id": "bad", "f": "x" * 32_001}

    def records(self):
        return [{"id": "a"}, self.BAD, {"id": "c"}]

    def test_default_raises_and_drops_partial_batch(self):
        stream = to_gmeta_ingest(self.records(), "id", PUBLIC)
        with pytest.raises(ValidationError) as exc:
            list(stream)
        assert exc.value.subject == "bad"
        assert list(stream) == []  # generator is finished; "a" was never emitted

    def test_skip_continues_and_logs(self, caplog):
        with caplog.at_level(logging.ERROR, logger="search_batcher"):
            docs = list(to_gmeta_ingest(self.records(), "id", PUBLIC, on_error=skip_on_error))
        assert subjects(docs) == ["a", "c"]
        assert "bad" in caplog.text and "field_size" in caplog.text

    def test_custom_handler_receives_error_and_return_means_skip(self):
        received = []
        docs = list(to_gmeta_ingest(self.records(), "id", PUBLIC, on_error=received.append))
        assert [e.subject for e in received] == ["bad"]
        assert subjects(docs) == ["a", "c"]

    def test_handler_raising_stops_the_stream(self):
        def stop(error):
            raise RuntimeError("stop")

        with pytest.raises(RuntimeError):
            list(to_gmeta_ingest(self.records(), "id", PUBLIC, on_error=stop))

    def test_oversized_entry_goes_to_handler(self):
        too_big = {"id": "big", "pad": ["x" * 32_000] * 400}
        received = []
        docs = list(to_gmeta_ingest([{"id": "a"}, too_big, {"id": "c"}], "id", PUBLIC, on_error=received.append))
        assert [e.subject for e in received] == ["big"]
        assert [v.rule for v in received[0].violations] == ["entry_size"]
        assert subjects(docs) == ["a", "c"]

    def test_non_json_value_is_per_record_bad_data(self, caplog):
        records = [{"id": "a"}, {"id": "inf", "v": float("inf")}, {"id": "c"}]
        with caplog.at_level(logging.ERROR, logger="search_batcher"):
            docs = list(to_gmeta_ingest(records, "id", PUBLIC, on_error=skip_on_error))
        assert subjects(docs) == ["a", "c"]
        assert "inf" in caplog.text and "json_safe" in caplog.text

    def test_type_error_always_propagates(self):
        received = []
        with pytest.raises(TypeError):
            list(to_gmeta_ingest([{"id": "a"}, "not a dict"], "id", PUBLIC, on_error=received.append))
        assert received == []

    @pytest.mark.parametrize("bad", [{"other": 1}, {"id": ""}, {"id": None}, {"id": 5}])
    def test_missing_subject_always_propagates(self, bad):
        received = []
        with pytest.raises(ValueError, match="no subject"):
            list(to_gmeta_ingest([{"id": "a"}, bad, {"id": "c"}], "id", PUBLIC, on_error=received.append))
        assert received == []


# ------------------------------------------------------------------ truncation


def utf8_len(text):
    return len(text.encode("utf-8", errors="surrogatepass"))


class TestClipUtf8:
    @pytest.mark.parametrize(
        ("text", "limit", "expected"),
        [
            ("xxxxx", 3, "xxx"),  # ASCII: one byte per character
            ("abé", 3, "ab"),  # é is 2 bytes; cutting at 3 would split it
            ("abé", 4, "abé"),  # exact fit is unchanged
            ("a😀", 4, "a"),  # 😀 is 4 bytes
            ("a😀", 5, "a😀"),
            ("ab\ud83d", 3, "ab"),  # lone surrogate (half an emoji, e.g. from JS truncation) at the cut
            ("ab\ud83dz", 5, "ab\ud83d"),  # ...kept whole when it fits
        ],
    )
    def test_cuts_only_on_character_boundaries(self, text, limit, expected):
        assert sb._clip_utf8(text, limit) == expected

    def test_lone_surrogates_before_the_cut_are_kept(self):
        # A POSIX filename with non-UTF-8 bytes, as os.fsdecode returns it. errors="ignore" would
        # silently drop the \udce9 characters; the incremental decoder only affects the cut.
        name = "r\udce9sum\udce9.pdf"
        assert sb._clip_utf8(name, 8) == "r\udce9sum"

    @pytest.mark.parametrize("text", ["plain ascii", "é" * 7, "a😀b😀c", "x\ud83dy\udce9z", "日本語テキスト"])
    def test_result_is_the_longest_prefix_within_the_limit(self, text):
        for limit in range(utf8_len(text) + 1):
            clipped = sb._clip_utf8(text, limit)
            assert text.startswith(clipped)
            assert utf8_len(clipped) <= limit
            longer = text[: len(clipped) + 1]
            assert len(longer) == len(clipped) or utf8_len(longer) > limit

    def test_known_limitation_combining_marks_can_be_separated(self):
        # Truncation is character-safe, not grapheme-safe: "e" + COMBINING ACUTE renders as "é".
        assert sb._clip_utf8("é", 1) == "e"


class TestTruncateFields:
    def test_clips_and_logs_each_field(self, caplog):
        content = {"a": "😀" * 8_001, "b": ["ok", "é" * 16_001]}  # 32,004 and 32,002 bytes
        with caplog.at_level(logging.WARNING, logger="search_batcher"):
            result = sb.truncate_fields(content, "subj")
        assert result == {"a": "😀" * 8_000, "b": ["ok", "é" * 16_000]}
        assert "'a'" in caplog.text and "'b[1]'" in caplog.text and "subj" in caplog.text

    def test_keeps_lone_surrogates_in_clipped_fields(self):
        text = "\udce9" + "x" * 32_000  # 3 + 32,000 bytes
        assert sb.truncate_fields({"f": text})["f"] == "\udce9" + "x" * 31_997

    def test_input_is_not_modified(self):
        content = {"f": "x" * 40_000}
        sb.truncate_fields(content)
        assert content == {"f": "x" * 40_000}
