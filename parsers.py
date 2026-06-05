from models import Metric
from datetime import datetime, timezone


def _num(value, default=0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _first(data: dict, names: tuple[str, ...], default=None):
    for name in names:
        if name in data:
            return data[name]
    return default


def _time(value) -> str | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    return str(value)


def _parse_usage_windows(data: dict, platform: str) -> list[Metric]:
    metrics = []
    for key, label in [("five_hour", "5小时用量"), ("seven_day", "本周用量")]:
        section = data.get(key)
        if not section:
            continue
        metrics.append(Metric(
            platform=platform,
            label=label,
            used=float(section["utilization"]),
            total=100.0,
            unit="%",
            reset_time=section.get("resets_at"),
        ))
    return metrics


def parse_claude(data: dict) -> list[Metric]:
    return _parse_usage_windows(data, "claude")


def parse_codex(data: dict) -> list[Metric]:
    rate_limit = data.get("rate_limit")
    if isinstance(rate_limit, dict):
        primary = rate_limit.get("primary_window") or {}
        secondary = rate_limit.get("secondary_window") or {}
        metrics = []
        if primary:
            metrics.append(Metric(
                platform="codex",
                label="5小时用量",
                used=_num(primary.get("used_percent")),
                total=100.0,
                unit="%",
                reset_time=_time(primary.get("reset_at")),
            ))
        if secondary:
            metrics.append(Metric(
                platform="codex",
                label="本周用量",
                used=_num(secondary.get("used_percent")),
                total=100.0,
                unit="%",
                reset_time=_time(secondary.get("reset_at")),
            ))
        return metrics
    return _parse_usage_windows(data, "codex")


def parse_kimi(data: dict) -> list[Metric]:
    usages = data.get("usages", [])
    if not usages:
        return []
    entry = usages[0]
    metrics = []

    # Weekly usage — entry.detail
    weekly = entry.get("detail", {})
    if weekly:
        used = float(weekly.get("used", 0))
        limit = float(weekly.get("limit", 100))
        pct = (used / limit * 100) if limit > 0 else 0
        metrics.append(Metric(
            platform="kimi",
            label="本周用量",
            used=round(pct, 1),
            total=100.0,
            unit="%",
            reset_time=weekly.get("resetTime"),
        ))

    # 5-hour usage — entry.limits[0].detail (300 min window)
    limits = entry.get("limits", [])
    if limits:
        hourly = limits[0].get("detail", {})
        used = float(hourly.get("used", 0))
        limit = float(hourly.get("limit", 100))
        pct = (used / limit * 100) if limit > 0 else 0
        metrics.append(Metric(
            platform="kimi",
            label="5小时用量",
            used=round(pct, 1),
            total=100.0,
            unit="%",
            reset_time=hourly.get("resetTime"),
        ))

    return metrics


def parse_trae(data: dict) -> list[Metric]:
    packs = data.get("user_entitlement_pack_list") or []
    visible = [pack for pack in packs if not pack.get("is_hide")]
    plan = next((pack for pack in visible if pack.get("status") == 1), visible[0] if visible else None)
    if not plan:
        return []

    quota = plan.get("quota") or (plan.get("entitlement_base_info") or {}).get("quota") or {}
    usage = plan.get("usage") or {}
    total = _num(quota.get("basic_usage_limit"))
    basic_used = _num(usage.get("basic_usage_amount"))
    bonus_used = _num(usage.get("bonus_usage_amount"))
    reset_time = _time(plan.get("expire_time") or plan.get("yearly_expire_time"))

    metrics = []
    if total > 0:
        metrics.append(Metric(
            platform="trae",
            label="Basic",
            used=basic_used,
            total=total,
            unit="$",
            reset_time=reset_time,
        ))
    metrics.append(Metric(platform="trae", label="Bonus", used=bonus_used, total=None, unit="$", reset_time=reset_time))
    return metrics


def parse_minimax(data: dict) -> list[Metric]:
    remains = data.get("model_remains") or {}
    items = remains if isinstance(remains, list) else list(remains.values()) if isinstance(remains, dict) else []
    item = next((entry for entry in items if isinstance(entry, dict) and entry.get("current_interval_total_count")), None)
    if not item:
        return []

    total_5h = _num(item.get("current_interval_total_count"))
    remaining_5h = _num(item.get("current_interval_usage_count"))
    pct_5h = round(((total_5h - remaining_5h) / total_5h * 100) if total_5h > 0 else 0, 1)

    total_wk = _num(item.get("current_weekly_total_count"))
    remaining_wk = _num(item.get("current_weekly_usage_count"))
    pct_wk = round(((total_wk - remaining_wk) / total_wk * 100) if total_wk > 0 else 0, 1)

    return [
        Metric(
            platform="minimax",
            label="5小时用量",
            used=pct_5h,
            total=100.0,
            unit="%",
            reset_time=_time(item.get("end_time")),
        ),
        Metric(
            platform="minimax",
            label="本周用量",
            used=pct_wk,
            total=100.0,
            unit="%",
            reset_time=_time(item.get("weekly_end_time")),
        ),
    ]


def _deepseek_biz_data(response: dict):
    return ((response.get("data") or {}).get("biz_data"))


def _usage_amounts(entry: dict) -> dict[str, float]:
    return {item.get("type"): _num(item.get("amount")) for item in entry.get("usage", [])}


def parse_deepseek(data: dict) -> list[Metric]:
    summary = _deepseek_biz_data(data.get("summary") or {}) or {}
    cost = _deepseek_biz_data(data.get("cost") or {}) or []
    amount = _deepseek_biz_data(data.get("amount") or {}) or {}

    normal_wallets = summary.get("normal_wallets") or []
    monthly_costs = summary.get("monthly_costs") or []
    balance = _num((normal_wallets[0] if normal_wallets else {}).get("balance"))
    monthly_cost = _num((monthly_costs[0] if monthly_costs else {}).get("amount"))
    if not monthly_cost and isinstance(cost, list) and cost:
        monthly_cost = sum(
            _num(item.get("amount"))
            for entry in (cost[0].get("total") or [])
            for item in (entry.get("usage") or [])
        )

    metrics = [
        Metric(platform="deepseek", label="充值余额", used=round(balance, 2), total=None, unit="CNY"),
        Metric(platform="deepseek", label="本月消费", used=round(monthly_cost, 2), total=None, unit="CNY"),
    ]

    for entry in amount.get("total") or []:
        model = entry.get("model")
        if model not in {"deepseek-v4-pro", "deepseek-v4-flash"}:
            continue
        usage = _usage_amounts(entry)
        token_total = (
            usage.get("PROMPT_TOKEN", 0)
            + usage.get("PROMPT_CACHE_HIT_TOKEN", 0)
            + usage.get("PROMPT_CACHE_MISS_TOKEN", 0)
            + usage.get("RESPONSE_TOKEN", 0)
        )
        metrics.append(Metric(platform="deepseek", label=f"{model} 请求次数", used=usage.get("REQUEST", 0), total=None, unit="次"))
        metrics.append(Metric(platform="deepseek", label=f"{model} Tokens", used=token_total, total=None, unit="tokens"))

    return metrics


def _antigravity_model_configs(data: dict) -> list[dict]:
    available_models = (data.get("available_models") or {}).get("response") or {}
    configs = available_models.get("clientModelConfigs") or []
    if configs:
        return configs

    user_status = ((data.get("user_status") or {}).get("userStatus") or {})
    cascade = user_status.get("cascadeModelConfigData") or {}
    return cascade.get("clientModelConfigs") or []


def parse_antigravity(data: dict) -> list[Metric]:
    return _parse_antigravity_with_platform(data, "antigravity")


def parse_antigravity_ide(data: dict) -> list[Metric]:
    return _parse_antigravity_with_platform(data, "antigravity_ide")


def _parse_antigravity_with_platform(data: dict, platform: str) -> list[Metric]:
    metrics: list[Metric] = []
    user_status = ((data.get("user_status") or {}).get("userStatus") or {})
    user_tier = user_status.get("userTier") or {}

    ai_credit = next(
        (
            credit
            for credit in user_tier.get("availableCredits") or []
            if credit.get("creditType") == "CREDIT_TYPE_USE_AI" or credit.get("creditType") == 1
        ),
        None,
    )
    if ai_credit:
        credit_amount = ai_credit.get("creditAmount")
        if credit_amount is not None:
            metrics.append(Metric(
                platform=platform,
                label="AI Credits",
                used=_num(credit_amount),
                total=None,
                unit="credits",
            ))

    seen = set()
    for config in _antigravity_model_configs(data):
        label = config.get("label") or config.get("displayName")
        quota = config.get("quotaInfo") or {}
        remaining = quota.get("remainingFraction")
        if not label or remaining is None or label in seen:
            continue
        seen.add(label)
        used_pct = max(0.0, min(100.0, round((1 - _num(remaining, 1.0)) * 100, 1)))
        metrics.append(Metric(
            platform=platform,
            label=label,
            used=used_pct,
            total=100.0,
            unit="%",
            reset_time=_time(quota.get("resetTime")),
        ))

    return metrics


def parse_openrouter(data: dict) -> list[Metric]:
    d = data.get("data", {})
    total = float(d.get("total_credits", 0) or 0)
    used  = float(d.get("total_usage",   0) or 0)
    remaining = round(total - used, 4)
    return [
        Metric(platform="openrouter", label="当前余额",   used=remaining,      total=None, unit="$"),
        Metric(platform="openrouter", label="累计消费",   used=round(used, 4), total=None, unit="$"),
        Metric(platform="openrouter", label="累计充值",   used=round(total, 4),total=None, unit="$"),
    ]


def _cursor_next_reset(start_of_month_iso: str | None) -> str | None:
    """Given the startOfMonth ISO string, return next month's date as the reset time."""
    if not start_of_month_iso:
        return None
    try:
        dt = datetime.fromisoformat(start_of_month_iso.replace("Z", "+00:00"))
        # Advance by one month
        month = dt.month % 12 + 1
        year = dt.year + (1 if dt.month == 12 else 0)
        next_reset = dt.replace(year=year, month=month)
        return next_reset.isoformat()
    except Exception:
        return None


def parse_cursor(data: dict) -> list[Metric]:
    usage   = data.get("usage")   or {}
    summary = data.get("summary") or {}
    metrics: list[Metric] = []

    # billingCycleEnd from /api/usage-summary is the accurate reset date.
    # Fall back to computing it from startOfMonth when summary is unavailable.
    reset_time = summary.get("billingCycleEnd") or _cursor_next_reset(usage.get("startOfMonth"))

    # ── /api/usage-summary — primary source for paid plans ───────────────────
    individual = summary.get("individualUsage") or {}
    plan       = individual.get("plan")     or {}
    on_demand  = individual.get("onDemand") or {}

    plan_used  = _num(plan.get("used",  0))
    plan_limit = _num(plan.get("limit") or 0)
    total_pct  = _num(plan.get("totalPercentUsed", 0))

    if plan_limit > 0:
        # Paid plan: dollar-based quota (Pro / Business / Team)
        metrics.append(Metric(
            platform="cursor",
            label="套餐用量",
            used=round(total_pct, 1),
            total=100.0,
            unit="%",
            reset_time=reset_time,
        ))
        metrics.append(Metric(
            platform="cursor",
            label="本月消费",
            used=round(plan_used, 2),
            total=round(plan_limit, 2),
            unit="$",
            reset_time=reset_time,
        ))

    # On-demand (pay-as-you-go) spend — only show when non-zero
    if on_demand.get("enabled") and _num(on_demand.get("used", 0)) > 0:
        metrics.append(Metric(
            platform="cursor",
            label="按需消费",
            used=round(_num(on_demand["used"]), 2),
            total=None,
            unit="$",
            reset_time=reset_time,
        ))

    # ── /api/usage — fallback: per-model request counts (free tier) ──────────
    if not metrics:
        # Prefer the accurate total from get-filtered-usage-events (free users only)
        events_total = data.get("events_total")
        if events_total is not None:
            metrics.append(Metric(
                platform="cursor",
                label="本月请求",
                used=int(events_total),
                total=None,
                unit="次",
                reset_time=reset_time,
            ))
        else:
            label_map = {
                "gpt-4":             "GPT-4 用量",
                "gpt-3.5-turbo":     "GPT-3.5 用量",
                "claude-3.5-sonnet": "Sonnet 用量",
                "claude-3-opus":     "Opus 用量",
            }
            has_model_data = False
            total_requests = 0
            for model_key, model_data in usage.items():
                if not isinstance(model_data, dict):
                    continue
                num_req = model_data.get("numRequests")
                if num_req is None:
                    continue
                has_model_data = True
                max_req = model_data.get("maxRequestUsage")
                if max_req:
                    pct = round(_num(num_req) / _num(max_req) * 100, 1)
                    label = label_map.get(model_key, f"{model_key} 用量")
                    metrics.append(Metric(
                        platform="cursor",
                        label=label,
                        used=pct,
                        total=100.0,
                        unit="%",
                        reset_time=reset_time,
                    ))
                else:
                    total_requests += int(_num(num_req))

            if has_model_data and not any(m.unit == "%" for m in metrics):
                metrics.append(Metric(
                    platform="cursor",
                    label="本月请求",
                    used=total_requests,
                    total=None,
                    unit="次",
                    reset_time=reset_time,
                ))

    return metrics


def parse_siliconflow(data: dict) -> list[Metric]:
    UNIT = 1e12  # values are in pico-yuan; divide to get CNY
    metrics = []

    financial = ((data.get("peek") or {}).get("data") or {}).get("financialInfo") or {}
    available = float(financial.get("available") or 0) / UNIT
    metrics.append(Metric(platform="siliconflow", label="账户余额", used=round(available, 4), total=None, unit="CNY"))

    wallets = ((data.get("wallets") or {}).get("data") or {}).get("wallets") or []
    if wallets:
        coupon_total = sum(float(w.get("balance") or 0) / UNIT for w in wallets)
        earliest_expiry = min((w["expiresAt"] for w in wallets if w.get("expiresAt")), default=None)
        metrics.append(Metric(
            platform="siliconflow",
            label="优惠券余额",
            used=round(coupon_total, 4),
            total=None,
            unit="CNY",
            reset_time=_time(earliest_expiry),
        ))

    return metrics


PARSERS = {
    "claude": parse_claude,
    "codex": parse_codex,
    "kimi": parse_kimi,
    "trae": parse_trae,
    "minimax": parse_minimax,
    "deepseek": parse_deepseek,
    "antigravity": parse_antigravity,
    "antigravity_ide": parse_antigravity_ide,
    "openrouter": parse_openrouter,
    "cursor": parse_cursor,
    "siliconflow": parse_siliconflow,
}
