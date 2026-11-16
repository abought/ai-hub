# Search bulk ingest script reqts

Globus Search is an elasticsearch based search system, and as such, will operate more efficiently if records are ingested in bulk (not one at a time).

A missing piece from tooling is support for **automated batching and chunking**.

Specifically: a stream of JSON records representing the data (`content`, in globus search terms) for each individual record (subject) should be fed into an ingest script. The script emits `GIngest` payload documents, each small enough to respect the Globus Search size limits, which can then be submitted to the ingest API one at a time.

Scope: this tool builds and validates payloads. Submitting them (rate limits, task polling) is out of scope. This is partly because LLMs may be helpful at preparing content, but should not have direct access to Globus credentials.

**Key references:**
* [Ingest](https://docs.globus.org/api/search/reference/ingest/) format docs
* [Service limits](https://docs.globus.org/api/search/limits/)
* [Principal URNs](https://docs.globus.org/api/search/overview/#principal_urns)

## Design principles

* **Follow the docs literally.** Every limit is the user-facing number from the limits page. Do not substitute rounder, "closer to a power of two", or otherwise invented values.
* **Limits apply to the record as the user provided it.** We do not know how Globus stores records internally. The `content` object itself is not counted as a nesting level, a field, or anything else.
* **No silent defaults for permissions.** Visibility must always be specified by the caller.
* **Assume data follows a contract.** Records are provided by an external data layer, and if records cannot be used (eg no subject or not a dict), stop the script immediately until the contract is followed. 


## Limits

All records are checked against the [documented service limits](https://docs.globus.org/api/search/limits/) during serialization and preparation of chunked files.

## Serialization behavior

Receive a stream of records and return an iterable of `GIngest` document chunks. Each `GMetaEntry` takes its subject from a field on the record (by field name) or from a function that receives the record and calculates it.

Applies [document wrappers](https://docs.globus.org/api/search/reference/ingest) appropriately and performs field validation per documented service behaviors.

* **Records and serializers.** Records must be a dict, or serializable to a dict via the user-provided serialization function. For example: `serializer=lambda m: m.model_dump(mode="json")` would be useful for pydantic. This tool provides no library specific helpers. The resulting record must be fully JSON-serializable.
* **Every record must have a subject.** A record without a usable subject is malformed input, and stops the pipeline.
* **Visibility is required**: there is no default. `None` and an empty list are errors.
* `truncate` is a special non-default option that clips string values larger than the field limit (on a UTF-8 boundary, logging the subject and field path to indicate that data was mutated). The default is to fail the pipeline, because if fields are routinely too long to fit globus search, a human should review data quality.


* **The chunker streams records in and batches out**. 
  * Acts as an iterator that emits one chunk at a time (<= the 10,000,000-byte payload limit), until we run out of records. No records means no chunks (never an empty document).


## Validation
A validation pass ensures that the record obeys the rules of Globus search (valid permissions, field length, etc). This tool is not responsible for validating user-provided content, and it is recommended to be used in tandem with a controlled field schema (eg pydantic).

An entire record is validated at once, and all errors for that record are returned at once. A user may specify whether errors fail the entire run, or just skip the record. (see error handling section)

Limits are those on the [limits page](https://docs.globus.org/api/search/limits/).

| Rule | Check |
|---|---|
| subject | validate length |
| visible_to | validate count, and that each item is `"public"`, `"all_authenticated_users"`, or a [principal URN](https://docs.globus.org/api/search/overview/#principal_urns) |
| json safe | validate that the entry serializes as JSON (e.g. no NaN/Infinity) |
| field size | validate length of each string value, including array elements |
| depth | validate nesting depth of `content` |
| entry size | validate that the entry fits in one payload with its wrapper; an oversize chunk is never emitted |

### Error handling and logging

**Errors never vanish silently.** Every violation or change to the data is logged using the stdlib `logging` module, in every mode. Errors are logged at `ERROR` level and warnings at `WARNING`. Each message includes the record subject, the rule, and the field path.

* **Raise/skip is a caller policy.** A validation error can either raise (stop all ingest) or handle and pass (that record is excluded from a batch, and serialization of other records may continue) This is configurable by a callback mechanism that receives a list of all problems with that record.
* **Malformed input always stops the pipeline**, regardless of the raise/skip policy: for example, a record that isn't a dict (and has no serializer), or a record without a usable subject.
* **Logging is not part of the policy.** The library logs every violation through a module logger (`logging.getLogger(__name__)`) *before* calling `on_error`, so both policies, and any custom callable, leave the same record. The library never configures handlers or prints. Applications decide where logs go.
* Truncation under `truncate=True` is also logged (at `WARNING`, with subject and field path), because it changes the user's data.

Dotted keys (`{"a.b": 1}`) are treated as ordinary keys. We do not guess how Globus escapes or expands them on ingest, so they do not affect depth or any other check.


## Quirks
* JSON dumping functions should not pretty print. This is both because of the 10MB payload limit (whitespace!) and because some usages assume one line per item.
* Size calculation uses stdlib `json.dumps` with its default arguments. That is what the Globus SDK does (via `requests`), so we measure what actually gets sent. Do not use alternate serializers like ujson or orjson, because some of the tools that handle `GIngest` documents might parse and re-serialize before uploading the file. 
  * Because `json.dumps` defaults to `ensure_ascii=True`, non-ASCII text grows when serialized. Size is always measured on the serialized form, never estimated from the Python objects.
* Size is measured on the uncompressed document.
* If data is truncated, attempt to split at the edge of a unicode character, rather than mid-byte.
  * Truncation is character-safe, not grapheme-safe: it never splits a character, but can separate a combining mark from its base letter, or split a multi-part emoji.


## Invocation
This script is intended to produce json blobs that may be composed flexible with other tools. It is not responsible for communicating with the Globus Search API.

### Direct function call
Useful in data pipelines: call `to_gmeta_ingest` with an iterable of records. This provides full control over logging, serialization, and per-record subject/visible_to/error handling rules. The LLM can treat this script as a library for custom usage.

### CLI
`search_batcher.py [--visible-to | --public | --all-authenticated] --subject-field [--input-dir] [--output-dir] [--truncate] [--on-error raise|skip]`

The CLI mode is coarse grained, eg it assumes every record has the same:
1. permissions: exactly one of `--visible-to` (a list, via nargs), `--public`, or `--all-authenticated` (convenience flags for the well-known values `public` and `all_authenticated_users`). They are mutually exclusive, and one of these options is required.
2. subject ID from fixed, top-level field name (`--subject-field`, required).
3. Options:
   * `--truncate`: clip oversized string fields instead of rejecting the record. Off by default. A human must sign off on changing content. For example, this might be used with a dataset where a small number of records are pathologically malformed in one field.
   * `--on-error raise|skip` (default `raise`): see "Error handling and logging".
4. Can receive inputs via:
   * `--input-dir` of individual records, one per file (`*.json` files containing a complete record, not recursive, files read in sorted order)
   * stdin, one JSON record per line (JSONL). Blank lines are skipped.
   * Malformed input from either source indicates a problem with the input source, and should cause an immediate failure because the contract of usage is not met.
5. Can deliver outputs via:
   1. `--output-dir` directory of one file per chunk, with zero-padded names like `chunk000001.json.gz`. The directory is created if missing, and must be empty if it exists.
      * This mode is designed for very large batches, so every chunk should always be compressed with `.gz`. It is the responsibility of the script that consumes these files to uncompress them before sending them to globus search.
   2. Stream to stdout, one line per chunk (each line is one complete `GIngest` document).

Script errors should be logged to stderr (so as not to interfere with stdout output mode for serialized records). The CLI exits non-zero if any error occurred, including records skipped under `--on-error skip`.
