# Key documentation: Flows

* Overview: https://docs.globus.org/api/flows/
* Examples: https://docs.globus.org/api/flows/examples/
* API specification: [human readable](https://flows.globus.org/redoc) / [OpenAPI format](https://flows.globus.org/openapi.json).
* Running actions [as different users](https://docs.globus.org/api/flows/authoring-flows/roles/) / looking up the "Flow Identity" string
* [Recipe: Require a flow for data movement](https://docs.globus.org/guides/recipes/collection-flows/)
* [Schedule a flow to run with globus timers](https://docs.globus.org/api/timers/getting-started/schedule-a-flow-run/) / [SDK usage](https://globus-sdk-python.readthedocs.io/en/latest/examples/timer_management/index.html)
* [Automatically build a user interface from  your flow definition](https://docs.globus.org/api/flows/input-schema/)

## Tooling and resources
* [Flows IDE](https://globus.github.io/flows-ide/): A GUI tool for authoring flows.
    * It may sometimes be useful to convert your generated flow into a shareable link to facilitate human review. See [instructions for sharing URLs](https://github.com/globus/flows-ide#sharing-flows-ide-state)`. Use `format=gzip`.
