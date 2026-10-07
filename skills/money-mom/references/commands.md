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

Extra flags for those three: `--ccy` (a code or word such as `USD` or `美元`; default: what the amount says, else what the accounts allow, else the base currency), `--strict` (fail instead of recording a pending entry when a name cannot be resolved), `--dry-run` (show the result, write nothing), `--override-lock REASON` (human only).

`AMOUNT` is positive; the direction comes from the command. It may carry its currency: `5美元`, `'HK$200'`, `'USD 5'`, `1,200元`. Single-quote anything with a `$`. A name can be a full account name, an alias, or a trailing run of name segments that matches exactly one open account (`咖啡` or `餐饮:咖啡`). A substring is only a suggestion and never accepted.

`spend`, `income` and `transfer` answer with `data.status` (`posted` or `pending`), `data.postings`, `data.unresolved` (slot, reason, candidates) and `data.reasons`.

| Command | Purpose |
|---|---|
| `add --posting "ACCOUNT AMOUNT CCY" --posting ...` | Record explicit postings. Use `?` as the account for an unknown one (pending only). `--status posted|pending`. |
| `add --from-json FILE\|-` | A transaction object, or a list of them written atomically. Fields: `date`, `status`, `narration`, `payee`, `postings`, `import_hash`, `source`, `confidence`, `meta`. |
| `exchange GIVE GET --from ACCOUNT --to ACCOUNT` | A currency exchange, e.g. `'100 USD' '720 CNY'`. Recorded through an Equity conversion account; the rate obtained is kept in the entry. Flags: `--give-ccy`, `--get-ccy`, plus the shared ones, `--strict`, `--dry-run`. |
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
| `balance [--account PREFIX] [--as-of D] [--all] [--in CCY]` | Exact balances. Zero balances hidden unless `--all`. `--in` also converts each balance with stored rates, and with `--account` gives a total (without it the total is omitted, since all accounts together net to zero); currencies without a rate are listed as missing (`partial`), never dropped. |
| `pending` | Entries waiting for confirmation. |
| `currency TEXT [--ccy C] [--account NAME ...]` | Which currency a piece of text means, and how it was decided. |
| `networth [--in CCY] [--as-of D]` | Assets minus liabilities per currency and as one total, from stored rates. |
| `report [--month YYYY-MM] [--in CCY] [--top N]` | Monthly report (read-only): income, spending, net and savings rate, spending by category with change from last month, the biggest items, net worth change, and what to double-check. |
| `alerts [--month YYYY-MM] [--in CCY]` | Read-only. Recurring charges (subscriptions) with cadence, amount, last and next date and cost per month, plus alerts worth a look: `price_change`, `overdue`, `large_expense`, `category_spike`, `possible_duplicate`. Every alert carries entry IDs and a summary; the rules come with the result. |
| `chart [KIND] [--month YYYY-MM] [--months N] [--in CCY] [--lang zh\|en] [--format html\|svg\|json] [--hide-amounts] [--out PATH] [--force]` | Draw charts (read-only; writes only the chart file). KIND: `sheet` (default, all charts on one HTML page), `spending`, `change`, `trend`, `networth`, `waterfall`. `--format json` prints the figures behind a chart and writes nothing; `--hide-amounts` draws shares and shapes only. Default file: `<ledger>/charts/`. |
| `rates update [--currency C ...] [--date D] [--dry-run]` | **The only command that uses the network.** Fetch ECB daily reference rates into the base currency and store them with their source. Sends only currency codes and a date. |
| `rates set BASE QUOTE RATE --source TEXT [--date D]` | Record a rate yourself: 1 BASE = RATE QUOTE. A source is required. |
| `rates list [--base B] [--quote Q]` | The latest stored rate for each pair. |
| `show ID` | One transaction or assertion with every event that touched it. |
| `import inspect FILE [--encoding E]` | Look at a statement (CSV or `.xlsx`) and suggest a mapping. Writes nothing. Says what it cannot decide (day/month dates, which sign is money in). |
| `import save-map NAME FILE\|-` / `import maps` | Validate a mapping (TOML) and store it under the ledger's `imports` folder / list the saved ones. |
| `import run FILE --map NAME --account ACCOUNT [--since D] [--until D] [--confidence F] [--encoding E] [--dry-run]` | Import a statement. Rows a rule matched post, the rest wait as pending, grouped by payee in the answer. A row that looks like an existing entry is held as pending too (`held_for_duplicates`) until `dupes resolve`. Repeats are skipped; the batch is all-or-nothing; a refused row is named by its statement line. An agent must give `--confidence`. |
| `import rule-add --map NAME --match TEXT (--account ACCOUNT \| --skip) [--field any\|payee\|description] [--regex]` / `import rule-list --map NAME` | Teach or list rules of a mapping (appended, lower priority than existing ones). |
| `import recheck --map NAME [--dry-run]` | After adding rules, confirm the pending rows of that mapping that now match. |
| `reconcile ACCOUNT FILE --map NAME` / `reconcile ACCOUNT --from-json FILE\|-` | Compare a statement with the ledger: missing, extra, different amounts, possible double charges, closing balance. Options: `--since`, `--until`, `--tolerance-days N` (default 3), `--closing-balance X --closing-date D` or `--closing-from-statement`, `--assert` (seal a clean result; locks the period). Changes nothing unless `--assert`. |
| `query "SELECT ..." [--limit N]` | One read-only statement against the SQLite cache. See `sql.md`. |
| `dupes list [--window N]` | Read-only (plain `dupes` does the same). Possible duplicate payments across sources: each pair with its `tier` (`likely` / `possible`), the evidence, and which one to keep if they are the same. Held statement rows are marked. Never decides; ask the user. |
| `dupes resolve ID1 ID2 (--same --keep ID \| --different) [--user-said TEXT]` | Record the user's answer. `--same` voids the other one (nothing is deleted); `--different` is remembered and releases a held row into the books. An agent must pass `--user-said`. |
| `verify` | Check the ledger's hash chain: finds events edited, removed or added by hand. Prints the chain head. Exit code 1 if something does not match. |
| `check` | Replay the whole ledger, re-verify every assertion and the hash chain. |
| `doctor` | Install and ledger health, plus settings (tone, base currency, threshold, pending count). A missing ledger is not an error. |
