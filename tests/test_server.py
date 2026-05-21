from fastapi.testclient import TestClient

import server
from models import Metric, PlatformResult


def test_usage_endpoint_returns_cached_results(monkeypatch):
    server.cache.clear()

    async def fake_refresh_all():
        result = PlatformResult(
            platform="claude",
            display_name="Claude",
            metrics=[
                Metric(
                    platform="claude",
                    label="5小时用量",
                    used=28,
                    total=100,
                    unit="%",
                    reset_time="2026-05-14T13:00:00Z",
                )
            ],
            last_updated="2026-05-14T16:00:00Z",
        )
        server.cache["claude"] = result
        return [result]

    monkeypatch.setattr(server, "refresh_all", fake_refresh_all)
    monkeypatch.setattr(server, "get_enabled_platforms", lambda: ["claude"])

    client = TestClient(server.app)
    response = client.get("/api/usage")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["platform"] == "claude"
    assert body[0]["metrics"][0]["label"] == "5小时用量"


def test_response_error_hint_reads_platform_error():
    raw = {"base_resp": {"status_code": 1004, "status_msg": "cookie is missing, log in again"}}

    assert server.response_error_hint(raw) == "cookie is missing, log in again"


def test_sort_metrics_puts_five_hour_before_weekly():
    metrics = [
        Metric(platform="kimi", label="本周用量", used=53, total=100, unit="%"),
        Metric(platform="kimi", label="5小时用量", used=0, total=100, unit="%"),
    ]

    sorted_metrics = server.sort_metrics(metrics)

    assert [metric.label for metric in sorted_metrics] == ["5小时用量", "本周用量"]
