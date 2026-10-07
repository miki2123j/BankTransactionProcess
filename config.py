"""Settings: every "reasonable" number in the brief lives here."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal


@dataclass
class Config:
    # Review rule 1: a single transaction above this amount is flagged.
    high_value_threshold: Decimal = Decimal("10000")

    # Review rule 2a: more than this many approved transactions on one
    # account in one calendar day is flagged.
    daily_txn_count_limit: int = 20

    # Review rule 2b: if the accounts file has a daily-limit column it is read
    # as a cap on the day's total money OUT ("amount") or as a per-account
    # override of the daily count ("count").
    account_limit_is: str = "amount"

    # Used for rule 2b when an account has no limit of its own (None = off).
    default_daily_amount_limit: Decimal | None = None

    # Review rule 3: more than `velocity_max_txns` approved transactions on
    # one account inside `velocity_window` is flagged.
    velocity_max_txns: int = 3
    velocity_window: timedelta = timedelta(minutes=10)

    # Process in timestamp order instead of file order.
    sort_by_time: bool = False
