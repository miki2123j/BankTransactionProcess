#!/usr/bin/env python3
"""Command line entry point.

    python3 main.py --accounts accounts.csv --transactions transactions.csv --out output
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from decimal import Decimal

from config import Config
from engine import TransactionEngine
from loaders import load_accounts, load_transactions
from reports import flagged_text, results_text, summary_text, write_outputs


def parse_args(argv=None) -> argparse.Namespace:
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
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
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
