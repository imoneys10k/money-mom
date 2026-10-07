# Querying the ledger

`money-mom query "SELECT ..."` runs one read-only statement against a SQLite cache that is rebuilt from the ledger whenever it changed. Anything other than a single `SELECT` (or `WITH ... SELECT`) is refused. There is a 5 second limit and a default limit of 1000 rows (`--limit`).

Amounts: `amount_text` is exact. `amount` is a float so that `SUM` works; round it when presenting (`ROUND(x, 2)`), and use `money-mom balance` when exactness matters.

## Views (start here)

| View | Contains |
|---|---|
| `v_postings` | One row per posting of every **posted** transaction: `txn_id, idx, account, amount_text, amount, ccy, date, narration, payee, source_type, source_ref, confidence, actor_type, actor_name` |
| `v_balances` | `account, ccy, amount`: current balance. |
| `v_monthly` | `month ('YYYY-MM'), account, ccy, amount`: activity per month. |
| `v_pending` | Entries waiting for confirmation: `id, date, payee, narration, confidence, source_type, source_ref, actor_type, actor_name, postings`. |

Signs follow double entry: **expenses are positive; income, liabilities you owe, and equity are negative**; asset balances are positive. Pending and voided entries are in none of the views.

## Tables

`txns(id, date, status, narration, payee, import_hash, source_type, source_ref, confidence, actor_type, actor_name, ts, confirmed_by, voided_by)` with `status` of `posted`, `pending` or `voided`; `postings(txn_id, idx, account, amount_text, amount, ccy)` where `account` is NULL for an unresolved side; `accounts(name, root, opened, closed, currencies)`; `assertions(id, date, account, amount_text, amount, ccy, voided_by)`; `events(seq, id, kind, ts, actor_type, actor_name, target)`.

## Examples

This month by top-level category:

```sql
WITH e AS (SELECT date, amount, substr(account, 10) AS rest FROM v_postings WHERE account LIKE 'Expenses:%')
SELECT substr(rest, 1, instr(rest || ':', ':') - 1) AS category, ROUND(SUM(amount), 2) AS total
FROM e WHERE date >= '2026-10-01' AND date < '2026-11-01'
GROUP BY category ORDER BY total DESC
```

September against October, per expense account:

```sql
SELECT account,
       ROUND(SUM(CASE WHEN month = '2026-09' THEN amount END), 2) AS sep,
       ROUND(SUM(CASE WHEN month = '2026-10' THEN amount END), 2) AS oct
FROM v_monthly WHERE account LIKE 'Expenses:%' AND month IN ('2026-09', '2026-10')
GROUP BY account ORDER BY account
```

The biggest single expenses of a month (to explain a jump):

```sql
SELECT date, COALESCE(payee, narration, '') AS what, account, amount_text, ccy
FROM v_postings
WHERE account LIKE 'Expenses:%' AND date >= '2026-10-01' AND date < '2026-11-01'
ORDER BY amount DESC LIMIT 5
```

Most frequent merchants:

```sql
SELECT payee, COUNT(*) AS n, ROUND(SUM(amount), 2) AS total
FROM v_postings WHERE account LIKE 'Expenses:%' AND payee IS NOT NULL
GROUP BY payee ORDER BY total DESC LIMIT 10
```

Weekly dining:

```sql
SELECT strftime('%Y-W%W', date) AS week, ROUND(SUM(amount), 2) AS total
FROM v_postings WHERE account LIKE 'Expenses:餐饮%'
GROUP BY week ORDER BY week DESC LIMIT 8
```

Net worth per currency (currencies are never converted or mixed):

```sql
SELECT ccy, ROUND(SUM(amount), 2) AS net
FROM v_balances WHERE account LIKE 'Assets:%' OR account LIKE 'Liabilities:%'
GROUP BY ccy
```

Recurring charges that changed price (same payee, different amounts):

```sql
SELECT payee, MIN(amount) AS lowest, MAX(amount) AS highest, COUNT(*) AS times
FROM v_postings WHERE account LIKE 'Expenses:%' AND payee IS NOT NULL
GROUP BY payee HAVING COUNT(*) > 1 AND MIN(amount) <> MAX(amount)
```

Everything an agent recorded with low confidence, still waiting:

```sql
SELECT id, date, payee, confidence, postings FROM v_pending ORDER BY date
```
