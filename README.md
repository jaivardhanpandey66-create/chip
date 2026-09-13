<div align="center">

# CHIP 3.0

**A holographic AI agent that runs on your machine — streaming, tool-enabled, and open.**

Python · C++ acceleration · Rust acceleration

</div>

---

## Overview

CHIP is a self-hosted agentic assistant wrapped in a holographic JARVIS-style
interface. It delegates reasoning to any [OpenRouter](https://openrouter.ai)
model while running **on your own hardware and under your own control**. Text,
tools, and voice stream live to the browser via Server-Sent Events — no
Electron, no vendor lock-in, no data leaving your network except the prompts
you choose to send.

## Features

- **Live streaming agent loop** — responses and tool activity stream into the
  UI via SSE as they happen.
- **Memory (optional)** — links to the [CORTEX](https://github.com/jaivardhanpandey66-create/cortex)
  long-term store: recalls relevant memories each turn, saves every exchange,
  and offers `recall` / `memorize` tools. Start `cortex_web.py` (port 8200) to
  enable; CHIP degrades to memory-less automatically otherwise.
- **Telemetry (optional)** — `system_info` pulls live per-core CPU, memory,
  swap, disk, network, thermals and GPU from the
  [STARK](https://github.com/jaivardhanpandey66-create/stark) command center
  (port 8100) when it is running, falling back to a direct `/proc` read.
- **PLAN / BUILD modes** — plan mode restricts the agent to read-only tools;
  switch any time with `Tab`.
- **Tool use** — run commands, read/write/edit files, glob & grep search,
  git operations, web fetch, system inspection.
- **Sub-agent delegation** — a `delegate` tool spawns parallel research
  sub-agents, mirroring the opencode `Task` tool.
- **Live model picker** — switch between any model the API exposes, per
  request, at runtime.
- **Sessions & context budget** — conversation history in a bounded store;
  long chats are auto-trimmed to a configurable token budget.
- **Voice in / voice out** — browser speech-to-text plus browser or
  server-side TTS (Google Translate proxy) for spoken replies.
- **Native acceleration** — hot paths implemented in C++ and Rust with a
  pure-Python fallback.

## Architecture

```
┌──────────────┐   SSE/JSON    ┌──────────────┐      ┌──────────────────┐
│  Browser UI  │ ────────────► │  chip_web.py │ ───► │  OpenRouter API  │
│ (holographic │   (chip_ui)   │ Python agent │      │   (any model)    │
│   HTML/JS)   │ ◄──────────── │    engine    │ ◄─── │                  │
└──────────────┘  audio MP3    └──────────────┘      └──────────────────┘
                                    │  ctypes
                        ┌───────────┴────────────┐
                        ▼                        ▼
              ┌────────────────────┐   ┌────────────────────┐
              │ libchip_native.so  │   │     chip_rs.so     │
              │      (C++)         │   │       (Rust)       │
              │ • TTS LRU cache    │   │ • SHA-256 (ETag)   │
              │ • session store    │   │ • token budgeting  │
              │ • SSE framing      │   │                    │
              └────────────────────┘   └────────────────────┘
```

### Project layout

| Path | Description |
|------|-------------|
| `chip_web.py` | HTTP/SSE server + Python agent engine (tools, PLAN/BUILD, delegation) |
| `chip_ui.html` | Holographic front end (canvas hex-grid, ARC reactor, gauges) |
| `chip.py` | Original terminal agent (CLI reference) |
| `chip_native.cpp` | C++ core — bounded, thread-safe TTL LRU, session store, SSE framing |
| `chip_rs/` | Rust core — dependency-free SHA-256, CJK-aware token estimator |
| `build.sh` / `build_rs.sh` | Build the C++ / Rust cores |

Both native cores load at runtime via `ctypes` and are **optional**: without
them the server runs in a pure-Python mode.

## Getting started

### Prerequisites

- Python 3.10+
- An API key from [OpenRouter](https://openrouter.ai) (or any
  OpenAI-compatible endpoint)
- Optional: `g++` for the C++ core, [Rustup](https://rustup.rs) for the Rust core

### 1. Install

```bash
git clone https://github.com/jaivardhanpandey66-create/chip.git
cd chip
pip3 install --user openai
```

### 2. Configure your API key

```bash
# option A — environment
export OPENROUTER_API_KEY="sk-or-v1-..."

# option B — config file
mkdir -p ~/.config/chip && echo -n 'sk-or-v1-...' > ~/.config/chip/key
```

### 3. Build the native cores (optional)

```bash
./build.sh      # → libchip_native.so (C++)
./build_rs.sh   # → chip_rs.so        (Rust)
```

### 4. Run

```bash
python3 chip_web.py                    # → http://127.0.0.1:8000
python3 chip_web.py --model anthropic/claude-sonnet-4-20250514
python3 chip_web.py --port 9000 --host 0.0.0.0
```

Open <http://127.0.0.1:8000> — the interface boots with a welcome sequence
and an animated holographic core.

## Usage

### Interface

| Control | Action |
|---------|--------|
| Text input | Type a request; the agent streams its reply and tool activity live |
| `Tab` | Toggle **PLAN** (read-only) / **BUILD** (full tools) |
| Model picker | Live switch between models exposed by the API |
| `Space` (hold) | Dictate using browser speech-to-text |
| **VOX** | Enable spoken replies (browser TTS + server-side fallback) |
| **MIC** | Continuous voice-in mode |

### API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Holographic UI |
| `GET` | `/api/models` | Models available via the configured API |
| `GET` | `/api/tts?text=…&lang=…` | MP3 speech synthesis (cached, `ETag`/`304`) |
| `GET` | `/api/stats` | Native-core status, cache & session statistics |
| `POST` | `/api/chat` | One-shot JSON completion |
| `POST` | `/api/chat/stream` | Streaming SSE completion (primary) |

`POST /api/chat/stream` request body:

```json
{
  "message": "find the largest file in ~",
  "session": "work-1",
  "mode": "build",
  "model": "anthropic/claude-sonnet-4-20250514"
}
```

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | — | API key (or `~/.config/chip/key`) |
| `CHIP_MODEL` | `meta-llama/llama-3.3-70b-instruct` | Default model |
| `CHIP_CONTEXT_TOKENS` | `32000` | Token budget for context trimming |
| `--port` / `--host` / `--model` / `--max-steps` | — | CLI overrides |

## Native acceleration

The C++ and Rust cores own carefully scoped hot paths. Both are optional and
load via `ctypes` with a pure-Python fallback:

- **C++ (`libchip_native.so`)** — a bounded, thread-safe, TTL-expiring LRU for
  TTS audio (repeat phrases: ~500 ms network fetch → ~3 ms cache hit), the
  session-message store, and SSE frame construction.
- **Rust (`chip_rs.so`)** — a dependency-free SHA-256 used for TTS `ETag`
  headers so browsers revalidate with `304 Not Modified`, plus a CJK-aware
  token estimator that scores the full message batch in one `C` call to keep
  long conversations inside budget.

Both binaries are built at development time and **not committed** to the
repository.

## Security considerations

- The agent is able to run arbitrary commands and modify files — it is **not
  sandboxed**. It refuses obviously destructive patterns
  (`rm -rf`, `mkfs`, `cron` shutdown, etc.), but you should run it only on
  machines you own and audit its actions.
- Your API key is read from the environment or `~/.config/chip/key`, both of
  which are **outside** the repository. No secret material is ever committed.

## Development

```bash
python3 -m py_compile chip_web.py        # syntax check
./build.sh && ./build_rs.sh              # rebuild native cores
python3 bench_native.py                  # end-to-end TTS cache benchmark
```

## License

MIT (add your own license file if you redistribute).