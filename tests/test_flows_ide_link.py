"""Behavior tests for the flows_ide_link CLI."""

import base64
import gzip
from urllib.parse import parse_qs, urlsplit

import flows_ide_link as fil

DEFINITION = '{\n  "StartAt": "A",\n  "States": {"A": {"Type": "Pass", "End": true}}\n}\n'
SCHEMA = '{"type": "object"}'


def decode(value):
    return gzip.decompress(base64.b64decode(value)).decode("utf-8")


def run(capsys, *argv):
    code = fil.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_definition_only(tmp_path, capsys):
    path = tmp_path / "d.json"
    path.write_text(DEFINITION)
    code, out, _ = run(capsys, str(path))
    assert code == 0
    url = urlsplit(out.strip())
    assert f"{url.scheme}://{url.netloc}{url.path}" == fil.IDE_URL
    query = parse_qs(url.query)
    assert query["format"] == ["gzip"]
    assert "s" not in query
    assert decode(query["d"][0]) == DEFINITION  # contents encoded as-is


def test_definition_and_schema(tmp_path, capsys):
    d, s = tmp_path / "d.json", tmp_path / "s.json"
    d.write_text(DEFINITION)
    s.write_text(SCHEMA)
    code, out, _ = run(capsys, str(d), str(s))
    query = parse_qs(urlsplit(out.strip()).query)
    assert code == 0
    assert decode(query["d"][0]) == DEFINITION
    assert decode(query["s"][0]) == SCHEMA


def test_missing_file(tmp_path, capsys):
    code, out, err = run(capsys, str(tmp_path / "nope.json"))
    assert (code, out) == (1, "")
    assert err.startswith("error:")


def test_invalid_json(tmp_path, capsys):
    d = tmp_path / "d.json"
    d.write_text("{not json")
    code, out, err = run(capsys, str(d))
    assert (code, out) == (1, "")
    assert "invalid JSON" in err


def test_invalid_schema(tmp_path, capsys):
    d, s = tmp_path / "d.json", tmp_path / "s.json"
    d.write_text(DEFINITION)
    s.write_text("oops")
    code, out, err = run(capsys, str(d), str(s))
    assert (code, out) == (1, "")
    assert "s.json" in err
