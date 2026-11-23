"""Structured model input and output. A live model has to satisfy the same models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    name: Literal["get_order", "quote_refund", "confirm_proposal"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    stage: Literal["plan", "draft"]
    messages: list[dict[str, str]]


class ModelResponse(BaseModel):
    tool_calls: list[ToolCall] = Field(default_factory=list)
    content: dict[str, Any] | None = None


class PlanOutput(BaseModel):
    explanation_hint: Literal[
        "none",
        "missing_order_reference",
        "multiple_orders",
        "ambiguous_proposal",
        "nothing_to_confirm",
        "confirm_amount_mismatch",
    ] = "none"


class DraftOutput(BaseModel):
    reply: str = Field(min_length=1)


class Decision(BaseModel):
    """Facts the reply is allowed to mention. Filled from tools, not from the model."""

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "order_status",
        "refund_proposed",
        "refund_executed",
        "refund_declined",
        "already_refunded",
        "not_found",
        "clarify",
    ]
    explanation_code: str
    order_id: str | None = None
    order_status: str | None = None
    carrier: str | None = None
    tracking_number: str | None = None
    item_name: str | None = None
    items_summary: str | None = None
    quantity: int | None = None
    amount: str | None = None
    claimed_amount: str | None = None
    currency: str | None = None
    proposal_id: str | None = None
    refund_id: str | None = None
    duplicate: bool = False
    reason: str | None = None
    allowed_order_ids: list[str] = Field(default_factory=list)
    allowed_amounts: list[str] = Field(default_factory=list)
    allowed_item_names: list[str] = Field(default_factory=list)
    allowed_tracking_numbers: list[str] = Field(default_factory=list)
    allowed_emails: list[str] = Field(default_factory=list)
    forbidden_item_names: list[str] = Field(default_factory=list)
    forbidden_tracking_numbers: list[str] = Field(default_factory=list)


class IncomingMessage(BaseModel):
    authenticated_customer_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
