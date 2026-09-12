# CHIP 3.0 — holographic AI agent for your Linux machine

CHIP is an agentic chat assistant that runs locally and talks to any
OpenRouter model. It streams live, uses tools to act on your machine
(files, commands, git, web), supports PLAN/BUILD modes, sub-agent
delegation, and voice in/out — wrapped in a holographic JARVIS-style UI.

## Components

| File | What it is |
|------|------------|
| `chip_web.py` | Web + agent engine: streaming SSE agent loop, PLAN/BUILD modes, `delegate` sub-agent, model picker, sessions, Google-TTS proxy, `/api/stats` |
| `chip_ui.html` | Holographic front end (canvas hex-grid, ARC reactor, live streaming, spacebar voice toggle) |
| `chip.py` | Original CHIP terminal agent (CLI) |
| `chip_native.cpp` | C++ acceleration core → `libchip_native.so` (TTS LRU cache, session store, SSE framing) |
| `chip_rs/` | Rust acceleration core → `chip_rs.so` (dep-free SHA-256 for TTS ETag/304, token-budget context trimming) |
| `build.sh` | Compiles the C++ core |
| `build_rs.sh` | Compiles the Rust core |

## Setup

1. Install Python deps:
   ```bash
   pip3 install --user openai
   ```
2. Create a project at [openrouter.ai](https://openrouter.ai), then save an API key:
   ```bash
   export OPENROUTER_API_KEY=sk-or-...        # or:
   mkdir -p ~/.config/chip && echo -n 'sk-or-...' > ~/.config/chip/key
   ```
3. Build the native cores (optional — pure-Python fallback otherwise):
   ```bash
   ./build.sh     # needs g++      → libchip_native.so
   ./build_rs.sh  # needs rustup   → chip_rs.so
   ```

## Run

```bash
python3 chip_web.py                 # → http://127.0.0.1:8000
python3 chip_web.py --model anthropic/claude-sonnet-4-20250514
```

### UI controls
- **Tab** — toggle PLAN / BUILD mode (PLAN = read-only tools only)
- **Model picker** — choose any OpenRouter model live
- **Space** — hold to talk (Web Speech API); ⏻ mic on/off
- **VOX** — spoken replies via browser TTS + server-side fallback

## API

| Route | Purpose |
|-------|---------|
| `GET /` | Holographic UI |
| `GET /api/models` | Available OpenRouter models |
| `GET /api/tts?text=…&lang=en` | MP3 speech (cached, ETag/304) |
| `GET /api/stats` | Native core status, cache + session stats |
| `POST /api/chat` | One-shot JSON chat |
| `POST /api/chat/stream` | SSE streaming chat (the real one) |

## How the native cores help

- **C++** owns the stateful hot paths: a TTL LRU for TTS audio (repeat
  phrases: ~500 ms network → ~3 ms cache), the session store, and SSE
  frame building.
- **Rust** owns real compute: a dependency-free SHA-256 (TTS `ETag` →
  browsers get `304 Not Modified`) and a CJK-aware token estimator that
  auto-trims long conversations to a budget
  (`CHIP_CONTEXT_TOKENS`, default 32000), scoring the whole message batch
  in one C-call.

Both load at runtime via ctypes and degrade to pure Python if the `.so`
is missing — `GET /api/stats` reports `"native"` / `"rust"` status.

## Security notes

- Commands are **NOT sandboxed** — the agent can do anything your user
  can. It guards against obviously destructive commands
  (`rm -rf`, `mkfs`, …) but run it with care.
- API key lives in `OPENROUTER_API_KEY` or `~/.config/chip/key`; gitignored.