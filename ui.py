"""Package-owned editor surfaces, sharing only the framework's public rendering helpers."""

from functools import lru_cache
from html import escape
from pathlib import Path
import json
import re
import secrets

from bloxsmith_app.block_api import render_inspector_template
from . import logic

# Fields shown for each operation; the editor hides the others.
OP_FIELDS = {
    "set": ("path", "value_type", "value"),
    "default": ("path", "value_type", "value"),
    "append": ("path", "value_type", "value"),
    "copy": ("from", "to"),
    "rename": ("from", "to"),
    "remove": ("path",),
}
HELP = {
    "path": "path: names separated by dots, [index] for array items and [\"name\"] for names with dots, brackets "
            "or surrounding spaces, e.g. customer.email, items[0].price or headers[\"content-type\"]. Empty means "
            "the whole message, or the whole item with each_item. Missing names are created as objects.",
    "from_to": "from and to (copy, rename): source and target paths, with the same syntax. In keep_only, copy reads "
               "the received message; rename and remove change the result being built.",
    "value_type": "value_type: string, number (kept exactly as written), boolean, null, json (any JSON value) or "
                  "template (text with references).",
    "value": "In a template, {reference} inserts the value at that path of the received message, never an edited "
             "one; a template made of one reference keeps its JSON type. Write {escape} for literal braces.",
    "sample": "Edited like a received text message with the settings being edited, including unapplied changes. "
              "Nothing is saved, sent or run.",
}
HELP_PARAMS = {"value": {"reference": "{{ path }}", "escape": "\\{{"}}
PLACEHOLDERS = {"string": "text", "number": "12.50", "json": "{\"a\":1}", "template": "Hello {{ name }}"}


@lru_cache(maxsize=4)
def _field_specs(path):
    return tuple(json.loads(Path(path).read_text(encoding="utf-8")))


def field_specs(block):
    """Trusted setting definitions (key, control type, options, bounds, English help)."""
    return _field_specs(str(block.directory / "fields.json"))


def replace_config(result, node):
    """Replace edited settings exactly; generic graph patches otherwise deep-merge objects."""
    patch = result.get("node_patch") if isinstance(result, dict) else None
    if not isinstance(patch, dict) or not isinstance(patch.get("config"), dict):
        return result
    settings = {**(node.get("config") or {}), **patch.pop("config")}
    result.setdefault("graph_operations", []).append({"op": "update_node_config",
        "node_id": str(node.get("id") or ""), "config": settings, "replace": True})
    return result


def _text(block, key, fallback, tag="span", attrs="", params=None):
    """Catalog-owned text with its marker (and parameters); never used for user content."""
    name = f"block.{block.kind}.{key}"
    extra = ""
    if params:
        extra = f' data-i18n-params="{escape(json.dumps(params, ensure_ascii=False), quote=True)}"'
    text = escape(block.translate(name, params, fallback=fallback))
    return f'<{tag}{attrs} data-i18n="{name}"{extra}>{text}</{tag}>'


def _attr(block, name, key, fallback):
    """Translated attribute plus its marker, e.g. aria-label."""
    full = f"block.{block.kind}.{key}"
    return f' {name}="{escape(block.translate(full, fallback=fallback), quote=True)}" data-i18n-{name}="{full}"'


def _options(values, selected, extra=None):
    html = []
    for value in values:
        data = f' {extra[value]}' if extra and value in extra else ""
        chosen = " selected" if value == selected else ""
        html.append(f'<option value="{escape(value, quote=True)}"{data}{chosen}>{escape(value)}</option>')
    return "".join(html)


def _hint(block, key, element_id, fallback, params=None):
    """Translated help paragraph with a stable id for aria-describedby."""
    return _text(block, f"help_{key}", fallback, "p", f' id="{element_id}" class="field-hint"', params)


def _shown(row, field):
    """Which conditional fields one row shows for its operation and value type."""
    fields = OP_FIELDS[row["op"]]
    if field == "value":
        return "value" in fields and row["value_type"] not in {"null", "boolean"}
    if field == "value_bool":
        return "value" in fields and row["value_type"] == "boolean"
    return field in fields


def row_html(block, prefix, row, number):
    """One operation row; field labels are technical keys, help and actions are translated."""
    op_data = {op: f'data-fields="{" ".join(OP_FIELDS[op])}"' for op in logic.OPS}
    move_up = _attr(block, "aria-label", "move_up", "Move this operation up") + _attr(block, "title", "move_up", "Move this operation up")
    move_down = _attr(block, "aria-label", "move_down", "Move this operation down") + _attr(block, "title", "move_down", "Move this operation down")

    def hidden(field):
        return "" if _shown(row, field) else " hidden"

    def text_input(field, value, placeholder, help_id):
        return (f'<input type="text" data-ef-field="{field}" value="{escape(value, quote=True)}" spellcheck="false" '
                f'autocomplete="off" placeholder="{escape(placeholder, quote=True)}" aria-describedby="{help_id}">')

    placeholder = PLACEHOLDERS.get(row["value_type"], "")
    return (
        '<li class="ef-row" data-ef-row>'
        '<div class="ef-row-head">'
        f'<span class="ef-row-title">{_text(block, "operation_label", "Operation")} '
        f'<span data-ef-number>{number}</span></span>'
        '<span class="ef-row-actions">'
        f'<button type="button" class="ghost-btn ef-icon" data-ef-move="up" data-block-mutation-control{move_up}>'
        '<span aria-hidden="true">↑</span></button>'
        f'<button type="button" class="ghost-btn ef-icon" data-ef-move="down" data-block-mutation-control{move_down}>'
        '<span aria-hidden="true">↓</span></button>'
        '<button type="button" class="ghost-btn ef-remove" data-ef-remove data-block-mutation-control>'
        f'{_text(block, "remove", "Remove")}</button>'
        '</span></div>'
        '<div class="ef-fields">'
        '<label class="ef-field ef-field-op"><span class="ef-key">op</span>'
        f'<select data-ef-field="op">{_options(logic.OPS, row["op"], op_data)}</select></label>'
        f'<label class="ef-field ef-field-path" data-ef-show="path"{hidden("path")}><span class="ef-key">path</span>'
        f'{text_input("path", row["path"], "customer.email", f"{prefix}-help-path")}</label>'
        f'<label class="ef-field ef-field-path" data-ef-show="from"{hidden("from")}><span class="ef-key">from</span>'
        f'{text_input("from", row["from"], "name", f"{prefix}-help-from_to")}</label>'
        f'<label class="ef-field ef-field-path" data-ef-show="to"{hidden("to")}><span class="ef-key">to</span>'
        f'{text_input("to", row["to"], "customer.name", f"{prefix}-help-from_to")}</label>'
        f'<label class="ef-field ef-field-type" data-ef-show="value_type"{hidden("value_type")}>'
        '<span class="ef-key">value_type</span>'
        f'<select data-ef-field="value_type" aria-describedby="{prefix}-help-value_type">'
        f'{_options(logic.VALUE_TYPES, row["value_type"])}</select></label>'
        f'<label class="ef-field ef-field-value" data-ef-show="value"{hidden("value")}><span class="ef-key">value</span>'
        f'{text_input("value_text", row["value_text"], placeholder, f"{prefix}-help-value")}</label>'
        f'<label class="ef-field ef-field-value" data-ef-show="value_bool"{hidden("value_bool")}>'
        '<span class="ef-key">value</span>'
        f'<select data-ef-field="value_bool">{_options(("true", "false"), row["value_bool"])}</select></label>'
        '</div>'
        '<p class="field-hint ef-row-error" data-ef-row-error hidden></p>'
        '</li>'
    )


def setting_html(block, prefix, spec, value, apply_to):
    """One scalar setting from fields.json: segment, select, text or integer, with its help and error line."""
    key, kind = spec["key"], spec["type"]
    ident = f"{prefix}-{key}"
    help_id = f"{ident}-help"
    hint = _hint(block, key, help_id, spec["help"])
    error = f'<p class="field-hint ef-field-error" data-ef-field-error="{key}" id="{ident}-error" hidden></p>'
    hidden = " hidden" if spec.get("array_only") and apply_to != "each_item" else ""
    wrapper = f' data-ef-array-only{hidden}' if spec.get("array_only") else ""
    if kind == "segment":
        chosen = value if value in spec["options"] else spec["options"][0]
        radios = "".join(
            f'<label class="ef-segment-option"><input type="radio" name="{ident}" value="{escape(option, quote=True)}" '
            f'data-ef-setting="{key}"{" checked" if option == chosen else ""} aria-describedby="{help_id}">'
            f'<span>{escape(option)}</span></label>' for option in spec["options"])
        return (f'<fieldset class="ef-segment-field"{wrapper}><legend class="ef-key">{key}</legend>'
                f'<div class="ef-segment">{radios}</div>{hint}{error}</fieldset>')
    if kind == "select":
        chosen = value if value in spec["options"] else spec["options"][0]
        control = (f'<select id="{ident}" data-ef-setting="{key}" aria-describedby="{help_id}">'
                   f'{_options(spec["options"], chosen)}</select>')
    elif kind == "integer":
        text = str(value) if isinstance(value, int) and not isinstance(value, bool) else ""
        control = (f'<input id="{ident}" type="number" inputmode="numeric" min="{spec["min"]}" max="{spec["max"]}" '
                   f'step="1" data-ef-setting="{key}" value="{escape(text, quote=True)}" aria-describedby="{help_id}">')
    else:
        text = value if isinstance(value, str) else ""
        control = (f'<input id="{ident}" type="text" data-ef-setting="{key}" value="{escape(text, quote=True)}" '
                   f'spellcheck="false" autocomplete="off" placeholder="{escape(spec.get("placeholder", ""), quote=True)}" '
                   f'aria-describedby="{help_id}">')
    return f'<div class="field-group ef-setting"{wrapper}><label for="{ident}" class="ef-key">{key}</label>{control}{hint}{error}</div>'


def editor_html(block, node, surface):
    """The complete operation editor shared by the modal and the inspector."""
    config = logic.settings(node.get("config") or {})
    apply_to = config.get("apply_to") if config.get("apply_to") in logic.APPLY_TO else "message"
    prefix = escape(re.sub(r"[^A-Za-z0-9_-]", "-", f"{block.kind}-{node.get('id', '')}-{surface}"), quote=True)
    sections = {"mode": [], "operations": [], "advanced": []}
    for spec in field_specs(block):
        sections[spec["section"]].append(setting_html(block, prefix, spec, config.get(spec["key"]), apply_to))
    rows = logic.editor_rows(node.get("config") or {})
    empty = {"op": "set", "path": "", "from": "", "to": "", "value_type": "string", "value_text": "", "value_bool": "true"}
    row_items = "".join(row_html(block, prefix, row, number) for number, row in enumerate(rows, start=1))
    sample_attrs = (f'id="{prefix}-sample" data-ef-sample rows="5" spellcheck="false" '
                    f'placeholder="{escape(json.dumps({"name": "Ada", "price": 12.5}), quote=True)}" '
                    f'aria-describedby="{prefix}-help-sample"')
    order = _text(block, "order_hint", "Operations run in order, top to bottom, each on the result of the previous "
                  "ones.", "p", ' class="field-hint ef-order"')
    title = _text(block, "preview_title", "Test with sample JSON", "h3", f' id="{prefix}-preview"')
    return (
        f'<div class="ef-editor" data-ef-editor data-surface="{escape(surface, quote=True)}" '
        f'data-apply-to="{apply_to}" data-max="{logic.MAX_OPERATIONS}">'
        f'<section class="ef-mode" aria-label="mode">{"".join(sections["mode"])}</section>'
        f'<section class="ef-section" aria-labelledby="{prefix}-operations">'
        f'<div class="ef-section-head"><h3 id="{prefix}-operations" class="ef-key">operations</h3>'
        f'<span class="field-hint ef-count" data-ef-count>{len(rows)} / {logic.MAX_OPERATIONS}</span></div>'
        f'{order}'
        f'<ol class="ef-rows" data-ef-rows>{row_items}</ol>'
        f'<template data-ef-row-template>{row_html(block, prefix, empty, 1)}</template>'
        '<div class="ef-add-line"><button type="button" class="ghost-btn ef-add" data-ef-add '
        f'data-block-mutation-control>{_text(block, "add", "Add an operation")}</button></div>'
        '<p class="field-hint ef-group-error" data-ef-group-error role="status" aria-live="polite" hidden></p>'
        f'{"".join(sections["operations"])}'
        f'<div class="ef-help">{_hint(block, "path", f"{prefix}-help-path", HELP["path"])}'
        f'{_hint(block, "from_to", f"{prefix}-help-from_to", HELP["from_to"])}'
        f'{_hint(block, "value_type", f"{prefix}-help-value_type", HELP["value_type"])}'
        f'{_hint(block, "value", f"{prefix}-help-value", HELP["value"], HELP_PARAMS["value"])}</div>'
        '</section>'
        '<details class="owned-advanced" data-ef-advanced>'
        f'{_text(block, "advanced", "Advanced settings", "summary")}'
        f'<div class="owned-fields">{"".join(sections["advanced"])}</div></details>'
        f'<section class="ef-preview" aria-labelledby="{prefix}-preview">'
        f'{title}'
        f'<div class="field-group"><label for="{prefix}-sample">{_text(block, "sample", "Sample message")}</label>'
        f'<textarea {sample_attrs}></textarea>{_hint(block, "sample", f"{prefix}-help-sample", HELP["sample"])}</div>'
        '<div class="ef-preview-actions"><button type="button" class="ghost-btn" data-ef-test>'
        f'{_text(block, "test", "Test the edits")}</button></div>'
        '<div class="ef-preview-result" data-ef-preview-result role="status" aria-live="polite"></div>'
        '</section></div>'
    )


def _with_editor(template, render, editor):
    """Insert the editor after generic placeholder rendering, through an unguessable marker."""
    marker = f"<!--edit-fields-editor-{secrets.token_hex(8)}-->"
    return render(template.replace("{{ editor }}", marker)).replace(marker, editor)


def modal(block, node, payload):
    """Use the generic draft host; refreshing runtime must not replace pending settings."""
    template = (block.directory / "block_modal.html").read_text(encoding="utf-8")
    html = _with_editor(template, lambda text: block._render_generic_modal_template(
        template=text, node=node, payload=payload or {}), editor_html(block, node, "modal"))
    return {"html": html, "context": {"node_id": str(node.get("id", "")), "node_kind": block.kind}}


def inspector(block, node, payload):
    """Render the same editor with the standard Ports tab and sticky Apply control."""
    template = (block.directory / "inspector_panel.html").read_text(encoding="utf-8")
    html = _with_editor(template, lambda text: render_inspector_template(
        template=text, node={**node, "kind": block.kind, "type": block.kind}, payload=payload),
        editor_html(block, node, "inspector"))
    return {"html": html, "context": {"node_id": str(node.get("id", "")), "full_panel": True}}


def card(block, node):
    """Mode, first operation in short form plus "and N more"; single-pass so content is never re-substituted."""
    summary = logic.card_summary(node.get("config") or {})
    if summary is None:
        detail = _text(block, "card_invalid", "Invalid settings: open the block to fix them.", "span", ' class="ef-card-invalid"')
    else:
        scope = []
        if summary["mode"] == "keep_only":
            scope.append(_text(block, "card_keep_only", "Keep only", "span", ' class="ef-card-mode"'))
        if summary["apply_to"] == "each_item":
            if summary["array_path"]:
                scope.append(_text(block, "card_items_at", "Each item of", "span", ' class="ef-card-mode"')
                             + f' <span class="ef-card-path">{escape(summary["array_path"])}</span>')
            else:
                scope.append(_text(block, "card_items", "Each item", "span", ' class="ef-card-mode"'))
        detail = (" · ".join(scope) + ": ") if scope else ""
        detail += f'<span class="ef-card-first">{escape(summary["first"])}</span>'
        if summary["more"]:
            params = {"count": summary["more"]}
            name = f"block.{block.kind}.card_more"
            text = block.translate(name, params, fallback="and {count} more")
            detail += (f' <span class="ef-card-more" data-i18n="{name}" '
                       f'data-i18n-params="{escape(json.dumps(params), quote=True)}">{escape(text)}</span>')
    values = {"title": escape(str(node.get("title") or block.default_title())),
              "purpose": escape(block.description(), quote=True), "detail": detail}
    template = (block.directory / "node_card.html").read_text(encoding="utf-8")
    html = re.sub(r"__(title|purpose|detail)__", lambda match: values[match.group(1)], template)
    return {"html": html, "context": {"node_id": str(node.get("id") or ""), "node_classes": [block.kind + "-node"]}}
