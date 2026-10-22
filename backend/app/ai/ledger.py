"""Pricing metadata and the AI usage ledger with atomic budget reservations.

Before every Claude API request an upper-bound cost is reserved; afterwards the
reservation is reconciled with the reported usage. Requests whose outcome is
unknown (timeouts, dropped connections) keep their reservation and are marked
"uncertain" until an owner reconciles them against the Claude Console. These
are estimates, not an invoice.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db import session_scope
from app.errors import AppError
from app.models import AIUsage, utcnow


class ModelRates(BaseModel):
    input: float = Field(ge=0)
    cache_write_5m: float = Field(ge=0)
    cache_write_1h: float = Field(ge=0)
    cache_read: float = Field(ge=0)
    output: float = Field(ge=0)


class Pricing(BaseModel):
    price_version: str
    currency: str
    unit: str
    source_url: str
    effective_date: str
    verified: bool = False
    notes: str = ""
    models: dict[str, ModelRates]

    @classmethod
    def load(cls, path: Path) -> "Pricing":
        pricing = cls.model_validate(json.loads(path.read_text()))
        if pricing.currency != "USD" or pricing.unit != "per_million_tokens" or not pricing.models:
            raise ValueError("pricing file must use USD per million tokens and list at least one model")
        return pricing

    def max_rates(self) -> ModelRates:
        return ModelRates(**{k: max(getattr(r, k) for r in self.models.values())
                             for k in ModelRates.model_fields})

    def rates_for(self, model: str) -> tuple[ModelRates, bool]:
        """Return (rates, known). Unknown models are priced at the highest listed rates."""
        if model in self.models:
            return self.models[model], True
        return self.max_rates(), False


@dataclass
class TokenUsage:
    model: str
    input_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0
    output_tokens: int = 0


def cost_of(pricing: Pricing, usage: TokenUsage) -> tuple[float, bool]:
    rates, known = pricing.rates_for(usage.model)
    cost = (usage.input_tokens * rates.input + usage.cache_write_5m * rates.cache_write_5m
            + usage.cache_write_1h * rates.cache_write_1h + usage.cache_read * rates.cache_read
            + usage.output_tokens * rates.output) / 1_000_000
    return cost, known


class BudgetExceeded(AppError):
    def __init__(self, period: str):
        message = ("Today's AI allowance has been reached. You can still browse versions, compare, and export."
                   if period == "daily" else
                   "This month's AI allowance has been reached. You can still browse versions, compare, and export.")
        super().__init__(f"ai_budget_{period}", message, status_code=429)


def _start_of_day(now: dt.datetime) -> dt.datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class Ledger:
    """Budgets apply to the whole application (the owner's funded workspace), across all users."""

    def __init__(self, pricing: Pricing, daily_budget: float, monthly_budget: float):
        self.pricing = pricing
        self.daily_budget = daily_budget
        self.monthly_budget = monthly_budget
        self._lock = threading.Lock()

    def upper_bound(self, model: str, input_tokens: int, max_output_tokens: int, fallback_possible: bool) -> float:
        rates = self.pricing.max_rates() if fallback_possible else self.pricing.rates_for(model)[0]
        # Input priced at the higher of base input and 1-hour cache-write rates, so cache writes are covered.
        input_rate = max(rates.input, rates.cache_write_1h)
        return (input_tokens * input_rate + max_output_tokens * rates.output) / 1_000_000

    def committed(self, since: dt.datetime) -> float:
        """Spend counted against budgets: reconciled cost plus open (reserved/uncertain) reservations."""
        with session_scope() as session:
            reconciled = session.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(
                AIUsage.created_at >= since, AIUsage.status == "reconciled")) or 0.0
            reserved = session.scalar(select(func.coalesce(func.sum(AIUsage.reserved_usd), 0.0)).where(
                AIUsage.created_at >= since, AIUsage.status.in_(("reserved", "uncertain")))) or 0.0
        return float(reconciled) + float(reserved)

    def reserve(self, *, user_id: str, turn_id: str | None, model: str, amount: float, purpose: str = "chat") -> str:
        now = utcnow()
        with self._lock:
            if self.committed(_start_of_day(now)) + amount > self.daily_budget:
                raise BudgetExceeded("daily")
            if self.committed(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)) + amount > \
                    self.monthly_budget:
                raise BudgetExceeded("monthly")
            with session_scope() as session:
                row = AIUsage(user_id=user_id, turn_id=turn_id, purpose=purpose, status="reserved",
                              requested_model=model, reserved_usd=amount,
                              price_version=self.pricing.price_version)
                session.add(row)
                session.flush()
                return row.id

    def reconcile(self, usage_id: str, *, request_id: str | None, served_model: str,
                  usages: list[TokenUsage], fallback_used: bool) -> float:
        total, all_known = 0.0, True
        for u in usages:
            cost, known = cost_of(self.pricing, u)
            total += cost
            all_known &= known
        with self._lock, session_scope() as session:
            row = session.get(AIUsage, usage_id)
            row.status = "reconciled"
            row.request_id = request_id
            row.served_model = served_model
            row.input_tokens = sum(u.input_tokens for u in usages)
            row.cache_write_5m_tokens = sum(u.cache_write_5m for u in usages)
            row.cache_write_1h_tokens = sum(u.cache_write_1h for u in usages)
            row.cache_read_tokens = sum(u.cache_read for u in usages)
            row.output_tokens = sum(u.output_tokens for u in usages)
            row.cost_usd = total
            row.fallback_used = fallback_used
            if not all_known:
                row.error_code = "unpriced_model_estimated_at_max_rate"
        return total

    def release(self, usage_id: str, error_code: str) -> None:
        """The request certainly incurred no usage (e.g. rejected before processing)."""
        with self._lock, session_scope() as session:
            row = session.get(AIUsage, usage_id)
            row.status, row.error_code, row.cost_usd = "released", error_code, 0.0

    def mark_uncertain(self, usage_id: str, error_code: str) -> None:
        """Usage is unknown (timeout/connection loss): keep the reservation counted against budgets."""
        with self._lock, session_scope() as session:
            row = session.get(AIUsage, usage_id)
            row.status, row.error_code = "uncertain", error_code

    def summary(self) -> dict[str, Any]:
        now = utcnow()
        day, month = _start_of_day(now), now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with session_scope() as session:
            def total(column, since, statuses):
                return float(session.scalar(select(func.coalesce(func.sum(column), 0.0)).where(
                    AIUsage.created_at >= since, AIUsage.status.in_(statuses))) or 0.0)
            uncertain = session.scalar(select(func.count()).select_from(AIUsage).where(
                AIUsage.status == "uncertain")) or 0
            recent = session.scalars(select(AIUsage).order_by(AIUsage.created_at.desc()).limit(25)).all()
            rows = [{"id": r.id, "created_at": r.created_at.isoformat(), "status": r.status,
                     "requested_model": r.requested_model, "served_model": r.served_model,
                     "request_id": r.request_id, "input_tokens": r.input_tokens,
                     "cache_write_tokens": r.cache_write_5m_tokens + r.cache_write_1h_tokens,
                     "cache_read_tokens": r.cache_read_tokens, "output_tokens": r.output_tokens,
                     "cost_usd": round(r.cost_usd, 6), "reserved_usd": round(r.reserved_usd, 6),
                     "fallback_used": r.fallback_used, "error_code": r.error_code} for r in recent]
            today_spent = total(AIUsage.cost_usd, day, ("reconciled",))
            today_open = total(AIUsage.reserved_usd, day, ("reserved", "uncertain"))
            month_spent = total(AIUsage.cost_usd, month, ("reconciled",))
            month_open = total(AIUsage.reserved_usd, month, ("reserved", "uncertain"))
        return {
            "label": "Estimated from token usage and the configured price table; not an invoice. "
                     "Reconcile with the Claude Console usage and cost pages.",
            "price_version": self.pricing.price_version, "prices_verified": self.pricing.verified,
            "price_source": self.pricing.source_url,
            "today": {"estimated_usd": round(today_spent, 4), "open_reservations_usd": round(today_open, 4),
                      "budget_usd": self.daily_budget},
            "month": {"estimated_usd": round(month_spent, 4), "open_reservations_usd": round(month_open, 4),
                      "budget_usd": self.monthly_budget},
            "uncertain_requests": uncertain, "recent": rows,
        }
