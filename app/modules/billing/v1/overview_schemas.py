"""Schemas for org billing overview API."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema

BillingOverviewPeriodLiteral = Literal["today", "yesterday", "last_7_days", "last_30_days"]


class BillingOverviewKpiItem(BaseSchema):
    value: str
    change_pct: str | None = None
    comparison_label: str


class BillingOverviewMeta(BaseSchema):
    period_start: str
    period_end: str
    prior_period_start: str
    prior_period_end: str
    timezone: str
    definitions_version: str
    chart_year: int


class BillingOverviewKpis(BaseSchema):
    total_billed: BillingOverviewKpiItem
    payments_received: BillingOverviewKpiItem
    outstanding_balance: BillingOverviewKpiItem
    overdue_amount: BillingOverviewKpiItem
    credit_notes_issued: BillingOverviewKpiItem
    refunds_issued: BillingOverviewKpiItem


class RevenueTrendMonth(BaseSchema):
    month: int = Field(ge=1, le=12)
    revenue: str
    refunds: str
    net_revenue: str


class PaymentMethodUsageItem(BaseSchema):
    method: Literal["CARD", "BANK_TRANSFER", "CASH"]
    amount: str
    percent: str


class InvoiceStatusChartItem(BaseSchema):
    status: str
    count: int
    total_value: str


class BillingActivityMonth(BaseSchema):
    month: int = Field(ge=1, le=12)
    invoices_amount: str
    invoices_count: int
    payments_amount: str
    payments_count: int


class BillingOverviewCharts(BaseSchema):
    revenue_trend: list[RevenueTrendMonth]
    payment_method_usage: list[PaymentMethodUsageItem]
    invoice_status: list[InvoiceStatusChartItem]
    billing_activity: list[BillingActivityMonth]


class BillingOverviewResponse(BaseSchema):
    meta: BillingOverviewMeta
    kpis: BillingOverviewKpis
    charts: BillingOverviewCharts
