"""Building the reports and writing them to disk."""
from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

from engine import TransactionEngine
from models import Result
from parsing import money


def results_text(results: list[Result]) -> str:
    """One line per transaction: 'TX001 APPROVED' / 'TX002 REJECTED - REASON'."""
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


_CSV_HEADER = ["transaction_id", "account_id", "type", "amount", "timestamp", "status",
               "rejection_reason", "flagged_for_review", "review_reasons", "balance_after"]


def _csv_row(r: Result) -> list[str]:
    return [r.txn.txn_id, r.txn.account_id, r.txn.txn_type or "", r.txn.raw_amount,
            r.txn.raw_timestamp, r.status, r.reason, "YES" if r.flagged else "NO",
            "; ".join(r.review_reasons), money(r.balance_after)]


def _write_results_csv(path: Path, results: list[Result]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CSV_HEADER)
        writer.writerows(_csv_row(r) for r in results)


def write_outputs(engine: TransactionEngine, out_dir, account_headers, balance_col) -> Path:
    """Writes every report into `out_dir` and returns that folder."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "transaction_results.txt").write_text(results_text(engine.results), encoding="utf-8")
    (out / "processing_summary.txt").write_text(summary_text(engine), encoding="utf-8")
    (out / "flagged_report.txt").write_text(flagged_text(engine), encoding="utf-8")

    _write_results_csv(out / "transaction_results.csv", engine.results)
    _write_results_csv(out / "flagged_transactions.csv", [r for r in engine.results if r.flagged])

    with open(out / "accounts_updated.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=account_headers)
        writer.writeheader()
        for account in engine.accounts.values():
            writer.writerow({**account.row, balance_col: money(account.balance)})
    return out
