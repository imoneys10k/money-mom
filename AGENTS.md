# Agent 约定

给参与开发 Money Mom 的 AI agent。开始前先读 [README.md](README.md)、[ROADMAP.md](ROADMAP.md) 和 [docs/design.md](docs/design.md)。

## 红线

- 仓库是公开的：**不得提交任何真实财务数据、账号、凭证、密钥或个人信息。** 示例与测试只用合成数据。
- 不实现任何转账、下单或其他资金操作。
- 不让 LLM 直接写账本文本；必须经结构化意图和确定性校验。
- 缺失数据保持缺失，**null 不是 0**。
- 账本只追加，修正靠冲销事件，不改历史。

## 工作方式

- 先做最小可验证的改动，再考虑抽象。一次只推进 ROADMAP 里的一项。
- 每完成一项：勾选 ROADMAP、更新 README 状态、追加 CHANGELOG；需要时发 Release。
- README 只描述已经存在的能力，计划中的内容必须标明“计划中”。
- 涉及金额的逻辑必须有测试，包括借贷平衡与多币种的边界情况。
- 不引入不必要的第三方运行时依赖。
- **README.md（中文，默认）与 README.en.md 必须同步更新**；主页 `site/index.html` 的中英文也要一起改。
- 改了 `skills/money-mom/SKILL.md` 或任何命令的行为，要把 SKILL.md 里的命令真的跑一遍；校验：`uvx --from "git+https://github.com/agentskills/agentskills#subdirectory=skills-ref" skills-ref validate skills/money-mom`。
- 发布按 [docs/releasing.md](docs/releasing.md) 的顺序，尤其是：README 定稿后才构建 wheel、哈希写进 INSTALL.md、发布后在隔离环境验证。
- 规则要配“变异测试”：故意破坏一条规则，确认至少一个测试会失败；没失败说明测试没覆盖到。
