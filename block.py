"""Edit Fields: set, rename, copy or remove fields of each JSON message without code."""

from bloxsmith_app.block_api import (APPLICATION_JSON, BlockDefinition, BlockRuntimeOutput, BlockRuntimePreparation,
                                     BlockRuntimeResult)
from . import logic, ui
from .paths import EditError


# FB1 - set writes typed values (string, number kept as written, boolean, null, json) and creates missing
#       intermediate objects; an array index must exist or equal the array length.
# FB2 - copy, rename, remove, default and append run in order on the working document; on_missing skip or fail
#       decides what a missing source does.
# FB3 - template values read {{ path }} references from the original input, never from intermediate results; a
#       template that is one reference keeps its JSON type, otherwise values are interpolated as text.
# FB4 - keep_only starts from an empty object: only set/copy targets (and what later operations derive) are output.
# FB5 - each_item edits every element of the message array, or of the array at array_path, in order, and outputs
#       the edited array; max_items bounds it and nothing is truncated.
# FB6 - Settings are validated when applied (UI action, live validation) and at run preparation; invalid
#       settings never execute.
# FB7 - Errors never publish a partial result: on_error fail stops the activation; route publishes a content-free
#       error document on error. Depth (32) and output size (8 MiB) are bounded.
# FB8 - Behave identically in centralized and zeromq_active runtimes and in bundled, managed and linked packages,
#       with translated responsive surfaces: operation rows, live validation, sample preview, draft and Apply.
class EditFieldsBlock(BlockDefinition):
    """Field editing policy lives in the block configuration, never in incoming messages."""

    kind = "edit_fields"
    OUTPUTS = ("out", "error")

    def ui_assets(self, surface="modal"):
        """Use the manifest's asset list for bundled surfaces as well as hosted releases."""
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def prepare_runtime(self, context):
        """Refuse invalid settings before any activation, without side effects."""
        try:
            logic.configuration(context.config)
        except EditError as exc:
            raise ValueError(f"Invalid Edit Fields settings: {exc}") from None
        return BlockRuntimePreparation()

    def execute_runtime(self, context):
        """Edit one message (or each item of its array) and publish the whole result, or nothing."""
        if not context.has_input_value("in", "1"):
            return BlockRuntimeResult(status="skipped", outputs=[], logs=["[edit-fields] No message to edit."])
        raw = context.input_value("in", "1")
        try:
            options = logic.configuration(context.config)
        except EditError as exc:
            return BlockRuntimeResult(status="failed", outputs=[], error=self._settings_error(exc), exit_code=1,
                                      logs=[f"[edit-fields-error] Invalid Edit Fields settings: {exc} "
                                            "Nothing was published."])
        config = options.to_config()
        cancelled = context.services.get("cancel_requested", lambda: False)
        try:
            result = logic.process(raw, options, cancelled=cancelled)
        except EditError as exc:
            return self._failure(context, exc, options, config)
        except (MemoryError, RecursionError, ValueError, TypeError):
            exc = EditError("internal", "the message could not be edited safely.")
            return self._failure(context, exc, options, config)
        if result is None:
            return BlockRuntimeResult(status="skipped", outputs=self._outputs(context, {}),
                                      logs=["[edit-fields] Cancelled before publishing; nothing was published."])
        report = result["report"]
        logs = [f"[edit-fields] {logic.describe_skip(entry, config)}" for entry in result["skipped"]]
        if options.apply_to == "each_item":
            logs.append(f"[edit-fields] Edited {report['items']} item(s): {report['applied']} operation run(s) "
                        f"applied, {report['skipped']} skipped ({options.mode}).")
        else:
            logs.append(f"[edit-fields] Edited the message: {report['applied']} of {report['operations']} "
                        f"operation(s) applied, {report['skipped']} skipped ({options.mode}).")
        return BlockRuntimeResult(status="success", outputs=self._outputs(context, {"out": result["text"]}),
                                  content_type=APPLICATION_JSON, last_message=logic.dumps(report),
                                  worker_received=logic.dumps(report), metadata={self.kind: report}, logs=logs)

    def _failure(self, context, exc, options, config):
        """on_error fail: failed activation; route: content-free error document. Never a partial result."""
        detail = logic.describe(exc, config)
        if options.on_error == "route":
            document = logic.dumps(logic.envelope(exc, config))
            return BlockRuntimeResult(status="success", outputs=self._outputs(context, {"error": document}),
                                      content_type=APPLICATION_JSON, last_message=document,
                                      worker_received=exc.code, metadata={self.kind: {"error_code": exc.code}},
                                      logs=[f"[edit-fields] Routed {exc.code} to the error port: {detail} "
                                            "Nothing was published on out."])
        reason = self.translate(f"block.{self.kind}.err_{exc.code}", exc.params, fallback=exc.message)
        if exc.index:
            params = {"index": exc.index, "label": logic.short_label(config["operations"][exc.index - 1]),
                      "reason": reason}
            if exc.item is not None:
                message = self.translate(f"block.{self.kind}.op_failed_item", {**params, "item": exc.item},
                                         fallback="Operation {index} ({label}) failed on item [{item}]: {reason}")
            else:
                message = self.translate(f"block.{self.kind}.op_failed", params,
                                         fallback="Operation {index} ({label}) failed: {reason}")
        else:
            message = self.translate(f"block.{self.kind}.message_failed", {"reason": reason},
                                     fallback="The message could not be edited: {reason}")
        return BlockRuntimeResult(status="failed", outputs=[], error=message, exit_code=1,
                                  metadata={self.kind: {"error_code": exc.code}},
                                  logs=[f"[edit-fields-error] {detail} Nothing was published."])

    def _outputs(self, context, values):
        """Emit the ports that carry a value; an explicit non-emission clears the other's stale value."""
        outputs = []
        for port in context.output_ports:
            name = str(port.name)
            if name not in self.OUTPUTS:
                continue
            value = values.get(name)
            outputs.append(BlockRuntimeOutput(port_id=int(port.id), port_name=name, value=value or "",
                                              emitted=value is not None, content_type=APPLICATION_JSON))
        return outputs

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Validate and canonicalize editor drafts before persisting them; refuse invalid settings."""
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = dict((values or {}).get("node_patch") or {})
            if "config" in patch:
                draft = logic.editor_settings(patch["config"] if isinstance(patch["config"], dict) else {})
                try:
                    options = logic.configuration({**(node.get("config") or {}), **draft})
                except EditError as exc:
                    return {"error": self._settings_error(exc)}
                patch["config"] = options.to_config()
                values = {**(values or {}), "node_patch": patch}
        result = super().handle_ui_action(node=node, action="modal_update_fields" if action == "save_settings" else action,
                                          values=values, payload=payload)
        return ui.replace_config(result, node)

    def handle_ui_request(self, *, node, route, method, values, payload=None):
        """Read-only draft checks: live validation and the sample preview call the same editing code."""
        draft = (values or {}).get("config")
        draft = draft if isinstance(draft, dict) else {}
        stored = node.get("config") if isinstance(node.get("config"), dict) else {}
        if route == "validate":
            errors = logic.validation(draft, stored)
            return {"valid": not errors, "errors": errors}
        if route == "preview":
            return logic.preview(draft, (values or {}).get("sample", ""), stored)
        return super().handle_ui_request(node=node, route=route, method=method, values=values, payload=payload)

    def _settings_error(self, error):
        """Translated, index-aware message for settings refused on Apply or found invalid at run time."""
        detail = self.translate(f"block.{self.kind}.err_{error.code}", error.params, fallback=error.message)
        if error.index:
            detail = self.translate(f"block.{self.kind}.invalid_operation", {"index": error.index, "reason": detail},
                                    fallback="Operation {index}: {reason}")
        return self.translate(f"block.{self.kind}.invalid_settings", {"detail": detail},
                              fallback="Invalid Edit Fields settings: {detail}")

    def render_modal(self, *, node, payload=None):
        """Render the owned operation editor inside the opaque modal contract."""
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        """Keep the same editor usable in the narrow inspector, with the Ports tab."""
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        """Show the mode, the first operation in short form and how many follow."""
        return ui.card(self, node)
