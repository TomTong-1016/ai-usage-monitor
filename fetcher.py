from __future__ import annotations

import http.cookiejar
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).parent


def load_config() -> dict:
    """Load user config from config.json (not committed to git)."""
    config_path = ROOT / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


@dataclass(frozen=True)
class PlatformConfig:
    name: str
    display_name: str
    cookie_file: str
    url: str
    method: str = "GET"
    json_body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


PLATFORMS: dict[str, PlatformConfig] = {
    "claude": PlatformConfig(
        name="claude",
        display_name="Claude",
        cookie_file="claude.ai_cookies.txt",
        url="https://claude.ai/api/organizations/{claude_org_id}/usage",
        headers={
            "accept": "application/json",
            "referer": "https://claude.ai/",
        },
    ),
    "codex": PlatformConfig(
        name="codex",
        display_name="Codex",
        cookie_file="chatgpt.com_cookies.txt",
        url="https://chatgpt.com/backend-api/wham/usage",
        headers={
            "accept": "application/json",
            "referer": "https://chatgpt.com/",
        },
    ),
    "trae": PlatformConfig(
        name="trae",
        display_name="Trae",
        cookie_file="www.trae.ai_cookies.txt",
        url="https://api-sg-central.trae.ai/trae/api/v1/pay/user_current_entitlement_list",
        method="POST",
        json_body={"require_usage": True},
        headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json",
            "origin": "https://www.trae.ai",
            "referer": "https://www.trae.ai/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        },
    ),
    "minimax": PlatformConfig(
        name="minimax",
        display_name="MiniMax",
        cookie_file="platform.minimaxi.com_cookies.txt",
        url="https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains",
        headers={
            "accept": "application/json, text/plain, */*",
            "origin": "https://platform.minimaxi.com",
            "referer": "https://platform.minimaxi.com/",
        },
    ),
    "kimi": PlatformConfig(
        name="kimi",
        display_name="Kimi",
        cookie_file="www.kimi.com_cookies.txt",
        url="https://www.kimi.com/apiv2/kimi.gateway.billing.v1.BillingService/GetUsages",
        method="POST",
        json_body={"scope": [4]},
        headers={
            "accept": "application/json",
            "origin": "https://www.kimi.com",
            "referer": "https://www.kimi.com/",
        },
    ),
    "deepseek": PlatformConfig(
        name="deepseek",
        display_name="DeepSeek",
        cookie_file="platform.deepseek.com_cookies.txt",
        url="https://platform.deepseek.com/api/v0/users/get_user_summary",
        headers={
            "accept": "*/*",
            "referer": "https://platform.deepseek.com/usage",
        },
    ),
    "antigravity": PlatformConfig(
        name="antigravity",
        display_name="Google Antigravity",
        cookie_file="",
        url="local://antigravity",
    ),
    "openrouter": PlatformConfig(
        name="openrouter",
        display_name="OpenRouter",
        cookie_file="",
        url="https://openrouter.ai/api/v1/credits",
        headers={
            "accept": "application/json",
        },
    ),
    "cursor": PlatformConfig(
        name="cursor",
        display_name="Cursor",
        cookie_file="",
        url="local://cursor",
    ),
}


OVERRIDE_HOST_ALLOWLIST: dict[str, set[str]] = {
    "claude": {"claude.ai"},
    "codex": {"chatgpt.com"},
    "trae": {"api-sg-central.trae.ai", "www.trae.ai"},
    "minimax": {"www.minimaxi.com", "platform.minimaxi.com"},
    "kimi": {"www.kimi.com"},
    "deepseek": {"platform.deepseek.com"},
}


def load_cookie_jar(cookie_file: str | Path) -> http.cookiejar.MozillaCookieJar:
    path = Path(cookie_file)
    if not path.is_absolute():
        cookie_dir_path = ROOT / "cookie" / path
        path = cookie_dir_path if cookie_dir_path.exists() else ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Cookie file not found: {path.name}")

    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def cookie_value(jar: http.cookiejar.CookieJar, name: str) -> str | None:
    for cookie in jar:
        if cookie.name == name:
            return cookie.value
    return None


def build_headers(config: PlatformConfig, jar: http.cookiejar.CookieJar | None = None) -> dict[str, str]:
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        ),
        **config.headers,
    }

    if config.name == "kimi" and jar is not None:
        token = cookie_value(jar, "kimi-auth")
        if token:
            headers["authorization"] = f"Bearer {token}"

    return headers


def merge_headers(*header_sets: dict[str, str]) -> dict[str, str]:
    """Merge HTTP headers case-insensitively while preserving the latest spelling."""
    merged: dict[str, str] = {}
    canonical_keys: dict[str, str] = {}
    for headers in header_sets:
        for key, value in headers.items():
            lowered = key.lower()
            previous = canonical_keys.get(lowered)
            if previous is not None and previous in merged:
                del merged[previous]
            canonical_keys[lowered] = key
            merged[key] = value
    return merged


def load_request_override(name: str) -> dict[str, Any]:
    path = ROOT / "request_overrides" / f"{name}.json"
    if not path.exists():
        return {}
    override = json.loads(path.read_text())
    url = override.get("url")
    allowed_hosts = OVERRIDE_HOST_ALLOWLIST.get(name)
    if url and allowed_hosts and urlparse(url).hostname not in allowed_hosts:
        return {}
    return override


def parse_header_txt(name: str) -> dict[str, Any]:
    exact_path = ROOT / "header-txt" / f"{name}.txt"
    path = exact_path if exact_path.exists() else ROOT / "header-txt" / f"{name}-header.txt"
    if not path.exists():
        return {}

    lines = [line.strip() for line in path.read_text(errors="ignore").splitlines() if line.strip()]
    override: dict[str, Any] = {"headers": {}}

    pseudo_headers: dict[str, str] = {}
    for i, line in enumerate(lines[:-1]):
        lowered = line.lower()
        value = lines[i + 1]
        if lowered == "request url":
            override["url"] = value
            override["_captured_url"] = value
        elif lowered == "request method":
            override["method"] = value.upper()
        elif lowered in {":authority", ":method", ":path", ":scheme"}:
            pseudo_headers[lowered] = value

    if "url" not in override and {":authority", ":path"} <= pseudo_headers.keys():
        scheme = pseudo_headers.get(":scheme", "https")
        override["url"] = f"{scheme}://{pseudo_headers[':authority']}{pseudo_headers[':path']}"
        override["_captured_url"] = override["url"]
    if "method" not in override and ":method" in pseudo_headers:
        override["method"] = pseudo_headers[":method"].upper()

    request_start = next((i for i, line in enumerate(lines) if line.lower() == ":authority"), None)
    if request_start is None:
        return {key: value for key, value in override.items() if value}

    skip_headers = {":authority", ":method", ":path", ":scheme", "accept-encoding", "content-length"}
    i = request_start
    while i + 1 < len(lines):
        key = lines[i]
        value = lines[i + 1]
        lowered = key.lower()
        if lowered in skip_headers:
            i += 2
            continue
        if lowered.startswith(":"):
            i += 2
            continue
        override["headers"][key] = value
        i += 2

    if name == "trae":
        captured_url = override.get("_captured_url", "")
        if captured_url and "/trae/api/v1/pay/user_current_entitlement_list" not in captured_url:
            override["_invalid_reason"] = "Trae entitlement request header not captured"
        override["url"] = PLATFORMS["trae"].url
        override["method"] = "POST"
        override.setdefault("json_body", {"require_usage": True})

    return {key: value for key, value in override.items() if value}


def _has_cookie_header(override: dict[str, Any]) -> bool:
    headers = override.get("headers") or {}
    return any(key.lower() == "cookie" and value for key, value in headers.items())


def _load_cookie_jar_for_request(config: PlatformConfig, override: dict[str, Any]) -> http.cookiejar.CookieJar | None:
    try:
        return load_cookie_jar(config.cookie_file)
    except FileNotFoundError:
        if _has_cookie_header(override):
            return None
        raise


def merged_request_override(name: str) -> dict[str, Any]:
    header_override = parse_header_txt(name)
    file_override = load_request_override(name)
    headers = {
        **file_override.get("headers", {}),
        **header_override.get("headers", {}),
    }
    merged = {**file_override, **header_override}
    if headers:
        merged["headers"] = headers
    return merged


# ─── Claude org ID auto-discovery ────────────────────────────────────────────

async def _ensure_claude_org_id(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    """Return the Claude org ID, discovering and caching it if not already in config.json."""
    config_data = load_config()
    org_id = config_data.get("claude_org_id", "")
    if org_id:
        return org_id

    resp = await client.get("https://claude.ai/api/organizations", headers=headers)
    resp.raise_for_status()
    orgs = resp.json()
    if not orgs:
        raise ValueError("No Claude organizations found — make sure you are signed in.")
    org_id = orgs[0]["uuid"]

    # Cache to config.json so we don't need to rediscover on every start
    config_data["claude_org_id"] = org_id
    (ROOT / "config.json").write_text(
        json.dumps(config_data, indent=2, ensure_ascii=False) + "\n"
    )
    return org_id


def fetch_codex_app_cache_usage() -> dict[str, Any]:
    script = r"""
const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');

const dirs = [
  path.join(os.homedir(), 'Library/Application Support/Codex/Cache/Cache_Data'),
  path.join(os.homedir(), 'Library/Application Support/Codex/Partitions/codex-browser-app/Cache/Cache_Data'),
];

const files = [];
for (const dir of dirs) {
  if (!fs.existsSync(dir)) continue;
  for (const name of fs.readdirSync(dir)) {
    const file = path.join(dir, name);
    let stat;
    try {
      stat = fs.statSync(file);
    } catch {
      continue;
    }
    if (stat.isFile()) files.push({ file, mtimeMs: stat.mtimeMs });
  }
}
files.sort((a, b) => b.mtimeMs - a.mtimeMs);

function emitUsage(parsed) {
  if (parsed && parsed.rate_limit) {
    process.stdout.write(JSON.stringify({
      plan_type: parsed.plan_type,
      rate_limit: parsed.rate_limit,
      credits: parsed.credits,
      source: 'codex-app-cache',
    }));
    process.exit(0);
  }
}

for (const entry of files) {
  const file = entry.file;
  const data = fs.readFileSync(file);
  if (!data.includes(Buffer.from('backend-api/wham/usage'))) continue;
  for (let offset = 0; offset < data.length; offset++) {
    try {
      const text = zlib.brotliDecompressSync(data.subarray(offset)).toString('utf8');
      emitUsage(JSON.parse(text));
    } catch {}
  }
}
process.exit(2);
"""
    result = subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True, timeout=5)
    if result.returncode != 0 or not result.stdout.strip():
        raise FileNotFoundError("Codex App usage cache not found")
    return json.loads(result.stdout)


async def fetch_with_override(config: PlatformConfig, override: dict[str, Any], timeout: float) -> dict[str, Any]:
    jar = _load_cookie_jar_for_request(config, override)
    headers = merge_headers(build_headers(config, jar), override.get("headers", {}))
    url = override.get("url", config.url)
    method = override.get("method", config.method).upper()
    json_body = override.get("json_body", config.json_body)
    raw_body = override.get("body")

    async with httpx.AsyncClient(cookies=jar, timeout=timeout, follow_redirects=True, headers=headers) as client:
        if method == "POST":
            if raw_body is not None:
                response = await client.post(url, content=raw_body, headers=headers)
            else:
                response = await client.post(url, json=json_body or {}, headers=headers)
        else:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            snippet = " ".join(response.text[:240].split())
            raise ValueError(f"Non-JSON response from {config.name}: HTTP {response.status_code} {snippet}")
        return response.json()


async def fetch_deepseek_usage(config: PlatformConfig, timeout: float) -> dict[str, Any]:
    overrides = [
        parse_header_txt("deepseek"),
        parse_header_txt("deepseek-header2"),
        parse_header_txt("deepseek-header3"),
        parse_header_txt("deepseek-summary"),
        parse_header_txt("deepseek-cost"),
        parse_header_txt("deepseek-amount"),
    ]
    requests: dict[str, dict[str, Any]] = {}
    fallback_override: dict[str, Any] | None = None
    authenticated_fallback: dict[str, Any] | None = None

    for override in overrides:
        if not override:
            continue
        fallback_override = fallback_override or override
        if any(key.lower() == "authorization" for key in (override.get("headers") or {})):
            authenticated_fallback = authenticated_fallback or override
        url = override.get("url", "")
        if "/api/v0/users/get_user_summary" in url:
            requests["summary"] = override
        elif "/api/v0/usage/cost" in url:
            requests["cost"] = override
        elif "/api/v0/usage/amount" in url:
            requests["amount"] = override

    if "summary" not in requests and (authenticated_fallback or fallback_override):
        requests["summary"] = {**(authenticated_fallback or fallback_override), "url": config.url, "method": "GET"}

    return {
        key: await fetch_with_override(config, override, timeout)
        for key, override in requests.items()
    }


def antigravity_ports() -> list[int]:
    result = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return []

    ports: list[int] = []
    for line in result.stdout.splitlines():
        if "language_" not in line and "language_server" not in line:
            continue
        match = re.search(r"127\.0\.0\.1:(\d+)\s+\(LISTEN\)", line)
        if match:
            ports.append(int(match.group(1)))
    return ports


async def _antigravity_rpc(
    client: httpx.AsyncClient,
    base_url: str,
    csrf_token: str,
    method: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{base_url}/exa.language_server_pb.LanguageServerService/{method}",
        json={},
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": csrf_token,
        },
    )
    response.raise_for_status()
    return response.json()


async def fetch_antigravity_usage(timeout: float) -> dict[str, Any]:
    ports = antigravity_ports()
    if not ports:
        raise FileNotFoundError("Antigravity App language server not found")

    async with httpx.AsyncClient(verify=False, timeout=timeout, follow_redirects=True) as client:
        last_error: Exception | None = None
        for port in ports:
            base_url = f"https://127.0.0.1:{port}"
            try:
                home = await client.get(base_url)
                home.raise_for_status()
                match = re.search(r'"csrfToken":"([^"]+)"', home.text)
                if not match:
                    continue
                csrf_token = match.group(1)
                return {
                    "user_status": await _antigravity_rpc(client, base_url, csrf_token, "GetUserStatus"),
                    "available_models": await _antigravity_rpc(client, base_url, csrf_token, "GetAvailableModels"),
                    "source": "antigravity-local-language-server",
                }
            except Exception as exc:
                last_error = exc
                continue

    if last_error:
        raise FileNotFoundError(f"Antigravity App usage endpoint not reachable: {last_error}") from last_error
    raise FileNotFoundError("Antigravity App usage endpoint not found")


def _cursor_db_conn():
    """Return a read-only SQLite connection to Cursor's local state database."""
    import sqlite3 as _sqlite3
    db_path = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    if not db_path.exists():
        raise FileNotFoundError(
            "Cursor database not found — make sure Cursor is installed and you've signed in at least once"
        )
    uri = db_path.as_uri() + "?mode=ro"
    try:
        return _sqlite3.connect(uri, uri=True)
    except _sqlite3.OperationalError:
        return _sqlite3.connect(str(db_path))


def _jwt_decode_payload(token: str) -> dict:
    """Decode the payload segment of a JWT without verifying the signature."""
    import base64 as _base64
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(_base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _jwt_user_id(token: str) -> str:
    """Extract userId from JWT sub claim: 'auth0|user_xxx' → 'user_xxx'."""
    sub = _jwt_decode_payload(token).get("sub", "")
    return sub.split("|")[-1] if "|" in sub else sub


def _jwt_is_expired(token: str) -> bool:
    """Return True if the JWT has an exp claim that is already in the past."""
    import time as _time
    exp = _jwt_decode_payload(token).get("exp")
    return bool(exp and _time.time() > exp)


def fetch_cursor_token() -> str:
    """Read Cursor credentials from local SQLite and return a valid WorkosCursorSessionToken.

    The cookie format cursor.com expects is:  userId::accessToken
    where userId is extracted from the JWT sub claim.

    If the stored accessToken is expired, we fall back to the refreshToken
    (Cursor keeps both in SQLite).  The refreshToken is long-lived and also
    accepted as the WorkosCursorSessionToken session value.
    """
    conn = _cursor_db_conn()
    try:
        cur = conn.cursor()

        def _read(key: str) -> str:
            row = cur.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
            return (row[0] or "").strip() if row else ""

        access_token = _read("cursorAuth/accessToken")
        refresh_token = _read("cursorAuth/refreshToken")

        if not access_token and not refresh_token:
            raise FileNotFoundError(
                "Cursor auth token not found in database — please sign in to Cursor"
            )

        # Pick whichever token is still valid; prefer access_token.
        for token in (access_token, refresh_token):
            if not token:
                continue
            # Already composed (shouldn't happen, but safe)
            if "::" in token:
                return token
            if _jwt_is_expired(token):
                continue
            user_id = _jwt_user_id(token)
            if user_id:
                return f"{user_id}::{token}"

        # All tokens appear expired — use the best one we have and let the API decide
        token = access_token or refresh_token
        user_id = _jwt_user_id(token)
        if user_id:
            return f"{user_id}::{token}"
        return token
    finally:
        conn.close()


async def fetch_cursor_usage(timeout: float) -> dict[str, Any]:
    """Fetch Cursor usage via urllib (matches what the browser sends; httpx gets 401)."""
    import asyncio as _asyncio
    import urllib.request as _urlreq
    import urllib.error as _urlerr

    token = fetch_cursor_token()
    user_id = token.split("::")[0] if "::" in token else ""

    def _get(url: str) -> dict:
        req = _urlreq.Request(url, headers={
            "Accept": "application/json",
            "Cookie": f"WorkosCursorSessionToken={token}",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            ),
            "Referer": "https://cursor.com/dashboard/usage",
        })
        with _urlreq.urlopen(req, timeout=int(timeout)) as r:
            return json.loads(r.read())

    def _sync_fetch() -> dict[str, Any]:
        import time as _time
        from datetime import datetime, timezone

        # /api/usage?user=<id>  — per-model request counts
        usage_url = f"https://cursor.com/api/usage?user={user_id}" if user_id else "https://cursor.com/api/usage"
        try:
            usage = _get(usage_url)
        except _urlerr.HTTPError as exc:
            raise httpx.HTTPStatusError(
                str(exc), request=httpx.Request("GET", usage_url),
                response=httpx.Response(exc.code),
            ) from exc

        # /api/usage-summary  — higher-level summary (fast/slow request quotas for paid plans)
        summary: dict = {}
        try:
            summary = _get("https://cursor.com/api/usage-summary")
        except Exception:
            pass

        # /api/auth/stripe  — subscription / membership info
        stripe: dict = {}
        try:
            stripe = _get("https://cursor.com/api/auth/stripe")
        except Exception:
            pass

        # /api/dashboard/get-filtered-usage-events  — actual request count for free users
        # Free users have plan.limit == 0 in summary; paid users show dollar-based metrics instead.
        events_total: int | None = None
        try:
            individual = (summary.get("individualUsage") or {})
            plan = individual.get("plan") or {}
            plan_limit = float(plan.get("limit") or 0)
            billing_start = summary.get("billingCycleStart")

            if plan_limit == 0 and billing_start:
                dt = datetime.fromisoformat(billing_start.replace("Z", "+00:00"))
                start_ms = str(int(dt.timestamp() * 1000))
                end_ms = str(int(_time.time() * 1000))

                def _post_events(page: int) -> dict:
                    body = json.dumps({
                        "teamId": 0,
                        "startDate": start_ms,
                        "endDate": end_ms,
                        "page": page,
                        "pageSize": 100,
                    }).encode()
                    req = _urlreq.Request(
                        "https://cursor.com/api/dashboard/get-filtered-usage-events",
                        data=body,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "Cookie": f"WorkosCursorSessionToken={token}",
                            "User-Agent": (
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
                            ),
                            "Referer": "https://cursor.com/cn/dashboard/usage",
                            "Origin": "https://cursor.com",
                        },
                    )
                    with _urlreq.urlopen(req, timeout=int(timeout)) as r:
                        return json.loads(r.read())

                data = _post_events(1)
                # API returns totalUsageEventsCount directly — no need to paginate
                events_total = int(data.get("totalUsageEventsCount") or len(data.get("usageEventsDisplay") or []))
        except Exception:
            pass

        return {"usage": usage, "summary": summary, "stripe": stripe, "events_total": events_total}

    return await _asyncio.to_thread(_sync_fetch)


async def fetch_openrouter_usage(timeout: float) -> dict[str, Any]:
    api_key = load_config().get("openrouter_api_key", "")
    if not api_key:
        raise FileNotFoundError("OpenRouter Management API Key not configured")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {api_key}", "accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


async def fetch_platform(
    config: PlatformConfig,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    if config.name == "codex":
        try:
            return fetch_codex_app_cache_usage()
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise FileNotFoundError(f"Codex App usage cache not found: {exc}") from exc
    if config.name == "deepseek":
        return await fetch_deepseek_usage(config, timeout)
    if config.name == "antigravity":
        return await fetch_antigravity_usage(timeout)
    if config.name == "openrouter":
        return await fetch_openrouter_usage(timeout)
    if config.name == "cursor":
        return await fetch_cursor_usage(timeout)

    override = merged_request_override(config.name)
    if config.name == "trae" and override.get("_invalid_reason"):
        raise FileNotFoundError(
            "Trae 用量请求头未捕获：请重新加载 Chrome 插件后访问 Trae 账户/用量页面，"
            "等待 user_current_entitlement_list 请求出现，再导出 credentials.json。"
        )
    jar = _load_cookie_jar_for_request(config, override)
    headers = merge_headers(build_headers(config, jar), override.get("headers", {}))
    raw_url = override.get("url", config.url)

    # For Claude, auto-discover org ID from the API if it's not yet in config.json
    if config.name == "claude" and "{claude_org_id}" in raw_url:
        _tmp_client = httpx.AsyncClient(cookies=jar, timeout=timeout, follow_redirects=True)
        try:
            org_id = await _ensure_claude_org_id(_tmp_client, headers)
        finally:
            await _tmp_client.aclose()
        raw_url = raw_url.replace("{claude_org_id}", org_id)

    # Substitute any remaining user-specific placeholders from config.json
    try:
        url = raw_url.format(**load_config()) if "{" in raw_url else raw_url
    except KeyError as exc:
        raise ValueError(
            f"Missing config key {exc} required by {config.name}. "
            "Run the Chrome extension to export credentials and generate config.json."
        ) from exc
    method = override.get("method", config.method).upper()
    json_body = override.get("json_body", config.json_body)
    raw_body = override.get("body")

    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            cookies=jar,
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        )

    try:
        if method == "POST":
            if raw_body is not None:
                response = await client.post(url, content=raw_body, headers=headers)
            else:
                response = await client.post(url, json=json_body or {}, headers=headers)
        else:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            snippet = " ".join(response.text[:240].split())
            raise ValueError(f"Non-JSON response from {config.name}: HTTP {response.status_code} {snippet}")
        return response.json()
    finally:
        if close_client:
            await client.aclose()
