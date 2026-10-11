"""Edit Fields settings, operations and editor helpers.

Pure functions only: no framework import and no I/O. Operations are declarative
settings: nothing in a message is ever evaluated as code, and a failure never
produces a partial result.
"""

from __future__ import annotations

from collections.abc import Mapping
import re

from . import paths
from .paths import MISSING, EditError, error

MODES = ("edit", "keep_only")
APPLY_TO = ("message", "each_item")
OPS = ("set", "copy", "rename", "remove", "default", "append")
VALUED = frozenset({"set", "default", "append"})
MOVES = frozenset({"copy", "rename"})
VALUE_TYPES = ("string", "number", "boolean", "null", "json", "template")
ON_MISSING = ("skip", "fail")
PARSE_MODES = ("auto", "always", "never")
ERROR_POLICIES = ("fail", "route")
OPERATION_KEYS = {
    "set": ("op", "path", "value", "value_type"),
    "default": ("op", "path", "value", "value_type"),
    "append": ("op", "path", "value", "value_type"),
    "copy": ("op", "from", "to"),
    "rename": ("op", "from", "to"),
    "remove": ("op", "path"),
}

MAX_OPERATIONS = 64
MAX_ITEMS = 10_000
MAX_VALUE_CHARS = 65_536
MAX_REFERENCES = 64
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_COPIED_VALUES = 8_000_000
MAX_SAMPLE_CHARS = 262_144
MAX_PREVIEW_CHARS = 100_000
CANCEL_EVERY = 256
DEFAULTS = {
    "mode": "edit",
    "apply_to": "message",
    "array_path": "",
    "max_items": MAX_ITEMS,
    "operations": [{"op": "set", "path": "processed", "value": True, "value_type": "boolean"}],
    "on_missing": "skip",
    "parse_json": "auto",
    "on_error": "fail",
}
_SETTING_CHOICES = {
    "mode": (MODES, "mode must be edit or keep_only."),
    "apply_to": (APPLY_TO, "apply_to must be message or each_item."),
    "on_missing": (ON_MISSING, "on_missing must be skip or fail."),
    "parse_json": (PARSE_MODES, "parse_json must be auto, always or never."),
    "on_error": (ERROR_POLICIES, "on_error must be fail or route."),
}
_ARRAY_PATH_CODES = {"path_type": "array_path_type", "path_too_long": "array_path_too_long",
                     "path_too_deep": "array_path_too_deep", "path_invalid": "array_path_invalid",
                     "path_spaces": "array_path_spaces"}


class Skip(Exception):
    """An operation whose source does not resolve, with ``on_missing: skip``."""

    def __init__(self, code, message, params=None):
        super().__init__(message)
        self.code = code
        self.message = message.format(**(params or {}))
        self.params = dict(params or {})


def thaw(value):
    """Plain dicts/lists for read-only mappings and tuples (prepare-time snapshots)."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def settings(raw):
    """Manifest defaults merged with stored settings; framework-owned keys are kept as they are."""
    return {**DEFAULTS, **thaw(raw or {})}


# --------------------------------------------------------------------------- templates


_QUOTED = re.compile(r'\["(?:[^"\\]|\\.)*"\]')
_SPACE = re.compile(r"\s")
# Braces are passed as parameters: catalog texts never hold literal braces.
BRACES = {"open": "{{", "close": "}}", "escape": "\\{{"}
JSON_EXAMPLE = {"example": '{"a":1}'}


def _shown(reference):
    """A template reference as written in messages, bounded."""
    text = reference if len(reference) <= 80 else reference[:79] + "…"
    return "{{ " + text + " }}"


class Template:
    """Parsed ``{{ path }}`` template; references read the original input."""

    __slots__ = ("parts", "single")

    def __init__(self, parts):
        self.parts = tuple(parts)
        self.single = len(self.parts) == 1 and isinstance(self.parts[0], tuple)


def parse_template(text, index):
    """Split a template into text and references; ``\\{{`` writes a literal ``{{``."""
    if len(text) > MAX_VALUE_CHARS:
        raise error("value_too_long", "value is longer than {max} characters.", {"max": MAX_VALUE_CHARS},
                    index=index, field="value")
    parts, buffer, position, references = [], [], 0, 0
    while position < len(text):
        if text.startswith("\\{{", position):
            buffer.append("{{")
            position += 3
            continue
        if text.startswith("{{", position):
            end = text.find("}}", position + 2)
            if end < 0:
                raise error("template_unclosed", "A template reference opened with {open} has no closing {close}; "
                            "write {escape} for literal braces.", BRACES, index=index, field="value")
            reference = text[position + 2:end].strip()
            if not reference:
                raise error("template_empty", "A template reference is empty: write a path between {open} and "
                            "{close}.", BRACES, index=index, field="value")
            try:
                # A reference is only a path: plain names hold no spaces here ({{ price * 2 }} is refused);
                # a name with spaces is written ["first name"].
                if _SPACE.search(_QUOTED.sub("", reference)):
                    raise error("path_invalid", "spaces", index=index)
                steps = paths.parse_path(reference, field="reference", index=index)
            except EditError:
                raise error("template_reference_invalid", "The template reference {reference} is not a valid "
                            "path.", {"reference": _shown(reference)}, index=index, field="value") from None
            references += 1
            if references > MAX_REFERENCES:
                raise error("template_too_many", "A template holds at most {max} references.",
                            {"max": MAX_REFERENCES}, index=index, field="value")
            if buffer:
                parts.append("".join(buffer))
                buffer = []
            parts.append((reference, steps))
            position = end + 2
            continue
        buffer.append(text[position])
        position += 1
    if buffer:
        parts.append("".join(buffer))
    return Template(parts)


# --------------------------------------------------------------------------- configuration


class Operation:
    """One validated operation with its parsed paths and prepared value."""

    __slots__ = ("index", "op", "data", "path", "source", "target", "value_type", "value", "template")

    def __init__(self, index, data):
        self.index = index
        self.op = data["op"]
        self.data = data
        self.path = self.source = self.target = ()
        self.value_type = data.get("value_type")
        self.value = None
        self.template = None

    def to_config(self):
        return {key: self.data[key] for key in OPERATION_KEYS[self.op] if key in self.data}


def _value(data, index):
    """Validate the configured value for its value_type and return what an activation writes."""
    value_type = data.get("value_type")
    if value_type not in VALUE_TYPES:
        raise error("value_type_invalid", "value_type must be string, number, boolean, null, json or template.",
                    index=index, field="value_type")
    if value_type == "null":
        return None, None
    if "value" not in data:
        raise error("value_missing", "{value_type} needs a value.", {"value_type": value_type}, index=index,
                    field="value")
    value = data["value"]
    if value_type in {"string", "template"}:
        if not isinstance(value, str):
            raise error("value_not_string", "value must be text for value_type {value_type}.",
                        {"value_type": value_type}, index=index, field="value")
        if value_type == "template":
            return None, parse_template(value, index)
        if len(value) > MAX_VALUE_CHARS:
            raise error("value_too_long", "value is longer than {max} characters.", {"max": MAX_VALUE_CHARS},
                        index=index, field="value")
        return value, None
    if value_type == "number":
        if isinstance(value, str) and paths.is_number_text(value.strip()):
            return paths.Literal(value.strip()), None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return paths.Literal(paths.dump(value)), None
            except ValueError:
                pass
        raise error("value_not_number", "value must be a JSON number such as 42, -0.5, 12.50 or 1e3.",
                    index=index, field="value")
    if value_type == "boolean":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true", None
        raise error("value_not_boolean", "value must be true or false.", index=index, field="value")
    # json: JSON text (as typed in the editor) or, in hand-written settings, the value itself.
    if isinstance(value, str):
        if len(value) > MAX_VALUE_CHARS:
            raise error("value_too_long", "value is longer than {max} characters.", {"max": MAX_VALUE_CHARS},
                        index=index, field="value")
        try:
            parsed = paths.parse_exact(value)
        except (ValueError, RecursionError):
            raise error("value_not_json", "value must be standard JSON text, for example {example}, [1,2] or \"text\".",
                        JSON_EXAMPLE,
                        index=index, field="value") from None
    else:
        if paths.deeper_than(value):
            raise error("value_too_deep", "value nests more than {max} levels.", {"max": paths.MAX_DEPTH},
                        index=index, field="value")
        try:
            parsed = paths.normalize(value)
        except (ValueError, RecursionError):
            raise error("value_not_json", "value must be standard JSON text, for example {example}, [1,2] or \"text\".",
                        JSON_EXAMPLE,
                        index=index, field="value") from None
    if paths.deeper_than(parsed):
        raise error("value_too_deep", "value nests more than {max} levels.", {"max": paths.MAX_DEPTH},
                    index=index, field="value")
    return parsed, None


def operation(raw, index, mode="edit"):
    """Validate one operation object and return an :class:`Operation`."""
    if not isinstance(raw, Mapping):
        raise error("operation_type", "Each operation must be an object.", index=index)
    data = dict(raw)
    op = data.get("op")
    if op not in OPS:
        raise error("op_invalid", "op must be set, copy, rename, remove, default or append.", index=index, field="op")
    for key in data:
        if key not in OPERATION_KEYS[op]:
            raise error("operation_unknown_key", "{op} does not use the field {key}.",
                        {"op": op, "key": str(key)[:40]}, index=index, field="op")
    item = Operation(index, data)
    if op in MOVES:
        for field in ("from", "to"):
            if field not in data:
                raise error("field_missing", "{op} needs {field}.", {"op": op, "field": field}, index=index,
                            field=field)
        item.source = paths.parse_path(data["from"], field="from", index=index)
        item.target = paths.parse_path(data["to"], field="to", index=index)
        if op == "rename" and not item.source:
            raise error("rename_root", "rename needs a field: {field} cannot be empty.", {"field": "from"},
                        index=index, field="from")
        if op == "rename" and not item.target:
            raise error("rename_root", "rename needs a field: {field} cannot be empty.", {"field": "to"},
                        index=index, field="to")
        # keep_only copies read the received message: copy id -> id keeps id. Anything else would be a no-op.
        if item.source == item.target and (op == "rename" or mode != "keep_only"):
            raise error("same_path", "from and to are the same path.", index=index, field="to")
        return item
    if "path" not in data:
        raise error("field_missing", "{op} needs {field}.", {"op": op, "field": "path"}, index=index, field="path")
    item.path = paths.parse_path(data["path"], field="path", index=index)
    if op == "remove" and not item.path:
        raise error("remove_root", "remove needs a field: path cannot be empty.", index=index, field="path")
    if op in VALUED:
        item.value, item.template = _value(data, index)
    return item


class Options:
    """Validated Edit Fields settings."""

    __slots__ = ("mode", "apply_to", "array_path", "array_steps", "max_items", "operations", "on_missing",
                 "parse_json", "on_error", "needs_original")

    def __init__(self, merged, operations, array_steps):
        self.mode = merged["mode"]
        self.apply_to = merged["apply_to"]
        self.array_path = merged["array_path"]
        self.array_steps = array_steps
        self.max_items = merged["max_items"]
        self.operations = tuple(operations)
        self.on_missing = merged["on_missing"]
        self.parse_json = merged["parse_json"]
        self.on_error = merged["on_error"]
        # Templates and keep_only copies read the original: only then is a separate working copy needed.
        self.needs_original = self.mode == "keep_only" or any(item.template is not None for item in operations)

    def to_config(self):
        return {"mode": self.mode, "apply_to": self.apply_to, "array_path": self.array_path,
                "max_items": self.max_items, "operations": [item.to_config() for item in self.operations],
                "on_missing": self.on_missing, "parse_json": self.parse_json, "on_error": self.on_error}


def array_steps(path):
    """Parse ``array_path``; errors name array_path, not an operation."""
    try:
        return paths.parse_path(path, field="array_path")
    except EditError as exc:
        raise error(_ARRAY_PATH_CODES.get(exc.code, "array_path_invalid"), exc.template, exc.params,
                    field="array_path") from None


def _valid_max_items(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_ITEMS


def problems(raw):
    """Every detectable settings error, general settings first, then one per operation."""
    if raw is not None and not isinstance(raw, Mapping):
        return [error("config_type", "The settings must be a JSON object.")]
    merged = settings(raw)
    found = []
    for key, (choices, message) in _SETTING_CHOICES.items():
        if merged.get(key) not in choices:
            found.append(error(f"{key}_invalid", message, field=key))
    if merged.get("apply_to") == "each_item":
        try:
            array_steps(merged.get("array_path"))
        except EditError as exc:
            found.append(exc)
        if not _valid_max_items(merged.get("max_items")):
            found.append(error("max_items_invalid", "max_items must be a whole number from 1 to {max}.",
                               {"max": MAX_ITEMS}, field="max_items"))
        if merged.get("parse_json") == "never":
            found.append(error("each_item_needs_json", "each_item reads the message as JSON: choose parse_json "
                               "auto or always.", field="parse_json"))
    rows = merged.get("operations")
    if not isinstance(rows, list):
        found.append(error("operations_type", "operations must be a list of operation objects.", field="operations"))
        return found
    if not rows:
        found.append(error("operations_empty", "Add at least one operation.", field="operations"))
    if len(rows) > MAX_OPERATIONS:
        found.append(error("operations_too_many", "Use at most {max} operations.", {"max": MAX_OPERATIONS},
                           field="operations"))
    for index, row in enumerate(rows[:MAX_OPERATIONS], start=1):
        try:
            operation(row, index, merged.get("mode"))
        except EditError as exc:
            found.append(exc)
    return found


def configuration(raw):
    """Validate settings and return :class:`Options`; raise the first EditError otherwise."""
    found = problems(raw)
    if found:
        raise found[0]
    merged = settings(raw)
    operations = [operation(row, index, merged["mode"]) for index, row in enumerate(merged["operations"], start=1)]
    if merged["apply_to"] == "each_item":
        return Options(merged, operations, array_steps(merged["array_path"]))
    # Ignored when apply_to is message: keep valid values for a later switch, reset unusable ones.
    path = merged.get("array_path")
    try:
        array_steps(path)
    except EditError:
        path = ""
    merged = {**merged, "array_path": path,
              "max_items": merged["max_items"] if _valid_max_items(merged.get("max_items")) else MAX_ITEMS}
    return Options(merged, operations, ())


# --------------------------------------------------------------------------- editing


def _blocked(item, steps, position, holder, expected):
    at = paths.format_path(steps[:position])
    return error("path_blocked", "cannot write at {path}: {at} is of type {found}, not {expected}.",
                 {"path": paths.format_path(steps), "at": at, "found": paths.kind(holder), "expected": expected},
                 index=item.index)


def _container(next_step):
    return [] if isinstance(next_step, int) else {}


def write(item, root, steps, value):
    """Write ``value`` at ``steps`` and return the (possibly replaced) root.

    Missing or null intermediate names become objects (arrays when the next step is an
    index). An index must exist or equal the array length (append); anything else fails.
    """
    if not steps:
        return value
    holder, last = root, len(steps) - 1
    for position, step in enumerate(steps):
        if isinstance(step, int):
            if not isinstance(holder, list):
                raise _blocked(item, steps, position, holder, "array")
            if step > len(holder):
                raise error("index_out_of_range", "cannot write at {path}: index [{position}] is past the end of "
                            "the array at {at} ({length} item(s)).",
                            {"path": paths.format_path(steps), "position": step, "length": len(holder),
                             "at": paths.format_path(steps[:position])}, index=item.index)
            if position == last:
                if step == len(holder):
                    holder.append(value)
                else:
                    holder[step] = value
                return root
            if step == len(holder):
                holder.append(_container(steps[position + 1]))
            elif holder[step] is None:
                holder[step] = _container(steps[position + 1])
            holder = holder[step]
        else:
            if not isinstance(holder, dict):
                raise _blocked(item, steps, position, holder, "object")
            if position == last:
                holder[step] = value
                return root
            child = holder.get(step)
            if child is None:
                child = holder[step] = _container(steps[position + 1])
            holder = child
    return root  # pragma: no cover - the loop always returns


def remove(root, steps):
    """Remove the value at ``steps``; return False when it does not resolve."""
    parent = paths.resolve(root, steps[:-1])
    step = steps[-1]
    if isinstance(step, int):
        if not isinstance(parent, list) or step >= len(parent):
            return False
        del parent[step]
        return True
    if not isinstance(parent, dict) or step not in parent:
        return False
    del parent[step]
    return True


class Run:
    """State of one document edit: copy budget and skipped operations."""

    __slots__ = ("options", "budget", "skipped", "applied")

    def __init__(self, options, budget):
        self.options = options
        self.budget = budget
        self.skipped = []
        self.applied = 0


def _missing(run, item, code, message, params):
    if run.options.on_missing == "fail":
        raise error(code, message, params, index=item.index)
    raise Skip(code, message, params)


def _render(run, item, original):
    """Render a template against the original input: one reference keeps its JSON type."""
    template = item.template
    if template.single:
        reference, steps = template.parts[0]
        found = paths.resolve(original, steps)
        if found is MISSING:
            _missing(run, item, "reference_missing", "the template reference {reference} does not resolve in the "
                     "input.", {"reference": _shown(reference)})
        return paths.clone(found, run.budget)
    pieces, size = [], 0
    for part in template.parts:
        if isinstance(part, str):
            text = part
        else:
            reference, steps = part
            found = paths.resolve(original, steps)
            if found is MISSING:
                _missing(run, item, "reference_missing", "the template reference {reference} does not resolve in "
                         "the input.", {"reference": _shown(reference)})
            text = paths.text_form(found)
        size += len(text)
        if size > MAX_OUTPUT_BYTES:
            raise error("output_too_large", "the result is larger than {max} bytes.", {"max": MAX_OUTPUT_BYTES},
                        index=item.index)
        pieces.append(text)
    run.budget.spend(1)
    return "".join(pieces)


def _configured(run, item, original):
    if item.template is not None:
        return _render(run, item, original)
    return paths.clone(item.value, run.budget)


def _step(run, item, document, original):
    """Apply one operation; return the new root."""
    op = item.op
    if op == "set":
        return write(item, document, item.path, _configured(run, item, original))
    if op == "default":
        current = paths.resolve(document, item.path)
        if current is MISSING or current is None:
            return write(item, document, item.path, _configured(run, item, original))
        return document
    if op == "append":
        current = paths.resolve(document, item.path)
        if current is not MISSING and current is not None and not isinstance(current, list):
            raise error("append_not_array", "cannot append at {path}: it is of type {found}, not array.",
                        {"path": paths.format_path(item.path), "found": paths.kind(current)}, index=item.index)
        value = _configured(run, item, original)
        if isinstance(current, list):
            current.append(value)
            return document
        return write(item, document, item.path, [value])
    if op == "remove":
        if not remove(document, item.path):
            _missing(run, item, "source_missing", "{field} {path} does not resolve.",
                     {"field": "path", "path": paths.format_path(item.path)})
        return document
    # copy and rename
    source_document = original if (op == "copy" and run.options.mode == "keep_only") else document
    found = paths.resolve(source_document, item.source)
    if found is MISSING:
        _missing(run, item, "source_missing", "{field} {path} does not resolve.",
                 {"field": "from", "path": paths.format_path(item.source)})
    if op == "copy":
        return write(item, document, item.target, paths.clone(found, run.budget))
    parent = item.source[:-1]
    old, new = item.source[-1], item.target[-1]
    holder = paths.resolve(document, parent)
    if parent == item.target[:-1] and isinstance(holder, dict) and isinstance(new, str) and new not in holder:
        # A rename inside the same object keeps the field at its position.
        entries = list(holder.items())
        holder.clear()
        for key, value in entries:
            holder[new if key == old else key] = value
        return document
    remove(document, item.source)
    return write(item, document, item.target, found)


def edit_document(value, options, run):
    """Run every operation on one document (the message or one item) and return the result."""
    if options.mode == "keep_only":
        document = {}
    elif options.needs_original:
        document = paths.clone(value, run.budget)
    else:
        document = value
    for item in options.operations:
        try:
            document = _step(run, item, document, value)
            run.applied += 1
        except Skip as skip:
            run.skipped.append((item.index, skip))
    return document


def message_value(raw, mode):
    """The value operations work on, according to parse_json (numbers kept as written)."""
    if isinstance(raw, str):
        if mode == "never":
            return raw
        if len(raw) > MAX_INPUT_BYTES or len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
            raise error("message_too_large", "the message is larger than {max} bytes.", {"max": MAX_INPUT_BYTES})
        try:
            value = paths.parse_exact(raw)
        except RecursionError:
            raise error("too_deep", "the value nests more than {max} levels.", {"max": paths.MAX_DEPTH}) from None
        except ValueError:
            if mode == "always":
                raise error("message_not_json", "the message is not valid JSON and parse_json is always.") from None
            return raw
    else:
        if paths.deeper_than(raw):
            raise error("too_deep", "the value nests more than {max} levels.", {"max": paths.MAX_DEPTH})
        try:
            value = paths.normalize(raw)
        except (ValueError, RecursionError):
            raise error("message_not_text", "the message is not text or JSON.") from None
        if mode == "never":
            return paths.dump(value)
    if paths.deeper_than(value):
        raise error("too_deep", "the value nests more than {max} levels.", {"max": paths.MAX_DEPTH})
    return value


def locate(message, options):
    """The array whose items are edited, in ``each_item`` mode."""
    found = paths.resolve(message, options.array_steps)
    if found is MISSING:
        raise error("array_path_missing", "array_path does not resolve in the message.")
    if not isinstance(found, list):
        if options.array_steps:
            raise error("array_path_not_array", "the value at array_path is not a JSON array.")
        raise error("not_array", "the message is not a JSON array.")
    if len(found) > options.max_items:
        raise error("too_many_items", "the array has {count} items, more than max_items ({max}).",
                    {"count": len(found), "max": options.max_items})
    return found


def _finish(result):
    if paths.deeper_than(result):
        raise error("result_too_deep", "the result nests more than {max} levels.", {"max": paths.MAX_DEPTH})
    text = paths.dump(result)
    if len(text) > MAX_OUTPUT_BYTES or len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise error("output_too_large", "the result is larger than {max} bytes.", {"max": MAX_OUTPUT_BYTES})
    return text


def process(raw, options, *, cancelled=None):
    """Edit one message; return the output text and a content-free report.

    Raises :class:`EditError` (with ``index``/``item`` when an operation is at fault);
    returns ``None`` when cancellation was requested. Nothing partial is ever returned.
    """
    message = message_value(raw, options.parse_json)
    run = Run(options, paths.Budget(MAX_COPIED_VALUES))
    skipped = {}
    if options.apply_to == "message":
        result = edit_document(message, options, run)
        for index, skip in run.skipped:
            skipped[index] = {"index": index, "code": skip.code, "params": skip.params, "message": skip.message,
                              "items": 1, "first_item": None}
        items = None
    else:
        source = locate(message, options)
        result, items = [], len(source)
        for position, value in enumerate(source):
            if cancelled is not None and position % CANCEL_EVERY == 0 and cancelled():
                return None
            run.skipped = []
            try:
                result.append(edit_document(value, options, run))
            except EditError as exc:
                exc.item = position
                raise
            for index, skip in run.skipped:
                entry = skipped.setdefault(index, {"index": index, "code": skip.code, "params": skip.params,
                                                   "message": skip.message, "items": 0, "first_item": position})
                entry["items"] += 1
    if cancelled is not None and cancelled():
        return None
    text = _finish(result)
    report = {"apply_to": options.apply_to, "mode": options.mode, "operations": len(options.operations),
              "applied": run.applied, "skipped": sum(entry["items"] for entry in skipped.values())}
    if items is not None:
        report["items"] = items
    return {"text": text, "value": result, "report": report,
            "skipped": [skipped[index] for index in sorted(skipped)]}


# --------------------------------------------------------------------------- labels and texts


def _short(value, limit=40):
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def short_label(raw):
    """Compact form such as ``set price = 12.50`` or ``rename name → full_name``; never message content."""
    data = raw.to_config() if isinstance(raw, Operation) else raw
    if not isinstance(data, Mapping):
        return "?"
    op = str(data.get("op") or "?")

    def where(key):
        value = data.get(key)
        return (value or "$") if isinstance(value, str) else "?"

    if op in MOVES:
        return f"{op} {_short(where('from'))} → {_short(where('to'))}"
    if op == "remove":
        return f"remove {_short(where('path'))}"
    value_type, value = data.get("value_type"), data.get("value")
    if value_type == "null":
        shown = "null"
    elif value_type in {"string", "template"}:
        shown = paths.dump(value) if isinstance(value, str) else "?"
    elif isinstance(value, str):
        shown = value.strip()
    else:
        try:
            shown = paths.dump(paths.normalize(value))
        except (ValueError, RecursionError):
            shown = "?"
    suffix = " (template)" if value_type == "template" else ""
    sign = {"set": "=", "default": "?=", "append": "+="}.get(op, "=")
    return f"{op} {_short(where('path'))} {sign} {_short(shown)}{suffix}"


def describe(exc, options_config=None):
    """English diagnostic naming the operation, its short label and the item; no message content."""
    if not exc.index:
        return exc.message
    label = ""
    rows = (options_config or {}).get("operations") if isinstance(options_config, Mapping) else None
    if isinstance(rows, list) and 0 < exc.index <= len(rows):
        label = f" ({short_label(rows[exc.index - 1])})"
    item = f" on item [{exc.item}]" if exc.item is not None else ""
    return f"Operation {exc.index}{label}{item}: {exc.message}"


def describe_skip(entry, options_config):
    rows = options_config.get("operations") if isinstance(options_config, Mapping) else None
    label = short_label(rows[entry["index"] - 1]) if isinstance(rows, list) and 0 < entry["index"] <= len(rows) else ""
    if entry.get("first_item") is None:
        return f"Operation {entry['index']} ({label}) skipped: {entry['message']}"
    return (f"Operation {entry['index']} ({label}) skipped on {entry['items']} item(s), first at item "
            f"[{entry['first_item']}]: {entry['message']}")


def envelope(exc, options_config):
    """Content-free JSON document published on ``error`` with ``on_error: route``."""
    rows = options_config.get("operations") if isinstance(options_config, Mapping) else None
    op = None
    if exc.index and isinstance(rows, list) and 0 < exc.index <= len(rows) and isinstance(rows[exc.index - 1], Mapping):
        op = rows[exc.index - 1].get("op")
    return {"code": exc.code, "message": describe(exc, options_config), "operation": exc.index,
            "op": op if isinstance(op, str) else None, "item": exc.item}


def card_summary(raw_settings):
    """Mode, apply_to, first operation and remaining count, or None when the settings are invalid."""
    try:
        options = configuration(raw_settings)
    except EditError:
        return None
    return {"mode": options.mode, "apply_to": options.apply_to, "array_path": options.array_path,
            "first": short_label(options.operations[0]), "more": len(options.operations) - 1}


# --------------------------------------------------------------------------- editor


def value_text(data):
    """Editor text of a configured value: as typed for text, number and JSON text."""
    if not isinstance(data, Mapping) or data.get("value_type") in {"null", "boolean"} or "value" not in data:
        return ""
    value = data["value"]
    if isinstance(value, str):
        return value
    try:
        return paths.dump(paths.normalize(value))
    except (ValueError, RecursionError):
        return ""


def editor_rows(raw_settings):
    """Rows for the server-rendered editor; invalid stored rows stay editable."""
    rows = settings(raw_settings).get("operations")
    if not isinstance(rows, list) or not rows:
        rows = [dict(DEFAULTS["operations"][0])]
    result = []
    for row in rows[:MAX_OPERATIONS]:
        row = row if isinstance(row, Mapping) else {}
        op = row.get("op") if row.get("op") in OPS else "set"
        value_type = row.get("value_type") if row.get("value_type") in VALUE_TYPES else "string"
        boolean = row.get("value") if value_type == "boolean" else True
        if isinstance(boolean, str):
            boolean = boolean.strip().lower() != "false"
        result.append({
            "op": op,
            "path": row.get("path") if isinstance(row.get("path"), str) else "",
            "from": row.get("from") if isinstance(row.get("from"), str) else "",
            "to": row.get("to") if isinstance(row.get("to"), str) else "",
            "value_type": value_type,
            "value_text": value_text(row) if value_type not in {"boolean", "null"} else "",
            "value_bool": "false" if boolean is False else "true",
        })
    return result


def editor_operation(row):
    """Canonical operation object from one editor row (only the fields the op uses)."""
    if not isinstance(row, Mapping) or "op" not in row:
        return row
    op = row.get("op")
    if op not in OPS:
        return {"op": op}
    data = {"op": op}
    for key in OPERATION_KEYS[op]:
        if key in {"op", "value", "value_type"}:
            continue
        data[key] = row.get(key, "")
    if op in VALUED:
        value_type = row.get("value_type")
        data["value_type"] = value_type
        if "value_text" not in row and "value_bool" not in row:
            # Already canonical (settings written by hand or by an API client): keep the value as given.
            if value_type != "null" and "value" in row:
                data["value"] = row["value"]
            return data
        text = row.get("value_text", "")
        if value_type == "boolean":
            data["value"] = str(row.get("value_bool", "true")).strip().lower() == "true"
        elif value_type == "number":
            data["value"] = text.strip() if isinstance(text, str) else text
        elif value_type != "null":
            data["value"] = text
    return data


def editor_settings(raw):
    """Canonical settings from an editor draft; ``max_items`` typed as digits becomes a number."""
    draft = dict(raw or {}) if isinstance(raw, Mapping) else {}
    rows = draft.get("operations")
    if isinstance(rows, list):
        draft["operations"] = [editor_operation(row) for row in rows]
    limit = draft.get("max_items")
    if isinstance(limit, str) and limit.strip().isdecimal() and len(limit.strip()) <= 9:
        draft["max_items"] = int(limit.strip())
    return draft


def validation(raw_draft, stored=None):
    """Every problem of an editor draft, as JSON-safe descriptions (empty when valid)."""
    return [item.describe() for item in problems({**thaw(stored or {}), **editor_settings(raw_draft)})]


def _pretty(value):
    text = paths.dump(value, indent=2)
    if len(text) > MAX_PREVIEW_CHARS:
        return text[:MAX_PREVIEW_CHARS] + "\n…", True
    return text, False


def preview(raw_draft, sample, stored=None):
    """Edit a pasted sample with a draft, exactly like a text message, without running the graph."""
    errors = validation(raw_draft, stored)
    if errors:
        return {"valid": False, "errors": errors}
    if not isinstance(sample, str):
        return {"valid": True, "errors": [], "sample_error": {"code": "sample_type", "params": {},
                "message": "The sample must be text."}}
    if len(sample) > MAX_SAMPLE_CHARS:
        return {"valid": True, "errors": [], "sample_error": {"code": "sample_too_large",
                "params": {"max": MAX_SAMPLE_CHARS}, "message": "The sample is longer than {max} characters."}}
    merged = {**thaw(stored or {}), **editor_settings(raw_draft)}
    options = configuration(merged)
    config = options.to_config()
    base = {"valid": True, "errors": [], "apply_to": options.apply_to, "mode": options.mode,
            "on_error": options.on_error}
    try:
        message = message_value(sample, options.parse_json)
        shown, cut = _pretty(message) if not isinstance(message, str) else (message[:MAX_PREVIEW_CHARS],
                                                                            len(message) > MAX_PREVIEW_CHARS)
        base.update(input=shown, input_json=not isinstance(message, str), input_truncated=cut)
        result = process(sample, options)
    except EditError as exc:
        failure = {**exc.describe(), "item": exc.item,
                   "label": short_label(config["operations"][exc.index - 1]) if exc.index else ""}
        return {**base, "status": "routed" if options.on_error == "route" else "failed", "failure": failure,
                "error_document": envelope(exc, config) if options.on_error == "route" else None}
    output, cut = _pretty(result["value"])
    skipped = [{**entry, "label": short_label(config["operations"][entry["index"] - 1])} for entry in result["skipped"]]
    return {**base, "status": "success", "output": output, "output_truncated": cut, "report": result["report"],
            "skipped": skipped}


def dumps(value):
    """Stable compact JSON for metadata, reports and last-message previews."""
    return paths.dump(value)
