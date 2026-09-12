#!/usr/bin/env python3
"""
CHIP 3.0 — an advanced agentic assistant for your terminal.

Uses OpenRouter (OpenAI-compatible API) for the brain and `rich` for the UI.
Can read/write/edit files, run commands, search code, manage git, fetch web
content, inspect processes, and undo changes.

Setup:
    export OPENROUTER_API_KEY=sk-or-...
    python3 chip.py

Flags:
    --auto              auto-run commands without asking
    --model NAME        override the default model
    --no-voice          disable text-to-speech
    --session NAME      resume a named session
    --resume            resume the most recent session
    --max-steps N       max tool-call steps per turn (default: 15)
    --temperature T     model temperature (default: 0.7)
    --compact           minimal UI output
    --json              output final answers as JSON (for scripting)
"""

import argparse
import copy
import difflib
import fnmatch
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("Missing dependency. Run: pip3 install --user openai rich")
    sys.exit(1)

from rich import box as box
from rich.console import Console
from rich.markup import escape as markup_escape
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

console = Console()

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="CHIP 3.0", add_help=False)
parser.add_argument("--auto", action="store_true", help="auto-run commands")
parser.add_argument("--model", type=str, default=None)
parser.add_argument("--no-voice", action="store_true")
parser.add_argument("--session", type=str, default=None)
parser.add_argument("--resume", action="store_true")
parser.add_argument("--max-steps", type=int, default=15)
parser.add_argument("--temperature", type=float, default=0.7)
parser.add_argument("--compact", action="store_true")
parser.add_argument("--json", action="store_true")
parser.add_argument("rest", nargs="*")
args = parser.parse_args()

AUTO_RUN = args.auto
COMPACT = args.compact
JSON_MODE = args.json
MAX_STEPS = args.max_steps
TEMPERATURE = args.temperature

MODEL = args.model or os.environ.get("CHIP_MODEL", "meta-llama/llama-3.3-70b-instruct")
BASE_URL = "https://openrouter.ai/api/v1"

HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".config", "chip")
SESSIONS_DIR = os.path.join(CONFIG_DIR, "sessions")
TRUSTED_DIRS = {HOME, os.path.normpath(HOME + "/Desktop"), os.path.normpath(HOME + "/Documents")}

DESTRUCTIVE_CMDS = re.compile(
    r"\b(rm\s+-rf|rmdir|mkfs|dd\b|shutdown|reboot|killall|pkill|:(){|format\b|cron)\b|>\s*/dev/", re.I
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

EDIT_HISTORY: dict[str, list[tuple[str, str, str]]] = {}
# path -> [(timestamp, old_content, new_content), ...]

ALIASES_FILE = os.path.join(CONFIG_DIR, "aliases.json")
ALIASES: dict[str, str] = {}


def load_aliases():
    global ALIASES
    if os.path.exists(ALIASES_FILE):
        try:
            ALIASES = json.loads(open(ALIASES_FILE).read())
        except Exception:
            ALIASES = {}


def save_aliases():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(ALIASES_FILE, "w") as f:
        json.dump(ALIASES, f, indent=2)


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------

def get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    keyfile = os.path.join(CONFIG_DIR, "key")
    if os.path.exists(keyfile):
        return open(keyfile).read().strip()
    console.print("[yellow]No API key found. Set OPENROUTER_API_KEY or paste it now:[/yellow]")
    key = console.input("[bold cyan]>[/bold cyan] ").strip()
    if key:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(keyfile, "w") as f:
            f.write(key)
        os.chmod(keyfile, 0o600)
        console.print("[green]Saved to ~/.config/chip/key (chmod 600)[/green]")
    return key


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

def _session_path(name: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", name)
    return os.path.join(SESSIONS_DIR, f"{safe}.jsonl")


def _find_latest_session() -> str | None:
    if not os.path.isdir(SESSIONS_DIR):
        return None
    files = sorted(
        [os.path.join(SESSIONS_DIR, f) for f in os.listdir(SESSIONS_DIR) if f.endswith(".jsonl")],
        key=os.path.getmtime,
    )
    return files[-1] if files else None


def load_session(path: str) -> list[dict]:
    messages = []
    if not os.path.exists(path):
        return messages
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return messages


def append_session(path: str, message: dict):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(message, default=str) + "\n")


def get_session_path() -> str | None:
    if args.session:
        return _session_path(args.session)
    if args.resume:
        p = _find_latest_session()
        if p:
            return p
        console.print("[yellow]No previous sessions found.[/yellow]")
    return None


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

def expand_alias(text: str) -> str:
    if text.startswith("!"):
        parts = text[1:].split(None, 1)
        name = parts[0] if parts else ""
        if name in ALIASES:
            rest = parts[1] if len(parts) > 1 else ""
            expanded = ALIASES[name]
            return f"{expanded} {rest}".strip() if rest else expanded
    return text


# ---------------------------------------------------------------------------
# Edit history / undo
# ---------------------------------------------------------------------------

def _record_edit(path: str, old: str, new: str):
    ts = datetime.now().isoformat()
    EDIT_HISTORY.setdefault(path, []).append((ts, old, new))


def _undo_last_edit(path: str) -> str:
    if path not in EDIT_HISTORY or not EDIT_HISTORY[path]:
        return f"No edit history for {path}"
    ts, old, new = EDIT_HISTORY[path].pop()
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(old)
        return f"Undid edit on {path} (from {ts})"
    except Exception as e:
        return f"Failed to undo: {e}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def is_trusted_path(p):
    p = os.path.realpath(p)
    return any(p.startswith(d) for d in TRUSTED_DIRS)


def tool_run_command(args):
    cmd = args["command"]
    if not AUTO_RUN and DESTRUCTIVE_CMDS.search(cmd):
        confirm = console.input(Panel(
            f"[yellow]This command could be destructive:[/yellow]\n[white]{cmd}[/white]\n"
            f"[cyan]Run? (y/N) [/cyan]", box=None, expand=False))
        if confirm.strip().lower() != "y":
            return "User declined to run this command."

    timeout = min(args.get("timeout", 300), 600)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                              cwd=args.get("cwd", os.getcwd()))
        out = proc.stdout.strip() or ""
        err = proc.stderr.strip() or ""
        code = proc.returncode
        result = f"exit_code={code}\n{out}{('' if not err else '\n[stderr]\n' + err)}".strip()
        return result or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Error running command: {e}"


def tool_read_file(args):
    path = os.path.expanduser(args["path"])
    if not os.path.exists(path):
        return f"File not found: {path}"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        lines = content.count("\n") + 1
        return f"[contents of {path} ({lines} lines)]\n{content}"
    except PermissionError:
        return f"Permission denied reading {path}"


def tool_write_file(args):
    path = os.path.expanduser(args["path"])
    content = args["content"]
    backup = args.get("backup", True)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        old = ""
        if backup and os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                old = f.read()
            _record_edit(path, old, content)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


def tool_edit_file(args):
    path = os.path.expanduser(args["path"])
    old_text = args["old_text"]
    new_text = args["new_text"]
    if not os.path.exists(path):
        return f"File not found: {path}"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if old_text not in content:
            return f"old_text not found in {path}. No changes made."
        count = content.count(old_text)
        if count > 1 and not args.get("replaceAll", False):
            return f"old_text found {count} times in {path}. Set replaceAll=true or provide more context."
        _record_edit(path, content, content.replace(old_text, new_text, 1))
        new_content = content.replace(old_text, new_text, 1 if not args.get("replaceAll") else -1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        diff = list(difflib.unified_diff(
            content.splitlines(), new_content.splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
        return f"Edited {path}\n" + "\n".join(diff[:50])
    except Exception as e:
        return f"Error editing {path}: {e}"


def tool_append_file(args):
    path = os.path.expanduser(args["path"])
    content = args["content"]
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error appending to {path}: {e}"


def tool_list_dir(args):
    path = os.path.expanduser(args.get("path", "."))
    if not os.path.isdir(path):
        return f"Not a directory: {path}"
    try:
        show_hidden = args.get("hidden", False)
        max_depth = args.get("max_depth", 1)
        entries = []
        for name in sorted(os.listdir(path)):
            if not show_hidden and name.startswith("."):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                entries.append(f"  [bold blue]{name}/[/bold blue]")
            else:
                size = os.path.getsize(full)
                suffix = f"  ({_human_size(size)})" if size > 0 else ""
                entries.append(f"  {name}{suffix}")
        return f"Contents of {path} ({len(entries)} entries):\n" + "\n".join(entries)
    except PermissionError:
        return f"Permission denied listing {path}"


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def tool_open(args):
    path = os.path.expanduser(args.get("path", ""))
    if not os.path.exists(path):
        return f"Not found: {path}"
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opened {path} with {opener}"
    except Exception as e:
        return f"Could not open {path}: {e}"


def tool_move_file(args):
    src = os.path.expanduser(args["source"])
    dst = os.path.expanduser(args["destination"])
    if not os.path.exists(src):
        return f"Source not found: {src}"
    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(src, dst)
        return f"Moved {src} -> {dst}"
    except Exception as e:
        return f"Error moving: {e}"


def tool_copy_file(args):
    src = os.path.expanduser(args["source"])
    dst = os.path.expanduser(args["destination"])
    if not os.path.exists(src):
        return f"Source not found: {src}"
    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return f"Copied {src} -> {dst}"
    except Exception as e:
        return f"Error copying: {e}"


def tool_delete_file(args):
    path = os.path.expanduser(args["path"])
    if not os.path.exists(path):
        return f"Not found: {path}"
    if not AUTO_RUN and not args.get("force", False):
        confirm = console.input(Panel(
            f"[red]Delete {path}?[/red]\n[cyan]Type 'yes' to confirm:[/cyan]", box=None, expand=False))
        if confirm.strip().lower() != "yes":
            return "User declined to delete."
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return f"Deleted {path}"
    except Exception as e:
        return f"Error deleting: {e}"


def tool_search_files(args):
    pattern = args["pattern"]
    root = os.path.expanduser(args.get("path", "."))
    max_results = args.get("max_results", 50)
    if not os.path.isdir(root):
        return f"Not a directory: {root}"
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        if not args.get("hidden", False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fnmatch.fnmatch(fname, pattern):
                matches.append(os.path.join(dirpath, fname))
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break
    if not matches:
        return f"No files matching '{pattern}' in {root}"
    return f"Found {len(matches)} files:\n" + "\n".join(matches)


def tool_grep(args):
    pattern = args["pattern"]
    path = args.get("path", ".")
    include = args.get("include", "*")
    max_results = args.get("max_results", 50)
    context_lines = args.get("context", 0)

    if os.path.isfile(path):
        files = [path]
    elif os.path.isdir(path):
        files = []
        for dirpath, _, filenames in os.walk(path):
            for fn in filenames:
                if fnmatch.fnmatch(fn, include) and not fn.startswith("."):
                    files.append(os.path.join(dirpath, fn))
    else:
        return f"Not found: {path}"

    results = []
    rgx = re.compile(pattern, re.I if args.get("ignore_case", True) else 0)
    for fp in files:
        if len(results) >= max_results:
            break
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if rgx.search(line):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    block = "".join(f"  {j+1:>5}: {lines[j]}" for j in range(start, end))
                    results.append(f"\n{fp}:{i+1}\n{block}")
                    if len(results) >= max_results:
                        break
        except (PermissionError, OSError):
            pass
    if not results:
        return f"No matches for '{pattern}'"
    return f"Found {len(results)} matches:\n" + "\n".join(results)


def tool_web_fetch(args):
    url = args["url"]
    timeout = min(args.get("timeout", 30), 60)
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, headers={
            "User-Agent": "CHIP/3.0 (terminal assistant)"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        max_chars = args.get("max_chars", 8000)
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... (truncated at {max_chars} chars)"
        return f"[{url}]\n{content}"
    except Exception as e:
        return f"Error fetching {url}: {e}"


def tool_list_processes(args):
    sort_by = args.get("sort", "cpu")
    top_n = args.get("top", 15)
    try:
        cmd = f"ps aux --sort=-{sort_by} | head -{top_n + 1}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() or "No processes found."
    except Exception as e:
        return f"Error: {e}"


def tool_system_info(args):
    info = {}
    try:
        info["hostname"] = subprocess.check_output("hostname", text=True, timeout=5).strip()
        info["kernel"] = subprocess.check_output("uname -r", text=True, timeout=5).strip()
        info["uptime"] = subprocess.check_output("uptime -p", text=True, timeout=5).strip()
        info["cpu"] = subprocess.check_output(
            "lscpu | grep 'Model name' | cut -d: -f2 | xargs", shell=True, text=True, timeout=5
        ).strip()
        info["memory"] = subprocess.check_output(
            "free -h | awk '/Mem:/ {printf \"%s / %s (%.0f%%)\", $3, $2, $3/$2*100}'",
            shell=True, text=True, timeout=5
        ).strip()
        info["disk"] = subprocess.check_output(
            "df -h / | awk 'NR==2 {printf \"%s / %s (%s)\", $3, $2, $5}'",
            shell=True, text=True, timeout=5
        ).strip()
        info["os"] = subprocess.check_output(
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'\"' -f2",
            shell=True, text=True, timeout=5
        ).strip()
    except Exception:
        pass
    lines = [f"  {k}: {v}" for k, v in info.items() if v]
    return "System Information:\n" + "\n".join(lines)


def tool_undo_edit(args):
    path = os.path.expanduser(args["path"])
    return _undo_last_edit(path)


def tool_git(args):
    subcommand = args.get("subcommand", "status")
    valid = {"status", "log", "diff", "add", "commit", "branch", "checkout", "stash",
             "pull", "push", "remote", "blame", "show", "reset", "merge", "rebase"}
    if subcommand not in valid:
        return f"Unknown git subcommand: {subcommand}. Valid: {', '.join(sorted(valid))}"
    parts = ["git", subcommand]
    if subcommand == "commit":
        msg = args.get("message", "")
        if not msg:
            return "git commit requires a 'message' argument."
        parts.extend(["-m", msg])
    elif subcommand == "add":
        paths = args.get("paths", ".")
        if isinstance(paths, str):
            paths = [paths]
        parts.extend(paths)
    elif subcommand == "checkout":
        branch = args.get("branch", "")
        if not branch:
            return "git checkout requires a 'branch' argument."
        parts.append(branch)
    else:
        for k, v in args.items():
            if k not in ("subcommand", "message", "paths", "branch") and v:
                parts.append(f"--{k}" if len(k) > 1 else f"-{k}")
                if v is not True:
                    parts.append(str(v))

    if not AUTO_RUN and subcommand in ("commit", "push", "reset", "rebase", "merge"):
        cmd_str = " ".join(shlex.quote(str(p)) for p in parts)
        confirm = console.input(Panel(
            f"[yellow]Git command:[/yellow]\n[white]{cmd_str}[/white]\n"
            f"[cyan]Run? (y/N) [/cyan]", box=None, expand=False))
        if confirm.strip().lower() != "y":
            return "User declined."

    try:
        proc = subprocess.run(parts, capture_output=True, text=True, timeout=30)
        out = proc.stdout.strip() or ""
        err = proc.stderr.strip() or ""
        return f"{' '.join(parts)}\nexit_code={proc.returncode}\n{out}{('' if not err else '\n' + err)}".strip()
    except Exception as e:
        return f"Git error: {e}"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command. Use for executing code, installing packages, checking system info, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 300)"},
                    "cwd": {"type": "string", "description": "Working directory for the command"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a text file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file. Backs up the original automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "backup": {"type": "boolean", "description": "Create backup before writing (default true)"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Make a surgical edit to a file by replacing old_text with new_text. Shows a diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Exact text to find and replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "replaceAll": {"type": "boolean", "description": "Replace all occurrences (default false)"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "Append content to the end of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories with sizes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path (defaults to cwd)"},
                    "hidden": {"type": "boolean", "description": "Show hidden files"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Find files by name pattern (glob) recursively.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py', 'test_*'"},
                    "path": {"type": "string", "description": "Root directory (defaults to cwd)"},
                    "max_results": {"type": "integer", "description": "Max files to return"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with regex. Shows context lines around matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "File or directory to search in"},
                    "include": {"type": "string", "description": "File filter, e.g. '*.py'"},
                    "context": {"type": "integer", "description": "Context lines around match (default 0)"},
                    "ignore_case": {"type": "boolean", "description": "Case insensitive (default true)"},
                    "max_results": {"type": "integer"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open",
            "description": "Open a file, folder, or URL using the system default opener.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File/folder/URL to open"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file/directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copy a file or directory tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file or directory. Requires confirmation unless force=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "force": {"type": "boolean", "description": "Skip confirmation (default false)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch content from a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "max_chars": {"type": "integer", "description": "Max chars to return"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "List running processes sorted by CPU or memory usage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort": {"type": "string", "description": "Sort by: cpu, mem, rss, pid (default cpu)"},
                    "top": {"type": "integer", "description": "Number of processes to show (default 15)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_info",
            "description": "Get system information: hostname, kernel, CPU, memory, disk, OS.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": "Run a git command (status, log, diff, add, commit, branch, checkout, pull, push, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "subcommand": {"type": "string", "description": "Git subcommand: status, log, diff, add, commit, branch, checkout, pull, push, stash, blame, show, reset, merge, rebase, remote"},
                    "message": {"type": "string", "description": "Commit message (for commit)"},
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Files to add (for add)"},
                    "branch": {"type": "string", "description": "Branch name (for checkout)"},
                },
                "required": ["subcommand"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_edit",
            "description": "Undo the last edit made to a file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]

FUNCS = {
    "run_command": tool_run_command,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "append_file": tool_append_file,
    "list_dir": tool_list_dir,
    "search_files": tool_search_files,
    "grep": tool_grep,
    "open": tool_open,
    "move_file": tool_move_file,
    "copy_file": tool_copy_file,
    "delete_file": tool_delete_file,
    "web_fetch": tool_web_fetch,
    "list_processes": tool_list_processes,
    "system_info": tool_system_info,
    "git": tool_git,
    "undo_edit": tool_undo_edit,
}

ACTION_VERBS = {
    "run_command": "running",
    "read_file": "reading",
    "write_file": "writing",
    "edit_file": "editing",
    "append_file": "appending to",
    "list_dir": "listing",
    "search_files": "searching for files",
    "grep": "searching contents of",
    "open": "opening",
    "move_file": "moving",
    "copy_file": "copying",
    "delete_file": "deleting",
    "web_fetch": "fetching",
    "list_processes": "listing processes",
    "system_info": "gathering system info",
    "git": "running git",
    "undo_edit": "undoing edit on",
}

# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

TOOL_COLORS = {
    "run_command": "cyan",
    "read_file": "green",
    "write_file": "yellow",
    "edit_file": "yellow",
    "append_file": "yellow",
    "list_dir": "blue",
    "search_files": "blue",
    "grep": "magenta",
    "open": "bright_cyan",
    "move_file": "red",
    "copy_file": "red",
    "delete_file": "red",
    "web_fetch": "bright_green",
    "list_processes": "bright_blue",
    "system_info": "bright_blue",
    "git": "bright_yellow",
    "undo_edit": "bright_red",
}


def header():
    return Panel(
        Text.assemble(
            (" CHIP 3.0 ", "bold white on magenta"),
            ("  advanced terminal assistant  ", "white on dark_gray"),
            ("\n", ""),
            (f"model: {MODEL}  |  steps: {MAX_STEPS}  |  temp: {TEMPERATURE}", "italic grey58"),
        ),
        expand=False, box=box.HEAVY, border_style="magenta")


def show_tool_call(name, args_repr):
    color = TOOL_COLORS.get(name, "white")
    label = Text.assemble((" \u23f8 ", "black on cyan"), (f" {name} ", f"bold {color}"))
    console.print(label, f"[dark_khaki]{markup_escape(args_repr[:120])}[/dark_khaki]")


def describe_action(name, args):
    verb = ACTION_VERBS.get(name, f"calling {name}")
    detail = (args.get("command") or args.get("path") or args.get("url")
              or args.get("pattern", "") or args.get("source", "")
              or args.get("content", "")[:40] or "")
    return f"{verb} {detail}".strip()


def banner(title):
    if COMPACT:
        console.print(f"[bold cyan]>>[/bold cyan] [bold]{title}[/bold]")
    else:
        console.print(Panel(f"[bold]{title}[/bold]", box=box.DOUBLE, expand=False,
                            border_style="cyan"))


def print_tool_result(name, result):
    color = TOOL_COLORS.get(name, "white")
    if name == "edit_file" and "\n" in str(result):
        console.print(Panel(
            Syntax(str(result), "diff", theme="monokai", word_wrap=True),
            title=f"\u2713 {name}", border_style=color, title_align="left"))
    elif len(str(result)) > 300:
        console.print(Panel(
            Syntax(str(result)[:2000], "bash", theme="monokai", word_wrap=True),
            title=f"\u2713 {name}", border_style=color, title_align="left"))
    else:
        console.print(f"  [{color}]\u2713 {name}[/color]: {markup_escape(str(result)[:200])}")


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------

class Voice:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self._engine = None
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        while True:
            text = self._q.get()
            if text is None:
                return
            try:
                if self._engine is None:
                    import pyttsx3
                    self._engine = pyttsx3.init()
                    self._engine.setProperty("rate", 180)
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:
                pass
            finally:
                self._q.task_done()

    def speak(self, text):
        if not self.enabled or not text:
            return
        clean = re.sub(r"[*_`#\[\]()~|>]", "", text).strip()
        if clean:
            self._q.put(clean)

    def toggle(self):
        self.enabled = not self.enabled
        console.print(f"[yellow]\U0001f50a voice {'ON' if self.enabled else 'OFF'}[/yellow]")
        return self.enabled


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------

def stream_response(client, messages):
    """Stream an API response, returning (content, tool_calls)."""
    response = {"content": "", "tool_calls": []}
    tool_buffer = {}

    try:
        with console.status(f"[magenta]thinking...[/magenta]", spinner="dots12"):
            stream = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=TEMPERATURE,
                stream=True,
            )

        live_panel = Panel("", title="assistant", border_style="green",
                           title_align="left", box=box.ROUNDED)
        if COMPACT:
            with Live(live_panel, console=console, refresh_per_second=20) as live:
                for chunk in stream:
                    _process_chunk(chunk, response, tool_buffer, live)
        else:
            with Live(live_panel, console=console, refresh_per_second=20) as live:
                for chunk in stream:
                    _process_chunk(chunk, response, tool_buffer, live)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        return None, []
    except Exception as e:
        console.print(f"[red]API error:[/red] {e}")
        return None, []

    tool_calls = []
    if tool_buffer:
        for i in sorted(tool_buffer):
            e = tool_buffer[i]
            tool_calls.append({
                "id": e["id"],
                "type": "function",
                "function": {"name": e["name"], "arguments": e["args"]},
            })

    return response, tool_calls


def _process_chunk(chunk, response, tool_buffer, live):
    choice = chunk.choices[0].delta
    if choice.content:
        response["content"] += choice.content
        live.update(Panel(
            Text(response["content"], style="green"),
            title="assistant", border_style="green",
            title_align="left", box=box.ROUNDED))
    if choice.tool_calls:
        for tc in choice.tool_calls:
            idx = tc.index
            e = tool_buffer.setdefault(idx, {"id": "", "name": "", "args": ""})
            if tc.id:
                e["id"] += tc.id
            if tc.function:
                if tc.function.name:
                    e["name"] += tc.function.name
                if tc.function.arguments:
                    e["args"] += tc.function.arguments


# ---------------------------------------------------------------------------
# Command-line chat mode
# ---------------------------------------------------------------------------

def chat_once(client, prompt, messages):
    """Handle a single user message through the agent loop."""
    messages.append({"role": "user", "content": prompt})
    if session_path:
        append_session(session_path, {"role": "user", "content": prompt, "ts": datetime.now().isoformat()})

    banner("Processing...")

    step = 0
    last_name, last_args = None, {}
    final_answer = ""

    for _ in range(MAX_STEPS):
        response, tool_calls = stream_response(client, messages)
        if response is None:
            break

        if not tool_calls:
            if response["content"]:
                messages.append({"role": "assistant", "content": response["content"]})
                final_answer = response["content"]
                if session_path:
                    append_session(session_path, {"role": "assistant", "content": response["content"], "ts": datetime.now().isoformat()})
            break

        response_data = {"role": "assistant", "content": response["content"] or ""}
        if response["content"]:
            messages.append({"role": "assistant", "content": response["content"]})
        response_data["tool_calls"] = tool_calls
        messages.append(response_data)

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                tc_args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                tc_args = {}
            last_name, last_args = name, tc_args

            show_tool_call(name, tc["function"]["arguments"])
            console.print(f"[cyan]\u279c {markup_escape(describe_action(name, tc_args))}[/cyan]")

            if name in FUNCS:
                with console.status(f"[cyan]{markup_escape(describe_action(name, tc_args))}...[/cyan]", spinner="dots"):
                    try:
                        result = FUNCS[name](tc_args)
                    except Exception as ex:
                        result = f"Tool raised {ex!r}"
                print_tool_result(name, result)
            else:
                console.print(f"[yellow]\u26a0 unknown tool {name}[/yellow]")
                result = f"Unknown tool: {name}"

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
            if session_path:
                append_session(session_path, {"role": "tool", "tool_call_id": tc["id"], "content": str(result)[:500], "ts": datetime.now().isoformat()})
            step += 1

        if step >= MAX_STEPS:
            console.print(f"[yellow]Reached max steps ({MAX_STEPS}).[/yellow]")
            break

    if final_answer:
        voice.speak(final_answer)

    if JSON_MODE and final_answer:
        console.print(json.dumps({"answer": final_answer, "steps": step}))

    return final_answer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global voice, session_path

    key = get_api_key()
    if not key:
        return

    load_aliases()
    voice = Voice(enabled=not args.no_voice)

    if not COMPACT:
        console.print(header())
        console.print(f"[dim]\U0001f50a voice: {'on' if voice.enabled else 'off'}  "
                      f"| max-steps: {MAX_STEPS}  | temp: {TEMPERATURE}[/dim]")
        console.print("[grey37]Commands: 'exit' quit, 'voice on/off', 'alias NAME=CMD', "
                      "'undo PATH', 'history PATH', 'sessions', 'clear'[/grey37]\n")

    client = OpenAI(base_url=BASE_URL, api_key=key, timeout=180)
    messages = [{"role": "system", "content": (
        "You are CHIP 3.0, an advanced agent inside the user's terminal on a Linux machine. "
        "You can run shell commands, read/write/edit files, search for files and text, "
        "manage git repositories, fetch web content, list processes, get system info, "
        "move/copy/delete files, and undo your edits. "
        "Prefer using tools over guessing. Show clear diffs when editing files. "
        "Verify command results. Summarize what you did. The home directory is " + HOME + "."
    )}]

    session_path = get_session_path()
    if session_path:
        existing = load_session(session_path)
        if existing:
            messages.extend(existing)
            console.print(f"[green]Resumed session: {os.path.basename(session_path)} "
                          f"({len(existing)} messages)[/green]")
        else:
            console.print(f"[dim]New session: {os.path.basename(session_path)}[/dim]")

    while True:
        try:
            user_input = console.input("[bold magenta]\u25b6 [/bold magenta]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not user_input.strip():
            continue

        raw = user_input.strip()
        lower = raw.lower()

        if lower in ("exit", "quit"):
            console.print("[dim]Goodbye![/dim]")
            break

        if lower in ("voice on", "voice off", "mute", "unmute"):
            voice.toggle()
            continue

        if lower == "clear":
            messages = messages[:1]
            console.print("[dim]Conversation cleared.[/dim]")
            continue

        if lower == "sessions":
            if os.path.isdir(SESSIONS_DIR):
                for f in sorted(os.listdir(SESSIONS_DIR)):
                    size = os.path.getsize(os.path.join(SESSIONS_DIR, f))
                    console.print(f"  {f}  ({_human_size(size)})")
            else:
                console.print("  (no sessions)")
            continue

        if lower.startswith("history "):
            path = os.path.expanduser(raw.split(None, 1)[1])
            if path in EDIT_HISTORY:
                for ts, old, new in EDIT_HISTORY[path]:
                    console.print(f"  [dim]{ts}[/dim]  ({len(old)} -> {len(new)} bytes)")
            else:
                console.print("  (no edit history)")
            continue

        if lower.startswith("undo "):
            path = os.path.expanduser(raw.split(None, 1)[1])
            console.print(tool_undo_edit({"path": path}))
            continue

        if lower.startswith("alias "):
            parts = raw.split(None, 1)[1].split("=", 1)
            if len(parts) == 2:
                ALIASES[parts[0].strip()] = parts[1].strip()
                save_aliases()
                console.print(f"[green]Alias set: {parts[0].strip()} = {parts[1].strip()}[/green]")
            else:
                console.print("[yellow]Usage: alias NAME=COMMAND[/yellow]")
            continue

        user_input = expand_alias(user_input)
        chat_once(client, user_input, messages)
        console.print("[dim]done. ask anything else or 'exit'.[/dim]\n")


if __name__ == "__main__":
    main()
