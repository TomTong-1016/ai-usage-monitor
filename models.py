from pydantic import BaseModel
from typing import Optional


class Metric(BaseModel):
    platform: str
    label: str
    used: float
    total: Optional[float] = None
    unit: str  # "%" or "$"
    reset_time: Optional[str] = None
    reset_times: Optional[list[str]] = None
    subtitle: Optional[str] = None


class PlatformResult(BaseModel):
    platform: str
    display_name: str
    metrics: list[Metric]
    error: Optional[str] = None
    last_updated: str
