"""Paths and exact JSON values for Edit Fields.

Pure functions only: no framework import and no I/O. The path syntax is the one of the
shared condition language (dot names, ``[index]``), extended with ``["quoted key"]``
for names that hold dots, brackets or surrounding spaces. Every JSON number read from
text is kept as the literal that was written, so ``12.50`` stays ``12.50``.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re

MAX_PATH_SEGMENTS = 32
MAX_PATH_LENGTH = 512
MAX_DEPTH = 32

MISSING = object()
_KEY = re.compile(r"[^.\[\]]+")
_INDEX = re.compile(r"0|[1-9][0-9]{0,8}")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", re.ASCII)
_DECODER = json.JSONDecoder()
_STRING = json.encoder.encode_basestring


class EditError(ValueError):
    """Explicit configuration or editing failure with a stable machine ``code``.

    ``index`` is the 1-based operation number (None for message-level problems or
    general settings), ``field`` the operation field or setting at fault, and
    ``item`` the 0-based array item in ``each_item`` mode.
    """

    def __init__(self, code, template, params=None, *, index=None, field=None, item=None):
        self.code = code
        self.template = template
        self.params = dict(params or {})
        self.message = template.format(**self.params)
        self.index = index
        self.field = field
        self.item = item
        super().__init__(self.message)

    def describe(self):
        """JSON-safe description for UI responses."""
        return {"index": self.index, "field": self.field, "code": self.code,
                "params": self.params, "message": self.message}

    def __str__(self):
        return f"Operation {self.index}: {self.message}" if self.index else self.message


def error(code, message, params=None, **where):
    return EditError(code, message, params, **where)


# --------------------------------------------------------------------------- paths


def parse_path(path, *, field="path", index=None):
    """Parse ``customer.email``, ``items[0].price``, ``headers["content-type"]`` or ``[0]``.

    Returns a tuple of steps: ``str`` for an object key, ``int`` for an array index.
    The empty path is the whole value. A quoted key is a JSON string literal.
    """
    if not isinstance(path, str):
        raise error("path_type", "{field} must be text.", {"field": field}, index=index, field=field)
    if path == "":
        return ()
    if len(path) > MAX_PATH_LENGTH:
        raise error("path_too_long", "{field} is longer than {max} characters.",
                    {"field": field, "max": MAX_PATH_LENGTH}, index=index, field=field)
    invalid = error("path_invalid", "{field} is not a valid path: separate names with dots, use [index] for "
                    "array items and [\"name\"] for names with dots or brackets, for example items[0].price.",
                    {"field": field}, index=index, field=field)
    steps, position, size = [], 0, len(path)
    while position < size:
        char = path[position]
        if char == "[":
            if path.startswith('["', position):
                try:
                    key, end = _DECODER.raw_decode(path, position + 1)
                except (ValueError, RecursionError):
                    raise invalid from None
                if not isinstance(key, str) or end >= size or path[end] != "]":
                    raise invalid
                steps.append(key)
                position = end + 1
            else:
                end = path.find("]", position)
                if end < 0 or not _INDEX.fullmatch(path[position + 1:end]):
                    raise invalid
                steps.append(int(path[position + 1:end]))
                position = end + 1
        else:
            if char == ".":
                if not steps:
                    raise invalid
                position += 1
            elif steps:
                raise invalid
            match = _KEY.match(path, position)
            if not match:
                raise invalid
            key = match.group()
            if key != key.strip():
                raise error("path_spaces", "A name in {field} starts or ends with a space: write it as "
                            "[\"name\"].", {"field": field}, index=index, field=field)
            steps.append(key)
            position = match.end()
        if len(steps) > MAX_PATH_SEGMENTS:
            raise error("path_too_deep", "{field} has more than {max} segments.",
                        {"field": field, "max": MAX_PATH_SEGMENTS}, index=index, field=field)
    return tuple(steps)


def format_path(steps):
    """Canonical text of parsed steps; ``$`` is the whole value."""
    if not steps:
        return "$"
    parts = []
    for step in steps:
        if isinstance(step, int):
            parts.append(f"[{step}]")
        elif _KEY.fullmatch(step) and step == step.strip():
            parts.append(("." if parts else "") + step)
        else:
            parts.append("[" + json.dumps(step, ensure_ascii=False) + "]")
    return "".join(parts)


def resolve(value, steps):
    """Follow parsed steps; anything that does not resolve is MISSING, never an error."""
    for step in steps:
        if isinstance(step, int):
            if not isinstance(value, list) or step >= len(value):
                return MISSING
            value = value[step]
        else:
            if not isinstance(value, dict) or step not in value:
                return MISSING
            value = value[step]
    return value


# --------------------------------------------------------------------------- exact JSON values


class Literal:
    """A JSON number kept exactly as written (``12.50``, ``1e3``, ``-0``, long integers)."""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return f"Literal({self.text})"

    def __eq__(self, other):
        return isinstance(other, Literal) and other.text == self.text

    def __hash__(self):
        return hash(self.text)

    def __deepcopy__(self, _memo):
        return self


def _reject_constant(_name):
    raise ValueError("NaN and Infinity are not JSON")


def parse_exact(text):
    """Parse standard JSON keeping every number literal; raise ValueError or RecursionError."""
    return json.loads(text, parse_float=Literal, parse_int=Literal, parse_constant=_reject_constant)


def is_number_text(text):
    """True for a standard JSON number literal such as ``12.50``, ``-3`` or ``1e3``."""
    return isinstance(text, str) and len(text) <= 1000 and _NUMBER.fullmatch(text) is not None


def kind(value):
    """JSON type name of a value, for messages that never copy content."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (Literal, int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def deeper_than(value, limit=MAX_DEPTH):
    """True when containers nest more than ``limit`` levels (``{}`` is one level).

    Iterative and bounded by ``limit``: cyclic or very deep native values stop early.
    """
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (Mapping, list, tuple)):
            if depth > limit:
                return True
            children = item.values() if isinstance(item, Mapping) else item
            stack.extend((child, depth + 1) for child in children if isinstance(child, (Mapping, list, tuple)))
    return False


def normalize(value):
    """Plain JSON containers from a native value (read-only mappings, tuples); depth checked first."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be text")
            result[key] = normalize(item)
        return result
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, Literal)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("not a JSON value")


class Budget:
    """Bound the JSON values copied during one activation (cheap guard against blow-ups)."""

    __slots__ = ("left", "limit")

    def __init__(self, limit):
        self.left = limit
        self.limit = limit

    def spend(self, count):
        self.left -= count
        if self.left < 0:
            raise error("too_much_data", "the edits copy more than {max} JSON values in one activation.",
                        {"max": self.limit})


def clone(value, budget):
    """Independent copy of a JSON value, iterative so that depth never matters."""
    if not isinstance(value, (dict, list)):
        budget.spend(1)
        return value
    root = {} if isinstance(value, dict) else []
    stack, count = [(value, root)], 1
    while stack:
        source, target = stack.pop()
        count += len(source)
        if isinstance(source, dict):
            for key, item in source.items():
                if isinstance(item, (dict, list)):
                    copy = {} if isinstance(item, dict) else []
                    stack.append((item, copy))
                    target[key] = copy
                else:
                    target[key] = item
        else:
            for item in source:
                if isinstance(item, (dict, list)):
                    copy = {} if isinstance(item, dict) else []
                    stack.append((item, copy))
                    target.append(copy)
                else:
                    target.append(item)
    budget.spend(count)
    return root


def dump(value, indent=None):
    """JSON text with number literals verbatim; compact unless ``indent`` is given.

    Callers check :func:`deeper_than` first, so the recursion depth is bounded.
    """
    parts = []
    write = parts.append

    def emit(item, level):
        if isinstance(item, str):
            write(_STRING(item))
        elif isinstance(item, Literal):
            write(item.text)
        elif item is None:
            write("null")
        elif item is True:
            write("true")
        elif item is False:
            write("false")
        elif isinstance(item, int):
            write(int.__repr__(item))
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non-finite number")
            write(float.__repr__(item))
        elif isinstance(item, dict):
            if not item:
                write("{}")
                return
            write("{")
            inner = None if indent is None else "\n" + " " * (indent * (level + 1))
            first = True
            for key, member in item.items():
                if not isinstance(key, str):
                    raise ValueError("object keys must be text")
                if not first:
                    write(",")
                first = False
                if inner:
                    write(inner)
                write(_STRING(key))
                write(":" if indent is None else ": ")
                emit(member, level + 1)
            if indent is not None:
                write("\n" + " " * (indent * level))
            write("}")
        elif isinstance(item, list):
            if not item:
                write("[]")
                return
            write("[")
            inner = None if indent is None else "\n" + " " * (indent * (level + 1))
            for position, member in enumerate(item):
                if position:
                    write(",")
                if inner:
                    write(inner)
                emit(member, level + 1)
            if indent is not None:
                write("\n" + " " * (indent * level))
            write("]")
        else:
            raise ValueError("not a JSON value")

    emit(value, 0)
    return "".join(parts)


def text_form(value):
    """Text inserted by template interpolation: strings as is, other values as JSON text."""
    if isinstance(value, str):
        return value
    return dump(value)
