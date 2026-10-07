<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.png">
    <img alt="Money Mom: let your AI look after your money like Mom would" src="assets/banner-light.png" width="100%">
  </picture>
</p>

<p align="center">
  <a href="README.md">简体中文</a> &nbsp;·&nbsp; <b>English</b> &nbsp;·&nbsp; <a href="https://imoneys10k.github.io/money-mom/?lang=en">Website</a>
</p>

<p align="center">
  <a href="https://github.com/imoneys10k/money-mom/actions/workflows/test.yml"><img alt="tests" src="https://github.com/imoneys10k/money-mom/actions/workflows/test.yml/badge.svg"></a>
  <img alt="status: pre-alpha" src="https://img.shields.io/badge/status-pre--alpha-C2603B">
  <img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-2b2b2b">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-2b2b2b">
</p>

# Money Mom

**Let your AI look after your money like Mom would.**

Money Mom is a **bookkeeping skill** for AI agents (Claude Code, Codex, Cursor, Gemini CLI, ...). You don't open a budgeting app: you say one sentence, or drop in a bank statement, and your AI records, queries and reconciles. The books are plain text files on your own computer. Nothing is uploaded and no company holds your data.

It is not another budgeting app. It makes AI bookkeeping **trustworthy**: the AI only understands what you said, and **a program keeps and checks the books**.

> **Status: pre-alpha (`0.1.0a2`).** The ledger core, the command line and the skill work and are tested. It is not on PyPI or npm yet, and reconciliation, statement import and investor features are still ahead (see the [roadmap](ROADMAP.md)). Don't make it the only copy of your books.

## One-sentence install

Tell your AI agent:

```text
Please read https://github.com/imoneys10k/money-mom/blob/main/INSTALL.md and follow it to install Money Mom for me.
```

The agent reads [INSTALL.md](INSTALL.md): it installs the `money-mom` command (pinned version, SHA-256 verified), puts the skill into your agent, asks whether you want Chinese or English account names and which base currency, and finishes with a self-check that writes nothing. It tells you what it is about to do at each step.

## What it feels like

```text
You   Lunch at Luckin, 38, paid with Alipay
Mom   Recorded: coffee 38, from Alipay.

You   Sent Xiao Wang 200
Mom   Is that a loan, a treat, or a gift?              <- asks when unsure, never guesses

You   Salary 18,500 arrived in my bank card
Mom   Recorded. Your savings rate is 39% so far, up from last month.

You   Why did I spend so much on takeout this month?
Mom   612 more than last month, mostly one 486 dinner on the 20th. Every number links to its entry.
```

> This is the target experience. Recording, follow-up questions and querying are implemented through the skill and command line; the savings-rate remark depends on the monthly report, which is planned.

## Why you can trust it

| Principle | How |
|---|---|
| **Double-entry** | Every amount records where it came from and where it went; each currency must balance exactly or the whole entry is refused. |
| **The AI never writes the books** | The AI says "spent 38, from Alipay, on coffee"; direction, checks and writing are done by a program. |
| **Append-only** | A mistake is voided and re-recorded, never erased. Every entry keeps its source, confidence and author. |
| **Never guesses** | Account names resolve only by full name, alias or a unique match; anything vague becomes *pending* with candidates listed. An AI confidence below 0.9 also waits. |
| **Locked after reconciling** | A period you checked against the bank is locked; only you can change it, with a written reason. |
| **Local-first** | Your data stays on your machine as human-readable text; the program does not use the network by default and has no runtime dependencies. The one exception is `rates update`, which you run yourself to fetch exchange rates; it sends only currency codes and a date, never an amount or an account. |

## How it works

```mermaid
flowchart LR
  U["You<br/>say a sentence / drop a file"] --> A["AI agent"]
  A -->|"reads SKILL.md"| S["money-mom skill"]
  S -->|"structured intent<br/>spend · income · transfer"| C["money-mom CLI<br/>resolves names · checks balance"]
  C --> L[("Local ledger<br/>append-only JSONL")]
  L --> Q[("SQLite read-only cache<br/>so the AI can query with SQL")]
```

The ledger is a stream of append-only events (open, transaction, confirm, void, balance assertion); balances are derived from it. See the [data model](docs/data-model.md) and the [design notes](docs/design.md) (in Chinese).

## Try the command line

Needs Python 3.11+. Install from source:

```bash
git clone https://github.com/imoneys10k/money-mom && cd money-mom
python -m pip install -e .

export MONEY_MOM_HOME=~/MoneyMom        # keep the ledger outside the repo
money-mom init --template en --base-currency USD --date 2026-01-01

money-mom transfer 5000 --from opening --to checking --date 2026-01-02   # opening balance
money-mom income 3200 --to checking --category salary --date 2026-10-01
money-mom spend 4.50 --from cash --category coffee --payee "Blue Bottle"  # direction is decided by the program
money-mom spend 45 --from card --category cof                              # unsure name: not guessed, kept pending

money-mom pending                                  # what is waiting for you
money-mom confirm <ID> --category restaurants      # fill it in by name, then it posts
money-mom balance                                  # exact balances
money-mom query "SELECT month, account, amount FROM v_monthly"
money-mom doctor                                   # self-check
```

## Many currencies

You are not tied to one currency:

```bash
money-mom open Assets:UsdAccount --currency USD               # a dollar-only account
money-mom spend '5 USD' --from cash --category coffee                  # currency in the amount: 5 USD, HK$200, 5美元
money-mom spend 5 --from UsdAccount --category coffee                   # or decided by the account: a USD-only account means USD
money-mom exchange 100 USD 720 CNY --from UsdAccount --to checking    # exchange: the rate you actually got is kept

money-mom rates update      # the only command that uses the network: ECB daily reference rates
money-mom networth          # all currencies as one total in your base currency
money-mom balance --account Assets --in CNY  # asset accounts converted, with a total
```

- **A currency is never guessed.** For an ambiguous symbol such as `$` or `¥`, the accounts involved decide first, then the currencies already in your ledger; a pick that is not your base currency waits for confirmation, and anything still unclear is a question with candidates. Single-quote a `$` (the shell treats `$5` as a variable) or use `--ccy USD`.
- **Rates are daily reference prices, not a live feed.** The source is the European Central Bank (via Frankfurter): one rate per business day, about 30 currencies, **no TWD**. For anything it does not cover, record one yourself with `money-mom rates set`, which requires a source.
- **Conversions can be checked.** Every rate is stored with its source; conversion uses only stored rates, never one dated after the day asked about. A currency with no rate is listed, not silently dropped, and rates older than 7 days are flagged.

Every command accepts `--json`; exit codes are 0 success, 1 the ledger refused, 2 usage error. Run `money-mom --help`, or see the [command reference](skills/money-mom/references/commands.md).

## Compatible AI agents

The skill follows the open [Agent Skills](https://agentskills.io) standard (`SKILL.md`) and passes the official validator. The same skill is meant to work in Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot and other agents that support the standard.

To be straight about it: the installer (`npx skills add`) has been verified in an isolated environment to place the skill where Claude Code, Codex and Gemini CLI read it. **Conversation behaviour has not been tested in every agent yet**; that is on the [roadmap](ROADMAP.md).

## Status

| | |
|---|---|
| Done | Ledger core · command line · SQLite queries · intents (spend / income / transfer) · many currencies (recognition, rate conversion, exchange) · Chinese and English account templates · skill and install guide · `doctor` · CI on Linux, macOS and Windows |
| Next | Monthly report · bank reconciliation and statement import (Alipay, WeChat, bank PDFs) · subscription and anomaly alerts · "Mom" tone levels |
| Later | PyPI / npm · Claude Code plugin marketplace · Beancount export · investor pack (HK/US holdings with cost basis, realised and unrealised gains) |

What it will not do: move money or place orders, store bank credentials, or do more than read-only import of bank and broker data.

## Documentation

- [INSTALL.md](INSTALL.md): install guide for agents
- [skills/money-mom/SKILL.md](skills/money-mom/SKILL.md): usage guide for agents
- [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md)
- [docs/design.md](docs/design.md) (design principles) · [docs/data-model.md](docs/data-model.md) · [docs/agents.md](docs/agents.md) (how each agent loads skills); these three are in Chinese for now

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Needs Python 3.11+, no third-party runtime dependencies. AI agents working on this repo should read [AGENTS.md](AGENTS.md) first. The repository is public: **never commit real financial data.**

## License

[MIT](LICENSE)
