import json
from pathlib import Path

import httpx
import pytest
import respx

from fetcher import (
    PlatformConfig,
    build_headers,
    fetch_deepseek_usage,
    fetch_platform,
    fetch_with_override,
    load_cookie_jar,
    load_request_override,
    merge_headers,
    parse_header_txt,
)
from scripts.curl_to_override import parse_curl


def write_cookie_file(path: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = ["# Netscape HTTP Cookie File"]
    for domain, name, value in rows:
        domain_specified = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(f"{domain}\t{domain_specified}\t/\tFALSE\t2147483647\t{name}\t{value}")
    path.write_text("\n".join(lines))


def test_load_cookie_jar(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    write_cookie_file(cookie_file, [(".example.com", "session", "abc")])

    jar = load_cookie_jar(cookie_file)

    assert any(cookie.name == "session" for cookie in jar)


def test_build_headers_adds_kimi_bearer(tmp_path):
    cookie_file = tmp_path / "kimi.txt"
    write_cookie_file(cookie_file, [("www.kimi.com", "kimi-auth", "token-123")])
    jar = load_cookie_jar(cookie_file)
    config = PlatformConfig(
        name="kimi",
        display_name="Kimi",
        cookie_file=str(cookie_file),
        url="https://www.kimi.com/api",
        method="POST",
    )

    headers = build_headers(config, jar)

    assert headers["authorization"] == "Bearer token-123"


def test_merge_headers_replaces_case_insensitive_duplicates():
    headers = merge_headers(
        {"user-agent": "default", "content-type": "application/json"},
        {"User-Agent": "captured", "Content-Type": "text/plain"},
    )

    assert headers == {"User-Agent": "captured", "Content-Type": "text/plain"}


@pytest.mark.asyncio
@respx.mock
async def test_fetch_platform_posts_json_with_cookies(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    write_cookie_file(cookie_file, [(".example.com", "session", "abc")])
    config = PlatformConfig(
        name="example",
        display_name="Example",
        cookie_file=str(cookie_file),
        url="https://example.com/usage",
        method="POST",
        json_body={"scope": [4]},
    )
    route = respx.post("https://example.com/usage").mock(return_value=httpx.Response(200, json={"ok": True}))

    data = await fetch_platform(config)

    assert data == {"ok": True}
    assert route.called


def test_load_request_override_missing():
    assert load_request_override("missing-platform") == {}


def test_load_request_override_ignores_wrong_platform_host(monkeypatch, tmp_path):
    import fetcher

    override_dir = tmp_path / "request_overrides"
    override_dir.mkdir()
    (override_dir / "trae.json").write_text(json.dumps({
        "url": "https://bailian-cs.console.aliyun.com/data/api.json",
        "method": "POST",
    }))
    monkeypatch.setattr(fetcher, "ROOT", tmp_path)

    assert load_request_override("trae") == {}


def test_parse_curl_preserves_cookie_header():
    override = parse_curl("curl 'https://example.com/api' -H 'accept: application/json' -b '_token=secret; group=1'")

    assert override["headers"]["Cookie"] == "_token=secret; group=1"


def test_parse_curl_preserves_form_body():
    override = parse_curl(
        "curl 'https://example.com/api' "
        "-H 'content-type: application/x-www-form-urlencoded' "
        "--data-raw 'params=%7B%7D&region=cn-beijing&sec_token=abc'"
    )

    assert override["method"] == "POST"
    assert override["body"] == "params=%7B%7D&region=cn-beijing&sec_token=abc"


def test_parse_header_txt_missing():
    assert parse_header_txt("missing-platform") == {}


def test_parse_header_txt_reconstructs_url_from_pseudo_headers(monkeypatch, tmp_path):
    import fetcher

    header_dir = tmp_path / "header-txt"
    header_dir.mkdir()
    (header_dir / "claude-header.txt").write_text(
        "\n".join([
            ":authority",
            "claude.ai",
            ":method",
            "GET",
            ":path",
            "/api/organizations/org-123/usage",
            ":scheme",
            "https",
            "accept",
            "application/json",
        ])
    )
    monkeypatch.setattr(fetcher, "ROOT", tmp_path)

    override = parse_header_txt("claude")

    assert override["url"] == "https://claude.ai/api/organizations/org-123/usage"
    assert override["method"] == "GET"
    assert override["headers"]["accept"] == "application/json"


def test_parse_header_txt_keeps_trae_cookie_header(monkeypatch, tmp_path):
    import fetcher

    header_dir = tmp_path / "header-txt"
    header_dir.mkdir()
    (header_dir / "trae-header.txt").write_text(
        "\n".join([
            ":authority",
            "api-sg-central.trae.ai",
            ":method",
            "POST",
            ":path",
            "/trae/api/v1/pay/user_current_entitlement_list",
            ":scheme",
            "https",
            "authorization",
            "Bearer token",
            "cookie",
            "session=abc",
        ])
    )
    monkeypatch.setattr(fetcher, "ROOT", tmp_path)

    override = parse_header_txt("trae")

    assert override["headers"]["cookie"] == "session=abc"


def test_parse_header_txt_forces_trae_entitlement_url(monkeypatch, tmp_path):
    import fetcher

    header_dir = tmp_path / "header-txt"
    header_dir.mkdir()
    (header_dir / "trae-header.txt").write_text(
        "\n".join([
            "Request URL",
            "https://api-sg-central.trae.ai/trae/api/v1/pay/query_user_usage_group_by_session",
            "Request Method",
            "POST",
            ":authority",
            "api-sg-central.trae.ai",
            ":method",
            "POST",
            ":path",
            "/trae/api/v1/pay/query_user_usage_group_by_session",
            ":scheme",
            "https",
            "authorization",
            "Bearer token",
        ])
    )
    monkeypatch.setattr(fetcher, "ROOT", tmp_path)

    override = parse_header_txt("trae")

    assert override["url"] == fetcher.PLATFORMS["trae"].url
    assert override["json_body"] == {"require_usage": True}
    assert override["_invalid_reason"] == "Trae entitlement request header not captured"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_with_override_allows_cookie_header_without_cookie_file():
    config = PlatformConfig(
        name="minimax",
        display_name="MiniMax",
        cookie_file="missing.txt",
        url="https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains",
    )
    override = {
        "headers": {"Cookie": "session=from-captured-request"},
        "url": config.url,
    }
    route = respx.get(config.url).mock(return_value=httpx.Response(200, json={"ok": True}))

    data = await fetch_with_override(config, override, timeout=5)

    assert data == {"ok": True}
    assert route.called


@pytest.mark.asyncio
async def test_fetch_deepseek_usage_classifies_endpoint_headers(monkeypatch):
    captured = {}

    def fake_parse_header_txt(name):
        mapping = {
            "deepseek": {"url": "https://platform.deepseek.com/api/v0/client/settings?scope=banner", "headers": {"Cookie": "x=1"}},
            "deepseek-header2": {"url": "https://platform.deepseek.com/api/v0/usage/cost?month=5&year=2026", "headers": {"Cookie": "x=1"}},
            "deepseek-header3": {"url": "https://platform.deepseek.com/api/v0/usage/amount?month=5&year=2026", "headers": {"Cookie": "x=1"}},
        }
        return mapping.get(name, {})

    async def fake_fetch_with_override(config, override, timeout):
        captured[override["url"]] = True
        return {"url": override["url"]}

    import fetcher

    monkeypatch.setattr(fetcher, "parse_header_txt", fake_parse_header_txt)
    monkeypatch.setattr(fetcher, "fetch_with_override", fake_fetch_with_override)
    config = PlatformConfig(
        name="deepseek",
        display_name="DeepSeek",
        cookie_file="missing.txt",
        url="https://platform.deepseek.com/api/v0/users/get_user_summary",
    )

    data = await fetch_deepseek_usage(config, timeout=5)

    assert data["summary"]["url"] == "https://platform.deepseek.com/api/v0/users/get_user_summary"
    assert data["cost"]["url"].startswith("https://platform.deepseek.com/api/v0/usage/cost")
    assert data["amount"]["url"].startswith("https://platform.deepseek.com/api/v0/usage/amount")
    assert "https://platform.deepseek.com/api/v0/client/settings?scope=banner" not in captured
