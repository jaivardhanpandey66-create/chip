"""DESK wiring regression tests — every AI can manage office files."""
import os
HOME = os.path.expanduser("~")

BACKENDS = [os.path.join(HOME, "chip_web.py"),
            os.path.join(HOME, "jarvis", "jarvis_web.py"),
            os.path.join(HOME, "john", "john_web.py"),
            os.path.join(HOME, "friday", "friday_web.py")]

def test_desk_tools_present():
    for p in BACKENDS:
        t = open(p, errors="ignore").read()
        assert "DESK_DIR" in t, f"DESK_DIR missing in {p}"
        assert "tool_desk_list" in t, f"desk_list missing in {p}"
        assert "tool_desk_open" in t, f"desk_open missing in {p}"
        assert "tool_desk_save" in t, f"desk_save missing in {p}"
        assert '"desk_list"' in t and '"desk_open"' in t and '"desk_save"' in t, \
            f"TOOLS registration missing in {p}"

def test_desk_sandboxed():
    for p in BACKENDS:
        t = open(p, errors="ignore").read()
        assert "_desk_safe" in t, f"sandbox helper missing in {p}"
        assert "Documents" in t and "Desk" in t, f"desk dir missing in {p}"
