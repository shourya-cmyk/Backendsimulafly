import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AnalyticsSummary(BaseModel):
    total_products: int
    published_products: int
    impressions: int
    clicks: int
    ai_mentions: int
    ai_image_generations: int
    external_redirects: int
    total_spend: float
    ctr: float
    start_date: datetime
    end_date: datetime


class ProductPerformanceRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    title: str
    sku: str
    status: str
    impressions: int
    clicks: int
    ai_mentions: int
    ai_image_generations: int
    external_redirects: int
    spend: float
    ctr: float
    health_score: str


class ProductPerformanceList(BaseModel):
    items: list[ProductPerformanceRow]
    start_date: datetime
    end_date: datetime


class RagQueryStat(BaseModel):
    query: str
    count: int


class ProductAnalyticsDetail(BaseModel):
    product_id: uuid.UUID
    title: str
    sku: str
    status: str
    impressions: int
    clicks: int
    ai_mentions: int
    ai_image_generations: int
    external_redirects: int
    spend: float
    ctr: float
    health_score: str
    health_reason: str | None
    ai_relevance_score: float | None
    top_rag_queries: list[RagQueryStat]
    daily_impressions: list[int]
    daily_clicks: list[int]


class DiagnosticAlert(BaseModel):
    product_id: uuid.UUID
    title: str
    issue_type: Literal["zero_click", "low_ai_relevance", "missing_metadata"]
    detail: str


class DiagnosticsResponse(BaseModel):
    alerts: list[DiagnosticAlert]
