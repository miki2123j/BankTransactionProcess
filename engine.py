"""The engine: runs each transaction through validate -> apply -> review."""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from config import Config
from models import APPROVED, REJECTED, Account, Movement, Result, Transaction
from review import Reviewer
from validation import Rejection, Validator


class TransactionEngine:
    def __init__(self, accounts: dict[str, Account], config: Config | None = None):
        self.accounts = accounts
        self.cfg = config or Config()
        self.validator = Validator(accounts)
        self.reviewer = Reviewer(self.cfg)
        self.results: list[Result] = []

    def process_all(self, txns: list[Transaction]) -> list[Result]:
        if self.cfg.sort_by_time:
            txns = sorted(txns, key=lambda t: (t.timestamp is None, t.timestamp or datetime.min))
        for txn in txns:
            self.process(txn)
        return self.results

    def process(self, txn: Transaction) -> Result:
        try:
            move = self.validator.validate(txn)          # 1. validate
        except Rejection as rejection:
            result = Result(txn, REJECTED, reason=rejection.reason)
        else:
            self._apply(move)                            # 2. update balances
            reasons = self.reviewer.review(txn, move)    # 3. flag for review
            result = Result(txn, APPROVED, review_reasons=reasons,
                            amount=move.amount, balance_after=move.account.balance)
        self.results.append(result)
        return result

    @staticmethod
    def _apply(move: Movement) -> None:
        if move.money_out:
            move.account.balance -= move.amount
        else:
            move.account.balance += move.amount
        if move.destination is not None:
            move.destination.balance += move.amount

    def summary(self) -> dict:
        rejected = [r for r in self.results if r.status == REJECTED]
        return {
            "processed": len(self.results),
            "approved": len(self.results) - len(rejected),
            "rejected": len(rejected),
            "flagged": sum(r.flagged for r in self.results),
            "rejections_by_reason": Counter(r.reason for r in rejected),
        }
