"""The ICO packer inside scripts/render_icons.py: pure function, no Qt.

scripts/ is not a package, so the module is loaded by file path. Its
PySide6 imports live inside functions deliberately — importing the module
must not require Qt.
"""

import importlib.util
import struct
from pathlib import Path


def _load_render_icons():
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "render_icons.py"
    )
    spec = importlib.util.spec_from_file_location("render_icons", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_ico_directory_and_offsets():
    ri = _load_render_icons()
    entries = [(16, b"PNG-SIXTEEN"), (32, b"PNG-THIRTYTWO!")]
    ico = ri.build_ico(entries)
    reserved, image_type, count = struct.unpack_from("<HHH", ico, 0)
    assert (reserved, image_type, count) == (0, 1, 2)
    # Directory entry 0: width byte, height byte, then offset at +12.
    w0, h0 = ico[6], ico[7]
    size0, offset0 = struct.unpack_from("<II", ico, 6 + 8)
    assert (w0, h0) == (16, 16)
    assert size0 == len(b"PNG-SIXTEEN")
    assert offset0 == 6 + 16 * 2  # header + two 16-byte directory entries
    assert ico[offset0 : offset0 + size0] == b"PNG-SIXTEEN"
    size1, offset1 = struct.unpack_from("<II", ico, 6 + 16 + 8)
    assert offset1 == offset0 + size0
    assert ico[offset1 : offset1 + size1] == b"PNG-THIRTYTWO!"


def test_build_ico_encodes_256px_as_zero_width_byte():
    ri = _load_render_icons()
    ico = ri.build_ico([(256, b"BIGPNG")])
    assert ico[6] == 0 and ico[7] == 0  # 256 is stored as 0 per ICO format
