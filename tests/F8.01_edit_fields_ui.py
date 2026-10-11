"""FB8: real release UI — operation rows, op-dependent fields, live validation, sample tests, drafts, EN/FR, responsive."""

from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from playwright.sync_api import expect, sync_playwright
from block_test_artifacts import artifact_path
from block_test_packages import install_test_package
from ui_smoke_common import create_project_api, graph_payload, isolated_server, project_editor_url
from blocs.edit_fields.block import EditFieldsBlock

VIEWPORTS = ((1440, 900), (800, 700), (390, 740), (320, 568))
GEOMETRY = """(el, bodySelector) => {
  const r = el.getBoundingClientRect(), body = el.querySelector(bodySelector) || el;
  const actions = [...el.querySelectorAll('[data-close-block-modal], [data-owned-apply]')].filter(a => a.getClientRects().length);
  const rows = [...el.querySelectorAll('[data-ef-row], .ef-mode, .ef-preview-result, .ef-pane')];
  return {
    inside: r.left >= -1 && r.right <= innerWidth + 1 && r.top >= -1 && r.bottom <= innerHeight + 1,
    overflow: el.scrollWidth > el.clientWidth + 2 || body.scrollWidth > body.clientWidth + 2
      || rows.some(row => row.scrollWidth > row.clientWidth + 2),
    controls: actions.every(a => { const b = a.getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight + 1; }),
    background: getComputedStyle(el).backgroundColor,
    unlabeled: [...el.querySelectorAll('input, select, textarea')].filter(c => !c.labels?.length
      && !c.getAttribute('aria-label') && !c.getAttribute('aria-labelledby')).length,
    small: [...el.querySelectorAll('[data-ef-row] button, [data-ef-add], [data-ef-test], .ef-segment-option')]
      .filter(b => b.getClientRects().length && b.getBoundingClientRect().height < 32).length,
  };
}"""
SAMPLE = json.dumps({"first": "Ada", "last": "Lovelace", "price": 1, "secret": "s-1"})
ROWS = json.dumps({"rows": [{"first": "Ada", "last": "Lovelace", "price": 1}, {"first": "Alan", "last": "Turing"}]})


def geometry(panel, body=".owned-body"):
    """Opaque panel inside the viewport, reachable actions, no horizontal overflow, labeled controls."""
    measured = panel.evaluate(GEOMETRY, body)
    assert measured["inside"] and measured["controls"] and not measured["overflow"], measured
    assert not measured["unlabeled"] and not measured["small"], measured
    assert measured["background"] not in {"transparent", "rgba(0, 0, 0, 0)"}, measured


class Renders:
    """Track in-flight inspector renders: the shell re-renders the inspector after selection and card refreshes."""

    def __init__(self, page):
        self.pending, self.last = 0, time.monotonic()
        page.on("request", lambda request: self.track(request, 1))
        page.on("requestfinished", lambda request: self.track(request, -1))
        page.on("requestfailed", lambda request: self.track(request, -1))

    def track(self, request, delta):
        if request.url.endswith("/inspector-panel"):
            self.pending, self.last = max(0, self.pending + delta), time.monotonic()

    def settle(self, page, quiet=1.2, timeout=45):
        """Return once no inspector render is pending or has finished for ``quiet`` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            page.wait_for_timeout(100)
            if self.pending == 0 and time.monotonic() - self.last >= quiet:
                return
        raise AssertionError("Inspector renders did not settle")


def row(scope, number):
    return scope.locator("[data-ef-row]").nth(number - 1)


def field(item, name):
    return item.locator(f'[data-ef-field="{name}"]')


def setting(scope, name):
    return scope.locator(f'[data-ef-setting="{name}"]')


def segment(scope, value):
    return scope.locator(".ef-segment-option", has=scope.page.locator(f'input[value="{value}"]'))


def edit_rows(modal):
    """# - FB8 - FB6 - rows, op-dependent fields, live validation, reordering and removal."""
    first = row(modal, 1)
    expect(field(first, "value_bool")).to_be_visible()
    expect(field(first, "value_text")).to_be_hidden()
    field(first, "value_type").select_option("template")
    expect(field(first, "value_text")).to_be_visible()
    expect(field(first, "value_text")).to_have_attribute("placeholder", "Hello {{ name }}")
    expect(field(first, "value_bool")).to_be_hidden()
    field(first, "path").fill("full_name")
    field(first, "value_text").fill("{{ first")
    error = first.locator("[data-ef-row-error]")
    expect(error).to_contain_text("has no closing }}")
    expect(field(first, "value_text")).to_have_attribute("aria-invalid", "true")
    field(first, "value_text").fill("{{ first }} {{ last }}")
    expect(error).to_be_hidden()
    modal.locator("[data-ef-add]").click()
    expect(modal.locator("[data-ef-row]")).to_have_count(2)
    second = row(modal, 2)
    expect(field(second, "path")).to_be_focused()
    field(second, "op").select_option("rename")
    expect(field(second, "path")).to_be_hidden()
    expect(field(second, "value_type")).to_be_hidden()
    field(second, "from").fill("first")
    field(second, "to").fill("first_name")
    modal.locator("[data-ef-add]").click()
    third = row(modal, 3)
    field(third, "path").fill("price")
    field(third, "value_type").select_option("number")
    expect(field(third, "value_text")).to_have_attribute("placeholder", "12.50")
    field(third, "value_text").fill("12,50")
    expect(third.locator("[data-ef-row-error]")).to_contain_text("value must be a JSON number")
    field(third, "value_text").fill("12.50")
    expect(third.locator("[data-ef-row-error]")).to_be_hidden()
    modal.locator("[data-ef-add]").click()
    fourth = row(modal, 4)
    field(fourth, "op").select_option("remove")
    expect(field(fourth, "value_text")).to_be_hidden()
    field(fourth, "path").fill("")
    expect(fourth.locator("[data-ef-row-error]")).to_contain_text("path cannot be empty")
    field(fourth, "path").fill("secret")
    modal.locator("[data-ef-add]").click()
    fifth = row(modal, 5)
    field(fifth, "op").select_option("copy")
    field(fifth, "from").fill("a")
    field(fifth, "to").fill("a")
    expect(fifth.locator("[data-ef-row-error]")).to_contain_text("from and to are the same path")
    expect(modal.locator("[data-ef-count]")).to_have_text("5 / 64")
    second.locator('[data-ef-move="up"]').click()
    expect(field(row(modal, 1), "from")).to_have_value("first")
    row(modal, 1).locator('[data-ef-move="down"]').click()
    expect(field(row(modal, 1), "path")).to_have_value("full_name")
    row(modal, 5).locator("[data-ef-remove]").click()
    expect(modal.locator("[data-ef-row]")).to_have_count(4)
    expect(modal.locator("[data-ef-count]")).to_have_text("4 / 64")


def edit_modes(modal):
    """# - FB5 - FB6 - each_item reveals array_path and max_items, each validated live in place."""
    array_path, max_items = setting(modal, "array_path"), setting(modal, "max_items")
    expect(array_path).to_be_hidden()
    segment(modal, "each_item").click()
    expect(array_path).to_be_visible()
    expect(max_items).to_have_value("10000")
    array_path.fill("rows..")
    path_error = modal.locator('[data-ef-field-error="array_path"]')
    expect(path_error).to_contain_text("array_path is not a valid path")
    expect(array_path).to_have_attribute("aria-invalid", "true")
    max_items.fill("0")
    expect(modal.locator('[data-ef-field-error="max_items"]')).to_contain_text("from 1 to 10000")
    array_path.fill("rows")
    max_items.fill("500")
    expect(path_error).to_be_hidden()
    expect(array_path).not_to_have_attribute("aria-invalid", "true")
    advanced = modal.locator("[data-ef-advanced]")
    expect(advanced).to_have_attribute("open", "")  # left open by the previews
    setting(modal, "parse_json").select_option("never")
    expect(modal.locator('[data-ef-field-error="parse_json"]')).to_contain_text("choose parse_json auto or always")
    modal.locator("[data-ef-advanced] > summary").click()  # collapse, then trigger a new check
    expect(advanced).not_to_have_attribute("open", "")
    max_items.fill("400")
    expect(advanced).to_have_attribute("open", "")  # an error inside collapsed settings reopens them
    setting(modal, "parse_json").select_option("auto")
    max_items.fill("500")
    expect(modal.locator('[data-ef-field-error="parse_json"]')).to_be_hidden()


def preview(scope, sample, status, *texts):
    """# - FB3 - FB7 - FB8 - the sample test edits the draft with the same Python code as a run."""
    result = scope.locator("[data-ef-preview-result]")
    scope.locator("[data-ef-sample]").fill(sample)
    scope.locator("[data-ef-test]").click()
    expect(result).to_have_attribute("data-status", status)
    for text in texts:
        expect(result).to_contain_text(text)
    return result


def message_previews(modal):
    """# - FB1 - FB2 - FB3 - FB7 - input and output side by side, skips, failures and routed documents."""
    result = preview(modal, SAMPLE, "success", "The edited message; nothing was saved or sent.")
    output = result.locator('[data-ef-pane="output"] pre')
    expect(output).to_contain_text('"full_name": "Ada Lovelace"')
    expect(output).to_contain_text('"price": 12.50')
    expect(output).to_contain_text('"first_name": "Ada"')
    expect(output).not_to_contain_text("secret")
    expect(result.locator('[data-ef-pane="input"] pre')).to_contain_text('"secret": "s-1"')
    result = preview(modal, '{"last": "Lovelace"}', "success",
                     'Operation 1 (set full_name = "{{ first }} {{ last }}" (template)) skipped: the template '
                     "reference {{ first }} does not resolve in the input.",
                     "Operation 2 (rename first → first_name) skipped: from first does not resolve.")
    expect(result.locator(".ef-skipped li")).to_have_count(3)
    setting(modal, "on_missing").select_option("fail")
    preview(modal, '{"last": "Lovelace"}', "failed", "The activation would fail and publish nothing (on_error fail).",
            'Operation 1 (set full_name = "{{ first }} {{ last }}" (template)) failed: the template reference '
            "{{ first }} does not resolve in the input.")
    modal.locator("[data-ef-advanced] > summary").click()
    setting(modal, "on_error").select_option("route")
    result = preview(modal, '{"last": "Lovelace"}', "routed", "this document would be published on error")
    expect(result.locator('[data-ef-pane="error"] pre')).to_contain_text('"code": "reference_missing"')
    expect(result.locator('[data-ef-pane="output"]')).to_have_count(0)
    setting(modal, "on_error").select_option("fail")
    setting(modal, "on_missing").select_option("skip")
    preview(modal, "not json at all", "failed", "Operation 3 (set price = 12.50) failed: cannot write at price: $ is of "
            "type string, not object.")
    preview(modal, "x" * 262_145, "invalid", "The sample is longer than 262144 characters.")


def main():
    expect.set_options(timeout=20_000)
    for origin in ("managed", "linked"):
        with isolated_server() as server, sync_playwright() as playwright:
            model = install_test_package(server, "edit_fields", origin=origin)
            candidate = EditFieldsBlock().build_node_payload(node_id="ui-case", position={"x": 160, "y": 160})
            candidate["block_version"] = model["version"]
            project = create_project_api(server, document=graph_payload("Edit Fields editor", [candidate], []))["project"]
            url = project_editor_url(server.base_url, project["project_id"], workspace_project_id=project["workspace_project_id"])
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                renders = Renders(page)
                page.add_init_script("window.localStorage.setItem('bloxsmith.inspectorPinned','true')")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                card = page.locator('.canvas-node[data-node-id="ui-case"]')
                expect(card.locator(".node-data-preview")).to_contain_text("set processed = true")
                card.locator("h3").dblclick()
                modal = page.locator(".ef-modal")
                expect(modal).to_be_visible()
                apply = modal.locator("[data-owned-apply]")
                expect(apply).to_be_disabled()
                expect(modal.locator("[data-ef-row]")).to_have_count(1)
                expect(field(row(modal, 1), "path")).to_have_value("processed")
                # Cancel discards a draft.
                field(row(modal, 1), "path").fill("state")
                segment(modal, "keep_only").click()
                expect(apply).to_be_enabled()
                modal.locator(".owned-footer [data-close-block-modal]").click()
                card.locator("h3").dblclick()
                expect(field(row(modal, 1), "path")).to_have_value("processed")
                expect(modal.locator('[data-ef-setting="mode"][value="edit"]')).to_be_checked()
                expect(apply).to_be_disabled()
                edit_rows(modal)
                message_previews(modal)
                edit_modes(modal)
                result = preview(modal, ROWS, "success", "2 items edited; nothing was saved or sent.",
                                 "Operation 4 (remove secret) skipped on 2 items, first at item [0]")
                expect(result.locator('[data-ef-pane="output"] pre')).to_contain_text('"full_name": "Alan Turing"')
                segment(modal, "keep_only").click()
                result = preview(modal, ROWS, "success", "2 items edited")
                output = result.locator('[data-ef-pane="output"] pre')
                expect(output).not_to_contain_text("last")
                expect(output).to_contain_text('"price": 12.50')
                with page.expect_response(lambda r: r.url.endswith("/ui-action") and r.request.method == "POST") as saved:
                    apply.click()
                assert not saved.value.json().get("error"), saved.value.json()
                expect(modal.locator("[data-ef-status]")).to_have_text("Settings applied.")
                expect(apply).to_be_disabled()
                modal.locator(".owned-footer [data-close-block-modal]").click()
                expect(card.locator(".node-data-preview")).to_contain_text(
                    'Keep only · Each item of rows: set full_name = "{{ first }} {{ last }}" (template) and 3 more')
                page.reload()
                card.locator("h3").dblclick()
                expect(modal.locator("[data-ef-row]")).to_have_count(4)
                expect(modal.locator('[data-ef-setting="mode"][value="keep_only"]')).to_be_checked()
                expect(modal.locator('[data-ef-setting="apply_to"][value="each_item"]')).to_be_checked()
                expect(setting(modal, "array_path")).to_have_value("rows")
                expect(setting(modal, "max_items")).to_have_value("500")
                expect(field(row(modal, 1), "value_type")).to_have_value("template")
                expect(field(row(modal, 1), "value_text")).to_have_value("{{ first }} {{ last }}")
                expect(field(row(modal, 2), "op")).to_have_value("rename")
                expect(field(row(modal, 2), "to")).to_have_value("first_name")
                expect(field(row(modal, 3), "value_text")).to_have_value("12.50")
                expect(field(row(modal, 4), "op")).to_have_value("remove")
                expect(setting(modal, "on_error")).to_have_value("fail")
                preview(modal, ROWS, "success", "2 items edited")
                modal.locator(".ef-section").evaluate("el => el.scrollIntoView({block: 'start'})")
                page.screenshot(path=artifact_path(f"edit-fields-{origin}-rows.png"))
                for width, height in VIEWPORTS:
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(150)
                    geometry(modal)
                    page.screenshot(path=artifact_path(f"edit-fields-{origin}-{width}.png"))
                # Keyboard: add then remove a row without the mouse.
                page.set_viewport_size({"width": 1440, "height": 900})
                modal.locator("[data-ef-add]").focus()
                page.keyboard.press("Enter")
                expect(modal.locator("[data-ef-row]")).to_have_count(5)
                expect(field(row(modal, 5), "path")).to_be_focused()
                row(modal, 5).locator("[data-ef-remove]").focus()
                page.keyboard.press("Enter")
                expect(modal.locator("[data-ef-row]")).to_have_count(4)
                modal.locator(".owned-footer [data-close-block-modal]").click()
                # Inspector: same editor, Ports tab, sample test and Apply from the sticky toolbar.
                card.click(position={"x": 20, "y": 20})
                inspector = page.locator('[data-properties-surface="inspector"][data-node-id="ui-case"]:visible')
                expect(inspector).to_be_visible()
                renders.settle(page)
                expect(inspector.locator("[data-ef-row]")).to_have_count(4)
                assert inspector.evaluate("el => el.scrollWidth <= el.clientWidth + 2")
                preview(inspector, ROWS, "success", "2 items edited")
                segment(inspector, "message").click()
                segment(inspector, "edit").click()
                expect(setting(inspector, "array_path")).to_be_hidden()
                field(row(inspector, 3), "value_text").fill("99")
                result = preview(inspector, SAMPLE, "success", "The edited message")
                expect(result.locator('[data-ef-pane="output"] pre')).to_contain_text('"price": 99')
                with page.expect_response(lambda r: r.url.endswith("/ui-action") and r.request.method == "POST") as saved:
                    inspector.locator("[data-owned-apply]").click()
                assert not saved.value.json().get("error"), saved.value.json()
                expect(card.locator(".node-data-preview")).to_contain_text("set full_name")
                expect(card.locator(".node-data-preview")).not_to_contain_text("Each item")
                inspector.locator('[data-inspector-tab="ports"]').click()
                expect(inspector.locator('[data-inspector-panel-tab="ports"]')).to_contain_text("error")
                inspector.locator('[data-inspector-tab="general"]').click()
                expect(inspector.locator('[data-inspector-panel-tab="general"]')).to_be_visible()
                page.screenshot(path=artifact_path(f"edit-fields-{origin}-inspector.png"))
                # French: translated chrome, live validation, previews and card text follow the language.
                page.goto(server.base_url + "/")
                page.locator("#homeApplicationSettingsButton").click()
                page.locator("#applicationLanguageSelect").select_option("fr")
                page.wait_for_function("window.CWMessages.getLanguage() === 'fr'")
                page.goto(url)
                expect(card.locator(".node-data-preview")).to_contain_text("et 3 autres")
                card.locator("h3").dblclick()
                expect(modal.locator("[data-ef-add]")).to_have_text("Ajouter une opération")
                expect(modal.locator("[data-ef-test]")).to_have_text("Tester les modifications")
                expect(modal.locator("[data-ef-advanced] > summary")).to_have_text("Réglages avancés")
                result = preview(modal, SAMPLE, "success", "Le message modifié ; rien n’a été enregistré ni envoyé.")
                expect(result.locator('[data-ef-pane="output"] h4')).to_have_text("Sortie (out)")
                segment(modal, "each_item").click()
                setting(modal, "array_path").fill("rows")
                preview(modal, ROWS, "success", "2 éléments modifiés ; rien n’a été enregistré ni envoyé.",
                        "Opération 4 (remove secret) omise sur 2 éléments")
                field(row(modal, 3), "value_text").fill("12,5")
                expect(row(modal, 3).locator("[data-ef-row-error]")).to_have_text(
                    "value doit être un nombre JSON comme 42, -0.5, 12.50 ou 1e3.")
                setting(modal, "max_items").fill("20000")
                expect(modal.locator('[data-ef-field-error="max_items"]')).to_have_text(
                    "max_items doit être un nombre entier de 1 à 10000.")
                preview(modal, ROWS, "invalid", "Corrigez les réglages signalés pour les tester.")
                for width, height in ((1440, 900), (390, 740)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(150)
                    geometry(modal)
                    page.screenshot(path=artifact_path(f"edit-fields-{origin}-fr-{width}.png"))
                page.set_viewport_size({"width": 1440, "height": 900})
                modal.locator(".owned-footer [data-close-block-modal]").click()
                card.click(position={"x": 20, "y": 20})
                expect(inspector).to_be_visible()
                renders.settle(page)
                expect(inspector.locator("[data-ef-add]")).to_have_text("Ajouter une opération")
                page.screenshot(path=artifact_path(f"edit-fields-{origin}-fr-inspector.png"))
                assert not errors, errors
                assert page.evaluate("!window.CWBlockUiBlocks?.edit_fields")
            finally:
                browser.close()
        print(f"[ok] edit_fields {origin} UI", flush=True)


if __name__ == "__main__":
    main()
