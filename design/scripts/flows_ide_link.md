# Flows IDE Link Tool
The Flows IDE is a tool aimed at human review of Globus Flows definitions. It allows direct links to any flow definition via url parameters. We should develop a simple CLI tool that allows the LLM to send a human 

## Generating the link
See: https://github.com/globus/flows-ide#sharing-flows-ide-state

Since flow definitions may be long, we will always encode the URL via the **gzip + base64 encoding** method.

## CLI
`flows_ide_link.py definition.json [schema.json]`

The URL is generated to stdout (exit code 0) or an error is output (exit code 1).
