# Architectural guidance

## Use cases
* A flexible search and discovery system. Most commonly used to discover datasets (groups of files) or individual files, across one or more storage systems
* Flexible permissions system integrates with Globus auth. A single subject (item) can have multiple representations (entries), eg "public view" vs "log in to see additional info". This is useful for building data browsers (web apps)
* Search indices can also be used as a thin archival layer: a search index can be created that mirrors the filesystem before data is backed up offline to tape. Since the search index is a hosted service, the index remains accessible even when the tape drive is disconnected. 
  * This archival index can be populated from filesystem metadata (type, size), or by extracting metadata from structured data formats.

## Anti-patterns
* Is **not** a durable primary data store, and should not be treated as a replacement for a key-value database. (eg a data deposition flow that writes to search and nowhere else) 
  * It is built on elasticsearch, which means it provides *eventual consistency* (a read-after-write could produce stale data). 
  * Updates and deletes are also somewhat inefficient
* Although the input looks like JSON, mappings are strongly typed. Changing the type of a field requires deleting and re-creating the index, which can be slow and expensive. See best practices below.

## Best practices
* Batch updates and deletes, and check task status for result
* Populate all documents from a validated and highly structured schema, such as pydantic serializers
* Be aware of service limits, such as document and field size constraints. May not be suitable for, eg, full-text document search unless your text is short (eg abstracts).
* Field mappings are auto-determined from the first document in which a field is seen. To avoid schema issues later (such as if messy data provides a string where you expect a date), create a seed document (visible to no one) with dummy data, and make that the first document in the index. Update this seed document when the schema changes in order to "lock in" the field type.
