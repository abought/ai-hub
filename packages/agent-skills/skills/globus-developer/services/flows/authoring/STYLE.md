# Style guide for globus flows
Globus Flows use a JSON-formatted schema for describing the state machine. This guide provides rules on format, readability, and naming conventions. This skill provide a script (`format_flow.py`) that can be used to apply and check these rules.

## Key Ordering
Each section of a flow definition should be ordered in logical groupings that describe steps according to  _what_, _who_, _where_, and _how_. Things that are specific to one kind of step (_properties_) typically appear last within a state, so that a human can quickly understand the role of every step by scanning the same preamble for every flow step.

Details and exact ordering are below.

### Top level
The top level definition typically contains three keys, in the following order: `Comment`, `StartAt`, and `States`. If there are additional keys at the top level, `States` should always appear last. 

### State order
States should appear in roughly the order they are executed. This may be determined by starting at the specified `StartAt`, and listing the next step.  In the case of branching logic, the branches should appear close together in the file, but the order is not rigorously prescribed.

Terminal states (`End: true` or `Type: Fail`) should appear at the end of the flow definition regardless of other rules, because they may be reached via more than one branch.  Listing terminal states at the end of the file makes it easier for a human to scroll to key points quickly. 

Generic non-terminal success and failure message states (`Type: Pass`) should appear after other branches, but before the final terminal states.  

### Within each `States` member
Within each state, keys should appear in the following order according to a logical, human-readable grouping, as follows. A line break must be added between sections for further readability. Keys that are not present are ignored and do not throw an error.

* _What_: `Type`, `ActionUrl`, `End`, `Error`, `Cause`,  `Comment`
* _Who_: `RunAs`
* _Where_: `Next`, `ResultPath`, `WaitTime`, `Default` , `Choices`, `ExceptionOnActionFailure`, `Catch`,
  * Within a `Catch` state, order keys are: `Next`, `ResultPath`, `ErrorEquals`
  * The error handling states (`ExceptionOnActionFailure`, `Catch`) should have a blank line between them and other items in this section.
* _How_: `Parameters`
  * Within each `Parameters` object, the optional section `__Private_Parameters` should be listed last

### Future directions
This guide does not yet specify ordering of the `Parameters` section, which is dependent on the action provider. Future improvements may extend the ordering rules. For now, parameters are sorted lexicographically, excluding trailing `.$` words when determining sort order.

## Naming conventions
### States 
Each individual state should be given a name in `CamelCase` syntax.

 Generic failure or messaging states (as described above) should be prefixed with `Success` or `Fail` to make their role clear. This does not preclude use of these prefixes elsewhere.


### Result storage
A given flow step can store its results in one of several possible places. These are advisory only, and the human author has final discretion on where results are stored.

* States that define global variables (such as filesystem paths used by many other steps) should store results under `$.meta`. This will remain visible to all subsequent steps.
* States whose result is needed by the next step may be stored in `$.prev`, allowing it to overwrite previous states (TODO author: double check how keys are merged/replaced in amazon states language/ globus flows)
  * If multiple steps need to store persistent results, they may do so via sub-keys named after the state: `$.prev.FirstState`, `$.prev.SecondState`, etc. This mechanism should be used sparingly, because step functions have a limit on the size of the retained state.
* At the end, the result may be stored in `$` as no further context is needed. This is also a useful reset in the scenario where prior states generated very large output, beyond the size limit of what Amazon Step Functions may safely pass between steps.
* States that generate an error should store their result under `$.error`. (or `$._private_error`- see note below)


**A note on visibility**: in rare cases, a flow may choose to expose private resources (such as a compute function) to the user in a controlled way. Such "system internals" are typically indicated via the `"RunAs": "Flow"` directive. In this case, the naming rules for result path may optionally be prefixed by `_private`: (`$._private_error`, `$._private_prev`). This has the effect of hiding private resource information from the user-visible logs. A linter should suggest that as a warning-level message if a state is `RunAs: Flow`, but leave the final choice to the human author.
