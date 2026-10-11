"""Paths, operations, templates, keep_only, each_item, limits, settings, runtime results and editor contract (no server)."""

from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import json
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from bloxsmith_app.block_api import BlockRuntimeContext, BlockRuntimePreparation
from blocs.edit_fields import logic, paths
from blocs.edit_fields.block import EditFieldsBlock
from blocs.edit_fields.paths import EditError

PACKAGE = Path(__file__).resolve().parents[1]
EN = json.loads((PACKAGE / "locales/en.json").read_text(encoding="utf-8"))
FR = json.loads((PACKAGE / "locales/fr.json").read_text(encoding="utf-8"))


def S(path, value, value_type="string"):
    item = {"op": "set", "path": path, "value_type": value_type}
    if value_type != "null":
        item["value"] = value
    return item


def D(path, value, value_type="string"):
    return {**S(path, value, value_type), "op": "default"}


def A(path, value, value_type="string"):
    return {**S(path, value, value_type), "op": "append"}


def C(source, target):
    return {"op": "copy", "from": source, "to": target}


def R(source, target):
    return {"op": "rename", "from": source, "to": target}


def X(path):
    return {"op": "remove", "path": path}


def edit(message, *ops, **settings):
    return logic.process(message, logic.configuration({"operations": list(ops), **settings}))


def out(message, *ops, **settings):
    return edit(message, *ops, **settings)["text"]


def fails(message, *ops, **settings):
    try:
        edit(message, *ops, **settings)
    except EditError as exc:
        assert EN[f"block.edit_fields.err_{exc.code}"] == exc.template, exc.code
        return exc
    raise AssertionError(f"accepted {str(message)[:200]!r} with {str(ops)[:300]}")


def config_error(raw):
    try:
        logic.configuration(raw)
    except EditError as exc:
        assert EN[f"block.edit_fields.err_{exc.code}"] == exc.template, exc.code
        return exc
    raise AssertionError(f"Accepted invalid settings: {raw}")


def path_syntax():
    """# - FB1 - FB6 - the shared path syntax plus ["quoted"] names; canonical text; missing is not an error."""
    parse = paths.parse_path
    assert parse("") == () and parse("customer.email") == ("customer", "email")
    assert parse("items[0].price") == ("items", 0, "price") and parse("[0].name") == (0, "name")
    assert parse('headers["content-type"]') == ("headers", "content-type")
    assert parse('["utm.source"]') == ("utm.source",) and parse('data["a[1]"][2]') == ("data", "a[1]", 2)
    assert parse('["say \\"hi\\""]["  padded "]') == ('say "hi"', "  padded ") and parse('[""]') == ("",)
    assert parse("a.0") == ("a", "0"), "a dotted digit is a name, not an index"
    assert parse("first name.é") == ("first name", "é")
    assert paths.format_path(parse('a["b.c"][0][" x"].d')) == 'a["b.c"][0][" x"].d'
    assert paths.format_path(()) == "$"
    for bad, code in (("a..b", "path_invalid"), (".a", "path_invalid"), ("a.", "path_invalid"), ("a[", "path_invalid"),
                      ("a[01]", "path_invalid"), ("a[-1]", "path_invalid"), ('a["b"', "path_invalid"),
                      ('a["b"]c', "path_invalid"), ("a[x]", "path_invalid"), ('["b]', "path_invalid"),
                      ("a. b", "path_spaces"), ("k" * 513, "path_too_long"), (".".join(["k"] * 33), "path_too_deep"),
                      (3, "path_type")):
        try:
            parse(bad)
        except EditError as exc:
            assert exc.code == code, (bad, exc.code)
            assert EN["block.edit_fields.err_" + code] == exc.template
        else:
            raise AssertionError(bad)
    assert len(parse(".".join(["k"] * 32))) == 32
    doc = {"a": [{"b": None}], "x.y": 1}
    assert paths.resolve(doc, ("a", 0, "b")) is None and paths.resolve(doc, ("x.y",)) == 1
    for missing in (("a", 1), ("a", "0"), ("z",), ("a", 0, "b", "c"), ("x.y", "q")):
        assert paths.resolve(doc, missing) is paths.MISSING, missing


def set_values():
    """# - FB1 - typed values, exact number literals, creation of intermediates, index and blocking rules."""
    message = '{"id": 7, "price": 12.50, "big": 12345678901234567890123, "tiny": 1e-7}'
    assert out(message, S("name", "Ada"), S("qty", "3", "number"), S("ok", True, "boolean"),
               S("none", None, "null"), S("tags", '["a", {"b": 1.0}]', "json"), S("price", " 9.90 ", "number")) == (
        '{"id":7,"price":9.90,"big":12345678901234567890123,"tiny":1e-7,"name":"Ada","qty":3,"ok":true,'
        '"none":null,"tags":["a",{"b":1.0}]}')
    assert out("{}", S("n", "-0", "number"), S("e", "1E+3", "number"), S("f", 2.5, "number"), S("i", 10, "number"),
               S("b", "FALSE", "boolean"), S("j", {"native": [1, None]}, "json"), S("s", '"x"', "json")) == (
        '{"n":-0,"e":1E+3,"f":2.5,"i":10,"b":false,"j":{"native":[1,null]},"s":"x"}')
    assert out("{}", S("a.b.c", "deep")) == '{"a":{"b":{"c":"deep"}}}'
    assert out("{}", S("rows[0].id", "1", "number")) == '{"rows":[{"id":1}]}', "index step creates an array"
    assert out("{}", S("m[0][0]", "x")) == '{"m":[["x"]]}'
    assert out('{"a": null}', S("a.b", "x")) == '{"a":{"b":"x"}}', "null intermediates are replaced"
    assert out('{"a": [1, 2]}', S("a[1]", "two"), S("a[2]", "three")) == '{"a":[1,"two","three"]}'
    assert out('{"a": [null]}', S("a[0].k", "v")) == '{"a":[{"k":"v"}]}'
    assert out('{"keep": 1}', S("", '{"new": true}', "json")) == '{"new":true}', "an empty path replaces the message"
    assert out('[1, 2]', S("[2]", "3", "number")) == "[1,2,3]"
    assert out('{"a": 1, "b": 2}', S("a", "x")) == '{"a":"x","b":2}', "an existing key keeps its position"
    error = fails('{"a": "text"}', S("a.b", "x"))
    assert (error.code, error.index) == ("path_blocked", 1)
    assert error.message == "cannot write at a.b: a is of type string, not object."
    assert fails('{"a": {"b": 5}}', S("a.b[0]", "x")).message == "cannot write at a.b[0]: a.b is of type number, not array."
    assert fails('{"a": [1]}', S("a.k", "x")).message == "cannot write at a.k: a is of type array, not object."
    index = fails('{"a": [1, 2]}', S("a[3]", "x"))
    assert index.code == "index_out_of_range" and index.params == {"path": "a[3]", "position": 3, "length": 2, "at": "a"}
    assert fails("{}", S("rows[1]", "x")).code == "index_out_of_range", "a new array starts at [0]"
    assert fails('"text"', S("a", "x")).message == "cannot write at a: $ is of type string, not object."
    # Configured values are copied for every write: later edits never change the settings or another field.
    options = logic.configuration({"operations": [S("t", "[1]", "json"), C("t", "u"), A("t", "2", "number")]})
    assert logic.process("{}", options)["text"] == '{"t":[1,2],"u":[1]}'
    assert logic.process("{}", options)["text"] == '{"t":[1,2],"u":[1]}'


def other_operations():
    """# - FB2 - copy, rename, remove, default and append in order on the working document; on_missing."""
    assert out('{"a": {"b": [1, 2]}}', C("a.b", "c"), A("c", "3", "number")) == '{"a":{"b":[1,2]},"c":[1,2,3]}'
    assert out('{"a": 1}', C("", "copy")) == '{"a":1,"copy":{"a":1}}'
    assert out('{"wrap": {"x": 1}}', C("wrap", "")) == '{"x":1}', "copy to the empty path unwraps"
    # rename: position kept in the same object, moved otherwise; sequential reads see previous results.
    assert out('{"a": 1, "fname": "Ada", "z": 2}', R("fname", "first_name")) == '{"a":1,"first_name":"Ada","z":2}'
    assert out('{"a": 1, "b": 2, "c": 3}', R("a", "c")) == '{"b":2,"c":1}', "an existing target is replaced"
    assert out('{"a": {"x": 1}, "b": {}}', R("a.x", "b.y")) == '{"a":{},"b":{"y":1}}'
    assert out('{"a": {"x": 1}}', R("a", "a.inner")) == '{"a":{"inner":{"x":1}}}', "rename into itself wraps"
    assert out('{"a": 1}', R("a", "b"), R("b", "c"), C("c", "d")) == '{"c":1,"d":1}'
    assert out('{"items": ["a", "b", "c"]}', R("items[0]", "first")) == '{"items":["b","c"],"first":"a"}'
    assert out('{"a": 1, "b": {"c": 2, "d": 3}}', X("a"), X("b.c")) == '{"b":{"d":3}}'
    assert out('{"l": [1, 2, 3]}', X("l[1]")) == '{"l":[1,3]}'
    assert out('{"a": null, "b": 0, "c": ""}', D("a", "x"), D("b", "x"), D("c", "x"), D("d.e", "1", "number")) == (
        '{"a":"x","b":0,"c":"","d":{"e":1}}'), "default writes only when missing or null"
    assert out('{"t": null}', A("t", "x"), A("u", '{"k": 1}', "json"), A("t", "[1]", "json")) == (
        '{"t":["x",[1]],"u":[{"k":1}]}'), "append creates the array, and appends an array as one element"
    assert fails('{"t": "x"}', A("t", "y")).message == "cannot append at t: it is of type string, not array."
    assert out('[1]', A("", "2", "number")) == "[1,2]"
    # on_missing: skip leaves the operation out with a reason; fail stops with the operation index.
    result = edit('{"a": 1}', R("nope", "b"), C("x.y", "z"), X("gone"), S("ok", "yes"))
    assert result["text"] == '{"a":1,"ok":"yes"}'
    assert result["report"] == {"apply_to": "message", "mode": "edit", "operations": 4, "applied": 1, "skipped": 3}
    assert [(e["index"], e["code"], e["message"]) for e in result["skipped"]] == [
        (1, "source_missing", "from nope does not resolve."), (2, "source_missing", "from x.y does not resolve."),
        (3, "source_missing", "path gone does not resolve.")]
    error = fails('{"a": 1}', S("ok", "yes"), C("x", "y"), on_missing="fail")
    assert (error.code, error.index, error.message) == ("source_missing", 2, "from x does not resolve.")
    assert logic.describe(error, {"operations": [S("ok", "yes"), C("x", "y")]}) == (
        "Operation 2 (copy x → y): from x does not resolve.")


def templates():
    """# - FB3 - references read the received message; one reference keeps its type; interpolation; escapes."""
    message = ('{"first": "Ada", "last": "Lovelace", "price": 12.50, "n": 3, "ok": true, "none": null, '
               '"items": [1, {"a": 2.0}], "o": {"k.v": "dot"}, "ünï": "ç"}')
    assert out(message, S("full", "{{ first }} {{last}}", "template")) .endswith('"full":"Ada Lovelace"}')
    single = edit(message, S("t_items", "{{ items }}", "template"), S("t_price", "{{price}}", "template"),
                  S("t_ok", "{{ ok }}", "template"), S("t_none", "{{ none }}", "template"),
                  S("t_o", "{{ o }}", "template"), S("t_dot", '{{ o["k.v"] }}', "template"))["value"]
    assert paths.dump({key: single[key] for key in ("t_items", "t_price", "t_ok", "t_none", "t_o", "t_dot")}) == (
        '{"t_items":[1,{"a":2.0}],"t_price":12.50,"t_ok":true,"t_none":null,"t_o":{"k.v":"dot"},"t_dot":"dot"}')
    text = edit(message, S("t", "{{ price }} | {{ ok }} | {{ none }} | {{ items }} | {{ ünï }} | \\{{ first }} | }}",
                           "template"))["value"]["t"]
    assert text == '12.50 | true | null | [1,{"a":2.0}] | ç | {{ first }} | }}', text
    assert edit(message, S("t", " {{ n }}", "template"))["value"]["t"] == " 3", "text around a reference makes a string"
    # Always the received message, never intermediate results.
    assert out('{"a": "old"}', S("a", "new"), S("b", "{{ a }}", "template"), R("a", "c"),
               S("d", "was {{ a }}", "template")) == '{"c":"new","b":"old","d":"was old"}'
    assert out('{"a": [1]}', S("b", "{{ a }}", "template"), A("b", "2", "number"), S("c", "{{ a }}", "template")) == (
        '{"a":[1],"b":[1,2],"c":[1]}'), "a referenced value is copied, never shared"
    skipped = edit('{"a": 1}', S("x", "Hi {{ name }}", "template"), D("y", "{{ gone }}", "template"))
    assert skipped["text"] == '{"a":1}' and [e["code"] for e in skipped["skipped"]] == ["reference_missing"] * 2
    assert skipped["skipped"][0]["message"] == "the template reference {{ name }} does not resolve in the input."
    error = fails('{"a": 1}', S("x", "Hi {{ name }}", "template"), on_missing="fail")
    assert error.code == "reference_missing" and error.params == {"reference": "{{ name }}"}
    assert EN["block.edit_fields.err_reference_missing"] == "the template reference {reference} does not resolve in the input."
    for bad, code in (("Hi {{ name", "template_unclosed"), ("{{ }}", "template_empty"),
                      ("{{ price * 2 }}", "template_reference_invalid"), ("{{ a..b }}", "template_reference_invalid"),
                      ("{{ a }}" * 65, "template_too_many"), ("x" * 65_537, "value_too_long")):
        error = config_error({"operations": [S("t", bad, "template")]})
        assert (error.code, error.index, error.field) == (code, 1, "value"), (bad, error.code)
    assert config_error({"operations": [S("t", "{{ price * 2 }}", "template")]}).params == {"reference": "{{ price * 2 }}"}
    assert logic.configuration({"operations": [S("t", "{{ a }}" * 64, "template")]})


def keep_only_mode():
    """# - FB4 - keep_only builds a new object: set/copy targets only, copy reading the received message."""
    message = '{"id": 7, "name": "Ada", "secret": "s3", "price": 12.50, "nested": {"x": 1}}'
    assert out(message, C("id", "id"), C("name", "customer.name"), S("source", "web"), mode="keep_only") == (
        '{"id":7,"customer":{"name":"Ada"},"source":"web"}')
    assert out(message, C("", "original"), mode="keep_only") == (
        '{"original":{"id":7,"name":"Ada","secret":"s3","price":12.50,"nested":{"x":1}}}')
    assert out(message, C("nested", "n"), R("n", "renamed"), X("renamed.x"), D("d", "1", "number"),
               A("list", "{{ price }}", "template"), mode="keep_only") == '{"renamed":{},"d":1,"list":[12.50]}'
    assert out(message, X("id"), mode="keep_only") == "{}", "remove acts on the result being built (skipped here)"
    assert out("plain words", C("", "text"), mode="keep_only", parse_json="never") == '{"text":"plain words"}'
    assert out("plain words", C("", "text"), mode="keep_only") == '{"text":"plain words"}', "auto keeps non-JSON text"
    assert out("[1,2]", C("", "text"), mode="keep_only", parse_json="never") == '{"text":"[1,2]"}'
    assert config_error({"operations": [C("id", "id")]}).code == "same_path", "copy a -> a is a no-op in edit"
    assert config_error({"mode": "keep_only", "operations": [R("id", "id")]}).code == "same_path"


def each_item_mode():
    """# - FB5 - every element in order, array_path, element-relative paths, aggregated skips, bounds."""
    items = '[{"sku": "A", "price": 12.50}, {"sku": "B"}, {"sku": "C", "price": 3}]'
    result = edit(items, D("price", "0", "number"), S("label", "{{ sku }}-{{ price }}", "template"), R("sku", "code"),
                  apply_to="each_item")
    assert result["text"] == ('[{"code":"A","price":12.50,"label":"A-12.50"},{"code":"B","price":0},'
                              '{"code":"C","price":3,"label":"C-3"}]'), result["text"]
    assert result["report"] == {"apply_to": "each_item", "mode": "edit", "operations": 3, "applied": 8, "skipped": 1,
                                "items": 3}
    assert result["skipped"] == [{"index": 2, "code": "reference_missing", "params": {"reference": "{{ price }}"},
                                  "message": "the template reference {{ price }} does not resolve in the input.",
                                  "items": 1, "first_item": 1}]
    assert logic.describe_skip(result["skipped"][0], {"operations": [{}, S("label", "x{{ sku }}", "template")]}) == (
        'Operation 2 (set label = "x{{ sku }}" (template)) skipped on 1 item(s), first at item [1]: '
        "the template reference {{ price }} does not resolve in the input.")
    envelope = '{"page": 1, "data": {"orders": [{"id": 1, "amount": 9.90, "note": "x"}, {"id": 2, "amount": 5}]}}'
    assert out(envelope, C("id", "id"), S("total", "{{ amount }}", "template"), mode="keep_only",
               apply_to="each_item", array_path="data.orders") == '[{"id":1,"total":9.90},{"id":2,"total":5}]'
    assert out('[1, "two", null, [3]]', S("", '{"wrapped": true}', "json"), apply_to="each_item") == (
        '[{"wrapped":true},{"wrapped":true},{"wrapped":true},{"wrapped":true}]')
    assert out('[1, "two"]', C("", "value"), mode="keep_only", apply_to="each_item") == '[{"value":1},{"value":"two"}]'
    assert out("[]", S("a", "x"), apply_to="each_item") == "[]"
    assert out('{"rows": [[1], [2]]}', A("", "0", "number"), apply_to="each_item", array_path="rows") == "[[1,0],[2,0]]"
    error = fails('[{"a": {}}, {"a": "x"}, {"a": "y"}]', S("a.b", "v"), apply_to="each_item")
    assert (error.code, error.index, error.item) == ("path_blocked", 1, 1), "the first failing item stops everything"
    for message, array_path, code, params in (('{"a": 1}', "", "not_array", {}), ("text", "", "not_array", {}),
                                              ('{"rows": 1}', "rows", "array_path_not_array", {}),
                                              ('{"x": []}', "rows", "array_path_missing", {}),
                                              ("[1,2,3,4]", "", "too_many_items", {"count": 4, "max": 3})):
        error = fails(message, S("a", "x"), apply_to="each_item", array_path=array_path, max_items=3)
        assert (error.code, error.params, error.index) == (code, params, None), (message, error.code)
    started = time.monotonic()
    big = json.dumps([{"n": index, "drop": "x"} for index in range(logic.MAX_ITEMS)])
    result = edit(big, X("drop"), S("ok", True, "boolean"), apply_to="each_item")
    assert result["report"]["items"] == 10_000 and json.loads(result["text"])[9_999] == {"n": 9_999, "ok": True}
    assert time.monotonic() - started < 10, "10,000 items edit in a few seconds at most"
    error = fails(json.dumps(list(range(logic.MAX_ITEMS + 1))), S("", "x"), apply_to="each_item")
    assert error.params == {"count": 10_001, "max": 10_000}
    calls = []
    options = logic.configuration({"apply_to": "each_item", "operations": [S("", "x")]})
    assert logic.process(big, options, cancelled=lambda: calls.append(1) or len(calls) > 2) is None


def settings_validation():
    """# - FB6 - invalid settings are refused with stable codes and indices, at save and at prepare."""
    one = [S("a", "x")]
    cases = [
        ("config_type", ["not", "an", "object"], None, None),
        ("mode_invalid", {"mode": "replace", "operations": one}, None, "mode"),
        ("apply_to_invalid", {"apply_to": "items", "operations": one}, None, "apply_to"),
        ("on_missing_invalid", {"on_missing": "ignore", "operations": one}, None, "on_missing"),
        ("parse_json_invalid", {"parse_json": "yes", "operations": one}, None, "parse_json"),
        ("on_error_invalid", {"on_error": "no_match", "operations": one}, None, "on_error"),
        ("operations_type", {"operations": {"op": "set"}}, None, "operations"),
        ("operations_empty", {"operations": []}, None, "operations"),
        ("operations_too_many", {"operations": one * 65}, None, "operations"),
        ("operation_type", {"operations": ["set a"]}, 1, None),
        ("op_invalid", {"operations": [*one, {"op": "move", "from": "a", "to": "b"}]}, 2, "op"),
        ("operation_unknown_key", {"operations": [{**S("a", "x"), "vaule": 1}]}, 1, "op"),
        ("operation_unknown_key", {"operations": [{**C("a", "b"), "path": "c"}]}, 1, "op"),
        ("field_missing", {"operations": [{"op": "copy", "from": "a"}]}, 1, "to"),
        ("field_missing", {"operations": [{"op": "remove"}]}, 1, "path"),
        ("rename_root", {"operations": [R("", "b")]}, 1, "from"),
        ("rename_root", {"operations": [R("a", "")]}, 1, "to"),
        ("remove_root", {"operations": [X("")]}, 1, "path"),
        ("same_path", {"operations": [R("a.b", 'a["b"]')]}, 1, "to"),
        ("path_invalid", {"operations": [S("a..b", "x")]}, 1, "path"),
        ("path_invalid", {"operations": [C("a[", "b")]}, 1, "from"),
        ("path_spaces", {"operations": [R("a", "b. c")]}, 1, "to"),
        ("value_type_invalid", {"operations": [S("a", "x", "text")]}, 1, "value_type"),
        ("value_missing", {"operations": [{"op": "set", "path": "a", "value_type": "string"}]}, 1, "value"),
        ("value_not_string", {"operations": [S("a", 5)]}, 1, "value"),
        ("value_not_number", {"operations": [S("a", "12,5", "number")]}, 1, "value"),
        ("value_not_number", {"operations": [S("a", "NaN", "number")]}, 1, "value"),
        ("value_not_number", {"operations": [S("a", True, "number")]}, 1, "value"),
        ("value_not_number", {"operations": [S("a", "0x1F", "number")]}, 1, "value"),
        ("value_not_number", {"operations": [S("a", float("inf"), "number")]}, 1, "value"),
        ("value_not_boolean", {"operations": [S("a", "yes", "boolean")]}, 1, "value"),
        ("value_not_json", {"operations": [S("a", "{a: 1}", "json")]}, 1, "value"),
        ("value_not_json", {"operations": [S("a", "[NaN]", "json")]}, 1, "value"),
        ("value_too_deep", {"operations": [S("a", "[" * 33 + "]" * 33, "json")]}, 1, "value"),
        ("value_too_long", {"operations": [S("a", "x" * 65_537)]}, 1, "value"),
        ("max_items_invalid", {"apply_to": "each_item", "max_items": 0, "operations": one}, None, "max_items"),
        ("max_items_invalid", {"apply_to": "each_item", "max_items": "10", "operations": one}, None, "max_items"),
        ("max_items_invalid", {"apply_to": "each_item", "max_items": True, "operations": one}, None, "max_items"),
        ("max_items_invalid", {"apply_to": "each_item", "max_items": 10_001, "operations": one}, None, "max_items"),
        ("array_path_invalid", {"apply_to": "each_item", "array_path": "a..", "operations": one}, None, "array_path"),
        ("array_path_type", {"apply_to": "each_item", "array_path": 3, "operations": one}, None, "array_path"),
        ("array_path_too_deep", {"apply_to": "each_item", "array_path": ".".join("k" * 33), "operations": one}, None,
         "array_path"),
        ("each_item_needs_json", {"apply_to": "each_item", "parse_json": "never", "operations": one}, None,
         "parse_json"),
    ]
    for code, raw, index, field in cases:
        error = config_error(raw)
        assert (error.code, error.index, error.field) == (code, index, field), (code, error.code, error.index, error.field)
    assert logic.configuration({"operations": [S("a", "[" * 32 + "]" * 32, "json")]})
    found = logic.problems({"mode": "x", "apply_to": "each_item", "array_path": "a..", "max_items": 0,
                            "operations": [S("a", "x"), X(""), S("b", "1,5", "number")]})
    assert [(item.field, item.code, item.index) for item in found] == [
        ("mode", "mode_invalid", None), ("array_path", "array_path_invalid", None),
        ("max_items", "max_items_invalid", None), ("path", "remove_root", 2), ("value", "value_not_number", 3)]
    # array_path and max_items are ignored (not checked) with apply_to message; unusable values are reset on Apply.
    ignored = logic.configuration({"array_path": "a..b", "max_items": 0, "operations": one})
    assert ignored.to_config()["array_path"] == "" and ignored.to_config()["max_items"] == 10_000
    kept = logic.configuration({"array_path": "rows", "max_items": 50, "operations": one})
    assert (kept.to_config()["array_path"], kept.to_config()["max_items"]) == ("rows", 50)
    options = logic.configuration({"operations": one, "execution": {"inhibited": False}, "future_framework_key": 1})
    assert options.to_config() == {"mode": "edit", "apply_to": "message", "array_path": "", "max_items": 10_000,
                                   "operations": [{"op": "set", "path": "a", "value": "x", "value_type": "string"}],
                                   "on_missing": "skip", "parse_json": "auto", "on_error": "fail"}
    frozen = MappingProxyType({"operations": (MappingProxyType(S("t", ("a", 1), "json")),)})
    assert logic.process("{}", logic.configuration(frozen))["text"] == '{"t":["a",1]}', "prepare snapshots are thawed"
    block = EditFieldsBlock()
    assert isinstance(block.prepare_runtime(SimpleNamespace(config=frozen)), BlockRuntimePreparation)
    for config, expected in (({"operations": ()}, "Invalid Edit Fields settings: Add at least one operation."),
                             ({"apply_to": "each_item", "array_path": "a[", "operations": one},
                              "Invalid Edit Fields settings: array_path is not a valid path"),
                             ({"operations": [S("a", "x"), S("b", "{{ x", "template")]},
                              "Invalid Edit Fields settings: Operation 2: A template reference opened with {{ has no "
                              "closing }}")):
        try:
            block.prepare_runtime(SimpleNamespace(config=MappingProxyType(config)))
        except ValueError as exc:
            assert str(exc).startswith(expected), str(exc)
        else:
            raise AssertionError(f"prepare accepted {config}")
    assert block.default_config() == logic.DEFAULTS and logic.configuration(block.default_config())


def limits_and_parsing():
    """# - FB7 - depth, size and copy limits; parse_json; never a partial result."""
    deep = "[" * 32 + "]" * 32
    assert out(deep, S("", "1", "number")) == "1", "32 levels are accepted"
    for message in ("[" * 33 + "]" * 33, "[" * 100_000 + "]" * 100_000):
        assert fails(message, S("", "x")).code == "too_deep"
    assert fails("{}", S(".".join(["k"] * 32), "[]", "json")).code == "result_too_deep"
    assert out("{}", S(".".join(["k"] * 31), "[]", "json")).count("{") == 31
    native = current = []
    for _ in range(50):
        current.append([])
        current = current[0]
    assert fails(native, S("", "x")).code == "too_deep", "deep native values fail before any work"
    cyclic = {}
    cyclic["self"] = cyclic
    assert fails(cyclic, S("a", "x")).code == "too_deep", "cyclic native values never loop"
    assert fails({1: "x"}, S("a", "x")).code == "message_not_text"
    assert out({"native": [1, 2.5, None, True]}, S("x", "1", "number")) == '{"native":[1,2.5,null,true],"x":1}'
    big = json.dumps({"blob": "x" * (5 * 1024 * 1024)})
    assert fails(big, C("blob", "copy")).code == "output_too_large", "8 MiB output bound"
    assert fails(json.dumps({"blob": "x" * (8 * 1024 * 1024)}), S("a", "x")).code == "message_too_large"
    assert fails(json.dumps({"blob": "é" * (4 * 1024 * 1024 + 1)}), S("a", "x")).code == "message_too_large", "bytes"
    error = fails(json.dumps({"blob": "x" * 140_000}), S("u", "{{ blob }}", "template"),
                  S("t", "{{ blob }}" * 64, "template"))
    assert error.code == "output_too_large" and error.index == 2, "a template text is bounded while it is built"
    started = time.monotonic()
    blow_up = [C("", f"k{index}") for index in range(64)]
    assert fails('{"a": [1, 2, 3, 4, 5, 6, 7, 8]}', *blow_up).code == "too_much_data", "self copies are bounded"
    assert time.monotonic() - started < 30
    # parse_json
    assert out("12.50", S("", "{{ }}" if False else "x")) == '"x"'
    assert out("12.50", C("", "v"), mode="keep_only") == '{"v":12.50}'
    assert out("12.50", C("", "v"), mode="keep_only", parse_json="never") == '{"v":"12.50"}'
    assert out("not json", C("", "v"), mode="keep_only") == '{"v":"not json"}'
    assert out("NaN", C("", "v"), mode="keep_only") == '{"v":"NaN"}', "NaN is not JSON: kept as text"
    assert fails("not json", S("a", "x"), parse_json="always").code == "message_not_json"
    assert fails("not json", S("a", "x")).code == "path_blocked", "text is a JSON string: no field to write"
    assert out({"n": 1}, C("", "raw"), mode="keep_only", parse_json="never") == '{"raw":"{\\"n\\":1}"}'
    assert out('{"a":1,"a":2}', S("b", "x")) == '{"a":2,"b":"x"}', "duplicate keys: the last one wins"


def context(value, config=None, content_type="application/json", services=None):
    ports = (SimpleNamespace(id=1, name="out"), SimpleNamespace(id=2, name="error"))
    inputs = {} if value is None else {"in": value}
    return BlockRuntimeContext(run_id="unit", node_id="ef", kind="edit_fields", title="Edit Fields",
                               config={**EditFieldsBlock().default_config(), **(config or {})},
                               inputs=inputs, input_content_types={"in": content_type},
                               input_ports=(SimpleNamespace(id=1, name="in", required=True,
                                                            execution_requirement="required_for_execution"),),
                               output_ports=ports, root_dir=ROOT, services=services or {})


def emitted(result):
    return {output.port_name: (output.value, output.content_type) for output in result.outputs if output.emitted}


def runtime_results():
    """# - FB7 - FB8 - direct results: success, skipped traces, fail and route without content, cancel, no input."""
    block = EditFieldsBlock()
    message = '{"name": "private-name", "price": 12.50, "secret": "private-secret"}'
    config = {"operations": [X("secret"), S("currency", "EUR"), R("missing", "x")]}
    for content_type in ("application/json", "text/plain", "message/plain"):
        result = block.execute_runtime(context(message, config, content_type))
        assert result.status == "success" and result.content_type == "application/json"
        assert emitted(result) == {"out": ('{"name":"private-name","price":12.50,"currency":"EUR"}', "application/json")}
        assert [(o.port_name, o.value) for o in result.outputs if not o.emitted] == [("error", "")]
        assert result.metadata["edit_fields"] == {"apply_to": "message", "mode": "edit", "operations": 3,
                                                  "applied": 2, "skipped": 1}
        assert result.logs == ["[edit-fields] Operation 3 (rename missing → x) skipped: from missing does not resolve.",
                               "[edit-fields] Edited the message: 2 of 3 operation(s) applied, 1 skipped (edit)."]
        assert "private" not in json.dumps(result.metadata) + result.last_message + "".join(result.logs)
    native = block.execute_runtime(context({"a": [1, 2]}, {"operations": [A("a", "3", "number")]}))
    assert emitted(native)["out"][0] == '{"a":[1,2,3]}'
    items = block.execute_runtime(context('[{"a": 1}, {"a": 2}]', {"apply_to": "each_item", "operations": [
        R("a", "b"), C("zz", "y")]}))
    assert emitted(items)["out"][0] == '[{"b":1},{"b":2}]'
    assert items.logs == ["[edit-fields] Operation 2 (copy zz → y) skipped on 2 item(s), first at item [0]: "
                          "from zz does not resolve.",
                          "[edit-fields] Edited 2 item(s): 2 operation run(s) applied, 2 skipped (edit)."]
    failing = {"apply_to": "each_item", "operations": [S("ok", True, "boolean"), S("address.city", "Lyon")]}
    rows = '[{"address": {}}, {"address": {}}, {"address": {}}, {"address": "private-address"}]'
    failed = block.execute_runtime(context(rows, failing))
    assert failed.status == "failed" and failed.outputs == [] and failed.exit_code == 1
    assert failed.error == ('Operation 2 (set address.city = "Lyon") failed on item [3]: cannot write at '
                            "address.city: address is of type string, not object.")
    assert failed.logs == ['[edit-fields-error] Operation 2 (set address.city = "Lyon") on item [3]: cannot write at '
                           "address.city: address is of type string, not object. Nothing was published."]
    routed = block.execute_runtime(context(rows, {**failing, "on_error": "route"}))
    assert routed.status == "success" and list(emitted(routed)) == ["error"]
    assert [(o.port_name, o.value) for o in routed.outputs if not o.emitted] == [("out", "")]
    document = json.loads(emitted(routed)["error"][0])
    assert document == {"code": "path_blocked", "message": 'Operation 2 (set address.city = "Lyon") on item [3]: cannot '
                        "write at address.city: address is of type string, not object.", "operation": 2, "op": "set",
                        "item": 3}
    assert "private" not in emitted(routed)["error"][0] + "".join(routed.logs)
    assert routed.logs[0].startswith("[edit-fields] Routed path_blocked to the error port: Operation 2")
    readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
    assert emitted(routed)["error"][0] in readme and failed.error.replace("failed ", "") .startswith("Operation 2")
    message_level = block.execute_runtime(context("not json", {"parse_json": "always", "on_error": "route"}))
    assert json.loads(emitted(message_level)["error"][0]) == {
        "code": "message_not_json", "message": "the message is not valid JSON and parse_json is always.",
        "operation": None, "op": None, "item": None}
    plain = block.execute_runtime(context('{"a": [1]}', {"apply_to": "each_item", "array_path": "a",
                                                         "operations": [S("x", "y")]}))
    assert plain.status == "failed" and plain.error == ("Operation 1 (set x = \"y\") failed on item [0]: cannot write "
                                                        "at x: $ is of type number, not object.")
    structure = block.execute_runtime(context('{"a": 1}', {"apply_to": "each_item", "operations": [S("x", "y")]}))
    assert structure.error == "The message could not be edited: the message is not a JSON array."
    invalid = block.execute_runtime(context('{"a": 1}', {"operations": []}))
    assert invalid.status == "failed" and invalid.error == "Invalid Edit Fields settings: Add at least one operation."
    invalid_routed = block.execute_runtime(context('{"a": 1}', {"operations": [], "on_error": "route"}))
    assert invalid_routed.status == "failed" and not invalid_routed.outputs, "invalid settings are never routed"
    assert block.execute_runtime(context(None)).status == "skipped"
    assert block.execute_runtime(context("   ")).status == "skipped", "a blank message is not edited"
    cancelled = block.execute_runtime(context('{"a": 1}', services={"cancel_requested": lambda: True}))
    assert cancelled.status == "skipped" and not any(output.emitted for output in cancelled.outputs)
    french = EditFieldsBlock().translate("block.edit_fields.op_failed_item",
                                         {"index": 2, "label": "x", "item": 3, "reason": "r"}, lang="fr")
    assert french == "L’opération 2 (x) a échoué sur l’élément [3] : r"


def editor_contract():
    """# - FB6 - FB8 - editor rows round trip, Apply canonicalization, live validation and the sample preview."""
    block = EditFieldsBlock()
    node = block.build_node_payload(node_id="ef-1")
    node["config"]["execution"] = {"inhibited": False}
    rows = [
        {"op": "set", "path": "price", "from": "", "to": "", "value_type": "number", "value_text": " 12.50 ", "value_bool": "true"},
        {"op": "rename", "path": "ignored", "from": "fname", "to": "first_name", "value_type": "string",
         "value_text": "ignored", "value_bool": "true"},
        {"op": "set", "path": "ok", "from": "", "to": "", "value_type": "boolean", "value_text": "", "value_bool": "false"},
        {"op": "set", "path": "tags", "from": "", "to": "", "value_type": "json", "value_text": '["a", 1.0]', "value_bool": "true"},
        {"op": "default", "path": "n", "from": "", "to": "", "value_type": "null", "value_text": "x", "value_bool": "true"},
        {"op": "append", "path": "log", "from": "", "to": "", "value_type": "template", "value_text": "{{ fname }}!",
         "value_bool": "true"},
        {"op": "remove", "path": "secret", "from": "x", "to": "y", "value_type": "string", "value_text": "", "value_bool": "true"},
    ]
    saved = block.handle_ui_action(node=node, action="save_settings", values={"node_patch": {
        "title": "Clean form", "config": {"mode": "edit", "apply_to": "each_item", "array_path": "rows",
                                          "max_items": "500", "operations": rows, "on_missing": "fail",
                                          "on_error": "route"}}})
    assert saved["node_patch"]["title"] == "Clean form"
    operation = saved["graph_operations"][0]
    assert operation["op"] == "update_node_config" and operation["replace"] is True and operation["node_id"] == "ef-1"
    config = operation["config"]
    assert config["execution"] == {"inhibited": False}
    assert (config["apply_to"], config["array_path"], config["max_items"], config["on_missing"], config["on_error"]) == (
        "each_item", "rows", 500, "fail", "route")
    assert config["operations"] == [
        {"op": "set", "path": "price", "value": "12.50", "value_type": "number"},
        {"op": "rename", "from": "fname", "to": "first_name"},
        {"op": "set", "path": "ok", "value": False, "value_type": "boolean"},
        {"op": "set", "path": "tags", "value": '["a", 1.0]', "value_type": "json"},
        {"op": "default", "path": "n", "value_type": "null"},
        {"op": "append", "path": "log", "value": "{{ fname }}!", "value_type": "template"},
        {"op": "remove", "path": "secret"}]
    assert logic.editor_rows(config) == [
        {"op": "set", "path": "price", "from": "", "to": "", "value_type": "number", "value_text": "12.50", "value_bool": "true"},
        {"op": "rename", "path": "", "from": "fname", "to": "first_name", "value_type": "string", "value_text": "",
         "value_bool": "true"},
        {"op": "set", "path": "ok", "from": "", "to": "", "value_type": "boolean", "value_text": "", "value_bool": "false"},
        {"op": "set", "path": "tags", "from": "", "to": "", "value_type": "json", "value_text": '["a", 1.0]', "value_bool": "true"},
        {"op": "default", "path": "n", "from": "", "to": "", "value_type": "null", "value_text": "", "value_bool": "true"},
        {"op": "append", "path": "log", "from": "", "to": "", "value_type": "template", "value_text": "{{ fname }}!",
         "value_bool": "true"},
        {"op": "remove", "path": "secret", "from": "", "to": "", "value_type": "string", "value_text": "", "value_bool": "true"}]
    hand = logic.editor_rows({"operations": [S("n", 3, "number"), S("j", {"a": [1, None]}, "json"), S("t", True, "boolean"),
                                             {"op": "teleport"}, "junk"]})
    assert [(row["op"], row["value_text"], row["value_bool"]) for row in hand] == [
        ("set", "3", "true"), ("set", '{"a":[1,null]}', "true"), ("set", "", "true"), ("set", "", "true"), ("set", "", "true")]
    refused = block.handle_ui_action(node=node, action="save_settings", values={"node_patch": {"config": {
        "apply_to": "each_item", "max_items": "lots", "operations": rows}}})
    assert refused == {"error": "Invalid Edit Fields settings: max_items must be a whole number from 1 to 10000."}
    refused = block.handle_ui_action(node=node, action="save_settings", values={"node_patch": {"config": {
        "operations": [rows[0], {**rows[0], "value_text": "12,50"}]}}})
    assert refused == {"error": "Invalid Edit Fields settings: Operation 2: value must be a JSON number such as 42, "
                                "-0.5, 12.50 or 1e3."}
    check = block.handle_ui_request(node=node, route="validate", method="POST", values={"config": {
        "apply_to": "each_item", "array_path": "a..", "operations": [{**rows[0], "path": "a..b"}, rows[1]]}})
    assert not check["valid"] and [(e["index"], e["field"], e["code"]) for e in check["errors"]] == [
        (None, "array_path", "array_path_invalid"), (1, "path", "path_invalid")]
    assert block.handle_ui_request(node=node, route="validate", method="POST", values={"config": {"operations": rows}}) == {
        "valid": True, "errors": []}
    sample = '{"fname": "Ada", "price": 1, "secret": "s", "log": []}'
    done = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"operations": rows[:2] + rows[5:]}, "sample": sample})
    assert done["status"] == "success" and done["report"]["applied"] == 4 and done["skipped"] == []
    assert json.loads(done["output"]) == {"price": 12.5, "first_name": "Ada", "log": ["Ada!"]}
    assert '"price": 12.50' in done["output"] and done["input"].startswith('{\n  "fname": "Ada"')
    skipped = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"operations": rows[1:2]}, "sample": "{}"})
    assert skipped["skipped"] == [{"index": 1, "code": "source_missing", "params": {"field": "from", "path": "fname"},
                                   "message": "from fname does not resolve.", "items": 1, "first_item": None,
                                   "label": "rename fname → first_name"}]
    failed = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"apply_to": "each_item", "operations": [{**rows[0], "path": "a.b"}]}, "sample": '[{"a": {}}, {"a": 1}]'})
    assert failed["status"] == "failed" and failed["error_document"] is None and "output" not in failed
    assert {key: failed["failure"][key] for key in ("index", "item", "code", "label")} == {
        "index": 1, "item": 1, "code": "path_blocked", "label": "set a.b = 12.50"}
    routed = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"on_error": "route", "operations": [S("x", "y")]}, "sample": "[1]"})
    assert routed["status"] == "routed" and routed["error_document"]["code"] == "path_blocked"
    items = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"apply_to": "each_item", "operations": [rows[1]]}, "sample": '[{"fname": "A"}, {}]'})
    assert items["report"]["items"] == 2 and items["skipped"][0]["first_item"] == 1
    text = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"mode": "keep_only", "operations": [C("", "text")]}, "sample": "hello"})
    assert text["input"] == "hello" and text["input_json"] is False and json.loads(text["output"]) == {"text": "hello"}
    invalid = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"operations": [S("a", "{{", "template")]}, "sample": "{}"})
    assert invalid["valid"] is False and invalid["errors"][0]["code"] == "template_unclosed"
    big = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"operations": rows[:1]}, "sample": "x" * (logic.MAX_SAMPLE_CHARS + 1)})
    assert big["sample_error"]["code"] == "sample_too_large"
    long = block.handle_ui_request(node=node, route="preview", method="POST", values={
        "config": {"operations": [S("b", "{{ a }}", "template")]}, "sample": json.dumps({"a": "x" * 60_000})})
    assert long["output_truncated"] is True and len(long["output"]) <= logic.MAX_PREVIEW_CHARS + 2
    try:
        block.handle_ui_request(node=node, route="unknown", method="POST", values={})
    except NotImplementedError:
        pass
    else:
        raise AssertionError("unknown routes must be refused")


def surfaces_and_language():
    """# - FB8 - escaped surfaces, node card summary and a complete, consistent EN/FR catalog."""
    assert EN.keys() == FR.keys()
    for key, text in EN.items():
        assert key.startswith("block.edit_fields.") and text and FR[key]
        assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", FR[key])), key
        assert "{{" not in text + FR[key] and "}}" not in text + FR[key], f"literal braces need parameters: {key}"
    model = json.loads((PACKAGE / "model.json").read_text(encoding="utf-8"))
    assert EN["block.edit_fields.model.title"] == model["title"]
    assert EN["block.edit_fields.model.description"] == model["description"]
    assert model["version"] == "0.1.0" and model["config"] == logic.DEFAULTS
    assert [port["name"] for port in model["ports"]["outputs"]] == ["out", "error"]
    assert [port["execution_requirement"] for port in model["ports"]["inputs"]] == ["required_for_execution"]
    used = set()
    for path in [*PACKAGE.glob("*.py"), *PACKAGE.glob("*.html"), *PACKAGE.glob("assets/js/*.js")]:
        source = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'"block\.edit_fields\.([a-z_.]*[a-z_])"', source))
        used |= set(re.findall(r'_text\(block, "([a-z_]+)"', source))
        used |= set(re.findall(r'_attr\(block, "[a-z-]+", "([a-z_]+)"', source))
        used |= {"help_" + key for key in re.findall(r'_hint\(block, "([a-z_]+)"', source)}
        used |= set(re.findall(r'\b(?:say|note|tr|pane)\([a-zA-Z]*,? ?"([a-z_]+)"', source))
        used |= set(re.findall(r'\["([a-z_]+)", "[A-Z]', source))
    used |= {"op_failed", "op_failed_item", "message_failed", "invalid_operation", "invalid_settings", "card_more",
             "preview_items", "skipped_line", "skipped_items_line"}
    assert len(used) > 40, sorted(used)
    for key in used:
        assert f"block.edit_fields.{key}" in EN or f"block.edit_fields.{key}.other" in EN, key
    from blocs.edit_fields import ui
    for spec in ui.field_specs(EditFieldsBlock()):
        assert EN[f"block.edit_fields.help_{spec['key']}"] == spec["help"], spec["key"]
        assert spec["key"] in logic.DEFAULTS
    for key, text in ui.HELP.items():
        assert EN[f"block.edit_fields.help_{key}"] == text, key
    source = (PACKAGE / "logic.py").read_text(encoding="utf-8") + (PACKAGE / "paths.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'(?:error|_missing\(run, item,|EditError)\(\s*"([a-z_]+)",', source)) | {
        f"{key}_invalid" for key in ("mode", "apply_to", "on_missing", "parse_json", "on_error")} | {
        "array_path_type", "array_path_too_long", "array_path_too_deep", "array_path_invalid", "array_path_spaces",
        "sample_type", "sample_too_large", "internal"}
    assert len(codes) >= 55, sorted(codes)
    for code in codes:
        assert f"block.edit_fields.err_{code}" in EN, code
    block = EditFieldsBlock()
    node = block.build_node_payload(node_id="x<1>")
    node["title"] = "<b>{{ editor }}</b>"
    node["config"].update({"mode": "keep_only", "apply_to": "each_item", "array_path": "<rows>",
                           "operations": [S("<script>", "</option>"), C("b", "c"), X("d")]})
    for surface in (block.render_modal(node=node), block.render_inspector_panel(node=node)):
        html = surface["html"]
        for placeholder in ("{{ node_title }}", "{{ node_id }}", "{{ editor }}</div>", "{{ runtime_html }}",
                            "{{ inputs_html }}"):
            assert placeholder not in html, placeholder
        assert "<script>" not in html and "&lt;b&gt;{{ editor }}&lt;/b&gt;" in html
        assert html.count("data-ef-editor") == 1 and 'value="&lt;/option&gt;"' in html and "data-owned-apply" in html
        assert "data-ef-row-template" in html and html.count("data-ef-row>") == 4
        assert 'value="&lt;rows&gt;"' in html and html.count("data-ef-array-only") == 2
        assert "data-ef-array-only hidden" not in html, "array settings are visible with each_item"
        assert "data-properties-surface=" in html and 'data-i18n="block.edit_fields.help_value"' in html
        assert "data-i18n-params=" in html and "{{ path }}" in html
    card = block.render_node_card(node=node)["html"]
    assert "Keep only" in card and "Each item of" in card and "&lt;rows&gt;" in card and "and 2 more" in card
    assert 'set &lt;script&gt; = &quot;&lt;/option&gt;&quot;' in card
    plain = EditFieldsBlock().render_node_card(node=EditFieldsBlock().build_node_payload(node_id="d"))["html"]
    assert plain.count("set processed = true") == 1 and "Each item" not in plain and "Keep only" not in plain
    message = EditFieldsBlock().render_modal(node=EditFieldsBlock().build_node_payload(node_id="m"))["html"]
    assert message.count("data-ef-array-only hidden") == 2, "array settings are hidden with apply_to message"
    rows = re.findall(r'<li class="ef-row" data-ef-row>.*?</li>', message)
    assert len(rows) == 2 and 'data-ef-show="value_bool">' in rows[0] and 'data-ef-show="value" hidden' in rows[0]
    assert 'data-ef-show="from" hidden' in rows[0] and 'data-ef-show="path">' in rows[0]
    broken = block.build_node_payload(node_id="b")
    broken["config"]["operations"] = []
    assert "Invalid settings" in block.render_node_card(node=broken)["html"]
    french = EditFieldsBlock().translate("block.edit_fields.help_value", ui.HELP_PARAMS["value"], lang="fr")
    assert "{{ path }}" in french and "\\{{" in french
    labels = [logic.short_label(item) for item in (S("p", "12.50", "number"), S("t", "Hi {{ n }}", "template"),
              D("d", None, "null"), A("tags", True, "boolean"), C("a", ""), R("x", "y"), X('h["a.b"]'),
              S("j", {"a": 1}, "json"), S("s", "x" * 60))]
    assert labels == ["set p = 12.50", 'set t = "Hi {{ n }}" (template)', "default d ?= null", "append tags += true",
                      "copy a → $", "rename x → y", 'remove h["a.b"]', 'set j = {"a":1}',
                      'set s = "' + "x" * 38 + "…"]


def readme_example():
    """# - FB1 - FB2 - FB3 - FB4 - FB5 - the README examples edit exactly as documented."""
    text = (PACKAGE / "README.md").read_text(encoding="utf-8")
    section = text.split("## Example", 1)[1]
    blocks = section.split("```json")
    settings = json.loads(blocks[1].split("```", 1)[0])
    message = blocks[2].split("```", 1)[0].strip()
    expected = blocks[3].split("```", 1)[0].strip()
    result = logic.process(message, logic.configuration(settings))
    assert result["text"] == expected, result["text"]
    assert [entry["index"] for entry in result["skipped"]] == [8]
    keep = logic.configuration({"mode": "keep_only", "apply_to": "each_item", "array_path": "orders",
                                "operations": [C("id", "id"), S("total", "{{ amount }}", "template")]})
    assert logic.process('{"orders": [{"id": 7, "amount": 9.90, "note": "x"}]}', keep)["text"] == '[{"id":7,"total":9.90}]'
    assert '`{"orders": [{"id": 7, "amount": 9.90, "note": "x"}]}` gives `[{"id":7,"total":9.90}]`' in text


def main():
    readme_example()
    path_syntax()
    set_values()
    other_operations()
    templates()
    keep_only_mode()
    each_item_mode()
    settings_validation()
    limits_and_parsing()
    runtime_results()
    editor_contract()
    surfaces_and_language()
    print("[ok] Edit Fields paths, operations, templates, modes, limits, settings, runtime and editor contracts")


if __name__ == "__main__":
    main()
