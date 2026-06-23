"""The instructions builder exists to make one invariant structural: volatile content is last.

If these hold, the cache-prefix discipline can't be broken by reordering call sites.
"""

from llamatui.instructions import build_instructions


def test_volatile_is_always_last_regardless_of_inputs():
    out = build_instructions(
        persona="PERSONA",
        capabilities=["CAP1", "CAP2"],
        ambient="AMBIENT",
        volatile="DATE",
    )
    assert out.endswith("DATE")
    # stable parts precede the volatile slot, in order
    assert out.index("PERSONA") < out.index("CAP1") < out.index("AMBIENT") < out.index("DATE")


def test_blanks_are_dropped():
    out = build_instructions(
        persona="P",
        capabilities=[None, "", "CAP"],
        ambient=None,
        volatile="V",
    )
    assert "\n\n\n" not in out
    assert out == "P\n\nCAP\n\nV"


def test_no_volatile_means_capabilities_or_persona_last():
    out = build_instructions(persona="P", capabilities=["CAP"])
    assert out.endswith("CAP")


def test_persona_only():
    assert build_instructions(persona="just me") == "just me"
