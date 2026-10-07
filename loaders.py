"""Reading the two input CSVs."""
from __future__ import annotations

import csv
import re
import sys
from decimal import Decimal
from pathlib import Path

from models import Account, Transaction
from parsing import parse_amount, parse_timestamp

ACTIVE_VALUES = {"ACTIVE", "A", "OPEN", "TRUE", "YES", "Y", "1"}

# Column names we recognise (compared lower-case, punctuation removed).
# If your file uses a different header, add it to the right list.
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


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _map_columns(fieldnames, spec, required, filename) -> dict:
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
