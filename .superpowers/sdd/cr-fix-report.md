# Code-Review Fix Wave Report — 2026-06-25

Branch: `feat/workspace-filesystem-agent`

## Fix 1 (HIGH, spec §H) — "Approve all this turn" gated on typed requests

**What changed:**
- `llamatui/approval.py`: Added `allow_approve_all: bool = False` param to `ApprovalModal.__init__`; stored as `self._allow_approve_all`. In `compose`, the "Approve all this turn" button is only yielded when `self._allow_approve_all is True`.
- `llamatui/app.py`: In `_resolve_approvals`, computed `has_typed = bool(typed)` and passes `allow_approve_all=has_typed` to `ApprovalModal`. A run_command-only batch gets `allow_approve_all=False` so the button never appears.

**Tests added** (`tests/test_approval.py`):
- `test_approval_modal_allow_approve_all_false_flag` — verifies flag stored correctly
- `test_approval_modal_allow_approve_all_true_flag` — verifies flag stored correctly
- `test_approval_modal_default_allow_approve_all_is_false` — default is False
- `test_resolve_approvals_run_command_only_has_typed_false` — run_command-only → has_typed=False
- `test_resolve_approvals_mixed_batch_has_typed_true` — mixed batch → has_typed=True

---

## Fix 2 (HIGH) — Settings default_workspace change rebuilds agent (not just workspace)

**What changed:**
- `llamatui/app.py` `_on_settings_closed` (~line 591): Changed `self._rebuild_workspace()` to `self._rebuild_agent()` guarded by `and not self._busy`. Mid-turn changes are skipped; idle changes rebuild both workspace AND agent together so tools are re-bound to the new Workspace instance.

**Tests added:** None new (behavior requires a running App); the change is a one-line fix. The intent is documented in the inline comment. Existing `test_app_resolve.py` covers the `resolve_workspace` precedence that underlies this.

---

## Fix 3 (altitude) — ALWAYS_PROMPT_TOOLS constant replaces hard-coded string

**What changed:**
- `llamatui/filesystem.py`: Added `ALWAYS_PROMPT_TOOLS = frozenset({"run_command"})` next to `build_tools`.
- `llamatui/app.py`: Imports `ALWAYS_PROMPT_TOOLS` from `.filesystem`; `_resolve_approvals` uses `name in ALWAYS_PROMPT_TOOLS` instead of `name == "run_command"`.
- `tests/test_filesystem.py`: Both `test_approve_all_excludes_run_command` and `test_approve_all_false_sends_all_to_prompt` updated to use `ALWAYS_PROMPT_TOOLS`.

**Tests added** (`tests/test_approval.py`):
- `test_always_prompt_tools_contains_run_command` — frozenset type + membership check

---

## Fix 4 (correctness, rare race) — Completed process honored over simultaneous cancel

**What changed:**
- `llamatui/filesystem.py` `_default_runner`: Reordered the post-`asyncio.wait` decision:
  - Old: `elif cancel_event.is_set(): status = "cancelled"` (fires even when proc also done)
  - New: `elif proc.returncode is not None: status = "ok"` (honors completion first)
  - Then: `else: status = "cancelled"` (only when proc is still running)

**Deterministic timing test not feasible:** The race window is sub-asyncio-tick; pre-setting cancel before process start tests a different scenario (cancel wins because proc hasn't started). Instead:

**Tests added** (`tests/test_filesystem.py`):
- `test_runner_status_logic_reordering` — pure logic test: stubs old vs new decision function, proves new logic returns "ok" when returncode is set + cancel is set, while old logic returned "cancelled"
- `test_runner_process_finishes_ok_with_cancel_event_idle` — integration: real process exits cleanly with cancel_event present but idle → "ok"

---

## Fix 5 (spec §H) — AWAITING phase routed through TurnState

**What changed:**
- `llamatui/turn.py`: Added `AWAITING = "awaiting approval"` constant next to THINKING/SEARCHING/WRITING/RUNNING.
- `llamatui/app.py`: Imports `AWAITING` from `.turn`. In `generate()`, right before calling `_resolve_approvals`, sets `stream.state.phase = AWAITING` and calls `view.reflect(stream.state, force=True)` so the status bar is updated through the normal `_on_turn_status` path. Removed the now-redundant direct `self._status("awaiting approval")` call from `_resolve_approvals`.

**Tests added** (`tests/test_turn.py`):
- `test_awaiting_phase_constant_exists_and_is_distinct` — AWAITING is a str, != all other phase constants, equals "awaiting approval"
- `test_turn_state_phase_can_be_set_to_awaiting` — TurnState.phase accepts AWAITING

---

## Fix 6 (spec §J) — preview_write diff output capped

**What changed:**
- `llamatui/filesystem.py` `preview_write`: After `"\n".join(diff)`, added cap check: if `len(diff_text) > PREVIEW_CAP`, truncates to `PREVIEW_CAP` chars and appends `"\n[… diff truncated]"`.

**Tests added** (`tests/test_filesystem.py`):
- `test_preview_write_diff_is_capped` — creates old/new content each within PREVIEW_CAP but differing on every line; asserts preview contains truncation marker and total length is bounded

---

## Fix 7 (efficiency) — append_command_output caches tail widget and uses line list

**What changed:**
- `llamatui/widgets.py` `AssistantTurn.__init__`: Replaced `_cmd_output_buf: str` with `_cmd_output_lines: list[str]` and added `_cmd_tail: Static | None = None`.
- `append_command_output`: On first call, creates tail Static and caches it as `self._cmd_tail` (no DOM query on subsequent calls). Accumulates chunks into `_cmd_output_lines` with correct partial-line merging. Only trims the list when it exceeds `_CMD_OUTPUT_TAIL_CAP` (avoids a full split+slice+join on every chunk). Passes `"".join(self._cmd_output_lines)` to `tail.update`.

**Tests updated** (`tests/test_widgets.py`):
- `_run_appends` helper rewritten to mirror the new `_cmd_output_lines` accumulation approach (with a fake tail Static) rather than the old `_cmd_output_buf` split-join loop. The three cap/partial-line tests still pass unchanged.

---

## Fix 8 (reuse) — JSON arg-parsing deduped in approval.py

**What changed:**
- `llamatui/approval.py`: Extracted module-level `_parse_args(call) -> dict` that reads `call.arguments`, parses JSON, and returns `{}` or `{"args": raw}` on failure. Both `_describe` and `_render_call` now call `_parse_args(call)` once at the top instead of repeating the try/except json.loads block. Behavior is identical.

**Tests:** Existing `_describe` tests (`test_approval.py`) and `_render_call` tests pass unchanged — they exercise the public behavior which is identical.

---

## Verify commands and output

```
uv run pytest tests/test_filesystem.py tests/test_approval.py tests/test_widgets.py tests/test_turn.py tests/test_app_resolve.py -v
→ 79 passed, 2 warnings

uv run pytest -q
→ 221 passed, 2 warnings in 5.96s  (was 210 before, +11 new tests)

uv run python -c "import llamatui.app, llamatui.approval, llamatui.filesystem, llamatui.widgets, llamatui.turn"
→ clean (ExperimentalWarning only, from agent_framework internals — not ours)
```

---

## Commits

See git log for SHAs. Three logical groups:
1. `fix(fs): gate approve-all button on typed reqs; ALWAYS_PROMPT_TOOLS constant; rebuild agent on workspace change`
2. `fix(fs): honor process completion over simultaneous cancel; cap preview_write diff; AWAITING phase constant`
3. `fix(fs): cache cmd-output tail widget + use line list; dedup json arg-parsing in approval`
