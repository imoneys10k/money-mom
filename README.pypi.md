# Money Mom

**Let your AI look after your money like Mom would.** 让 AI 像妈妈一样帮你管钱。

Money Mom is a **bookkeeping skill for AI agents** (Claude Code, Codex, Cursor, Gemini CLI, ...). You say a sentence, or drop in a bank statement, and your AI records, queries and reconciles. The books are plain text files on your own computer. Nothing is uploaded and no company holds your data.

It makes AI bookkeeping **trustworthy**: the AI only understands what you said, and **a program keeps and checks the books**.

> **Pre-alpha.** The core works and is tested, but don't keep your only copy of your books in it.

## What this package is

The engine behind the skill: the `money-mom` command line and the Python library. It has **no runtime dependencies** and needs Python 3.11 or newer.

- **Double-entry**: every currency must balance exactly or the entry is refused.
- **Append-only**: a mistake is voided and re-recorded, never erased; every entry keeps its source, confidence and author.
- **Never guesses**: an unclear account or currency becomes a question or a *pending* entry, never a silent guess.
- **Locked once reconciled**: a period checked against the bank can only be changed by you, with a written reason.
- **Many currencies, statements, reconciliation**: currency recognition, exchange rates you fetch yourself, CSV statement import with reusable mappings, line-by-line reconciliation.
- **Local-first**: the program does not use the network, except `money-mom rates update`, which you run yourself to fetch exchange rates (it sends only currency codes and a date).

## Install

Say this to your AI agent and let it follow the guide:

```text
Please read https://github.com/imoneys10k/money-mom/blob/main/INSTALL.md and follow it to install Money Mom for me.
```

Or do it yourself. This is a pre-release, so the version is pinned (`pip` and `uv` skip pre-releases otherwise):

```bash
uv tool install "money-mom==0.1.0a3"      # or: pipx install "money-mom==0.1.0a3"
money-mom doctor
money-mom init --template en --base-currency USD
money-mom spend 4.50 --from cash --category coffee
money-mom balance
```

Without installing anything permanent: `npx money-mom doctor` (needs [uv](https://docs.astral.sh/uv/)).

## Links

- Source, issues and the skill for your agent: https://github.com/imoneys10k/money-mom
- Website: https://imoneys10k.github.io/money-mom/
- 中文说明: https://github.com/imoneys10k/money-mom/blob/main/README.md
- Changelog: https://github.com/imoneys10k/money-mom/blob/main/CHANGELOG.md

MIT licensed.
