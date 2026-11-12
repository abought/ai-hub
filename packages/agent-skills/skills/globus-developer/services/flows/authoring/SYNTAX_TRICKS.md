# Syntax guide
Globus Flows are defined as finite state machines using a JSON-based language.

The core [authoring docs](https://docs.globus.org/api/flows/authoring-flows/) and [examples](https://docs.globus.org/api/flows/examples/) provide a wealth of information. This syntax guide aims to fill in certain missing pieces, including examples of syntax tricks to enable certain non-obvious behaviors.


## Defensive programming: how to handle values that may be missing
Sometimes, the output of a prior step contains a payload with fields that may or may not be present. For example, a Globus Transfer `stat` API response may not always have the `details.code` field in the response.

If you attempt to use the `variable.$` syntax to fetch a field that is not present, flows will hard-fail in a way that could bypass error handling altogether. This would be very bad!

The solution (in `ExpressionEval` and `Action` states) is to use `variable.=` syntax, which allows lazily evaluated python expressions that can check for a parameter's presence before accessing the value:

```json
{
  "Type": "ExpressionEval",
      "Parameters": {
        "source_is_dir.=": "('code' not in prev.GetInfoSourcePath.details or prev.GetInfoSourcePath.details.code != 'NotFound') and prev.GetInfoSourcePath.details.type == 'dir'"
      }
}
```

## Computed params are executed as Python
One subtlety of flows is that `.=` expressions are executed as python. In addition to the built-in [functions](https://docs.globus.org/api/flows/authoring-flows/expressions/#functions) in the docs, a less obvious feature is that you are able to use routine python language constructs such as f-strings, list-indexing, and string instance methods. This makes it possible to calculate even complex values during flow evaluation/

### Extending lists and dictionaries
Sometimes, a basic set of metadata might need to be extended with additional calculated fields during the flow. This can be done using `variable.=` expressions and the list or dictionary unpacking operators.

```json
{
  "Type": "ExpressionEval",
      "Parameters": {
        "content.=": "{**search_metadata, 'artifact_id': _context.run_id, 'artifacts': meta.search.file_payload }"
      }
}
```

### String manipulation
String concatenation and manipulation can be used to construct new values in a more readable way: eg f-strings instead of string concatenation. 

String methods are also a useful way to massage the difference in data formats between two providers. For example, `_context['user_id']'` is provided as a URN, but some services (such as file permissions grants) expect a bare UUID.

```json
{
  "Type": "ExpressionEval",
      "Parameters": {
        "dest_path.=": "f'/{share_level}/{dataset_id}/'",
        "_private_user_uuid.=": "_context['user_id'].rpartition(':')[-1]",
      }
}
```

For f-strings, note the single quote (indicating a python string) inside the double-quote (indicating a field value in the JSON based states language).

## Loops
Globus Flows syntax does not have a built-in for-loop or map construct. However, one can be approximated by combining a stored counter, a choice state, and an expression-eval increment. See [documentation example](https://docs.globus.org/api/flows/examples/looping_batched_move/)

