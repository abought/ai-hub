"""Behavior tests for the search_batcher CLI. Library rules are covered in test_search_batcher.py."""

import gzip
import io
import json
import logging
import subprocess
import sys
import types

import pytest

import search_batcher as sb


def jsonl(*records):
    return b"".join(json.dumps(r).encode() + b"\n" for r in records)


def run(monkeypatch, argv, stdin=b""):
    """Run the CLI in-process with the given stdin bytes. Returns the exit code."""
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(stdin)))
    return sb.main(argv)


def documents(text):
    return [json.loads(line) for line in text.splitlines()]


def subjects(docs):
    return [e["subject"] for d in docs for e in d["ingest_data"]["gmeta"]]


def test_stdout_pipeline_keeps_logs_off_stdout():
    # A real process, so stdout and stderr are genuinely separate streams.
    stdin = jsonl({"id": "a"}, {"id": "b", "long": "x" * 40_000}) + b"\n" + jsonl({"id": "c"})
    result = subprocess.run(
        [sys.executable, sb.__file__, "--public", "--subject-field", "id", "--truncate"],
        input=stdin,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    docs = documents(result.stdout.decode())  # every stdout line is a document
    assert subjects(docs) == ["a", "b", "c"]
    assert all(e["visible_to"] == ["public"] for d in docs for e in d["ingest_data"]["gmeta"])
    assert len(docs[0]["ingest_data"]["gmeta"][1]["content"]["long"]) == 32_000
    assert b"truncated" in result.stderr


def test_directory_in_gzipped_chunks_out(monkeypatch, tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    # ~3.5 MB each, so two fit in a 10 MB payload and the third starts a new chunk.
    for name in ("r3", "r1", "r2"):
        (in_dir / f"{name}.json").write_text(json.dumps({"id": name, "pad": ["x" * 30_000] * 117}))
    (in_dir / "notes.txt").write_text("not a record")

    code = run(monkeypatch, ["--public", "--subject-field", "id", "--input-dir", str(in_dir), "--output-dir", str(out_dir)])

    assert code == 0
    files = sorted(out_dir.iterdir())
    assert [f.name for f in files] == ["chunk000001.json.gz", "chunk000002.json.gz"]
    texts = [gzip.decompress(f.read_bytes()).decode() for f in files]
    docs = [json.loads(t) for t in texts]
    assert subjects(docs) == ["r1", "r2", "r3"]  # sorted file order
    assert [len(d["ingest_data"]["gmeta"]) for d in docs] == [2, 1]
    # Each file is exactly the measured payload: same serialization, no trailing newline.
    assert texts == [json.dumps(d) for d in docs]


def test_invalid_visibility_is_a_usage_error_before_any_work(monkeypatch, tmp_path, capsys):
    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, ["--visible-to", "nobody", "--subject-field", "id", "--output-dir", str(out_dir)], jsonl({"id": "a"}))
    assert exc.value.code == 2
    assert "nobody" in capsys.readouterr().err
    assert not out_dir.exists()


@pytest.mark.parametrize(("policy", "emitted"), [("skip", ["a", "c"]), ("raise", [])])
def test_on_error_policy(monkeypatch, capsys, caplog, policy, emitted):
    stdin = jsonl({"id": "a"}) + b'{"id": "bad", "v": Infinity}\n' + jsonl({"id": "c"})
    with caplog.at_level(logging.ERROR):
        code = run(monkeypatch, ["--public", "--subject-field", "id", "--on-error", policy], stdin)
    assert code == 1  # an invalid record is a failed run, even when skipped
    assert subjects(documents(capsys.readouterr().out)) == emitted
    assert "bad" in caplog.text and "json_safe" in caplog.text


@pytest.mark.parametrize(
    ("stdin", "message"),
    [
        (b'{"id": "a"}\n{oops\n', "stdin line 2"),
        (b'{"other": 1}\n', "no subject"),
        (b"[1]\n", "not a dict"),
    ],
)
def test_malformed_input_stops_the_run(monkeypatch, capsys, caplog, stdin, message):
    with caplog.at_level(logging.ERROR):
        code = run(monkeypatch, ["--public", "--subject-field", "id", "--on-error", "skip"], stdin)
    assert code == 1
    assert capsys.readouterr().out == ""
    assert message in caplog.text


def test_non_empty_output_dir_is_refused(monkeypatch, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "chunk000001.json.gz"
    existing.write_bytes(b"previous run")
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, ["--public", "--subject-field", "id", "--output-dir", str(out_dir)], jsonl({"id": "a"}))
    assert exc.value.code == 2
    assert existing.read_bytes() == b"previous run"


def test_no_input_no_output(monkeypatch, capsys):
    assert run(monkeypatch, ["--public", "--subject-field", "id"], b"") == 0
    assert capsys.readouterr().out == ""
