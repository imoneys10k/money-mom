# 设计原则与决定

## 定位

Money Mom 是**账本的 agent 接口层**，不是又一个记账 App。价值在于：记账成本接近零、账目可信可追溯、数据在本地、不绑定某个 AI。

## 已做的决定

1. **复式记账。** 每笔交易的分录合计为零，否则拒绝入账。
2. **自研内核，输出标准格式。** 自己掌握数据模型与 agent 工作流，同时能导出 Beancount，用户不被锁住。
3. **事件溯源。** 账本是只追加的事件流（`txn` / `assert` / `void`），当前状态由事件推导；修正靠追加冲销，不改历史。
4. **LLM 不直接写账。** agent 输出结构化意图，由确定性脚本渲染成分录并校验；校验通过才写入。
5. **propose → validate → commit。** 低置信度进待确认区，由人确认。
6. **每条记录带来源与置信度。** 来源如截图、邮件、对话，用于追溯。
7. **缺失不是零。** 识别不出的金额或分类标为待确认，不静默猜测。
8. **SQL 查询。** 事件流推导出 SQLite，agent 直接写 SQL（LLM 对 SQL 比对 BQL 熟练）。
9. **CLI 是产品本体。** 三层兼容：CLI（任何有 shell 的 agent）→ 指令层（SKILL.md、AGENTS.md）→ 可选 MCP 服务。
10. **导入规则由 LLM 生成、规则确定性执行。** 新格式只需用户确认一次，之后不再调用 LLM。
11. **数据与代码分离。** 仓库只含合成的 demo 数据，用户真实账本放在仓库之外。
12. **人格只是语气。** “妈妈”影响说话方式，不影响数字，也不替用户做资金决定。
13. **不做资金操作。** 不转账、不下单、不存银行凭证，银行与券商数据只读导入。
14. **实现语言：Python 标准库。** 金额与持仓批次计算最怕出错，`decimal` 与 `fractions` 是标准库现成的。通过 `uvx` 分发，`uv` 会自带合适的 Python，不依赖用户系统 Python 版本。
15. **npm 只做轻量启动器。** `npx money-mom` 负责找到或安装 `uv`，再调用 Python 引擎；内核不用 JS 重写。
16. **skill 采用 Agent Skills 开放标准（`SKILL.md`）。** 一份 skill 覆盖 Claude Code、Codex、Cursor、Gemini CLI、GitHub Copilot 等；`SKILL.md` 只用规范里的通用字段。详见 [agents.md](agents.md)。
17. **引擎与 skill 分离，并锁定引擎版本。** skill 只是使用说明，通过 `uvx money-mom==x.y.z` 调用 PyPI 上的引擎。
18. **`AGENTS.md` 作为不支持 skill 的 agent 的兜底。**

## 待决策

- 事件流文件的具体格式与字段（M0 的 `docs/data-model.md`）。
- Python 最低版本（倾向 3.11，由 `uvx` 提供）。
- 引擎是随 skill 打包在 `scripts/` 里，还是只走 PyPI（目前倾向只走 PyPI，需要离线使用时再补）。
