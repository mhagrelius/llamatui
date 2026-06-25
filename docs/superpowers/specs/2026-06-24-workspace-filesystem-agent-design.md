# Workspace Filesystem Agent — Design (Slice 1)

**Date:** 2026-06-24
**Status:** Approved design, ready for implementation plan
**Scope:** The first slice of "act on my machine" — a workspace-scoped file/system
capability with an approval gate. Skills ("the brain") are a separate, later slice.

## Context

llamatui is a local-first assistant TUI (Microsoft Agent Framework + Textual) over a
local llama-server. It currently has two "reach beyond the chat" capabilities, each wired
through `AgentBuilder._capabilities()` as `(tools, guidance note, ambient block)`:

- **Web search** — Exa hosted MCP tool (`tools.py`), `approval_mode="never_require"`.
- **Memory** — a local knowledge graph exposed as `FunctionTool`s plus an ambient
  preamble (`memory.py`).

The user wants to add the ability for the assistant to **act on the machine**, starting
with **file/system tasks**, and is explicitly concerned with **security** and with
eventually teaching the model to "use CLI tools appropriately" via **skills**.

## Decomposition

What was requested is three interlocking subsystems:

1. **The hands** — the file/system toolset (typed safe tools + a gated shell-exec escape hatch).
2. **The guardrails** — workspace scoping + the approval gate + its TUI.
3. **The brain** — "thinking skills": on-demand procedural knowledge.

**This spec covers Slice 1 = hands + guardrails**, which are inseparable (you can't ship
`run_command` without the gate). Skills are **Slice 2** (their own spec): the framework
ships a `SkillsProvider` implementing the agentskills.io progressive-disclosure pattern,
and the codebase already anticipates moving the per-feature `*_GUIDANCE` notes into it.
Until then, the filesystem feature carries a guidance note like web/memory do.

## Decisions (from brainstorming)

- **Tool shape: Hybrid.** Typed safe tools (read/list/search) auto-run; a gated shell-exec
  escape hatch (`run_command`) for everything else, always requiring approval.
- **Safety boundary: workspace-default, escapable.** A workspace root; reads/ops inside it
  auto-run, ops outside it (and all mutations anywhere) require approval. The workspace is
  containment-by-default with a friction-gated escape, not a hard jail.
- **Build vs. adopt: build our own `filesystem` feature**, mirroring `memory.py`, rather
  than adopting the framework's `FileAccessProvider` (see below).

## A. Build vs. adopt `FileAccessProvider`

The framework's `agent_framework._harness._file_access.FileAccessProvider` is solid (six
tools; `FileSystemAgentFileStore` with traversal + symlink protection) but does not fit our
decisions:

- It is a **shared-store** model with its own root and `file_access_*` naming — not
  "workspace-default-but-escapable."
- It integrates via a **`ContextProvider` / `before_run`** path, not the `tools=` list that
  `AgentBuilder` and `memory` use today.
- It has **no read-vs-mutate approval split** and **no shell exec** — the two things our
  security model hinges on.
- It is marked `@experimental`.

**Decision:** build a small `filesystem` feature mirroring `memory.py`, **borrowing
`FileSystemAgentFileStore`'s path-safety logic** (traversal + symlink rejection) and using
the framework's **native `approval_mode`** for the gate. Exact-fit semantics, the existing
`build_tools()` seam, consistent UX, and the same no-server/no-UI testability as `memory`.
(Slice 2 makes the opposite call — *adopt* `SkillsProvider`.)

## B. Toolset & approval classification

New module `llamatui/filesystem.py` with a `Workspace` class exposing `build_tools()`
(mirroring `Memory.build_tools()`), plus a module-level `FILESYSTEM_GUIDANCE` note owned by
the module that owns the tools.

| Tool | Approval | Notes |
|---|---|---|
| `list_dir` | auto inside workspace | level/tree listing |
| `read_file` | auto inside workspace | size-capped |
| `search` | auto inside workspace | glob + content grep |
| `write_file` | **always_require** | create/overwrite |
| `move` | **always_require** | rename/move |
| `delete` | **always_require** | → OS recycle bin (`send2trash`), not hard delete |
| `run_command` | **always_require** | shell exec; `cwd` = workspace; timeout + output cap |

- Reads/list/search **outside** the workspace escalate to `always_require` (the "escapable"
  part). Inside-workspace reads never prompt.
- Gated tools are tagged with the framework's `@tool(approval_mode="always_require")` /
  `FunctionTool(..., approval_mode="always_require")` — the same lever the Exa MCP tool uses
  with `"never_require"`.

## C. Workspace resolution & path safety

- Workspace root defaults to the launch cwd; overridable via `--workspace PATH` and the
  settings panel; persisted in `settings.json`.
- Every path argument is resolved and normalized; a `_within_workspace()` predicate decides
  auto vs. approval. It does **not block** outside paths — it **escalates** them to approval.
- Symlink-escape is rejected outright (borrowing the framework store's logic).
- `delete` routes to the OS recycle bin (`send2trash`) so a mistaken deletion is recoverable.

## D. The approval gate (the one new mechanic)

The turn is driven by `async for update in self.agent.run(..., stream=True)` in
`app.generate()`.

- When the model calls a gated tool, the stream surfaces a `FunctionApprovalRequest` instead
  of executing it.
- `generate()` detects the request, pauses the worker, and shows a **Textual modal**
  rendering the tool and its concrete arguments — the command line for `run_command`, or the
  target path plus a preview/diff for `write_file`.
- The user chooses **Approve / Approve all (this turn) / Deny**. The decision is appended as
  the approval response and the run continues from where it paused.
- `TurnState` / `turn_view` gain a small **"awaiting approval"** phase so the status bar
  reflects it. Cancelling the turn (`Esc`) counts as Deny.

## E. Settings / CLI surface

- CLI: `--workspace PATH`, `--no-fs` (disable the whole feature) — mirroring `--no-web` /
  `--no-memory`.
- Settings panel: workspace root + an approval-policy toggle (*always ask* vs *auto-run reads
  only*; default *auto-run reads only*). Persisted to `settings.json`.
- `AgentBuilder._capabilities()` gains a `filesystem` branch appending the tools +
  `FILESYSTEM_GUIDANCE`, exactly like the `web` / `memory` branches.

## F. Testing

Unit-tested with no server and no UI, the same way `memory` is:

- Path classification: inside / outside / symlink → auto vs. approval.
- Each tool against a temp dir: list, read (size cap), search (glob + grep), write, move.
- `delete` routes to trash (mock `send2trash`).
- `run_command`: timeout and output-capping behavior; `cwd` is the workspace.
- Guidance/wording (the `FILESYSTEM_GUIDANCE` note).

The approval **modal** is thin glue; the **classification** logic is pure data and carries
the coverage.

## Risks to confirm during planning

1. The exact pause/resume shape of `agent.run(stream=True)` under an approval request — how
   the `FunctionApprovalRequest` arrives in the stream and how the response is fed back to
   continue the run.
2. Whether the local model reliably emits a structured `run_command` call vs. leaking it as
   text. `turn.py` already strips leaked tool markup, so there is precedent and a fallback.

## Out of scope (Slice 1)

- Skills / on-demand procedural knowledge (Slice 2 — adopt `SkillsProvider`).
- Generalizing the `*_GUIDANCE` notes into the skills system.
- Non-filesystem "act on my machine" domains (process/service management, scheduled ops).
