import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DailyMetric(BaseModel):
    date: str  # "YYYY-MM-DD"
    spend: float
    revenue: float
    pipeline: float
    drop_rate: float
    impressions: int = 0
    clicks: int = 0
    interactions: int = 0
    leads: int = 0
    converted: int = 0
    lost: int = 0


class RagQueryRow(BaseModel):
    query: str
    product_title: str
    count: int
    conversion_rate: float = 0.0


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
    daily_metrics: list[DailyMetric]
    total_leads: int
    pipeline_value: float
    drop_rate: float
    catalog_published: int
    catalog_archived: int
    catalog_draft: int
    catalog_paused: int
    top_queries: list[RagQueryRow] = []
    converted_leads: int = 0
    pending_leads_count: int = 0
    reach_count: int = 0


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
    category: str | None = None
    converted: int = 0
    est_roas: float = 0.0
    est_ros: float = 0.0
    orders_count: int = 0
    trend: str | None = None
    primary_image_url: str | None = None
    daily_impressions: list[int] = []


class ProductPerformanceList(BaseModel):
    items: list[ProductPerformanceRow]
    start_date: datetime
    end_date: datetime


class RagQueryStat(BaseModel):
    query: str
    count: int
    conversion_rate: float = 0.0


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
    leads_count: int = 0
    converted_count: int = 0
    cost_per_lead: float = 0.0
    avg_sale: float = 0.0
    token_roas: float = 0.0
    realized_revenue: float = 0.0
    potential_pipeline: float = 0.0
    primary_image_url: str | None = None


class DiagnosticAlert(BaseModel):
    product_id: uuid.UUID
    title: str
    issue_type: Literal["zero_click", "low_ai_relevance", "missing_metadata"]
    detail: str


class DiagnosticsResponse(BaseModel):
    alerts: list[DiagnosticAlert]
