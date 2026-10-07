"""Validation rules: decide whether a transaction is valid.

An invalid transaction is rejected with a reason and changes nothing.
"""
from __future__ import annotations

import re

from models import Account, Movement, Transaction
from parsing import parse_amount

# Rejection reasons
MISSING_ID = "MISSING TRANSACTION ID"
DUPLICATE = "DUPLICATE TRANSACTION"
INVALID_ACCOUNT = "INVALID ACCOUNT"
INACTIVE_ACCOUNT = "INACTIVE ACCOUNT"
INVALID_AMOUNT = "INVALID AMOUNT"
INVALID_TYPE = "INVALID TRANSACTION TYPE"
INVALID_DESTINATION = "INVALID DESTINATION ACCOUNT"
INSUFFICIENT_FUNDS = "INSUFFICIENT FUNDS"

CREDIT_TYPES = {"DEPOSIT", "CREDIT", "REFUND", "INTEREST", "TRANSFER_IN"}
DEBIT_TYPES = {"WITHDRAWAL", "WITHDRAW", "DEBIT", "PAYMENT", "PURCHASE", "FEE", "TRANSFER_OUT"}
TRANSFER_TYPES = {"TRANSFER"}


class Rejection(Exception):
    """Raised by the validator when a transaction is invalid."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def direction(txn: Transaction) -> str | None:
    """'credit', 'debit', 'transfer', or None for an unknown type."""
    if txn.txn_type is None:  # no type column: treat everything as money out
        return "debit"
    kind = re.sub(r"[\s\-]+", "_", txn.txn_type.strip().upper())
    if kind in CREDIT_TYPES:
        return "credit"
    if kind in DEBIT_TYPES:
        return "debit"
    if kind in TRANSFER_TYPES:
        return "transfer" if txn.destination else "debit"
    return None


class Validator:
    def __init__(self, accounts: dict[str, Account]):
        self.accounts = accounts
        self._seen_ids: set[str] = set()

    def validate(self, txn: Transaction) -> Movement:
        """Returns the money movement to apply, or raises Rejection.

        The checks run in this order and the first failure is the reason given.
        """
        if not txn.txn_id:
            raise Rejection(MISSING_ID)
        if txn.txn_id in self._seen_ids:
            raise Rejection(DUPLICATE)
        # Rejected transactions count as "processed" too, so an ID can never
        # be replayed once the engine has given it a result.
        self._seen_ids.add(txn.txn_id)

        account = self.accounts.get(txn.account_id)
        if account is None:
            raise Rejection(INVALID_ACCOUNT)
        if not account.active:
            raise Rejection(INACTIVE_ACCOUNT)

        amount = parse_amount(txn.raw_amount)
        if amount is None or amount <= 0:
            raise Rejection(INVALID_AMOUNT)

        kind = direction(txn)
        if kind is None:
            raise Rejection(INVALID_TYPE)

        destination = None
        if kind == "transfer":
            destination = self.accounts.get(txn.destination)
            if destination is None or not destination.active or destination is account:
                raise Rejection(INVALID_DESTINATION)

        money_out = kind in ("debit", "transfer")
        if money_out and account.balance - amount < 0:
            raise Rejection(INSUFFICIENT_FUNDS)

        return Movement(account, amount, money_out, destination)
