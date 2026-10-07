# Command reference

Global options, accepted before or after the command:

| Option | Meaning |
|---|---|
| `--ledger PATH` | Ledger directory. Default: `$MONEY_MOM_HOME`, else `~/MoneyMom`. |
| `--json` | Print one JSON document: `{"ok": true, "data": ...}` or `{"ok": false, "error": {...}}`. |
| `--actor TYPE:NAME` | Who is writing: `human:NAME` or `agent:NAME`. Default: `$MONEY_MOM_ACTOR`, else `human:user`. |

Exit codes: `0` success, `1` the ledger refused or something is wrong with it, `2` the command line itself was wrong.

## Recording

Shared flags for `spend`, `income`, `transfer` and `add`:
`--date YYYY-MM-DD` (default today), `--narration`, `--payee`, `--import-hash`, `--confidence 0..1`, `--source-type chat|file|screenshot|import|manual`, `--source-ref TEXT`, `--source-sha256 HEX`.
`--source-ref` must not be an absolute path when the type is `file`, `screenshot` or `import`.

| Command | Purpose |
|---|---|
| `spend AMOUNT --from ACCOUNT --category CATEGORY` | Expense paid from an asset or liability account. |
| `income AMOUNT --to ACCOUNT --category CATEGORY` | Money received from an income category. |
| `transfer AMOUNT --from ACCOUNT --to ACCOUNT` | Move money between your own accounts. Opening balances: `--from 期初` (Equity). |

Extra flags for those three: `--ccy` (default: base currency), `--strict` (fail instead of recording a pending entry when a name cannot be resolved), `--dry-run` (show the result, write nothing), `--override-lock REASON` (human only).

`AMOUNT` is positive; the direction comes from the command. A name can be a full account name, an alias, or a trailing run of name segments that matches exactly one open account (`咖啡` or `餐饮:咖啡`). A substring is only a suggestion and never accepted.

`spend`, `income` and `transfer` answer with `data.status` (`posted` or `pending`), `data.postings`, `data.unresolved` (slot, reason, candidates) and `data.reasons`.

| Command | Purpose |
|---|---|
| `add --posting "ACCOUNT AMOUNT CCY" --posting ...` | Record explicit postings. Use `?` as the account for an unknown one (pending only). `--status posted|pending`. |
| `add --from-json FILE\|-` | A transaction object, or a list of them written atomically. Fields: `date`, `status`, `narration`, `payee`, `postings`, `import_hash`, `source`, `confidence`, `meta`. |
| `confirm ID` | Turn a pending entry into a posted one as it is. |
| `confirm ID --category/--from/--to NAME` | Fill a slot of an entry made by `spend`/`income`/`transfer`. |
| `confirm ID --posting ...` | Replace the postings. Not combinable with slot flags. |
| `void ID --reason TEXT` | Void a transaction or an assertion. Nothing is deleted. |
| `assert ACCOUNT AMOUNT CCY [--date D]` | State the balance at the end of a day; locks that period for the account. |

## Accounts and names

| Command | Purpose |
|---|---|
| `init [--template cn\|en] [--base-currency CCY] [--tone gentle\|normal\|strict\|zen] [--date D]` | Create a ledger. `--date` is when template accounts open. |
| `open ACCOUNT [--date D] [--currency CCY ...]` | Open an account. Names look like `Assets:招行卡`; the first segment is `Assets`, `Liabilities`, `Equity`, `Income` or `Expenses`. |
| `close ACCOUNT [--date D]` | Close an account. Its balance must be zero. |
| `accounts` | List accounts. |
| `alias add ALIAS ACCOUNT` / `alias list` / `alias remove ALIAS` | Friendly names for accounts. Case-insensitive. |
| `resolve TERM [--type asset\|liability\|equity\|income\|expense] [--date D]` | Which account does a name mean, or why not? |

## Looking

| Command | Purpose |
|---|---|
| `balance [--account PREFIX] [--as-of D] [--all]` | Exact balances. Zero balances hidden unless `--all`. |
| `pending` | Entries waiting for confirmation. |
| `show ID` | One transaction or assertion with every event that touched it. |
| `query "SELECT ..." [--limit N]` | One read-only statement against the SQLite cache. See `sql.md`. |
| `check` | Replay the whole ledger and re-verify every assertion. |
| `doctor` | Install and ledger health, plus settings (tone, base currency, threshold, pending count). A missing ledger is not an error. |
