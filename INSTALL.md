# Install Money Mom (instructions for an AI agent)

> 这份文档是写给 AI agent 读的安装说明。用户只需要对你说：「请阅读 https://github.com/imoneys10k/money-mom/blob/main/INSTALL.md 并按说明为我安装 Money Mom」。

If a user asked you to install Money Mom, follow this file from top to bottom.

**How to behave**

- Before each step that changes the machine, tell the user in one sentence what you are about to do. Ask first for steps marked **[ask]**.
- Never use `sudo`. Never skip the checksum or the verification steps. Never pipe a download into a shell except the official `uv` installer in Step 2a, and only after the user agreed.
- If a step fails, stop and report the exact error. Do not look for a way around a failed check.
- Money Mom is pre-release software (`0.1.0a3`). Say so to the user.

## What gets installed

1. **The `money-mom` command**: a pinned release (`0.1.0a3`), a Python wheel downloaded from this repository's GitHub release and verified against a SHA-256 checksum below.
2. **The `money-mom` skill**: instructions for you, copied into your agent's skills folder.
3. **Optionally a ledger folder** (default `~/MoneyMom`): plain text files that hold the user's books.

Nothing runs in the background and nothing is uploaded. The program uses the network only when the user runs `money-mom rates update` to fetch exchange rates, and then it sends nothing but currency codes and a date. No account is needed.

## Step 1. See what is already there

```bash
money-mom --version
uv --version
```

If `money-mom --version` already prints `0.1.0a3`, skip to Step 3.

## Step 2. Install the command

### 2a. `uv` **[ask, only if it is missing]**

`uv` installs Python programs in an isolated place and fetches a suitable Python by itself, so the user's system Python does not matter. Use the official installer:

- macOS and Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

If the user would rather not install `uv`, use the pip route in 2d (needs Python 3.11 or newer).

### 2b. Download the wheel and check it

The release asset is `money_mom-0.1.0a3-py3-none-any.whl`. Its SHA-256 is:

```
65f6019f0972e37226f2ae96757b8b6f27c285945f66206c259923aef3473a98
```

macOS and Linux:

```bash
cd "$(mktemp -d)"
curl -fsSLO https://github.com/imoneys10k/money-mom/releases/download/v0.1.0a3/money_mom-0.1.0a3-py3-none-any.whl
echo "65f6019f0972e37226f2ae96757b8b6f27c285945f66206c259923aef3473a98  money_mom-0.1.0a3-py3-none-any.whl" | shasum -a 256 -c -
```

(On Linux without `shasum`, use `sha256sum -c -`.) Windows (PowerShell):

```powershell
cd (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ([guid]::NewGuid())))
curl.exe -fsSLO https://github.com/imoneys10k/money-mom/releases/download/v0.1.0a3/money_mom-0.1.0a3-py3-none-any.whl
(Get-FileHash .\money_mom-0.1.0a3-py3-none-any.whl -Algorithm SHA256).Hash.ToLower()   # must equal the value above
```

**If the checksum does not match, stop.** Tell the user and do not install.

### 2c. Install it

```bash
uv tool install ./money_mom-0.1.0a3-py3-none-any.whl
money-mom --version
```

If `money-mom` is not found, the tool folder is not on `PATH`. Run `uv tool update-shell` and tell the user that a new terminal may be needed. Meanwhile call it by its full path: `"$(uv tool dir --bin)/money-mom"`, and use that path for every later command in this conversation.

### 2d. Without `uv` (pip route)

Needs Python 3.11 or newer (`python3 --version`). From the folder holding the verified wheel:

```bash
python3 -m pip install --user ./money_mom-0.1.0a3-py3-none-any.whl
```

## Step 3. Install the skill

Find out which agent you are, then use the first method that works.

### Method A: the universal installer (needs Node.js)

```bash
DISABLE_TELEMETRY=1 npx -y skills add imoneys10k/money-mom --skill money-mom -g -a <agent> -y
```

`<agent>` is one of `claude-code`, `codex`, `cursor`, `gemini-cli`, `github-copilot`. Add one `-a` per agent the user wants. On Windows add `--copy` so it copies instead of symlinking. The installer is a third-party tool (Vercel's `skills`); it can send anonymous usage data, which `DISABLE_TELEMETRY=1` turns off. It puts the real files in `~/.agents/skills/money-mom` (read directly by Codex, Cursor, Gemini CLI and GitHub Copilot) and links Claude Code to it.

### Method B: copy the folder (no Node.js)

```bash
git clone --depth 1 --branch v0.1.0a3 https://github.com/imoneys10k/money-mom.git "$(mktemp -d)/mm"
```

(git may print `refs/tags/v0.1.0a3 ... is not a commit!`; that is only a warning about the annotated tag and the clone still succeeds), then copy `skills/money-mom/` from that clone to:

| Agent | Folder |
|---|---|
| Claude Code | `~/.claude/skills/money-mom/` |
| Codex, Cursor, Gemini CLI, GitHub Copilot | `~/.agents/skills/money-mom/` |

Some agents only read skills when a session starts. After installing, tell the user to open a **new session** so the skill is picked up.

## Step 4. Check it works

```bash
money-mom doctor
```

Expect `[ok]` lines for `python` and `sqlite`, and a note that there is no ledger yet. That is the normal state at this point and counts as healthy.

## Step 5. Create the ledger **[ask]**

Ask the user, in their language:

1. **Account names in Chinese or English?** (`--template cn` or `--template en`)
2. **Base currency?** Default `CNY` for `cn`, `USD` for `en`.
3. **Where?** Default `~/MoneyMom`. If they choose another place, set `MONEY_MOM_HOME` for them or pass `--ledger` every time.

Then:

```bash
money-mom init --template cn --base-currency CNY --date <this year>-01-01
money-mom doctor
```

Use January 1st of the current year as `--date` so entries can be back-dated to the start of the year.

Explain briefly: the books are plain text files in that folder, they never leave the computer, and they are worth backing up. Offer to run `git init` inside the ledger folder so every change is kept in history; do it only if the user says yes.

## Step 6. A harmless test

```bash
money-mom spend 1 --from 现金 --category 咖啡 --dry-run     # English template: --from cash --category coffee
```

It must print `Dry run, nothing written`. This proves names resolve and nothing is changed.

## Step 7. Tell the user it is ready

Say what you installed, where the ledger is, and show them how to talk to you, for example:

> 「午饭花了 38，支付宝付的」 · 「工资 18500 到银行卡了」 · 「这个月餐饮花了多少？」 · 「帮我对一下招行的账单」

Mention that anything you are unsure of is kept as *pending* for them to confirm, and that nothing is ever deleted or edited, only corrected by adding entries.

## Upgrading from an earlier version

If `money-mom --version` prints an older version, run Step 2 again (`uv tool install` replaces the old version) and Step 3 again (it overwrites the skill), then Step 4. **The ledger needs no migration**: an existing ledger keeps working, and its SQLite cache rebuilds itself.

One thing older ledgers lack: `exchange` records through an Equity account named `汇兑` (or `Conversions`). If `exchange` answers `no_conversion_account`, create it once with `money-mom open Equity:汇兑` (or `Equity:Conversions`). Ledgers created from the current templates already have it.

## Uninstall

```bash
uv tool uninstall money-mom                 # the command
rm -rf ~/.agents/skills/money-mom ~/.claude/skills/money-mom    # the skill (whichever exist)
```

The ledger folder is the user's data. **Never delete it unless the user explicitly asks.**

## Notes for the careful

- Everything is open source under the MIT license: https://github.com/imoneys10k/money-mom
- The wheel contains pure Python and no compiled code, and the program has no runtime dependencies.
- The wheel is reproducible: building the tagged source gives the same SHA-256.
