"""Unit tests: one or more per business rule.  Run with:  python -m unittest -v"""
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

import review as rv
import validation as v
from config import Config
from engine import TransactionEngine
from models import Account, Transaction
from reports import summary_text


def make_engine(**config):
    accounts = {
        "A1": Account("A1", Decimal("1000"), active=True),
        "A2": Account("A2", Decimal("500"), active=True, daily_limit=Decimal("300")),
        "A3": Account("A3", Decimal("900"), active=False),
        "BIG": Account("BIG", Decimal("100000"), active=True),
    }
    return TransactionEngine(accounts, Config(**config))


_counter = 0
T0 = datetime(2026, 10, 1, 9, 0, 0)


def txn(account, amount, kind="WITHDRAWAL", at=T0, txn_id=None, to=""):
    global _counter
    _counter += 1
    return Transaction(_counter, txn_id if txn_id is not None else f"T{_counter}",
                       account, str(amount), kind, str(at), at, to)


class ValidationRules(unittest.TestCase):
    def test_valid_withdrawal_updates_balance(self):
        eng = make_engine()
        r = eng.process(txn("A1", "250.50"))
        self.assertEqual((r.status, r.review_reasons), ("APPROVED", []))
        self.assertEqual(eng.accounts["A1"].balance, Decimal("749.50"))

    def test_valid_deposit_updates_balance(self):
        eng = make_engine()
        eng.process(txn("A1", "0.10", "DEPOSIT"))
        eng.process(txn("A1", "0.20", "DEPOSIT"))
        self.assertEqual(eng.accounts["A1"].balance, Decimal("1000.30"))  # no float drift

    def test_unknown_account_rejected(self):
        r = make_engine().process(txn("NOPE", 10))
        self.assertEqual((r.status, r.reason), ("REJECTED", v.INVALID_ACCOUNT))

    def test_inactive_account_rejected(self):
        eng = make_engine()
        r = eng.process(txn("A3", 10, "DEPOSIT"))
        self.assertEqual(r.reason, v.INACTIVE_ACCOUNT)
        self.assertEqual(eng.accounts["A3"].balance, Decimal("900"))

    def test_zero_negative_and_garbage_amounts_rejected(self):
        eng = make_engine()
        for bad in ("0", "0.00", "-5", "abc", "", "NaN"):
            self.assertEqual(eng.process(txn("A1", bad)).reason, v.INVALID_AMOUNT, bad)
        self.assertEqual(eng.accounts["A1"].balance, Decimal("1000"))

    def test_duplicate_id_rejected_and_not_applied_twice(self):
        eng = make_engine()
        eng.process(txn("A1", 100, txn_id="DUP"))
        r = eng.process(txn("A1", 100, txn_id="DUP"))
        self.assertEqual(r.reason, v.DUPLICATE)
        self.assertEqual(eng.accounts["A1"].balance, Decimal("900"))

    def test_id_of_a_rejected_transaction_cannot_be_reused(self):
        eng = make_engine()
        eng.process(txn("NOPE", 100, txn_id="X"))
        self.assertEqual(eng.process(txn("A1", 100, txn_id="X")).reason, v.DUPLICATE)

    def test_overdraft_rejected_but_exact_balance_allowed(self):
        eng = make_engine()
        self.assertEqual(eng.process(txn("A1", "1000.01")).reason, v.INSUFFICIENT_FUNDS)
        self.assertEqual(eng.process(txn("A1", "1000")).status, "APPROVED")
        self.assertEqual(eng.accounts["A1"].balance, Decimal("0"))

    def test_unknown_type_rejected(self):
        self.assertEqual(make_engine().process(txn("A1", 10, "REVERSAL")).reason, v.INVALID_TYPE)

    def test_transfer_moves_money_between_accounts(self):
        eng = make_engine()
        r = eng.process(txn("A1", 400, "TRANSFER", to="A2"))
        self.assertEqual(r.status, "APPROVED")
        self.assertEqual(eng.accounts["A1"].balance, Decimal("600"))
        self.assertEqual(eng.accounts["A2"].balance, Decimal("900"))

    def test_transfer_to_inactive_account_rejected(self):
        eng = make_engine()
        self.assertEqual(eng.process(txn("A1", 10, "TRANSFER", to="A3")).reason, v.INVALID_DESTINATION)
        self.assertEqual(eng.accounts["A1"].balance, Decimal("1000"))


class ReviewRules(unittest.TestCase):
    def test_high_value_flagged_but_still_processed(self):
        eng = make_engine()
        r = eng.process(txn("BIG", "10000.01"))
        self.assertEqual((r.status, r.review_reasons), ("APPROVED", [rv.HIGH_VALUE]))
        self.assertEqual(eng.accounts["BIG"].balance, Decimal("89999.99"))

    def test_amount_equal_to_threshold_not_flagged(self):
        self.assertFalse(make_engine().process(txn("BIG", "10000")).flagged)

    def test_rejected_transaction_is_never_flagged(self):
        r = make_engine().process(txn("A1", "50000"))
        self.assertEqual((r.status, r.flagged), ("REJECTED", False))

    def test_daily_count_limit(self):
        eng = make_engine(daily_txn_count_limit=3)
        flags = [eng.process(txn("A1", 1, at=T0 + timedelta(hours=i))).review_reasons for i in range(4)]
        self.assertEqual(flags, [[], [], [], [rv.DAILY_COUNT]])
        next_day = eng.process(txn("A1", 1, at=T0 + timedelta(days=1)))
        self.assertFalse(next_day.flagged)  # counter resets each day

    def test_daily_amount_limit_from_accounts_file(self):
        eng = make_engine()  # A2 has a 300 daily limit
        first = eng.process(txn("A2", 200, at=T0))
        second = eng.process(txn("A2", 150, at=T0 + timedelta(hours=2)))
        self.assertEqual((first.review_reasons, second.review_reasons), ([], [rv.DAILY_AMOUNT]))
        self.assertEqual(eng.accounts["A2"].balance, Decimal("150"))

    def test_deposits_do_not_count_towards_daily_amount(self):
        eng = make_engine()
        self.assertFalse(eng.process(txn("A2", 5000, "DEPOSIT")).flagged)

    def test_velocity(self):
        eng = make_engine(velocity_max_txns=3, velocity_window=timedelta(minutes=10))
        flags = [eng.process(txn("A1", 1, at=T0 + timedelta(minutes=i))).review_reasons for i in range(4)]
        self.assertEqual(flags, [[], [], [], [rv.VELOCITY]])
        later = eng.process(txn("A1", 1, at=T0 + timedelta(minutes=30)))
        self.assertFalse(later.flagged)

    def test_missing_timestamp_is_flagged(self):
        t = txn("A1", 10)
        t.timestamp = None
        self.assertEqual(make_engine().process(t).review_reasons, [rv.NO_TIMESTAMP])


class Summary(unittest.TestCase):
    def test_counts_add_up(self):
        eng = make_engine()
        eng.process(txn("A1", 10))
        eng.process(txn("NOPE", 10))
        eng.process(txn("BIG", 20000))
        s = eng.summary()
        self.assertEqual((s["processed"], s["approved"], s["rejected"], s["flagged"]), (3, 2, 1, 1))
        self.assertIn("Transactions Processed: 3", summary_text(eng))


if __name__ == "__main__":
    unittest.main()
