from llamatui.staging import PasteStaging
from llamatui.clipboard import ClipboardGrab
from llamatui.images import prepare_image
import io
from PIL import Image


def _att():
    buf = io.BytesIO(); Image.new("RGB", (30, 30), (0, 0, 0)).save(buf, format="PNG")
    return prepare_image(buf.getvalue())


def test_add_accumulates_and_take_clears():
    s = PasteStaging()
    s.add(ClipboardGrab([_att()]))
    s.add(ClipboardGrab([_att(), _att()]))
    assert s.pending and len(s.chips()) == 3
    taken = s.take()
    assert len(taken) == 3 and not s.pending


def test_add_reports_skipped_files():
    s = PasteStaging()
    msg = s.add(ClipboardGrab([], skipped=["notes.pdf"]))
    assert "notes.pdf" in msg
    assert not s.pending


def test_add_empty_grab_message():
    s = PasteStaging()
    msg = s.add(ClipboardGrab())
    assert "no image" in msg.lower()
