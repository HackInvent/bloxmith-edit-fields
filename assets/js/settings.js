/** Edit Fields editor: drafts stay local until Apply; checks call the block's own read-only routes. */
const KEY = "block.edit_fields.";
const PLACEHOLDERS = { string: "text", number: "12.50", json: '{"a":1}', template: "Hello {{ name }}" };

export function bindEditor(root, api) {
  const editor = root.querySelector("[data-ef-editor]");
  const apply = root.querySelector("[data-owned-apply]");
  if (!editor || !apply) return () => {};
  const controller = new AbortController();
  const { signal } = controller;
  const list = editor.querySelector("[data-ef-rows]");
  const template = editor.querySelector("template[data-ef-row-template]");
  const add = editor.querySelector("[data-ef-add]");
  const count = editor.querySelector("[data-ef-count]");
  const groupError = editor.querySelector("[data-ef-group-error]");
  const status = root.querySelector("[data-ef-status]");
  const sample = editor.querySelector("[data-ef-sample]");
  const test = editor.querySelector("[data-ef-test]");
  const result = editor.querySelector("[data-ef-preview-result]");
  const max = Number(editor.dataset.max) || 64;
  // IDs are DOM references for aria-describedby, not security tokens.
  const base = `edit-fields-${Math.random().toString(36).slice(2)}`;
  let serial = 0, dirty = false, pending = false, generation = 0, timer = 0, checkSeq = 0, testSeq = 0;
  const readOnly = () => Boolean(api.isReadOnly?.());
  const release = () => root.closest?.("[data-block-release]")?.dataset?.blockRelease || "";

  /** Translated text that follows a language change; fallback is the English text. */
  const say = (element, key, fallback, params = {}) => {
    if (!element) return;
    if (globalThis.CWI18n?.set) globalThis.CWI18n.set(element, KEY + key, params, fallback);
    else element.textContent = api.t?.(KEY + key, params, fallback) || fallback;
  };
  /** One-shot translation, used inside a larger sentence. */
  const tr = (key, fallback, params = {}) => {
    if (globalThis.CWI18n?.t) return globalThis.CWI18n.t(KEY + key, params, fallback, release());
    return String(fallback).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (name in params ? String(params[name]) : match));
  };
  /** Raw content (labels built from settings, JSON) never carries a translation marker. */
  const raw = (element, text) => {
    if (globalThis.CWI18n?.setText) globalThis.CWI18n.setText(element, text);
    else element.textContent = text;
  };
  const rows = () => [...list.querySelectorAll(":scope > [data-ef-row]")];
  const field = (row, name) => row.querySelector(`[data-ef-field="${name}"]`);
  const setting = name => editor.querySelector(`[data-ef-setting="${name}"]:checked`)
    ?? editor.querySelector(`[data-ef-setting="${name}"]:not([type="radio"])`);
  const applyTo = () => setting("apply_to")?.value || "message";

  /** Show only the fields the operation reads; the value control follows value_type. */
  function syncRow(row) {
    const option = field(row, "op").selectedOptions[0];
    const fields = new Set((option?.dataset.fields || "").split(" ").filter(Boolean));
    const type = field(row, "value_type").value;
    const visible = {
      path: fields.has("path"), from: fields.has("from"), to: fields.has("to"),
      value_type: fields.has("value_type"),
      value: fields.has("value") && type !== "null" && type !== "boolean",
      value_bool: fields.has("value") && type === "boolean",
    };
    for (const wrapper of row.querySelectorAll("[data-ef-show]")) wrapper.hidden = !visible[wrapper.dataset.efShow];
    field(row, "value_text").placeholder = PLACEHOLDERS[type] || "";
  }

  /** array_path and max_items are only read with apply_to each_item. */
  function syncMode() {
    const current = applyTo();
    editor.dataset.applyTo = current;
    for (const element of editor.querySelectorAll("[data-ef-array-only]")) element.hidden = current !== "each_item";
  }

  function prepare(row) {
    const error = row.querySelector("[data-ef-row-error]");
    if (!error.id) error.id = `${base}-${++serial}-error`;
    syncRow(row);
  }

  /** Positions, counts and the availability of every row action. */
  function renumber() {
    const items = rows();
    items.forEach((row, index) => {
      raw(row.querySelector("[data-ef-number]"), String(index + 1));
      row.querySelector('[data-ef-move="up"]').disabled = readOnly() || index === 0;
      row.querySelector('[data-ef-move="down"]').disabled = readOnly() || index === items.length - 1;
      row.querySelector("[data-ef-remove]").disabled = readOnly() || items.length === 1;
    });
    add.disabled = readOnly() || items.length >= max;
    raw(count, `${items.length} / ${max}`);
  }

  function sync() {
    apply.disabled = !dirty || pending || readOnly();
  }

  /** The draft sent to the server; rows are converted by the block, exactly like Apply. */
  function draft() {
    const limit = (setting("max_items")?.value || "").trim();
    return {
      mode: setting("mode")?.value,
      apply_to: applyTo(),
      array_path: setting("array_path")?.value ?? "",
      max_items: /^\d{1,9}$/.test(limit) ? Number(limit) : limit,
      on_missing: setting("on_missing")?.value,
      parse_json: setting("parse_json")?.value,
      on_error: setting("on_error")?.value,
      operations: rows().map(row => ({
        op: field(row, "op").value,
        path: field(row, "path").value,
        from: field(row, "from").value,
        to: field(row, "to").value,
        value_type: field(row, "value_type").value,
        value_text: field(row, "value_text").value,
        value_bool: field(row, "value_bool").value,
      })),
    };
  }

  function unmark(control, target) {
    control.removeAttribute("aria-invalid");
    const rest = (control.getAttribute("aria-describedby") || "").split(" ").filter(id => id && id !== target.id).join(" ");
    if (rest) control.setAttribute("aria-describedby", rest); else control.removeAttribute("aria-describedby");
  }

  function mark(control, target) {
    if (!control) return;
    control.setAttribute("aria-invalid", "true");
    const ids = (control.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
    if (!ids.includes(target.id)) control.setAttribute("aria-describedby", [...ids, target.id].join(" "));
  }

  function clearErrors() {
    for (const target of editor.querySelectorAll("[data-ef-row-error], [data-ef-field-error]")) {
      target.hidden = true;
      raw(target, "");
      const scope = target.closest("[data-ef-row], .ef-setting, .ef-segment-field") || editor;
      for (const control of scope.querySelectorAll("[aria-invalid]")) unmark(control, target);
    }
    groupError.hidden = true;
    raw(groupError, "");
  }

  /** The control an error names: value is the text input or the true/false choice. */
  function rowControl(row, name) {
    if (name === "value") return field(row, "value_text").closest("[hidden]") ? field(row, "value_bool") : field(row, "value_text");
    return name ? field(row, name) : null;
  }

  /** One message per row (the first problem found), per setting, and one for the group. */
  function showErrors(errors) {
    clearErrors();
    const items = rows();
    for (const error of errors || []) {
      const row = error.index ? items[error.index - 1] : null;
      let target = groupError, controls = [];
      if (row) {
        target = row.querySelector("[data-ef-row-error]");
        controls = [rowControl(row, error.field)].filter(Boolean);
      } else if (error.field) {
        const own = editor.querySelector(`[data-ef-field-error="${error.field}"]`);
        if (own && !own.parentElement?.closest("[hidden]")) {
          target = own;
          controls = [...editor.querySelectorAll(`[data-ef-setting="${error.field}"]`)];
        }
      }
      if (!target || !target.hidden) continue;
      target.hidden = false;
      say(target, `err_${error.code}`, error.message, error.params || {});
      for (const control of controls) mark(control, target);
      // A problem in a collapsed section (advanced settings) must stay visible.
      const section = target.closest("details");
      if (section && !section.open) section.open = true;
    }
  }

  /** Server-side validation of the current draft, ignoring answers to older drafts. */
  async function check() {
    const sequence = ++checkSeq;
    const response = await api.blockRequest("validate", { payload: { values: { config: draft() } } });
    if (signal.aborted || sequence !== checkSeq) return null;
    showErrors(response?.errors || []);
    return response;
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => { check().catch(() => {}); }, 350);
  }

  function changed() {
    generation += 1;
    checkSeq += 1; // answers about an older draft (row indices may have moved) are ignored
    dirty = true;
    raw(status, "");
    sync();
    schedule();
  }

  function addRow() {
    if (readOnly() || rows().length >= max) return;
    const row = template.content.firstElementChild.cloneNode(true);
    list.append(row);
    prepare(row);
    globalThis.CWI18n?.apply?.(row);
    renumber();
    changed();
    field(row, "path").focus();
  }

  function move(row, direction) {
    const sibling = direction === "up" ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling) return;
    if (direction === "up") sibling.before(row); else sibling.after(row);
    renumber();
    changed();
    const button = row.querySelector(`[data-ef-move="${direction}"]`);
    (button.disabled ? row.querySelector(`[data-ef-move="${direction === "up" ? "down" : "up"}"]`) : button).focus();
  }

  function remove(row) {
    if (rows().length <= 1) return;
    const next = row.nextElementSibling || row.previousElementSibling;
    row.remove();
    renumber();
    changed();
    (next?.querySelector("[data-ef-remove]:not(:disabled)") || add).focus();
  }

  async function save() {
    if (!dirty || pending || readOnly()) return;
    const started = generation;
    pending = true;
    sync();
    say(status, "checking", "Checking the settings…");
    try {
      let verdict = null;
      try { verdict = await check(); } catch { verdict = null; }
      if (signal.aborted) return;
      if (verdict && !verdict.valid) {
        say(status, "fix_errors", "Not applied: fix the highlighted settings first.");
        return;
      }
      const title = root.querySelector("[data-block-title-field]");
      const response = await api.applyAction("save_settings", { node_patch: { ...(title ? { title: title.value } : {}), config: draft() } });
      if (response?.error) throw new Error(response.error);
      if (signal.aborted) return;
      if (started === generation) dirty = false;
      say(status, "saved", "Settings applied.");
    } catch (error) {
      if (!signal.aborted) raw(status, String(error?.message || error));
    } finally {
      pending = false;
      if (!signal.aborted) sync();
    }
  }

  function note(key, fallback, params = {}) {
    const element = document.createElement("p");
    element.className = "field-hint";
    result.append(element);
    say(element, key, fallback, params);
    return element;
  }

  /** A titled block of JSON text: the content is raw data, the title is translated. */
  function pane(target, key, fallback, text, flag) {
    const box = document.createElement("div");
    box.className = "ef-pane";
    box.dataset.efPane = flag;
    const heading = document.createElement("h4");
    const pre = document.createElement("pre");
    box.append(heading, pre);
    target.append(box);
    say(heading, key, fallback);
    raw(pre, text);
    return box;
  }

  /** English-or-translated sentence naming the failing operation (and item) and the reason. */
  function failureText(failure) {
    const reason = tr(`err_${failure.code}`, failure.message, failure.params || {});
    if (!failure.index) return ["message_failed", "The message could not be edited: {reason}", { reason }];
    const params = { index: failure.index, label: failure.label || "", reason };
    if (failure.item !== null && failure.item !== undefined) {
      return ["op_failed_item", "Operation {index} ({label}) failed on item [{item}]: {reason}", { ...params, item: failure.item }];
    }
    return ["op_failed", "Operation {index} ({label}) failed: {reason}", params];
  }

  function showSkipped(skipped) {
    if (!skipped?.length) return;
    const ul = document.createElement("ul");
    ul.className = "ef-skipped";
    result.append(ul);
    for (const entry of skipped) {
      const item = document.createElement("li");
      ul.append(item);
      const reason = tr(`err_${entry.code}`, entry.message, entry.params || {});
      if (entry.first_item === null || entry.first_item === undefined) {
        say(item, "skipped_line", "Operation {index} ({label}) skipped: {reason}", { index: entry.index, label: entry.label, reason });
      } else {
        say(item, "skipped_items_line", "Operation {index} ({label}) skipped on {count} item(s), first at item [{item}]: {reason}",
          { index: entry.index, label: entry.label, count: entry.items, item: entry.first_item, reason });
      }
    }
  }

  function showResponse(head, response) {
    result.dataset.status = response.status;
    const panes = document.createElement("div");
    panes.className = "ef-panes";
    if (response.status === "success") {
      if (response.apply_to === "each_item") say(head, "preview_items", "{count} item(s) edited; nothing was saved or sent.", { count: response.report?.items ?? 0 });
      else say(head, "preview_success", "The edited message; nothing was saved or sent.");
      result.append(panes);
      pane(panes, "pane_input", "Input", response.input || "", "input");
      pane(panes, "pane_output", "Output (out)", response.output || "", "output");
      if (response.output_truncated || response.input_truncated) note("preview_truncated", "Long texts are shortened here; the run publishes them whole.");
      showSkipped(response.skipped);
      return;
    }
    const [key, fallback, params] = failureText(response.failure || {});
    if (response.status === "routed") {
      say(head, "preview_routed", "Nothing would be published on out; this document would be published on error (on_error route).");
    } else {
      say(head, "preview_failed", "The activation would fail and publish nothing (on_error fail).");
    }
    const reason = note(key, fallback, params);
    reason.classList.add("ef-reason");
    result.append(panes);
    if (response.input !== undefined) pane(panes, "pane_input", "Input", response.input || "", "input");
    if (response.status === "routed") pane(panes, "pane_error", "Error document (error)", JSON.stringify(response.error_document, null, 2), "error");
  }

  async function preview() {
    const sequence = ++testSeq;
    result.replaceChildren();
    delete result.dataset.status;
    const head = document.createElement("p");
    head.className = "ef-preview-head";
    result.append(head);
    say(head, "testing", "Testing…");
    try {
      const response = await api.blockRequest("preview", { payload: { values: { config: draft(), sample: sample.value } } });
      if (signal.aborted || sequence !== testSeq) return;
      if (!response?.valid) {
        result.dataset.status = "invalid";
        showErrors(response?.errors || []);
        say(head, "preview_invalid", "Fix the highlighted settings to test them.");
        return;
      }
      showErrors([]);
      if (response.sample_error) {
        result.dataset.status = "invalid";
        say(head, `err_${response.sample_error.code}`, response.sample_error.message, response.sample_error.params || {});
        return;
      }
      showResponse(head, response);
    } catch (error) {
      if (signal.aborted || sequence !== testSeq) return;
      result.dataset.status = "unavailable";
      say(head, "preview_unavailable", "The test could not run.");
    }
  }

  for (const row of rows()) prepare(row);
  renumber();
  syncMode();
  const watched = "[data-ef-field], [data-ef-setting], [data-block-title-field]";
  root.addEventListener("input", event => {
    if (event.target.matches(watched) && event.target.type !== "radio" && event.target.tagName !== "SELECT") changed();
  }, { signal });
  root.addEventListener("change", event => {
    const target = event.target;
    if (!target.matches(watched)) return;
    if (target.matches('[data-ef-field="op"], [data-ef-field="value_type"]')) syncRow(target.closest("[data-ef-row]"));
    if (target.matches('[data-ef-setting="apply_to"]')) syncMode();
    if (target.tagName === "SELECT" || target.type === "radio" || target.type === "checkbox") changed();
  }, { signal });
  list.addEventListener("click", event => {
    const button = event.target.closest("button");
    const row = button?.closest("[data-ef-row]");
    if (!row || button.disabled || readOnly()) return;
    if (button.matches("[data-ef-move]")) move(row, button.dataset.efMove);
    else if (button.matches("[data-ef-remove]")) remove(row);
  }, { signal });
  add.addEventListener("click", event => { event.preventDefault(); addRow(); }, { signal });
  apply.addEventListener("click", event => { event.preventDefault(); void save(); }, { signal });
  test.addEventListener("click", event => { event.preventDefault(); void preview(); }, { signal });
  root.querySelector("[data-block-title-field]")?.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); void save(); }
  }, { signal });
  if (readOnly()) {
    for (const control of editor.querySelectorAll("[data-ef-field], [data-ef-setting]")) control.disabled = true;
  }
  sync();
  return () => {
    controller.abort();
    clearTimeout(timer);
  };
}
