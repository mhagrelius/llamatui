"""Workspace — the per-conversation file/system deep module.

Owns the rooted scope: path resolution, the in/out **classification** that keeps typed
reads confined, the file operations, and (later) a cancellable command runner. Mirrors the
codebase's engine/surface split (cf. KnowledgeGraph/Memory): the security-critical
classification + exec logic is tested directly here, with no agent and no Textual; the thin
tool surface (build_tools/FILESYSTEM_GUIDANCE) only phrases it for the model.
"""

from __future__ import annotations

from pathlib import Path


def OUTSIDE_MSG(root: Path) -> str:
    return (
        f"Path is outside your workspace ({root}). Use run_command (which asks for "
        "approval) to reach it, or ask the user to widen the workspace."
    )


class Workspace:
    def __init__(self, root, *, runner=None, trash=None, shell: str | None = None) -> None:
        self.root = Path(root).resolve()
        self._runner = runner
        self._trash = trash
        self._shell = shell

    # ---- classification / path safety -----------------------------------
    def _confined(self, path: str) -> Path | None:
        """Resolve ``path`` against the root; return it only if it stays inside (symlinks
        resolved), else None. The single predicate the read tools and write share."""
        candidate = (self.root / path) if not Path(path).is_absolute() else Path(path)
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            return None
        if resolved == self.root or self.root in resolved.parents:
            return resolved
        return None

    # ---- mutation tool --------------------------------------------------
    def write_file(self, path: str, content: str) -> str:
        target = self._confined(path)
        if target is None:
            return OUTSIDE_MSG(self.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        rel = target.relative_to(self.root)
        return f"Wrote {rel} ({len(content)} chars)."
