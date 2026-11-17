# Architectural guidance
Flows can be used to coordinate multiple resources. It also enables several architectural patterns that cannot be realized by controlling resources directly.

## Automatically generate a web-based UI for actions
Globus Flows are defined as a JSONSchema format, including typed input schemas. Using the `https://app.globus.org` website, the flow input schema can automatically be used to create a web-based form to guide a user through a process. See [guided input schemas](https://docs.globus.org/api/flows/input-schema/) in the documentation for a list of form controls, custom widgets, and information on how user help text is displayed. This is helpful for quickly building out user-facing services on the hosted Globus SaaS platform, without deploying your own infrastructure.


## Use timers to run a flow on a schedule
If your service requires regular management (such as nightly backups or cleaning up temp files), you can use [Globus Timers to schedule a flow to run on a schedule](https://docs.globus.org/api/timers/getting-started/schedule-a-flow-run/). (see [SDK usage](https://globus-sdk-python.readthedocs.io/en/latest/examples/timer_management/index.html))

## Controlling how a resource is used
### `RunAs: Flow` prevents user from accessing a resource in any other way
Flows can be used to provide controlled access, allowing a protected resource to be used in *only* a specific intended way.

For example, a flow might allow copying data and automatically adding it to a search index- but *not* allow users to edit or delete existing search entries.

This is accomplished by the `RunAs` option, available in all `Action` states. (see [docs](https://docs.globus.org/api/flows/authoring-flows/roles/)) In this pattern:

* A user is allowed to run the flow. They are provided access to their source data collection (and a "temporary" destination data location, as in the [two-stage globus transfer example](https://docs.globus.org/api/flows/examples/two_hop/))
* The flow is allowed access to the search index, the "temporary" file storage, and the permanent data repository
* When the flow runs, the data is validated based on user-defined safeguards inside the flow. If it passes, it is moved from the source (using user permissions) and to the final archive + search index (using flow permissions). The flow follows only the instructions in its intended usage, which prevents 

In addition to controlling what operations are run against a resource, a flow can also use its own identity-based permissions to grant a user temporary access (eg, granting a user access to only one folder, only for the duration of the flow). However, if the flow fails before cleanup is run, temporary permissions can become permanent and are not cleaned up. Completely controlling resources with the Flow is more work to set up and maintain, but it provides stronger guarantees.   

### GCS integration: Flow transfer source/destination guides users to follow a process
Flows integrates tightly with other Globus services such as transfer. It can be used to [require a flow for data movement](https://docs.globus.org/guides/recipes/collection-flows/) at  either the source or destination end. This can be used for compliance review (human-in-the-loop approval steps outbound) or to mandate that the data inbound is in a specific form (eg [compress a folder before transfer](https://docs.globus.org/api/flows/examples/compute_tar_and_transfer/)).

Note that this approach is a more narrow restriction than true permissions based control. When using the GCS `--flow-transfer-source` or `--flow-transfer-destination` options, you are able to customize data *movement*, but other operations may still be allowed. In this mode, a user retains broader collection permissions, and can still do other things like delete files in that collection. Use `RunAs: Flow` or temporary permissions grants if your goal is to completely lock down access to a collection.
