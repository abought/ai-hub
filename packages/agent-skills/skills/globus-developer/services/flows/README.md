# Flows
## Overview
Flexible process automation for research data management; ensures that steps are followed in a compliance friendly way. 

Key details of syntax should be sourced from the [web-based docs](REFERENCES.md). When in doubt, most service-specific API action providers are thin wrappers for the underlying Globus APIs, and return the same payload fields and error behaviors. This means that if you don't see an option in the flows documentation (for an action provider, eg transfer), you should check the related docs for the parent service API. 

Additional tips and tricks may be found in [SYNTAX_TRICKS](authoring/SYNTAX_TRICKS.md).

## Provided scripts
### Flow Formatter
Applies the formatter script and runs advisory checks on a given flow definition. Run this before asking a human to review.

Usage:
```bash
uv run scripts/format_flow.py [infile.json] [--out name.json]
```

If no infile is provided, read from stdin.  If no output filename is provided, writes flow to stdout. Warnings/advisories are printed to stderr.

### Flows IDE Link
Generate a link to the Globus Flows IDE. Use this tool to help a human review your flow definition. Recommend running any appropriate lint or format tools first.
`uv run scripts/flows_ide_link.py definition.json [schema.json]`
