#!/usr/bin/env python3
"""Transaction processing engine (MVP).

Reads an accounts CSV and a transactions CSV, then for every transaction:
  1. validates it            (reject with a reason if invalid)
  2. applies it              (updates the account balance)
  3. checks the review rules (flags it, but still processes it)
and writes the results, a processing summary, a flagged-transactions report
and the updated account balances.

Usage:
    python engine.py --accounts accounts.csv --transactions transactions.csv --out output

Standard library only. Money is handled with Decimal, never float.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from bisect import bisect_right, insort
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration: every "reasonable" number in the brief lives here.
# --------------------------------------------------------------------------


@dataclass
class Config:
    # Review rule 1: a single transaction above this amount is flagged.
    high_value_threshold: Decimal = Decimal("10000")
    # Review rule 2a: more than this many approved transactions on one
    # account in one calendar day is flagged.
    daily_txn_count_limit: int = 5
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


# Rejection reasons
MISSING_ID = "MISSING TRANSACTION ID"
DUPLICATE = "DUPLICATE TRANSACTION"
INVALID_ACCOUNT = "INVALID ACCOUNT"
INACTIVE_ACCOUNT = "INACTIVE ACCOUNT"
INVALID_AMOUNT = "INVALID AMOUNT"
INVALID_TYPE = "INVALID TRANSACTION TYPE"
INVALID_DESTINATION = "INVALID DESTINATION ACCOUNT"
INSUFFICIENT_FUNDS = "INSUFFICIENT FUNDS"

# Review reasons
HIGH_VALUE = "HIGH VALUE"
DAILY_COUNT = "DAILY TRANSACTION LIMIT EXCEEDED"
DAILY_AMOUNT = "DAILY AMOUNT LIMIT EXCEEDED"
VELOCITY = "HIGH TRANSACTION FREQUENCY"
NO_TIMESTAMP = "MISSING OR INVALID TIMESTAMP"

CREDIT_TYPES = {"DEPOSIT", "CREDIT", "REFUND", "INTEREST", "TRANSFER_IN"}
DEBIT_TYPES = {"WITHDRAWAL", "WITHDRAW", "DEBIT", "PAYMENT", "PURCHASE", "FEE", "TRANSFER_OUT"}
TRANSFER_TYPES = {"TRANSFER"}
ACTIVE_VALUES = {"ACTIVE", "A", "OPEN", "TRUE", "YES", "Y", "1"}

# Column names we recognise (compared lower-case, punctuation removed).
ACCOUNT_COLS = {
    "id": ["accountid", "accountnumber", "accountno", "acctid", "account", "id"],
    "balance": ["balance", "currentbalance", "openingbalance", "accountbalance"],
    "status": ["status", "accountstatus", "state", "active", "isactive"],
    "daily_limit": ["dailylimit", "dailytransactionlimit", "dailytxnlimit", "dailytransactionlimits"],
}
TXN_COLS = {
    "id": ["transactionid", "txnid", "txid", "transid", "id"],
    "account": ["accountid", "accountnumber", "accountno", "acctid", "account",
                "fromaccount", "fromaccountid", "sourceaccount"],
    "amount": ["amount", "transactionamount", "txnamount", "value"],
    "type": ["type", "transactiontype", "txntype", "txtype"],
    "timestamp": ["timestamp", "datetime", "transactiondate", "transactiontime",
                  "date", "createdat", "time"],
    "destination": ["toaccount", "toaccountid", "destinationaccount",
                    "destinationaccountid", "targetaccount"],
}

# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


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
class Result:
    txn: Transaction
    status: str  # "APPROVED" or "REJECTED"
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
        if self.status == "APPROVED":
            return f"{self.label} APPROVED"
        return f"{self.label} REJECTED - {self.reason}"


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _map_columns(fieldnames, spec, required, filename):
    """Match the file's headers to the columns we need."""
    by_norm = {_norm(f): f for f in fieldnames or []}
    cols = {key: next((by_norm[a] for a in aliases if a in by_norm), None)
            for key, aliases in spec.items()}
    missing = [k for k in required if cols[k] is None]
    if missing:
        raise ValueError(
            f"{filename}: could not find column(s) for {missing}. "
            f"Headers found: {list(fieldnames or [])}"
        )
    return cols


def parse_amount(text) -> Decimal | None:
    """'1,250.50' / '$40' -> Decimal. Returns None if it is not a number."""
    cleaned = re.sub(r"[,$\s]", "", str(text or ""))
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


_TS_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
]


def parse_timestamp(text) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in _TS_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_accounts(path) -> tuple[dict[str, Account], list[str], str]:
    """Returns (accounts by id, original headers, name of the balance column)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = _map_columns(reader.fieldnames, ACCOUNT_COLS, ["id", "balance"], Path(path).name)
        accounts: dict[str, Account] = {}
        for row in reader:
            account_id = (row[cols["id"]] or "").strip()
            if not account_id:
                continue
            if account_id in accounts:
                print(f"warning: account {account_id} appears twice; keeping the first", file=sys.stderr)
                continue
            balance = parse_amount(row[cols["balance"]])
            active = True
            if cols["status"]:
                active = (row[cols["status"]] or "").strip().upper() in ACTIVE_VALUES
            if balance is None:  # unusable balance: never transact on it
                print(f"warning: account {account_id} has an unreadable balance; treated as inactive",
                      file=sys.stderr)
                balance, active = Decimal("0"), False
            limit = parse_amount(row[cols["daily_limit"]]) if cols["daily_limit"] else None
            accounts[account_id] = Account(account_id, balance, active, limit, row)
        return accounts, list(reader.fieldnames), cols["balance"]


def load_transactions(path) -> list[Transaction]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = _map_columns(reader.fieldnames, TXN_COLS, ["id", "account", "amount"], Path(path).name)

        def get(row, key):
            return (row[cols[key]] or "").strip() if cols[key] else ""

        txns = []
        for number, row in enumerate(reader, start=1):
            txns.append(Transaction(
                row_number=number,
                txn_id=get(row, "id"),
                account_id=get(row, "account"),
                raw_amount=get(row, "amount"),
                txn_type=get(row, "type") if cols["type"] else None,
                raw_timestamp=get(row, "timestamp"),
                timestamp=parse_timestamp(get(row, "timestamp")),
                destination=get(row, "destination"),
            ))
        return txns


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------


class TransactionEngine:
    def __init__(self, accounts: dict[str, Account], config: Config | None = None):
        self.accounts = accounts
        self.cfg = config or Config()
        self.results: list[Result] = []
        self._seen_ids: set[str] = set()
        self._daily_count = defaultdict(int)            # (account, date) -> approved txns
        self._daily_out = defaultdict(Decimal)          # (account, date) -> money out
        self._times = defaultdict(list)                 # account -> sorted approved timestamps

    # ---- public API -------------------------------------------------------

    def process_all(self, txns: list[Transaction]) -> list[Result]:
        if self.cfg.sort_by_time:
            txns = sorted(txns, key=lambda t: (t.timestamp is None, t.timestamp or datetime.min))
        for txn in txns:
            self.process(txn)
        return self.results

    def process(self, txn: Transaction) -> Result:
        result = self._process(txn)
        self.results.append(result)
        return result

    # ---- internals --------------------------------------------------------

    def _process(self, txn: Transaction) -> Result:
        def reject(reason):
            return Result(txn, "REJECTED", reason=reason)

        # -- 1. validate ----------------------------------------------------
        if not txn.txn_id:
            return reject(MISSING_ID)
        if txn.txn_id in self._seen_ids:
            return reject(DUPLICATE)
        # Rejected transactions count as "processed" too, so an ID can never
        # be replayed once the engine has given it a result.
        self._seen_ids.add(txn.txn_id)

        account = self.accounts.get(txn.account_id)
        if account is None:
            return reject(INVALID_ACCOUNT)
        if not account.active:
            return reject(INACTIVE_ACCOUNT)

        amount = parse_amount(txn.raw_amount)
        if amount is None or amount <= 0:
            return reject(INVALID_AMOUNT)

        kind = self._direction(txn)
        if kind is None:
            return reject(INVALID_TYPE)

        destination = None
        if kind == "transfer":
            destination = self.accounts.get(txn.destination)
            if destination is None or not destination.active or destination is account:
                return reject(INVALID_DESTINATION)

        money_out = kind in ("debit", "transfer")
        if money_out and account.balance - amount < 0:
            return reject(INSUFFICIENT_FUNDS)

        # -- 2. apply -------------------------------------------------------
        if money_out:
            account.balance -= amount
        else:
            account.balance += amount
        if destination is not None:
            destination.balance += amount

        # -- 3. review rules (never block the transaction) ------------------
        reasons = self._review(txn, account, amount, money_out)
        return Result(txn, "APPROVED", review_reasons=reasons,
                      amount=amount, balance_after=account.balance)

    def _direction(self, txn: Transaction) -> str | None:
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

    def _review(self, txn, account, amount, money_out) -> list[str]:
        cfg, reasons = self.cfg, []

        if amount > cfg.high_value_threshold:
            reasons.append(HIGH_VALUE)

        if txn.timestamp is None:
            reasons.append(NO_TIMESTAMP)  # time-based rules cannot run
            return reasons

        day = (account.account_id, txn.timestamp.date())
        self._daily_count[day] += 1
        if money_out:
            self._daily_out[day] += amount

        count_limit, amount_limit = cfg.daily_txn_count_limit, cfg.default_daily_amount_limit
        if account.daily_limit is not None:
            if cfg.account_limit_is == "count":
                count_limit = int(account.daily_limit)
            else:
                amount_limit = account.daily_limit
        if self._daily_count[day] > count_limit:
            reasons.append(DAILY_COUNT)
        if money_out and amount_limit is not None and self._daily_out[day] > amount_limit:
            reasons.append(DAILY_AMOUNT)

        times = self._times[account.account_id]
        insort(times, txn.timestamp)
        in_window = (bisect_right(times, txn.timestamp)
                     - bisect_right(times, txn.timestamp - cfg.velocity_window))
        if in_window > cfg.velocity_max_txns:
            reasons.append(VELOCITY)
        return reasons

    # ---- summaries --------------------------------------------------------

    def summary(self) -> dict:
        approved = [r for r in self.results if r.status == "APPROVED"]
        rejected = [r for r in self.results if r.status == "REJECTED"]
        return {
            "processed": len(self.results),
            "approved": len(approved),
            "rejected": len(rejected),
            "flagged": sum(r.flagged for r in self.results),
            "rejections_by_reason": Counter(r.reason for r in rejected),
        }


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def results_text(results: list[Result]) -> str:
    return "\n".join(r.line() for r in results) + "\n"


def summary_text(engine: TransactionEngine) -> str:
    s = engine.summary()
    lines = [
        f"Transactions Processed: {s['processed']}",
        f"Approved: {s['approved']}",
        f"Rejected: {s['rejected']}",
        f"Flagged For Review: {s['flagged']}",
    ]
    if s["rejections_by_reason"]:
        lines += ["", "Rejections By Reason:"]
        lines += [f"  {reason}: {n}" for reason, n in s["rejections_by_reason"].most_common()]
    return "\n".join(lines) + "\n"


def flagged_text(engine: TransactionEngine) -> str:
    flagged = [r for r in engine.results if r.flagged]
    lines = ["FLAGGED TRANSACTIONS REPORT", "=" * 27, "",
             f"Transactions Flagged For Review: {len(flagged)}",
             f"Total Value Flagged: {money(sum((r.amount for r in flagged), Decimal('0')))}"]
    if not flagged:
        return "\n".join(lines) + "\n"

    by_reason = Counter(reason for r in flagged for reason in r.review_reasons)
    lines += ["", "By Reason (a transaction can have more than one):"]
    lines += [f"  {reason}: {n}" for reason, n in by_reason.most_common()]

    by_account = Counter(r.txn.account_id for r in flagged)
    lines += ["", "By Account:"]
    lines += [f"  {account}: {n}" for account, n in by_account.most_common()]

    lines += ["", "Details:",
              f"  {'TXN ID':<10} {'ACCOUNT':<10} {'TYPE':<12} {'AMOUNT':>12}  {'TIMESTAMP':<20} REASONS"]
    for r in flagged:
        lines.append(
            f"  {r.label:<10} {r.txn.account_id:<10} {(r.txn.txn_type or ''):<12} "
            f"{money(r.amount):>12}  {r.txn.raw_timestamp:<20} {'; '.join(r.review_reasons)}"
        )
    return "\n".join(lines) + "\n"


def write_outputs(engine: TransactionEngine, out_dir, account_headers, balance_col) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "transaction_results.txt").write_text(results_text(engine.results), encoding="utf-8")
    (out / "processing_summary.txt").write_text(summary_text(engine), encoding="utf-8")
    (out / "flagged_report.txt").write_text(flagged_text(engine), encoding="utf-8")

    header = ["transaction_id", "account_id", "type", "amount", "timestamp", "status",
              "rejection_reason", "flagged_for_review", "review_reasons", "balance_after"]

    def as_row(r: Result):
        return [r.txn.txn_id, r.txn.account_id, r.txn.txn_type or "", r.txn.raw_amount,
                r.txn.raw_timestamp, r.status, r.reason, "YES" if r.flagged else "NO",
                "; ".join(r.review_reasons), money(r.balance_after)]

    for name, rows in (("transaction_results.csv", engine.results),
                       ("flagged_transactions.csv", [r for r in engine.results if r.flagged])):
        with open(out / name, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(as_row(r) for r in rows)

    with open(out / "accounts_updated.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=account_headers)
        writer.writeheader()
        for account in engine.accounts.values():
            writer.writerow({**account.row, balance_col: money(account.balance)})
    return out


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Bank transaction processing engine (MVP)")
    p.add_argument("--accounts", required=True, help="accounts CSV")
    p.add_argument("--transactions", required=True, help="transactions CSV")
    p.add_argument("--out", default="output", help="folder for the reports (default: output)")
    p.add_argument("--high-value", type=Decimal, default=Config.high_value_threshold,
                   help="flag a transaction above this amount (default 10000)")
    p.add_argument("--daily-count", type=int, default=Config.daily_txn_count_limit,
                   help="flag when an account passes this many transactions in a day (default 5)")
    p.add_argument("--daily-amount", type=Decimal, default=None,
                   help="daily money-out cap for accounts with no limit of their own (default: off)")
    p.add_argument("--account-limit-is", choices=["amount", "count"], default="amount",
                   help="how to read the accounts file's daily-limit column (default amount)")
    p.add_argument("--velocity-max", type=int, default=Config.velocity_max_txns,
                   help="flag when an account passes this many transactions in the window (default 3)")
    p.add_argument("--velocity-minutes", type=float, default=10,
                   help="length of the velocity window in minutes (default 10)")
    p.add_argument("--sort-by-time", action="store_true",
                   help="process in timestamp order instead of file order")
    p.add_argument("--quiet", action="store_true", help="do not print every transaction result")
    args = p.parse_args(argv)

    config = Config(
        high_value_threshold=args.high_value,
        daily_txn_count_limit=args.daily_count,
        default_daily_amount_limit=args.daily_amount,
        account_limit_is=args.account_limit_is,
        velocity_max_txns=args.velocity_max,
        velocity_window=timedelta(minutes=args.velocity_minutes),
        sort_by_time=args.sort_by_time,
    )
    try:
        accounts, headers, balance_col = load_accounts(args.accounts)
        txns = load_transactions(args.transactions)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    engine = TransactionEngine(accounts, config)
    engine.process_all(txns)
    out = write_outputs(engine, args.out, headers, balance_col)

    if not args.quiet:
        print(results_text(engine.results))
    print(summary_text(engine))
    print(flagged_text(engine))
    print(f"Reports written to: {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
