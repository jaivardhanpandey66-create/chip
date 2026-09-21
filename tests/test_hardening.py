"""Hardening regression tests — secrets, blocklist, gitignore."""
import os
import re
import sys

HOME = os.path.expanduser("~")

def _load_blocklist(path):
    text = open(path, errors="ignore").read()
    m = re.search(r"DESTRUCTIVE_CMDS\s*=\s*re\.compile\(\s*(.*?)\s*\)\s*$", text,
                  re.S | re.M)
    # Fallback: eval the concatenated string literals
    assert m, f"DESTRUCTIVE_CMDS not found in {path}"
    return m.group(0)


def test_no_hardcoded_keys():
    for p in [os.path.join(HOME, "jarvis", "jarvis_web.py"),
              os.path.join(HOME, "john", "john_web.py")]:
        text = open(p).read()
        # Legacy constant must remain empty — no live assignment like ^KEY = "sk-..."
        assert not re.search(r'^JARVIS_API_KEY\s*=\s*"sk-', text, re.M)
        assert not re.search(r'^JOHN_API_KEY\s*=\s*"sk-', text, re.M)
        # Must support env + key file
        assert "OPENROUTER_API_KEY" in text
        assert 'os.path.join(CONFIG_DIR, "key")' in text


def test_blocklist_covers_core_patterns():
    patterns = ["rm\\s+-rf", "mkfs", "shutdown", "diskpart", "/dev/"]
    # NOTE: chip_web.py intentionally excluded — owner runs CHIP at FULL
    # CONTROL with no blocklist. Guardrails remain on jarvis/john/friday.
    for p in [os.path.join(HOME, "jarvis", "jarvis_web.py"),
              os.path.join(HOME, "john", "john_web.py"),
              os.path.join(HOME, "friday", "friday_web.py")]:
        text = open(p, errors="ignore").read()
        for pat in patterns:
            assert pat in text, f"{pat} missing in {p}"


def test_chip_full_control():
    text = open(os.path.join(HOME, "chip_web.py"), errors="ignore").read()
    assert "FULL CONTROL" in text
    assert "PLAN MODE" not in text or "no PLAN/BUILD modes" in text
    assert "shutdown" in text.lower()  # power commands documented as allowed


def test_friday_blocklist_superset():
    text = open(os.path.join(HOME, "friday", "friday_web.py")).read()
    assert "dd\\s+of=" in text  # friday-specific dd guard retained
    assert "shutdown" in text  # chip patterns merged in


def test_gitignore_hardened():
    required = ["*.db", "key", ".env"]
    for repo in ["friday", "cortex", "stark", "jarvis", "john"]:
        gi = open(os.path.join(HOME, repo, ".gitignore")).read()
        for pat in required:
            assert pat in gi, f"{pat} missing in {repo}/.gitignore"


def test_default_bind_localhost():
    for p in [os.path.join(HOME, "chip_web.py"),
              os.path.join(HOME, "jarvis", "jarvis_web.py"),
              os.path.join(HOME, "john", "john_web.py")]:
        text = open(p).read()
        assert '"127.0.0.1"' in text
        assert "0.0.0.0" in text  # warning string must exist


def test_native_cores_identical():
    import hashlib
    def sha(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    a = sha(os.path.join(HOME, "chip_native.cpp"))
    b = sha(os.path.join(HOME, "jarvis", "jarvis_native.cpp"))
    c = sha(os.path.join(HOME, "john", "john_native.cpp"))
    assert a == b == c, "C++ cores diverged — deduplicate into shared/"
