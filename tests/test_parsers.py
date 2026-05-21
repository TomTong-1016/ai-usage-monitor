from models import Metric, PlatformResult
from parsers import parse_antigravity, parse_claude, parse_codex, parse_deepseek, parse_kimi, parse_minimax, parse_trae


def test_metric_percent():
    m = Metric(platform="claude", label="5小时用量", used=2, total=100, unit="%", reset_time="2026-05-14T20:43:00Z")
    assert m.used == 2
    assert m.unit == "%"


def test_metric_dollar_no_total():
    m = Metric(platform="trae", label="Bonus", used=0, total=None, unit="$", reset_time=None)
    assert m.total is None


def test_platform_result_error():
    r = PlatformResult(platform="kimi", display_name="Kimi", metrics=[], error="Cookie 已失效", last_updated="2026-05-14T16:00:00Z")
    assert r.error == "Cookie 已失效"
    assert r.metrics == []


# Claude tests
def test_parse_claude_returns_two_metrics(claude_data):
    metrics = parse_claude(claude_data)
    assert len(metrics) == 2


def test_parse_claude_labels(claude_data):
    metrics = parse_claude(claude_data)
    labels = [m.label for m in metrics]
    assert "5小时用量" in labels
    assert "本周用量" in labels


def test_parse_claude_five_hour_value(claude_data):
    metrics = parse_claude(claude_data)
    m = next(m for m in metrics if m.label == "5小时用量")
    assert m.used == 28.0
    assert m.unit == "%"
    assert m.reset_time is not None


def test_parse_claude_seven_day_value(claude_data):
    metrics = parse_claude(claude_data)
    m = next(m for m in metrics if m.label == "本周用量")
    assert m.used == 64.0


def test_parse_codex_uses_codex_platform(claude_data):
    metrics = parse_codex(claude_data)

    assert len(metrics) == 2
    assert {m.platform for m in metrics} == {"codex"}


def test_parse_codex_wham_usage():
    data = {
        "rate_limit": {
            "primary_window": {"used_percent": 77, "reset_at": 1778825875},
            "secondary_window": {"used_percent": 57, "reset_at": 1779173505},
        }
    }

    metrics = parse_codex(data)

    five_hour = next(m for m in metrics if m.label == "5小时用量")
    weekly = next(m for m in metrics if m.label == "本周用量")
    assert five_hour.used == 77
    assert five_hour.reset_time == "2026-05-15T06:17:55+00:00"
    assert weekly.used == 57


# Kimi tests
def test_parse_kimi_returns_two_metrics(kimi_data):
    metrics = parse_kimi(kimi_data)
    assert len(metrics) == 2


def test_parse_kimi_labels(kimi_data):
    metrics = parse_kimi(kimi_data)
    labels = [m.label for m in metrics]
    assert "5小时用量" in labels
    assert "本周用量" in labels


def test_parse_kimi_weekly_value(kimi_data):
    metrics = parse_kimi(kimi_data)
    m = next(m for m in metrics if m.label == "本周用量")
    assert m.used == 53.0
    assert m.unit == "%"


def test_parse_kimi_five_hour_value(kimi_data):
    metrics = parse_kimi(kimi_data)
    m = next(m for m in metrics if m.label == "5小时用量")
    assert m.used == 59.0
    assert m.reset_time is not None


def test_parse_minimax_usage_count_means_remaining():
    data = {
        "model_remains": [
            {
                "current_interval_total_count": 1500,
                "current_interval_usage_count": 1480,
                "current_weekly_total_count": 15000,
                "current_weekly_usage_count": 14000,
                "end_time": 1778828400000,
                "weekly_end_time": 1779033600000,
            }
        ]
    }

    metrics = parse_minimax(data)

    five_hour = next(m for m in metrics if m.label == "5小时用量")
    weekly = next(m for m in metrics if m.label == "本周用量")
    assert five_hour.used == 1.3   # (1500-1480)/1500*100
    assert five_hour.total == 100.0
    assert weekly.used == 6.7      # (15000-14000)/15000*100
    assert weekly.total == 100.0


def test_parse_trae_usage_amounts_mean_used():
    data = {
        "user_entitlement_pack_list": [
            {
                "display_desc": "Pro plan",
                "status": 1,
                "is_hide": False,
                "expire_time": 1804837654,
                "entitlement_base_info": {"quota": {"basic_usage_limit": 20}},
                "usage": {"basic_usage_amount": 12.5, "bonus_usage_amount": 8.25},
            }
        ]
    }

    metrics = parse_trae(data)

    basic = next(m for m in metrics if m.label == "Basic")
    bonus = next(m for m in metrics if m.label == "Bonus")
    assert basic.used == 12.5
    assert basic.total == 20
    assert bonus.used == 8.25
    assert bonus.total == 60


def test_parse_deepseek_combines_summary_and_amount():
    data = {
        "summary": {
            "data": {
                "biz_data": {
                    "normal_wallets": [{"currency": "CNY", "balance": "97.2663809200000000"}],
                    "monthly_costs": [{"currency": "CNY", "amount": "0.3757946800000000"}],
                }
            }
        },
        "amount": {
            "data": {
                "biz_data": {
                    "total": [
                        {
                            "model": "deepseek-v4-pro",
                            "usage": [
                                {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "1376384"},
                                {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "88524"},
                                {"type": "RESPONSE_TOKEN", "amount": "7128"},
                                {"type": "REQUEST", "amount": "18"},
                            ],
                        },
                        {
                            "model": "deepseek-v4-flash",
                            "usage": [
                                {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "56704"},
                                {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "29265"},
                                {"type": "RESPONSE_TOKEN", "amount": "1323"},
                                {"type": "REQUEST", "amount": "3"},
                            ],
                        },
                    ]
                }
            }
        },
    }

    metrics = parse_deepseek(data)

    assert next(m for m in metrics if m.label == "充值余额").used == 97.27
    assert next(m for m in metrics if m.label == "本月消费").used == 0.38
    assert next(m for m in metrics if m.label == "deepseek-v4-pro 请求次数").used == 18
    assert next(m for m in metrics if m.label == "deepseek-v4-pro Tokens").used == 1472036
    assert next(m for m in metrics if m.label == "deepseek-v4-flash 请求次数").used == 3
    assert next(m for m in metrics if m.label == "deepseek-v4-flash Tokens").used == 87292


def test_parse_deepseek_reads_monthly_cost_endpoint_when_summary_lacks_cost():
    data = {
        "summary": {"code": 40002, "msg": "Missing Token", "data": None},
        "cost": {
            "data": {
                "biz_data": [
                    {
                        "total": [
                            {
                                "model": "deepseek-v4-pro",
                                "usage": [
                                    {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "0.0344096000000000"},
                                    {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "0.2655720000000000"},
                                    {"type": "RESPONSE_TOKEN", "amount": "0.0427680000000000"},
                                ],
                            },
                            {
                                "model": "deepseek-v4-flash",
                                "usage": [
                                    {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "0.0011340800000000"},
                                    {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "0.0292650000000000"},
                                    {"type": "RESPONSE_TOKEN", "amount": "0.0026460000000000"},
                                ],
                            },
                        ]
                    }
                ]
            }
        },
        "amount": {"data": {"biz_data": {"total": []}}},
    }

    metrics = parse_deepseek(data)

    assert next(m for m in metrics if m.label == "本月消费").used == 0.38


def test_parse_antigravity_reads_credits_and_model_quota():
    data = {
        "user_status": {
            "userStatus": {
                "userTier": {
                    "availableCredits": [
                        {"creditType": "CREDIT_TYPE_USE_AI", "creditAmount": 1234}
                    ]
                }
            }
        },
        "available_models": {
            "response": {
                "clientModelConfigs": [
                    {
                        "label": "Gemini 3.1 Pro (High)",
                        "quotaInfo": {
                            "remainingFraction": 0.25,
                            "resetTime": "2026-05-27T02:20:38Z",
                        },
                    }
                ]
            }
        },
    }

    metrics = parse_antigravity(data)

    assert next(m for m in metrics if m.label == "AI Credits").used == 1234
    model = next(m for m in metrics if m.label == "Gemini 3.1 Pro (High)")
    assert model.used == 75
    assert model.total == 100
    assert model.reset_time == "2026-05-27T02:20:38Z"
