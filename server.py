from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fetcher import PLATFORMS, PlatformConfig, fetch_platform, load_config, parse_header_txt, ROOT
from models import Metric, PlatformResult
from parsers import PARSERS


DEFAULT_REFRESH_SECONDS = 30

# Platforms whose APIs are rate-sensitive get a longer refresh interval
PLATFORM_REFRESH_SECONDS: dict[str, int] = {
    "deepseek": 120,
    "minimax":  120,
}

cache: dict[str, PlatformResult] = {}
last_refresh: dict[str, float] = {}  # platform -> monotonic timestamp

# ─── Platform metadata ────────────────────────────────────────────────────────

# type: local_app  → server reads local app data directly, no credentials needed
# type: cookie     → needs cookie file only (e.g. Kimi reads kimi-auth from cookie)
# type: cookie_header → needs both cookie + auth header file (Claude, Trae, DeepSeek, …)

PLATFORM_META: dict[str, dict] = {
    "claude":      {"type": "cookie_header", "display_name": "Claude"},
    "codex":       {"type": "local_app",     "display_name": "Codex"},
    "antigravity": {"type": "local_app",     "display_name": "Google Antigravity"},
    "antigravity_ide": {"type": "local_app",     "display_name": "Antigravity IDE"},
    "kimi":        {"type": "cookie",        "display_name": "Kimi"},
    "trae":        {"type": "cookie_header", "display_name": "Trae"},
    "minimax":     {"type": "cookie_header", "display_name": "MiniMax"},
    "deepseek":    {"type": "cookie_header", "display_name": "DeepSeek"},
    "openrouter":   {"type": "api_key",       "display_name": "OpenRouter"},
    "cursor":       {"type": "local_app",     "display_name": "Cursor"},
    "siliconflow":  {"type": "cookie_header",  "display_name": "硅基流动"},
}


# ─── Config helpers ───────────────────────────────────────────────────────────

def get_enabled_platforms() -> list[str]:
    return load_config().get("enabled_platforms", [])


def _save_config(data: dict) -> None:
    (ROOT / "config.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )


def _update_enabled_platforms(enabled: list[str]) -> None:
    config = load_config()
    config["enabled_platforms"] = enabled
    _save_config(config)


# ─── Platform readiness ───────────────────────────────────────────────────────

def platform_is_ready(name: str) -> bool:
    """Return True if the platform has all required credentials / app data on disk."""
    home = Path.home()

    if name == "codex":
        base = home / "Library/Application Support/Codex"
        return base.exists() and any(
            d.exists() for d in [
                base / "Cache/Cache_Data",
                base / "Partitions/codex-browser-app/Cache/Cache_Data",
            ]
        )

    if name in {"antigravity", "antigravity_ide"}:
        return True  # verified at fetch time; always show if user adds it

    if name == "openrouter":
        return bool(load_config().get("openrouter_api_key", "").strip())

    if name == "cursor":
        db = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        return db.exists()

    cfg = PLATFORMS.get(name)
    if not cfg:
        return False

    header_override = {}
    needs_header = PLATFORM_META.get(name, {}).get("type") == "cookie_header"
    if needs_header:
        header_override = parse_header_txt(name)

    # Cookie must exist unless the captured request header already contains Cookie
    cookie_path = ROOT / "cookie" / cfg.cookie_file
    has_cookie_header = any(
        key.lower() == "cookie" and value
        for key, value in (header_override.get("headers") or {}).items()
    )
    if not cookie_path.exists() and not has_cookie_header:
        return False

    # cookie_header platforms also need a matching header file
    if needs_header:
        header_dir = ROOT / "header-txt"
        if not list(header_dir.glob(f"{name}*.txt")):
            return False
        if name == "trae" and header_override.get("_invalid_reason"):
            return False

    return True


# ─── Error helpers ────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            if "trae.ai" in str(exc.request.url):
                return "Trae 登录态无效：请重新导出凭据，Authorization 也会过期"
            if "cursor.com" in str(exc.request.url):
                return "Cursor 登录态无效：请重新在 Cursor App 中登录，或重启服务后重试"
            return "Cookie 已失效，请重新导出"
        return f"HTTP {status}: {exc.response.text[:240]}"
    if isinstance(exc, httpx.TimeoutException):
        return "接口超时，数据可能过时"
    if isinstance(exc, ValueError):
        return f"JSON 解析失败: {exc}"
    return str(exc)


def response_error_hint(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    base_resp = raw.get("base_resp")
    if isinstance(base_resp, dict) and base_resp.get("status_code") not in (None, 0, "0"):
        return str(base_resp.get("status_msg") or base_resp)
    code = raw.get("code")
    message = raw.get("message")
    if code and message:
        return f"{code}: {message}"
    if raw.get("successResponse") is False and message:
        return str(message)
    return None


def sort_metrics(metrics: list[Metric]) -> list[Metric]:
    order = {"5小时用量": 0, "本周用量": 1, "Basic": 0, "Bonus": 1}
    return sorted(metrics, key=lambda m: order.get(m.label, 99))


# ─── Refresh helpers ──────────────────────────────────────────────────────────

async def refresh_platform(
    config: PlatformConfig,
    fetcher: Callable[[PlatformConfig], object] = fetch_platform,
) -> PlatformResult:
    try:
        raw = await fetcher(config)  # type: ignore[misc]
        parser = PARSERS[config.name]
        metrics = sort_metrics(parser(raw))  # type: ignore[arg-type]
        error = response_error_hint(raw) if not metrics else None
        result = PlatformResult(
            platform=config.name,
            display_name=config.display_name,
            metrics=metrics,
            error=error,
            last_updated=now_iso(),
        )
    except Exception as exc:
        previous = cache.get(config.name)
        result = PlatformResult(
            platform=config.name,
            display_name=config.display_name,
            metrics=previous.metrics if previous else [],
            error=classify_error(exc),
            last_updated=previous.last_updated if previous else now_iso(),
        )
    cache[config.name] = result
    return result


async def refresh_all() -> list[PlatformResult]:
    """Refresh only the platforms the user has enabled."""
    enabled = get_enabled_platforms()
    configs = [PLATFORMS[name] for name in enabled if name in PLATFORMS]
    if not configs:
        return []
    results = list(await asyncio.gather(*(refresh_platform(cfg) for cfg in configs)))
    import time
    now = time.monotonic()
    for cfg in configs:
        last_refresh[cfg.name] = now
    return results


async def refresh_loop() -> None:
    import time
    while True:
        await asyncio.sleep(DEFAULT_REFRESH_SECONDS)
        enabled = get_enabled_platforms()
        now = time.monotonic()
        due = [
            PLATFORMS[name] for name in enabled
            if name in PLATFORMS
            and now - last_refresh.get(name, 0) >= PLATFORM_REFRESH_SECONDS.get(name, DEFAULT_REFRESH_SECONDS)
        ]
        if due:
            await asyncio.gather(*(refresh_platform(cfg) for cfg in due))
            for cfg in due:
                last_refresh[cfg.name] = time.monotonic()


# ─── App lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(refresh_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="AI Usage Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="dashboard"), name="dashboard")


# ─── Static routes ────────────────────────────────────────────────────────────

@app.get("/")
async def index() -> FileResponse:
    return FileResponse("dashboard/index.html")


@app.get("/assets/{name}.png")
async def asset(name: str) -> FileResponse:
    platform_name = name.lower()
    path = Path("icon") / f"{name}.png"
    if not path.exists():
        path = Path("icon") / f"{platform_name}.png"
    if not path.exists():
        path = Path("icon") / "claude.png"
    return FileResponse(path)


# ─── Usage API ────────────────────────────────────────────────────────────────

@app.get("/api/usage")
async def usage() -> list[PlatformResult]:
    enabled = get_enabled_platforms()
    if not cache:
        await refresh_all()
    return [v for k, v in cache.items() if k in enabled]


@app.post("/api/refresh")
async def refresh_now() -> list[PlatformResult]:
    return await refresh_all()


# ─── Platform management API ─────────────────────────────────────────────────

@app.get("/api/platforms")
async def list_platforms() -> list[dict]:
    """Return all known platforms with their enabled/ready/removed status."""
    config = load_config()
    enabled = config.get("enabled_platforms", [])
    removed = config.get("removed_platforms", [])
    return [
        {
            "id": name,
            "display_name": meta["display_name"],
            "type": meta["type"],
            "enabled": name in enabled,
            "ready": platform_is_ready(name),
            "removed": name in removed,
        }
        for name, meta in PLATFORM_META.items()
    ]


@app.post("/api/platforms/{name}/detect")
async def detect_and_enable(name: str) -> dict:
    """
    Check if a local-app platform is available on this machine.
    If found, enable it and trigger an immediate background refresh.
    """
    if name not in PLATFORM_META:
        raise HTTPException(status_code=404, detail="Unknown platform")

    ready = platform_is_ready(name)
    if not ready:
        display = PLATFORM_META[name]["display_name"]
        raise HTTPException(
            status_code=422,
            detail=f"{display} 未检测到，请确认 App 已安装并至少打开过一次。",
        )

    config = load_config()
    enabled: list[str] = config.get("enabled_platforms", [])
    if name not in enabled:
        enabled.append(name)
        config["enabled_platforms"] = enabled
        _save_config(config)

    # Kick off an immediate (non-blocking) refresh
    if name in PLATFORMS:
        asyncio.create_task(refresh_platform(PLATFORMS[name]))

    return {"ready": True, "enabled": enabled}


@app.post("/api/platforms/openrouter/apikey")
async def save_openrouter_apikey(payload: dict) -> dict:
    """Save OpenRouter Management API Key to config.json and enable the platform."""
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="api_key is required")
    if not api_key.startswith("sk-or-"):
        raise HTTPException(status_code=422, detail="Invalid key format — OpenRouter keys start with sk-or-")

    config = load_config()
    config["openrouter_api_key"] = api_key
    enabled: list[str] = config.get("enabled_platforms", [])
    removed: list[str] = config.get("removed_platforms", [])
    if "openrouter" in removed:
        removed.remove("openrouter")
        config["removed_platforms"] = removed
    if "openrouter" not in enabled:
        enabled.append("openrouter")
        config["enabled_platforms"] = enabled
    _save_config(config)

    asyncio.create_task(refresh_platform(PLATFORMS["openrouter"]))
    return {"ok": True}


@app.delete("/api/platforms/{name}")
async def remove_platform(name: str) -> dict:
    """Disable a platform, remove it from the dashboard, and add it to the
    removed_platforms blocklist so future credential imports won't re-enable it."""
    config = load_config()
    enabled: list[str] = config.get("enabled_platforms", [])
    if name in enabled:
        enabled.remove(name)
        config["enabled_platforms"] = enabled
    # Track explicitly removed platforms to prevent accidental re-import
    removed: list[str] = config.get("removed_platforms", [])
    if name not in removed:
        removed.append(name)
        config["removed_platforms"] = removed
    _save_config(config)
    cache.pop(name, None)
    return {"enabled": enabled}


# ─── Credentials import API ───────────────────────────────────────────────────

@app.post("/api/credentials/import")
async def import_credentials(
    file: UploadFile = File(...),
    enable_only: str | None = Form(None),
) -> dict:
    """
    Receive a credentials.json exported by the Chrome extension.
    Writes cookie and header files, updates config.json.

    enable_only: optional comma-separated list of platform IDs the user
    explicitly wants enabled from this import.  Platforms found in the JSON
    but NOT in enable_only are added to removed_platforms (blocklist) and
    will not be auto-enabled in future imports either.

    When enable_only is omitted the legacy behaviour applies: all ready
    platforms are enabled, EXCEPT those already in removed_platforms.
    """
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"JSON 格式错误: {exc}")

    # Write cookie files
    cookie_dir = ROOT / "cookie"
    cookie_dir.mkdir(exist_ok=True)
    for filename, text in data.get("cookies", {}).items():
        if text and text.strip():
            (cookie_dir / filename).write_text(text)

    # Write header files
    header_dir = ROOT / "header-txt"
    header_dir.mkdir(exist_ok=True)
    for filename, text in data.get("headers", {}).items():
        if text and text.strip():
            (header_dir / filename).write_text(text)

    # Update config (org ID, etc.)
    if data.get("claude_org_id"):
        config = load_config()
        config["claude_org_id"] = data["claude_org_id"]
        _save_config(config)

    # Parse the explicit selection sent by the frontend (if any)
    selected: set[str] | None = (
        {p.strip() for p in enable_only.split(",") if p.strip()}
        if enable_only is not None
        else None
    )

    config = load_config()
    enabled: list[str] = config.get("enabled_platforms", [])
    removed: list[str] = config.get("removed_platforms", [])
    newly_enabled: list[str] = []
    ready_platforms: list[str] = []

    for name in PLATFORMS:
        if not platform_is_ready(name):
            continue
        ready_platforms.append(name)

        if selected is not None:
            # User explicitly chose which platforms to enable from this JSON.
            if name in selected:
                # User wants this platform → enable it and lift any blocklist entry
                if name not in enabled:
                    enabled.append(name)
                    newly_enabled.append(name)
                if name in removed:
                    removed.remove(name)
            elif name not in enabled:
                # Platform is ready (its cookies were in the JSON) but the user
                # did NOT select it, and it isn't already active on the dashboard.
                # Add to blocklist so future imports won't auto-enable it.
                if name not in removed:
                    removed.append(name)
            # If the platform is already enabled but wasn't part of this import
            # selection, leave it completely untouched — this import is only
            # adding new platforms, not managing existing ones.
        else:
            # Legacy / no selection sent: enable anything ready that isn't blocked
            if name not in enabled and name not in removed:
                enabled.append(name)
                newly_enabled.append(name)

    config["enabled_platforms"] = enabled
    config["removed_platforms"] = removed
    _save_config(config)

    if newly_enabled:
        # Kick off immediate refresh for new platforms
        for name in newly_enabled:
            if name in PLATFORMS:
                asyncio.create_task(refresh_platform(PLATFORMS[name]))

    return {
        "newly_enabled": newly_enabled,
        "ready_platforms": ready_platforms,
        "all_enabled": enabled,
    }
