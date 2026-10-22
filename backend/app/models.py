"""ORM models for persisted application state."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workbook(Base):
    __tablename__ = "workbooks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("wb"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="inspecting")  # inspecting | ready | rejected | failed
    compatibility_status: Mapped[str] = mapped_column(String, default="pending")
    # pending | editable | inspect_only | needs_baseline_confirmation | rejected
    compatibility_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    inspection: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reference_outputs: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    baseline_confirmed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    active_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active_mapping_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    original_sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkbookVersion(Base):
    __tablename__ = "workbook_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ver"))
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)  # original | scenario
    label: Mapped[str] = mapped_column(String)
    file_path: Mapped[str] = mapped_column(String)  # exported artifact, relative to data_dir
    # Workbook whose stored results are read for this version: the recalculated artifact for scenario
    # versions, the round-trip recalculation for the original, or the original itself when inspect-only.
    values_file_path: Mapped[str] = mapped_column(String)
    values_source: Mapped[str] = mapped_column(String, default="recalculated")  # recalculated | excel_cached
    sha256: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    mapping_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(String, nullable=True)
    results: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # mapped output values
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("workbook_id", "seq"),)


class MappingVersion(Base):
    __tablename__ = "mapping_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("map"))
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String)  # demo | inferred | reviewed
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("workbook_id", "seq"),)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("conv"))
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # server-written summary of older turns
    # Persisted dialogue state, e.g. an open clarification in scripted demo mode.
    state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("turn"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")  # running | awaiting_job | completed | failed
    mode: Mapped[str] = mapped_column(String)  # demo | anthropic
    state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # persisted in-progress turn
    awaiting_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("conversation_id", "request_key"),)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("msg"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String)  # user | assistant
    text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("scn"))
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft")
    base_version_id: Mapped[str] = mapped_column(String)
    mapping_version_id: Mapped[str] = mapped_column(String)
    revision_of_id: Mapped[str | None] = mapped_column(String, nullable=True)
    interpreted_request: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScenarioChange(Base):
    __tablename__ = "scenario_changes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("chg"))
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[str] = mapped_column(String)
    operation: Mapped[str] = mapped_column(String)  # set | add | multiply
    operand: Mapped[Any] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text, default="")
    sheet: Mapped[str] = mapped_column(String)
    address: Mapped[str] = mapped_column(String)
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String)  # recalc
    purpose: Mapped[str] = mapped_column(String)  # scenario | round_trip
    status: Mapped[str] = mapped_column(String, default="queued")  # queued | running | succeeded | failed
    workbook_id: Mapped[str | None] = mapped_column(ForeignKey("workbooks.id", ondelete="CASCADE"), nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Validation(Base):
    __tablename__ = "validations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("val"))
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    passed: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("evt"))
    workbook_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String)
    endpoint: Mapped[str] = mapped_column(String)
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIUsage(Base):
    """Usage ledger: one row per Claude API request attempt (reserved, then reconciled)."""

    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("use"))
    user_id: Mapped[str] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    purpose: Mapped[str] = mapped_column(String, default="chat")  # chat | connection_check
    status: Mapped[str] = mapped_column(String, default="reserved")  # reserved | reconciled | uncertain | released
    requested_model: Mapped[str] = mapped_column(String)
    served_model: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_5m_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_1h_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reserved_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    price_version: Mapped[str] = mapped_column(String)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
