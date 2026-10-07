# money-mom (npm launcher)

**Let your AI look after your money like Mom would.** 让 AI 像妈妈一样帮你管钱。

This package only lets you run [Money Mom](https://github.com/imoneys10k/money-mom) with `npx`. The engine itself is a Python package (`money-mom` on PyPI); this launcher runs the exact version it was published with, through [uv](https://docs.astral.sh/uv/).

```bash
npx money-mom doctor
npx money-mom init --template en --base-currency USD
npx money-mom spend 4.50 --from cash --category coffee
```

- It **installs nothing** by itself. If `uv` is missing it prints how to install it and stops; it never runs an installer for you.
- Your books stay on your computer, as plain text files (default `~/MoneyMom`).
- The program does not use the network, except `money-mom rates update`, which you run yourself to fetch exchange rates.
- Pre-release software: don't keep your only copy of your books in it.

To let your AI agent install and use Money Mom, say to it:

```text
Please read https://github.com/imoneys10k/money-mom/blob/main/INSTALL.md and follow it to install Money Mom for me.
```

Source, docs and the skill for your agent: https://github.com/imoneys10k/money-mom · 中文: https://github.com/imoneys10k/money-mom/blob/main/README.md · MIT licensed.
