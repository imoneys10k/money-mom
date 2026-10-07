# Money Mom

**让 AI 像妈妈一样帮你管钱。**
*Let your AI look after your money like Mom would.*

> 状态：**pre-alpha**。已有账本内核和命令行（可在本地跑，已测试），**还没有 skill，也没有发布到 PyPI / npm**。下面“目标形态”里的对话是最终要达到的样子，进度见 [ROADMAP.md](ROADMAP.md)。

Money Mom 是一个装进 AI agent（Claude Code、Codex 等）的**记账能力**。你不用打开记账 App：说一句话或丢一个文件，AI 帮你记账、查账、对账；账本是你自己电脑上的文本文件，不被任何公司锁住。

## 目标形态

```text
你    午饭瑞幸 38，支付宝付的
妈妈  记好了：咖啡 38 元，从支付宝扣。

你    给小王转了 200
妈妈  这笔算借给他的、请客，还是礼金？

你    （丢进 alipay_2026-09.csv）
妈妈  142 笔都分好类了，有 3 笔我拿不准，你看一下。

你    这个月外卖怎么花这么多？
妈妈  比上月多了 612 元，主要是 20 号一顿聚餐 486 元，加上月底外卖变多。
```

## 为什么可信

AI 只负责理解你的话，**账由程序记**：

- **复式记账**：每笔钱都记“从哪来、到哪去”，两边对不上就拒绝入账。
- **只追加、不改历史**：记错了就追加一条冲销，随时可追溯、可回滚。
- **每笔带来源**：来自哪张截图、哪封邮件、置信度多少。
- **不确定就问，不猜**：拿不准的标为待确认，缺失的数据不会被当成 0。
- **本地优先**：数据默认只在你自己的机器上。

## 计划中的一句话安装

对任何受支持的 AI agent 说：

```text
请阅读 https://github.com/imoneys10k/money-mom/blob/main/INSTALL.md 并按说明为我安装 Money Mom。
```

`INSTALL.md` 和各平台安装包还在计划中（见 ROADMAP 的“安装与分发”）。

计划的做法：Money Mom 以通用的 [Agent Skills](https://agentskills.io) 标准（`SKILL.md`）发布，一份 skill 即可用于 Claude Code、Codex、Cursor、Gemini CLI、GitHub Copilot 等；记账引擎是发布在 PyPI 的 Python 包，由 skill 通过 `uvx` 调用，另提供 `npx` 启动器。各 agent 的加载方式见 [docs/agents.md](docs/agents.md)。

## 定位

- **通用**：普通人的日常记账，说话就能记。
- **专攻**：多地区、多账户的投资者——国内支付、港美银行与券商、多币种净资产（计划中的“投资者包”）。

## 现在就能试的（命令行）

需要 Python 3.11+。目前要从源码安装：

```bash
git clone https://github.com/imoneys10k/money-mom && cd money-mom
python -m pip install -e .

export MONEY_MOM_HOME=~/MoneyMom        # 账本放在仓库之外
money-mom init --template cn --date 2026-01-01      # 中文账户树：现金、支付宝、微信、银行卡……

money-mom transfer 20000 --from 期初 --to 银行卡 --date 2026-01-02   # 期初余额
money-mom income 18500 --to 银行卡 --category 工资 --date 2026-10-01
money-mom spend 38 --from 支付宝 --category 咖啡 --payee 瑞幸          # 借贷方向由程序决定
money-mom spend 45 --from 微信 --category 咖                          # 名字不确定：不猜，记为待确认

money-mom pending                                  # 看有什么等你确认
money-mom confirm <ID> --category 餐饮:聚餐         # 按名字补全，再入账
money-mom balance                                  # 精确到分的余额
money-mom check                                    # 重放整个账本并复核断言
money-mom query "SELECT month, account, amount FROM v_monthly"
```

要点：

- **从不靠猜。** 账户名只接受全名、别名（`money-mom alias add 招行卡 Assets:银行卡`）、或唯一匹配的末段（`咖啡` 即 `Expenses:餐饮:咖啡`）。说得含糊时整笔记为待确认，并列出候选账户。`--strict` 改为直接报错，`--dry-run` 只预览不写入。
- **置信度。** agent 记账时可带 `--confidence`，低于 0.9（可在 `money-mom.toml` 的 `auto_post_confidence` 调整）先记为待确认。
- 不平衡的账会被拒绝，退出码为 1；加 `--json` 得到给 agent 解析的结构化结果。需要手写分录时用 `money-mom add --posting "账户 金额 币种"`。完整命令见 `money-mom --help`。

## 开发

需要 Python 3.11 或更新版本，运行时没有第三方依赖。

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

## 文档

- [ROADMAP.md](ROADMAP.md)：功能待办与里程碑
- [docs/design.md](docs/design.md)：设计原则与已做的决定
- [AGENTS.md](AGENTS.md)：给参与开发的 AI agent 的约定
- [CHANGELOG.md](CHANGELOG.md)：变更记录

## 不做什么

不碰转账、下单等任何资金操作；不保存银行凭证；银行与券商数据只做只读导入。

## 许可证

[MIT](LICENSE)
