# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start the server
venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765

# Run all tests
venv/bin/pytest

# Run a single test file
venv/bin/pytest tests/test_fetcher.py

# Run a single test by name
venv/bin/pytest tests/test_fetcher.py::test_merge_headers_replaces_case_insensitive_duplicates
```

## Architecture

This is a macOS-only local dashboard that aggregates AI platform usage metrics. The stack is FastAPI + httpx + a single-page HTML frontend (`dashboard/index.html`).

**Request lifecycle:**
1. `server.py` — FastAPI app. Manages platform enable/disable state in `config.json`, runs a background `refresh_loop` every 30 s (2 min for DeepSeek, MiniMax), and exposes `/api/usage` + `/api/platforms` + `/api/credentials/import`.
2. `fetcher.py` — All HTTP fetch logic. `fetch_platform(config)` dispatches by platform name. Platforms with `local://` URLs (Codex, Antigravity, Cursor) read from the local machine instead of making web requests. Auth headers and cookies are loaded from `header-txt/` and `cookie/` directories (gitignored).
3. `parsers.py` — Pure functions that transform raw API responses into `list[Metric]`. Each parser is registered in `PARSERS: dict[str, Callable]`.
4. `models.py` — Two Pydantic models: `Metric` (a single usage data point) and `PlatformResult` (a platform's full state including error).

**Platform auth types** (defined in `server.py:PLATFORM_META`):
- `local_app` — reads local disk/process (Codex, Antigravity, Cursor); no credentials needed
- `cookie` — cookie file only (Kimi)
- `cookie_header` — cookie + captured auth header file (Claude, Trae, MiniMax, DeepSeek)
- `api_key` — API key stored in `config.json` (OpenRouter)

**Adding a new platform** requires touching:
1. `fetcher.py` — add a `PlatformConfig` entry to `PLATFORMS` and implement fetch logic
2. `parsers.py` — add a `parse_<name>` function and register it in `PARSERS`
3. `server.py` — add an entry to `PLATFORM_META` with `type` and `display_name`
4. `dashboard/index.html` — add platform card UI

**Credential files** (gitignored, written by server on import):
- `cookie/<domain>_cookies.txt` — Netscape-format cookie files
- `header-txt/<platform>*.txt` — raw HTTP request headers exported from Chrome DevTools
- `config.json` — user config: `claude_org_id`, `openrouter_api_key`, `enabled_platforms`, `removed_platforms`
- `request_overrides/<platform>.json` — optional per-platform URL/header overrides

**DeepSeek** is the only platform that makes multiple parallel requests (`summary`, `cost`, `amount`) — see `fetch_deepseek_usage` in `fetcher.py`.

**Codex** has a three-tier fallback: app-server binary → wham API with cookie → Brotli-compressed disk cache (parsed with embedded Node.js script).
