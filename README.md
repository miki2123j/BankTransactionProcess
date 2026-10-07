# Transaction Processing Engine (MVP)

Validates bank transactions, applies the business rules, updates balances, flags
transactions for manual review, and writes the operational reports.
Python 3.10+, standard library only: nothing to install.

## Run it

```
python main.py --accounts sample_data/accounts.csv --transactions sample_data/transactions.csv --out output
python -m unittest -v        # 20 tests, one or more per rule
```

## What comes out (in `output/`)

| File | Contents |
|---|---|
| `transaction_results.txt` | One line per transaction: `TX001 APPROVED` / `TX002 REJECTED - INVALID ACCOUNT` |
| `processing_summary.txt` | Processed / Approved / Rejected / Flagged For Review, plus rejections by reason |
| `flagged_report.txt` | Flagged transactions: totals, by reason, by account, and the detail lines |
| `transaction_results.csv` | Same results with reason, review flags and balance after, for Excel |
| `flagged_transactions.csv` | Just the flagged rows |
| `accounts_updated.csv` | The accounts file with the new balances |

## How a transaction is processed

Each transaction goes through three steps, in order:

1. **Validate.** The first failing check rejects it and nothing changes.
2. **Apply.** The balance is updated.
3. **Review.** Approved transactions are checked against the review rules. A flag never blocks the transaction.

| Rejected when | Reason shown |
|---|---|
| Transaction ID already seen | `DUPLICATE TRANSACTION` |
| Account not in the accounts file | `INVALID ACCOUNT` |
| Account status is not active | `INACTIVE ACCOUNT` |
| Amount is <= 0 or not a number | `INVALID AMOUNT` |
| Money out would take the balance below 0 | `INSUFFICIENT FUNDS` |
| Type is not recognised / ID is blank / transfer target is bad | `INVALID TRANSACTION TYPE` / `MISSING TRANSACTION ID` / `INVALID DESTINATION ACCOUNT` |

| Flagged when | Default | Flag to change it |
|---|---|---|
| Amount is above the threshold | 10,000 | `--high-value` |
| Account passes its daily number of transactions | 5 a day | `--daily-count` |
| Account's money out for the day passes its `daily_limit` column | per account | `--daily-amount`, `--account-limit-is` |
| Too many transactions in a short window | more than 3 in 10 minutes | `--velocity-max`, `--velocity-minutes` |

## Assumptions (the brief leaves these open)

- **Column names.** Common spellings are matched automatically (`account_id` / `AccountID` / `Account Number`, and so on). If a required column is not found, the error lists the headers it saw; add the name to `ACCOUNT_COLS` / `TXN_COLS` at the top of `loaders.py`.
- **Transaction types.** DEPOSIT, CREDIT, REFUND, INTEREST add money. WITHDRAWAL, DEBIT, PAYMENT, PURCHASE, FEE take money. TRANSFER moves money to the `to_account` column if there is one. With no type column, every transaction is treated as money out.
- **"Already processed"** includes rejected transactions, so an ID can never be replayed.
- **Order.** Transactions are processed in file order. Use `--sort-by-time` for timestamp order.
- **Flags apply to approved transactions only.** Rejected ones are reported with their reason instead.
- **"Exceeds" means strictly greater than**: exactly 10,000 is not flagged; a withdrawal of the whole balance is allowed.
- **Daily limit.** The `daily_limit` column is read as a cap on money out per calendar day. If your data means a number of transactions, pass `--account-limit-is count`.
- **Account status.** ACTIVE / OPEN / TRUE / YES / 1 count as active; anything else does not. No status column means all accounts are active.
- A transaction with an unreadable timestamp is processed and flagged, since the time-based rules cannot run on it.

## Code layout

| File | Responsibility |
|---|---|
| `main.py` | Command line: reads the options, runs the engine, prints and writes the reports |
| `config.py` | `Config`: every threshold and limit in one place |
| `models.py` | The data classes: `Account`, `Transaction`, `Movement`, `Result` |
| `parsing.py` | Text to amount / timestamp, and amount back to text |
| `loaders.py` | Reads the two CSVs and matches their column names |
| `validation.py` | The "invalid if" rules and rejection reasons (`Validator`) |
| `review.py` | The "must be reviewed if" rules and review reasons (`Reviewer`) |
| `engine.py` | `TransactionEngine`: validate -> update balance -> review, plus the summary counts |
| `reports.py` | Builds the three text reports and writes all output files |
| `test_engine.py` | Unit tests |

To change a business rule, edit `validation.py` or `review.py`; to change a number, edit `config.py` or pass a command-line option.
