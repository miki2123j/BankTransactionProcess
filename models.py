"""The data the engine passes around: accounts, transactions and results."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

APPROVED = "APPROVED"
REJECTED = "REJECTED"


@dataclass
class Account:
    account_id: str
    balance: Decimal
    active: bool = True
    daily_limit: Decimal | None = None
    row: dict = field(default_factory=dict)  # original CSV row, for writing back


@dataclass
class Transaction:
    row_number: int
    txn_id: str
    account_id: str
    raw_amount: str
    txn_type: str | None  # None = the file has no type column
    raw_timestamp: str = ""
    timestamp: datetime | None = None
    destination: str = ""


@dataclass
class Movement:
    """A transaction that passed validation: what money moves, and where."""
    account: Account
    amount: Decimal
    money_out: bool  # True = leaves `account`, False = arrives in it
    destination: Account | None = None  # set for transfers


@dataclass
class Result:
    txn: Transaction
    status: str  # APPROVED or REJECTED
    reason: str = ""  # rejection reason
    review_reasons: list[str] = field(default_factory=list)
    amount: Decimal | None = None
    balance_after: Decimal | None = None

    @property
    def flagged(self) -> bool:
        return bool(self.review_reasons)

    @property
    def label(self) -> str:
        return self.txn.txn_id or f"(no id, row {self.txn.row_number})"

    def line(self) -> str:
        if self.status == APPROVED:
            return f"{self.label} {APPROVED}"
        return f"{self.label} {REJECTED} - {self.reason}"
