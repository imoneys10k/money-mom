# Money Mom

**让 AI 像妈妈一样帮你管钱。**
*Let your AI look after your money like Mom would.*

> 状态：**pre-alpha**。目前只有账本内核（Python 库，已测试），**还没有命令行、skill 和可安装的版本**，下面的用法是目标形态，进度见 [ROADMAP.md](ROADMAP.md)。

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
