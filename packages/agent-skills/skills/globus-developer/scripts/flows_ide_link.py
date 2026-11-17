#!/usr/bin/env python3
"""
Generate a Globus Flows IDE link for human review of a flow definition.

Usage: flows_ide_link.py definition.json [schema.json]
"""

import argparse
import base64
import gzip
import json
import sys
from urllib.parse import quote

IDE_URL = "https://globus.github.io/flows-ide"


def encode(text):
    """gzip + base64 encode text, then URL-encode it for use as a query parameter."""
    packed = base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")
    return quote(packed, safe="")


def read_json_text(path):
    """Return the file's text unchanged, after checking that it is valid JSON."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        json.loads(text)
    except ValueError as err:
        raise ValueError(f"{path}: invalid JSON: {err}") from err
    return text


def build_link(definition, schema=None):
    query = [f"d={encode(definition)}"]
    if schema is not None:
        query.append(f"s={encode(schema)}")
    query.append("format=gzip")
    return f"{IDE_URL}?{'&'.join(query)}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("definition", help="flow definition JSON file")
    parser.add_argument("schema", nargs="?", help="flow input schema JSON file")
    args = parser.parse_args(argv)

    try:
        definition = read_json_text(args.definition)
        schema = read_json_text(args.schema) if args.schema else None
    except (OSError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    print(build_link(definition, schema))
    return 0


if __name__ == "__main__":
    sys.exit(main())
