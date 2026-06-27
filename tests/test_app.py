"""Guards for app-level keybindings.

Windows Terminal (and most terminals) reserve certain Ctrl combinations for
themselves — Ctrl+V (paste), Ctrl+Shift+V (paste), Ctrl+, (settings) — so a
keystroke bound to one of those NEVER reaches the Textual app. Binding an
action to a reserved key silently does nothing. This test fails if any app
binding lands on a known-reserved key.
"""

from llamatui.app import LlamaTUI

# Keys the terminal emulator typically intercepts before the app can see them.
TERMINAL_RESERVED = {
    "ctrl+v",        # paste
    "ctrl+shift+v",  # paste
    "ctrl+c",        # copy / interrupt
    "ctrl+shift+c",  # copy
    "ctrl+comma",    # Windows Terminal settings
    "ctrl+shift+p",  # command palette
    "ctrl+shift+f",  # find
    "ctrl+shift+t",  # new tab
    "ctrl+shift+w",  # close
}


def _binding_keys(bindings):
    for b in bindings:
        key = getattr(b, "key", None)
        if key is None and isinstance(b, (tuple, list)):
            key = b[0]
        if key:
            # A single binding entry may list comma-separated keys.
            for part in str(key).split(","):
                yield part.strip()


def test_no_binding_uses_a_terminal_reserved_key():
    offenders = sorted(
        k for k in _binding_keys(LlamaTUI.BINDINGS) if k in TERMINAL_RESERVED
    )
    assert not offenders, (
        f"app bindings use terminal-reserved keys (the terminal eats these, so "
        f"the action never fires): {offenders}"
    )
