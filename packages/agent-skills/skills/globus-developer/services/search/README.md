# Globus Search
Hosted search service built on Elasticsearch. Flexible data discovery tool allows user-defined schemas: may be used to describe a mix of files and datasets stored across multiple locations.

Search is not a replacement for a database (see [architectural guidance](ARCHITECTURE.md)), but it is a helpful add-on that enables powerful facet and aggregation features. Globus manages the infrastructure for you. Globus Search is included with every Globus Subscription.

For more information, see [references](REFERENCES.md).


## Provided scripts
* `scripts/search_batcher.py --help` - Generates chunked batches of records for ingest into globus search. Automatic chunking and service limit validation as a "missing piece" of Globus tooling, and this aims to simplify common mechanical operations.  May be used as a CLI tool, or by importing the method `to_gmeta_ingest(records_iterable)` in your own scripts for full control of all options.
    * Proof of concept; please file a bug report if problems observed in real usage.
