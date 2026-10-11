"""FB1-FB8: real graph runs in both runtimes, all package origins, modes, failures, routing, bursts and Stop."""

from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.edit_fields.block import EditFieldsBlock
from blocs.list.block import ListBlock
from block_test_packages import install_test_package, surface_payload
from ui_smoke_common import (create_run_api, data_edge, display_node, graph_payload, isolated_server, play_run_api,
                             prepare_run_api, stop_run_api, text_node, wait_for_run_predicate, wait_for_run_terminal)

BLOCK = EditFieldsBlock()
PACKAGE = Path(__file__).resolve().parents[1]
README = (PACKAGE / "README.md").read_text(encoding="utf-8").split("## Example", 1)[1].split("```json")
FORM_SETTINGS = json.loads(README[1].split("```", 1)[0])
FORM = README[2].split("```", 1)[0].strip()
FORM_OUT = README[3].split("```", 1)[0].strip()
ORDERS = '{"page": 1, "orders": [{"id": 7, "amount": 9.90, "note": "private-note"}, {"id": 8, "amount": 12}]}'
KEEP = {"mode": "keep_only", "apply_to": "each_item", "array_path": "orders", "operations": [
    {"op": "copy", "from": "id", "to": "id"},
    {"op": "set", "path": "total", "value": "{{ amount }}", "value_type": "template"}]}
BLOCKED = {"apply_to": "each_item", "operations": [
    {"op": "set", "path": "address.city", "value": "Lyon", "value_type": "string"}]}
ROWS = '[{"address": {}}, {"address": "private-address"}]'
PORTS = ((1, "out"), (2, "error"))


def edit_node(node_id, y, version, config=None):
    node = BLOCK.build_node_payload(node_id=node_id, position={"x": 380, "y": y})
    node["config"] = {**BLOCK.default_config(), **(config or {})}
    if version:
        node["block_version"] = version
    return node


def chain(prefix, source, version, y, config=None):
    """source -> Edit Fields -> out and error displays, with ids derived from the prefix."""
    node = edit_node(f"{prefix}-ef", y, version, config)
    nodes = [source, node, display_node(f"{prefix}-out", "Out", 760, y - 40),
             display_node(f"{prefix}-error", "Error", 760, y + 40)]
    edges = [data_edge(f"{prefix}-in", source["id"], 1, node["id"], 1),
             data_edge(f"{prefix}-o", node["id"], 1, f"{prefix}-out", 1),
             data_edge(f"{prefix}-e", node["id"], 2, f"{prefix}-error", 1)]
    return nodes, edges


def document(title, *chains):
    nodes, edges = [], []
    for chain_nodes, chain_edges in chains:
        nodes += chain_nodes
        edges += chain_edges
    return graph_payload(title, nodes, edges)


def published(run, node_id):
    """Port values actually published by one Edit Fields node."""
    outputs = run.get("output_values") or {}
    return {name: outputs[f"{node_id}:{port}"] for port, name in PORTS if (outputs.get(f"{node_id}:{port}") or {}).get("value")}


def received(run, node_id):
    """Display preview of one node (single line: the worker row collapses whitespace)."""
    text = str(((run.get("worker_rows") or {}).get(node_id) or {}).get("received") or "")
    return "" if text == "-" else " ".join(text.split())


def success_graph(version):
    """# - FB1 - FB2 - FB3 - FB4 - FB5 - FB7 - several independent edits in one run."""
    items = ListBlock().build_node_payload(node_id="list-src", position={"x": 80, "y": 520},
                                           config_overrides={"items": ['{"sku":"A","price":12.50}', '{"sku":"B","price":3}']})
    listed = {"apply_to": "each_item", "operations": [
        {"op": "rename", "from": "item.sku", "to": "sku"}, {"op": "copy", "from": "item.price", "to": "price"},
        {"op": "remove", "path": "item"}, {"op": "set", "path": "source", "value": "list", "value_type": "string"}]}
    return document("Edit Fields routes",
                    chain("form", text_node("form-src", "Form", FORM, 80, 80), version, 80, FORM_SETTINGS),
                    chain("keep", text_node("keep-src", "Orders", ORDERS, 80, 300), version, 300, KEEP),
                    chain("list", items, version, 520, listed),
                    chain("route", text_node("route-src", "Rows", ROWS, 80, 740), version, 740,
                          {**BLOCKED, "on_error": "route"}))


def check_success(server, mode, version, origin):
    created = create_run_api(server, success_graph(version), runtime_mode=mode)
    run = wait_for_run_terminal(server, created["run_id"], timeout_sec=60)
    assert run["status"] == "success", (mode, origin, run.get("logs"))
    form = published(run, "form-ef")
    assert list(form) == ["out"] and form["out"]["value"] == FORM_OUT, (mode, origin, form)
    assert form["out"]["content_type"] == "application/json"
    assert '"full_name":"Ada Lovelace"' in received(run, "form-out") and not received(run, "form-error")
    keep = published(run, "keep-ef")
    assert keep["out"]["value"] == '[{"id":7,"total":9.90},{"id":8,"total":12}]', (mode, origin, keep)
    listed = published(run, "list-ef")
    assert json.loads(listed["out"]["value"]) == [{"sku": "A", "price": 12.5, "source": "list"},
                                                  {"sku": "B", "price": 3, "source": "list"}], listed
    route = published(run, "route-ef")
    assert list(route) == ["error"] and not received(run, "route-out"), (mode, origin, route)
    assert json.loads(route["error"]["value"]) == {
        "code": "path_blocked", "operation": 1, "op": "set", "item": 1,
        "message": 'Operation 1 (set address.city = "Lyon") on item [1]: cannot write at address.city: address is of '
                   "type string, not object."}
    assert '"code":"path_blocked"' in received(run, "route-error")
    logs = "\n".join(run.get("logs") or [])
    assert "[edit-fields] Operation 8 (remove coupon) skipped: path coupon does not resolve." in logs, logs[-3000:]
    assert "[edit-fields] Edited the message: 7 of 8 operation(s) applied, 1 skipped (edit)." in logs
    assert "[edit-fields] Edited 2 item(s): 4 operation run(s) applied, 0 skipped (keep_only)." in logs
    assert "[edit-fields] Routed path_blocked to the error port" in logs
    own = [line for line in run.get("logs") or [] if line.startswith("[edit-fields")]
    assert own and "private" not in "".join(own), own


def check_failure(server, mode, version, origin):
    """# - FB7 - on_error fail: failed run, nothing published, an explicit message naming operation and item."""
    graph = document("Edit Fields fails", chain("fail", text_node("fail-src", "Rows", ROWS, 80, 80), version, 80, BLOCKED))
    created = create_run_api(server, graph, runtime_mode=mode)
    run = wait_for_run_terminal(server, created["run_id"], timeout_sec=60)
    assert run["status"] == "failed", (mode, origin, run.get("logs"))
    assert not published(run, "fail-ef") and not received(run, "fail-out") and not received(run, "fail-error")
    state = json.dumps(run, ensure_ascii=False)
    assert ('Operation 1 (set address.city = \\"Lyon\\") failed on item [1]: cannot write at address.city: address '
            "is of type string, not object.") in state, state[-4000:]
    own = [line for line in run.get("logs") or [] if line.startswith("[edit-fields")]
    error = str(((run.get("results") or {}).get("fail-ef") or {}).get("error") or "")
    assert own and "private-address" not in "".join(own) + error, own
    graph = document("Edit Fields not JSON", chain("text", text_node("text-src", "Text", "plain words", 80, 80), version, 80,
                                                   {"parse_json": "always"}))
    created = create_run_api(server, graph, runtime_mode=mode)
    run = wait_for_run_terminal(server, created["run_id"], timeout_sec=60)
    assert run["status"] == "failed" and not published(run, "text-ef"), (mode, origin, run.get("logs"))
    assert ("The message could not be edited: the message is not valid JSON and parse_json is always."
            in json.dumps(run, ensure_ascii=False))


def check_prepare(server, mode, version, origin):
    """# - FB6 - invalid settings are refused at preparation, before any activation."""
    graph = document("Edit Fields invalid", chain("bad", text_node("bad-src", "Bad", '{"a": 1}', 80, 80), version, 80,
                                                  {"operations": [{"op": "set", "path": "x", "value": "Hi {{ name",
                                                                   "value_type": "template"}]}))
    created = create_run_api(server, graph, runtime_mode=mode)
    run = wait_for_run_terminal(server, created["run_id"], timeout_sec=60)
    assert run["status"] == "failed", (mode, origin, run.get("logs"))
    logs = "\n".join(run.get("logs") or [])
    assert "[prepare-error] bad-ef" in logs and ("Invalid Edit Fields settings: Operation 1: A template reference "
                                                 "opened with {{ has no closing }}") in logs, logs[-3000:]
    assert not published(run, "bad-ef") and not received(run, "bad-out")


def check_burst(server, version, origin):
    """# - FB8 - Active Runtime edits every message of a burst individually (on_each_event)."""
    from blocs.event_or.block import EventOrBlock
    merge = EventOrBlock().build_node_payload(node_id="mix", position={"x": 260, "y": 120})
    node = edit_node("burst-ef", 120, version, {"operations": [
        {"op": "set", "path": "label", "value": "#{{ n }}", "value_type": "template"}]})
    graph = graph_payload("Edit Fields burst", [
        text_node("a-src", "A", '{"n":1}', 40, 40), text_node("b-src", "B", '{"n":2}', 40, 200),
        merge, node, display_node("burst-out", "Out", 760, 60)], [
        data_edge("a-mix", "a-src", 1, "mix", 1), data_edge("b-mix", "b-src", 1, "mix", 2),
        data_edge("mix-ef", "mix", 1, "burst-ef", 1), data_edge("burst-o", "burst-ef", 1, "burst-out", 1)])
    created = create_run_api(server, graph, runtime_mode="zeromq_active")
    run = wait_for_run_terminal(server, created["run_id"], timeout_sec=60)
    assert run["status"] == "success", (origin, run.get("logs"))
    logs = "\n".join(run.get("logs") or [])
    assert logs.count("[edit-fields] Edited the message: 1 of 1 operation(s) applied") == 2, logs[-3000:]
    # The display keeps what it last received; each edited message carries its own label, never a merge.
    shown = re.findall(r'\{"n":(\d),"label":"#(\d)"\}', received(run, "burst-out"))
    assert shown and all(number == label for number, label in shown), received(run, "burst-out")


def check_stop(server, version, origin):
    """# - FB8 - a loaded Active Runtime keeps editing new waves, then stops cleanly on Stop."""
    graph = document("Edit Fields stop", chain("stop", text_node("stop-src", "Orders", ORDERS, 80, 80), version, 80, KEEP))
    prepared = prepare_run_api(server, graph)
    run_id = prepared["run_id"]
    play_run_api(server, run_id)
    wait_for_run_predicate(server, run_id, lambda state: '"total":9.90' in received(state, "stop-out"),
                           "The Edit Fields block did not publish after Play.", timeout_sec=60)
    play_run_api(server, run_id)
    second = wait_for_run_predicate(server, run_id, lambda state: "\n".join(state.get("logs") or []).count(
        "[edit-fields] Edited 2 item(s)") >= 2, "A second wave was not edited.", timeout_sec=60)
    assert second["status"] == "running", second.get("status")
    stop_run_api(server, run_id)
    final = wait_for_run_terminal(server, run_id, timeout_sec=60)
    assert final["status"] == "cancelled", (origin, final.get("status"))
    assert "[edit-fields-error]" not in "\n".join(final.get("logs") or [])


def check_surfaces(server, model, version):
    """# - FB8 - release surfaces render through the package host and satisfy the locale contract."""
    node = edit_node("surface-ef", 80, version, KEEP)
    for surface in ("modal", "inspector_panel", "node_card"):
        html = surface_payload(server, model, node, surface=surface)["html"]
        assert "{{ node_title }}" not in html and "__title__" not in html
        if surface == "node_card":
            assert "Keep only" in html and "Each item of" in html and "copy id → id" in html and "and 1 more" in html, html
        else:
            assert html.count("data-ef-row>") == 3 and "data-owned-apply" in html and "data-ef-test" in html
            assert 'data-ef-setting="apply_to"' in html and 'value="orders"' in html


def main():
    for origin in (None, "managed", "linked"):
        with isolated_server() as server:
            version = None
            if origin:
                model = install_test_package(server, "edit_fields", origin=origin)
                version = model["version"]
                check_surfaces(server, model, version)
            for mode in ("centralized", "zeromq_active"):
                check_success(server, mode, version, origin)
                check_failure(server, mode, version, origin)
                check_prepare(server, mode, version, origin)
            check_burst(server, version, origin)
            check_stop(server, version, origin)
        print(f"[ok] Edit Fields runtime: both modes, modes, failures, routing, burst and Stop ({origin or 'bundled'})",
              flush=True)


if __name__ == "__main__":
    main()
