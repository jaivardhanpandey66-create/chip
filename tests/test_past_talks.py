"""Past Talks + eternal memory regression tests."""
import os
HOME = os.path.expanduser("~")

BACKENDS = [os.path.join(HOME, "chip_web.py"),
            os.path.join(HOME, "jarvis", "jarvis_web.py"),
            os.path.join(HOME, "john", "john_web.py"),
            os.path.join(HOME, "friday", "friday_web.py")]
UIS = [os.path.join(HOME, "chip_ui.html"),
       os.path.join(HOME, "jarvis", "jarvis_ui.html"),
       os.path.join(HOME, "john", "john_ui.html"),
       os.path.join(HOME, "friday", "friday_ui.html")]

def test_session_endpoints_present():
    for p in BACKENDS:
        t = open(p, errors="ignore").read()
        assert "/api/sessions" in t, f"sessions missing in {p}"
        assert "/api/history" in t, f"history missing in {p}"
        assert "SESSION_META" in t, f"index missing in {p}"

def test_memory_endpoints_present():
    for p in BACKENDS:
        t = open(p, errors="ignore").read()
        assert "/api/memory/search" in t, f"mem search missing in {p}"
        assert "/api/memory/remember" in t, f"mem remember missing in {p}"
        assert "CORTEX_URL" in t, f"cortex bridge missing in {p}"

def test_past_talks_ui_present():
    for p in UIS:
        t = open(p, errors="ignore").read().lower()
        assert "past talk" in t, f"panel missing in {p}"
        assert "/api/sessions" in t, f"js missing in {p}"
