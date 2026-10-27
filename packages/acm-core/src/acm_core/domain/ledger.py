"""Double-entry cost ledger and LLM call records (ARCHITECTURE.md §11).

Every cost movement is a transaction of two or more ``LedgerEntry`` rows sharing a
``transaction_id``; per currency, debits equal credits (``check_balanced``). Accounts are
colon-separated paths whose first segment says what they are:

- ``budget:<campaign_id>`` — what the campaign may still spend
- ``reserved:<run_id>`` — held for an attempt before submission
- ``spent:<campaign_id>`` — consumed
- ``provider:<provider_account_id>`` / ``model:<provider>`` — counterparties

Reservations move budget → reserved before submission; settlement moves reserved → spent by
the actual usage and releases the rest back to budget.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, JsonValue

from acm_core.domain.common import AgentRole, Currency, Entity, NonNegativeDecimal
from acm_core.errors import InvalidInputError
from acm_core.ids import (
    AttemptId,
    CampaignId,
    DecisionId,
    LedgerEntryId,
    LedgerTransactionId,
    ModelCallId,
    RunId,
)

__all__ = [
    "LEDGER_ACCOUNT_PATTERN",
    "LedgerDirection",
    "LedgerEntry",
    "LedgerEntryKind",
    "ModelCall",
    "ModelCallStatus",
    "check_balanced",
]

LEDGER_ACCOUNT_PATTERN = r"^[a-z][a-z_]*(:[A-Za-z0-9_\-.]+)+$"


class LedgerDirection(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class LedgerEntryKind(StrEnum):
    """Why the transaction was posted."""

    ALLOCATION = "allocation"
    """Budget granted or changed."""
    RESERVATION = "reservation"
    SETTLEMENT = "settlement"
    RELEASE = "release"
    """Unused reservation returned to the budget."""
    MODEL_CALL = "model_call"
    ADJUSTMENT = "adjustment"
    """Manual correction by an admin."""


class LedgerEntry(Entity):
    """One side of a double-entry transaction."""

    id: LedgerEntryId = Field(default_factory=LedgerEntryId.new)
    transaction_id: LedgerTransactionId
    campaign_id: CampaignId
    kind: LedgerEntryKind
    account: str = Field(pattern=LEDGER_ACCOUNT_PATTERN)
    direction: LedgerDirection
    currency: Currency
    amount: Decimal = Field(gt=0)
    normalized_amount: NonNegativeDecimal | None = None
    """The same cost in ``gpu_hour_norm`` for compute entries, for cross-provider burn."""
    attempt_id: AttemptId | None = None
    run_id: RunId | None = None
    model_call_id: ModelCallId | None = None
    memo: str | None = None


def check_balanced(entries: Iterable[LedgerEntry]) -> None:
    """Raise ``InvalidInputError`` unless the entries form one balanced transaction.

    Balanced means: at least two entries, one shared ``transaction_id``, and per currency
    the debit total equals the credit total.
    """
    items = list(entries)
    if len(items) < 2:
        raise InvalidInputError("a ledger transaction needs at least two entries")
    transaction_ids = {e.transaction_id for e in items}
    if len(transaction_ids) != 1:
        ids: list[JsonValue] = [str(t) for t in sorted(transaction_ids)]
        raise InvalidInputError(
            "ledger entries span several transactions", details={"transaction_ids": ids}
        )
    totals: defaultdict[Currency, Decimal] = defaultdict(Decimal)
    for entry in items:
        sign = 1 if entry.direction is LedgerDirection.DEBIT else -1
        totals[entry.currency] += sign * entry.amount
    unbalanced: dict[str, JsonValue] = {
        currency.value: str(total) for currency, total in totals.items() if total
    }
    if unbalanced:
        raise InvalidInputError(
            "ledger transaction is not balanced (debits - credits per currency)",
            details={"imbalance": unbalanced},
        )


class ModelCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class ModelCall(Entity):
    """One LLM request, recorded for cost accounting and agent replay."""

    id: ModelCallId = Field(default_factory=ModelCallId.new)
    agent: AgentRole
    provider: str = Field(min_length=1)
    """Model provider key in ``acm_agents.models`` (e.g. ``meta``, ``baseten``)."""
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cost: NonNegativeDecimal
    """Cost in ``llm_usd``."""
    status: ModelCallStatus = ModelCallStatus.OK
    error: str | None = None
    campaign_id: CampaignId | None = None
    run_id: RunId | None = None
    decision_id: DecisionId | None = None
