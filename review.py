"""Review rules: decide whether an approved transaction needs a manual look.

A flag never blocks a transaction; it only adds it to the review report.
"""
from __future__ import annotations

from bisect import bisect_right, insort
from collections import defaultdict
from decimal import Decimal

from config import Config
from models import Movement, Transaction

# Review reasons
HIGH_VALUE = "HIGH VALUE"
DAILY_COUNT = "DAILY TRANSACTION LIMIT EXCEEDED"
DAILY_AMOUNT = "DAILY AMOUNT LIMIT EXCEEDED"
VELOCITY = "HIGH TRANSACTION FREQUENCY"
NO_TIMESTAMP = "MISSING OR INVALID TIMESTAMP"


class Reviewer:
    def __init__(self, config: Config):
        self.cfg = config
        self._daily_count = defaultdict(int)     # (account, date) -> approved txns
        self._daily_out = defaultdict(Decimal)   # (account, date) -> money out
        self._times = defaultdict(list)          # account -> sorted approved timestamps

    def review(self, txn: Transaction, move: Movement) -> list[str]:
        """Returns the review reasons for an approved transaction (often none)."""
        reasons = []
        if move.amount > self.cfg.high_value_threshold:
            reasons.append(HIGH_VALUE)

        if txn.timestamp is None:
            reasons.append(NO_TIMESTAMP)  # the time-based rules cannot run
            return reasons

        reasons += self._daily_limits(txn, move)
        if self._too_frequent(txn, move):
            reasons.append(VELOCITY)
        return reasons

    def _daily_limits(self, txn: Transaction, move: Movement) -> list[str]:
        account, cfg = move.account, self.cfg
        day = (account.account_id, txn.timestamp.date())
        self._daily_count[day] += 1
        if move.money_out:
            self._daily_out[day] += move.amount

        count_limit, amount_limit = cfg.daily_txn_count_limit, cfg.default_daily_amount_limit
        if account.daily_limit is not None:
            if cfg.account_limit_is == "count":
                count_limit = int(account.daily_limit)
            else:
                amount_limit = account.daily_limit

        reasons = []
        if self._daily_count[day] > count_limit:
            reasons.append(DAILY_COUNT)
        if move.money_out and amount_limit is not None and self._daily_out[day] > amount_limit:
            reasons.append(DAILY_AMOUNT)
        return reasons

    def _too_frequent(self, txn: Transaction, move: Movement) -> bool:
        times = self._times[move.account.account_id]
        insort(times, txn.timestamp)
        in_window = (bisect_right(times, txn.timestamp)
                     - bisect_right(times, txn.timestamp - self.cfg.velocity_window))
        return in_window > self.cfg.velocity_max_txns
