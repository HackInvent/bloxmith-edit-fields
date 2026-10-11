# Edit Fields

Set, rename, copy or remove fields of each JSON message without code. Edit Fields
reshapes a JSON message with an ordered list of declarative operations: give a
field a typed value or a value built from other fields, rename or copy a field,
drop what the next block does not need, fill in defaults, add to a list. It can
also keep only the fields you name, and edit every item of an array. Operations
are settings, not code: nothing in a message is ever evaluated, fetched or run.

<!-- block-metadata:start -->
[![Block version: 0.1.0](https://img.shields.io/badge/block-0.1.0-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->

[![Edit Fields — reshape JSON messages](media/thumbnail.webp)](media/cover.png)

## When to use it

Use Edit Fields between two blocks whose JSON shapes do not match: rename
`fname` to `first_name`, add a constant `source: "web"`, build `full_name` from two
fields, remove a password or a bulky field, or keep just the three fields an API
expects. Before this block, such changes needed a Python block. Use **Filter**,
**If** or **Switch** to decide where a message goes; Edit Fields only changes what
it contains.

## Ports

| Direction | Port | Meaning |
| --- | --- | --- |
| Input | `in` | Required message: JSON (`application/json`, `message/*`) or text parsed as JSON (`text/plain`, see `parse_json`) |
| Output | `out` | The edited message (or array) as compact JSON, `application/json` |
| Output | `error` | Only with `on_error: route`: a JSON error document, see [Errors](#errors) |

For each message exactly one of `out` and `error` is published, or nothing when
the activation fails. A port that publishes nothing for a message has its previous
value cleared. An empty or whitespace-only message is not edited.

## Settings

| Setting | Values | Default |
| --- | --- | --- |
| `mode` | `edit` (start from the message) or `keep_only` (start from an empty object) | `edit` |
| `apply_to` | `message` or `each_item` (edit every element of an array) | `message` |
| `array_path` | Where the array is, with the path syntax below; empty when the message itself is the array (`each_item` only) | empty |
| `max_items` | 1 to 10,000: largest array accepted (`each_item` only) | `10000` |
| `operations` | 1 to 64 operation objects, run in order | `set processed = true` |
| `on_missing` | `skip` or `fail`: what a missing source does (see below) | `skip` |
| `parse_json` | `auto`, `always` or `never` | `auto` |
| `on_error` | `fail` or `route` | `fail` |

### Operations

Operations run **in order**, each on the result of the previous ones.

| `op` | Fields | Effect |
| --- | --- | --- |
| `set` | `path`, `value`, `value_type` | Writes the value at `path`, replacing what was there |
| `default` | `path`, `value`, `value_type` | Writes the value only when `path` is missing or `null` |
| `append` | `path`, `value`, `value_type` | Adds the value at the end of the array at `path`; creates `[value]` when `path` is missing or `null` |
| `copy` | `from`, `to` | Copies the value at `from` to `to` |
| `rename` | `from`, `to` | Moves the value at `from` to `to` (removed from `from`) |
| `remove` | `path` | Removes the field or array element at `path` |

```json
{"op": "set", "path": "customer.tier", "value": "gold", "value_type": "string"}
{"op": "rename", "from": "fname", "to": "first_name"}
{"op": "remove", "path": "password"}
```

An operation object accepts only its own fields: a misspelled field is refused, not
ignored. `append` of an array value adds that array as one element.

### Paths

The path syntax is the one of the If, Switch and Filter conditions, plus quoted
names:

- names separated by dots, `[index]` for array elements: `customer.email`,
  `items[0].price`, `[0].name` when the message is an array;
- `["name"]` for a name that holds dots, brackets or surrounding spaces, written as
  a JSON string: `headers["content-type"]`, `["utm.source"]`, `data["a[1]"]`;
- the empty path is the whole message (or the whole item with `each_item`);
- names are matched exactly, case included; at most 32 steps and 512 characters.

Reading a path that does not resolve gives **missing**, never an error. Writing
creates what is missing:

- a missing or `null` intermediate name becomes an object, or an array when the
  next step is an index (which must then be `[0]`);
- an array index must exist (the element is replaced) or equal the array length
  (the value is appended); `items[5]` on a two-element array is an error;
- writing through any other value (`a.b` when `a` is a string or a number) is an
  error, so data is never silently overwritten.

`set` with an empty path replaces the whole message. `remove` and `rename` need a
field: their paths cannot be empty.

### Value types

| `value_type` | `value` | Written as |
| --- | --- | --- |
| `string` | text | the text |
| `number` | a JSON number such as `42`, `-0.5`, `12.50`, `1e3` | the number, **exactly as written** (`12.50` stays `12.50`) |
| `boolean` | `true` or `false` | the boolean |
| `null` | ignored | `null` |
| `json` | JSON text, for example `{"a":1}`, `[1,2]`, `"text"` | that JSON value, number literals kept |
| `template` | text with `{{ path }}` references | see below |

In hand-written settings a `json` value may also be the JSON value itself instead
of its text; a text is always read as JSON text, so the JSON string `"x"` is
written `"\"x\""`. The editor stores numbers and JSON as the text you typed.

### Templates

A template is text with references to the **received message** (or the received
item): `Hello {{ customer.first_name }}`.

- References always read the message as it was received, never the result of
  earlier operations. A template can therefore still use a field that an earlier
  operation renamed or removed.
- A template that is exactly one reference, such as `{{ items }}`, keeps the JSON
  type of what it references: an array stays an array, `12.50` stays the number
  `12.50`.
- Otherwise the references are inserted into the text: strings as they are,
  other values as JSON text (`12.50`, `true`, `null`, `{"a":1}`).
- References use the path syntax above, except that a name with spaces must be
  quoted (`{{ ["first name"] }}`), and a quoted name cannot contain `}}`. Write
  `\{{` for literal braces; any other `{{` opens a reference. There are no
  expressions, functions or code: `{{ price * 2 }}` is refused.

### Missing sources (`on_missing`)

A source is the `from` of `copy` and `rename`, the `path` of `remove`, and every
template reference.

- `skip` (default): an operation whose source does not resolve is left out, and a
  trace says so (`[edit-fields] Operation 3 (remove coupon) skipped: path coupon
  does not resolve.`); the following operations still run.
- `fail`: the activation fails (or is routed, see `on_error`).

### `keep_only`

`mode: keep_only` starts from an empty object instead of the message: only what
`set` and `copy` write is output, like a projection. In this mode `copy` reads its
`from` in the **received message**, so `copy id → id` keeps `id`. `rename`,
`remove`, `default` and `append` act on the result being built (rename a field you
copied, for example). Templates read the received message in both modes.

### `each_item`

`apply_to: each_item` edits every element of a JSON array, in order, with paths
relative to the element: write `price`, not `orders[0].price`. The array is the
message itself or the value at `array_path`; the output is the edited **array
only**, not the surrounding document. Templates read the received element. It is
an error (see [Errors](#errors)) when there is no array to edit: the message is
not JSON or not an array, `array_path` does not resolve or does not hold an array,
or the array has more than `max_items` elements. Arrays are never truncated, and
with `keep_only` each element starts from an empty object.

### Message parsing (`parse_json`)

- `auto` (default): text that is valid standard JSON is parsed, whatever its
  content type; other text, including `NaN`, is kept as one JSON string.
- `always`: the message must be valid JSON, otherwise the activation fails.
- `never`: the received text is kept as one JSON string, even when it looks like
  JSON; with `keep_only`, `copy` from the empty path to `text` wraps it as
  `{"text": "..."}`. Refused with `each_item`, which needs an array.

## Errors

An error stops the whole activation: an operation that cannot write (`a.b` when
`a` is a string, an index past the end, `append` on a non-array), a missing source
with `on_missing: fail`, a message that is not JSON with `parse_json: always`, no
array for `each_item`, or a limit. **A partial result is never published**: with
`each_item`, one failing element means no output for the whole array.

- `on_error: fail` (default): the activation fails with a translated message that
  names the operation, its short form and the element, for example
  `Operation 2 (set address.city = "Lyon") failed on item [3]: cannot write at
  address.city: address is of type string, not object.` Nothing is published. In
  Active Runtime, like any failed block, this stops the block's worker for the run.
- `on_error: route`: the activation succeeds and publishes only `error`:

  ```json
  {"code":"path_blocked","message":"Operation 2 (set address.city = \"Lyon\") on item [3]: cannot write at address.city: address is of type string, not object.","operation":2,"op":"set","item":3}
  ```

  `operation` and `op` are `null` for message-level errors, `item` is `null`
  outside `each_item`. Wire `error` before choosing `route`: an unwired error port
  hides failures.

Invalid settings always fail, at preparation, whatever `on_error` says. Messages,
traces and error documents name operations, paths and JSON types, never message
content. Stable codes: `message_too_large`, `message_not_json`, `message_not_text`,
`too_deep`, `result_too_deep`, `array_path_missing`, `array_path_not_array`,
`not_array`, `too_many_items`, `source_missing`, `reference_missing`,
`path_blocked`, `index_out_of_range`, `append_not_array`, `output_too_large`,
`too_much_data`, `internal`.

## Framing decisions

The specification leaves some choices to the block. Edit Fields makes these:

- **Sequential working document**: `copy`, `rename`, `remove`, `default` and
  `append` see the result of the previous operations; only templates read the
  received message. `keep_only` copies read the received message, because the
  result starts empty.
- **`on_missing` covers template references too**, so a missing field never
  becomes an empty or invented value: `skip` leaves the whole `set`, `default` or
  `append` out, `fail` stops.
- **`on_error` is `fail` or `route`**, the convention of the workspace blocks that
  have an `error` port: `route` publishes a content-free document on `error` and
  succeeds; `fail` publishes nothing and fails.
- **`null` counts as missing** for `default`, for `append` (a new array is
  created) and for intermediate names when writing (replaced by an object or an
  array). Any other value in the way is an error, never overwritten silently.
- **`rename` keeps the field's position** when it stays in the same object and the
  new name is free; otherwise it removes the field, then writes it. Renaming a
  field into itself (`a` to `a.b`) wraps it. `from` and `to` must differ, except for a `copy` in `keep_only`, where `copy id → id` keeps `id`.
- **Every number read from JSON keeps its literal** (`12.50`, `1e3`, `-0`, long
  identifiers), in fields that are kept, copied or referenced by a template.
- **Key order** is kept; new fields are added at the end of their object.
- **Limits are fixed**: 64 operations, 32 path steps, 32 nesting levels for the
  message and the result, 8 MiB for the message and for the output, 64 references
  per template, 65,536 characters per configured value, and at most 8,000,000 JSON
  values copied in one activation (a guard against operations that copy the
  message into itself repeatedly). `max_items` (at most 10,000) bounds `each_item`.
- **Settings are checked when applied and when a run is prepared**: unknown
  operations or fields, paths, values that do not fit their `value_type`,
  templates, empty `remove`/`rename` paths, identical `from`/`to`, and, with
  `each_item`, `array_path`, `max_items` and `parse_json: never`. `array_path` and
  `max_items` are ignored with `apply_to: message`; unusable values are reset to
  their defaults when you Apply. Unknown top-level settings are ignored, so
  framework-owned settings never break a run.
- The default operation, `set processed = true`, is an example to replace.

## Editing

The modal and the inspector share one editor:

- `mode` and `apply_to` switches; `array_path` and `max_items` appear with
  `each_item`;
- one row per operation: `op`, then only the fields it uses (`path`, or `from` and
  `to`; `value_type` and `value`, with a `true`/`false` choice for booleans and no
  value for `null`), with **Add an operation**, move up/down and **Remove**;
- live validation: each row and setting shows its problem in the current language
  while you type, using the same Python code as the run;
- **Test the edits**: paste a sample message and see the input and the output side
  by side, the skipped operations, or the error the run would raise (and the error
  document with `route`), with the settings being edited, before Apply. Nothing is
  saved, sent or run.

Changes stay local until **Apply**; Cancel/Close discards them. Restart a loaded
Run to use new settings. The node card shows the mode, the first operation in
short form and how many follow, for example `Keep only · Each item of orders: copy
id → id and 2 more`.

## Example

Clean up a web form submission:

```json
{
  "mode": "edit",
  "apply_to": "message",
  "operations": [
    {"op": "rename", "from": "fname", "to": "first_name"},
    {"op": "set", "path": "full_name", "value": "{{ fname }} {{ lname }}", "value_type": "template"},
    {"op": "copy", "from": "meta[\"utm.source\"]", "to": "source"},
    {"op": "remove", "path": "meta"},
    {"op": "default", "path": "country", "value": "FR", "value_type": "string"},
    {"op": "append", "path": "tags", "value": "web", "value_type": "string"},
    {"op": "set", "path": "status.code", "value": "201", "value_type": "number"},
    {"op": "remove", "path": "coupon"}
  ],
  "on_missing": "skip",
  "parse_json": "auto",
  "on_error": "fail"
}
```

Received message:

```json
{"fname": "Ada", "lname": "Lovelace", "total": 12.50, "tags": ["form"], "country": null,
 "meta": {"utm.source": "newsletter", "session": "s-1"}}
```

`out`:

```json
{"first_name":"Ada","lname":"Lovelace","total":12.50,"tags":["form","web"],"country":"FR","full_name":"Ada Lovelace","source":"newsletter","status":{"code":201}}
```

The template still reads `fname` although the first operation renamed it, `12.50`
keeps its literal, `first_name` keeps the position of `fname`, and the last
operation is skipped with a trace because there is no `coupon`.

With `"mode": "keep_only", "apply_to": "each_item", "array_path": "orders"` and the
operations `copy id → id` and `set total = {{ amount }}` (template), the message
`{"orders": [{"id": 7, "amount": 9.90, "note": "x"}]}` gives `[{"id":7,"total":9.90}]`.

## Execution, limits and dependencies

- One activation per message in One Shot and Active Runtime (Active Runtime uses
  the `on_each_event` policy, so a burst of messages is edited one by one).
- No files, no persistent state, no network, no secrets, no third-party package:
  Python standard library only (`json`, `re`).
- Limits: see the framing decisions above; the editor sample is limited to 262,144
  characters and its displayed texts to 100,000 characters each.

## Security

Operations are trusted block settings, never read from incoming messages. Messages
are untrusted data: they are parsed as JSON at most, never executed, and their
content is not copied into errors, traces, error documents or run metadata by
this block (the run report holds counts only). The published output is, of
course, the edited message.

## Tests

Three package-owned suites run against a disposable, pinned BloxSmith 1.0.9 in the
private workspace:

- `F5.01`: the path syntax with quoted names, every operation and value type,
  write rules, number literals, templates (types, interpolation, escapes, reads
  from the received message), `keep_only`, `each_item`, `on_missing`, every limit
  (10,000 items, depth, 8 MiB output, copy budget), settings validation at Apply and
  preparation, direct runtime results with `fail` and `route`, the editor
  contract, the English/French catalogs and this README's example;
- `F5.02`: real graph runs in One Shot and Active Runtime, with bundled, managed
  and linked package installations: edit, `keep_only`, `each_item` (from a text
  message and from the List block), `fail` and `route`, refusal at preparation, a
  two-message burst through Event OR, and Stop of a loaded run;
- `F8.01`: the real modal and inspector in Chromium for managed and linked
  packages: operation rows and their fields, live validation, the sample test,
  Apply/Cancel, persistence, keyboard use, English and French at desktop and
  mobile widths.

Not qualified: browsers other than Chromium, other BloxSmith versions, and
messages close to the 8 MiB limit (the suites go up to 10,000 array items).
