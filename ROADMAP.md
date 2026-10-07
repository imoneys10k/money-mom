# Roadmap

每完成一项：勾选这里 → 更新 README 状态 → 追加 CHANGELOG → 需要时发 Release → 更新主页。
`[x]` 已完成，`[ ]` 未开始。版本号是目标，不是承诺。

## M0 · 立项与决策

- [x] 命名 Money Mom，确定 slogan
- [x] 创建公开仓库，MIT 许可证
- [x] 确定方向：开源、复式记账、兼容多种 AI agent、通用加投资者专攻
- [x] 决策：实现语言为 Python 标准库，npm 仅做轻量启动器
- [x] 决策：折中路线——自研内核与事件流存储（JSONL + SQLite 缓存），同时导出 Beancount
- [x] 调研各 agent 的 skill 加载方式，结论见 [docs/agents.md](docs/agents.md)（MCP 配置留到 M6 再查）
- [x] 数据模型 v0 规格草案：[docs/data-model.md](docs/data-model.md)（待你确认后再进入实现）

## M1 · v0.1 内核与“记一笔”

- [x] 账户、分录、币种与高精度金额、借贷平衡校验（`src/money_mom/`，82 个测试）
- [x] 事件流存储：只追加、按月分文件、`void` 冲销、跨进程文件锁、整批全有或全无
- [x] SQLite 缓存：指纹校验、按需重建、只读沙箱（授权器 + 超时 + 行数上限），含 `v_postings` / `v_pending` / `v_balances` / `v_monthly` 视图
- [x] CLI：`init` / `open` / `close` / `add`（含 `--from-json` 批量原子写入）/ `confirm` / `void` / `assert` / `check` / `balance` / `pending` / `accounts` / `show` / `query`，均支持 `--json`
- [x] 结构化意图 `spend` / `income` / `transfer` → 分录渲染；名字解析（全名、别名、唯一末段；子串只作候选，从不自动采纳）；`--dry-run`、`--strict`
- [x] propose → validate → commit：未解析的名字或低于阈值（默认 0.9）的置信度一律记为待确认，`pending` 列出，`confirm ID --category 餐饮` 按槽位补全
- [x] 余额断言与锁定（含人工覆盖、agent 不可覆盖）
- [x] 账户树模板：`init --template cn|en`（含别名）；`alias` 与 `resolve` 命令
- [x] 换汇 `exchange`（经 Equity 中转账户，每个币种各自平衡，记下实际成交汇率）
- [ ] 更多意图：`refund`（退款）、借出与还款
- [ ] 导出 Beancount，并以 `bean-check` 做差分测试（开发期依赖，非运行时依赖）
- [x] `SKILL.md`（通过官方校验器）与命令、SQL 参考；`doctor` 自检；agent 记账必须带置信度且低于阈值不能直接入账
- [ ] 评测集 v0 与合成 demo 账本（不含任何真实数据）
- [ ] 月报命令 `report`

## M2 · 安装与分发（贯穿各里程碑）

目标：对 AI 说一句话就能装好，且跨 agent、跨平台。

- [x] 仓库布局：`skills/money-mom/SKILL.md`，CI 里用 `skills-ref validate` 校验
- [x] `INSTALL.md`：写给 agent 读的安装说明（校验 SHA-256），README 里放“一句话安装”提示词
- [x] 通用安装器 `npx skills add` 已在隔离环境验证：文件放到 `~/.agents/skills`，Claude Code 软链接过去；**各 agent 内的对话效果尚未逐一实测**
- [ ] 发布 PyPI 包 `money-mom`（引擎），skill 锁定版本调用 `uvx money-mom==x.y.z`
- [ ] 发布 npm 包 `money-mom`（轻量启动器，找到或安装 `uv` 后调用引擎）
- [ ] `money-mom init`：创建账本目录（仓库之外）
- [x] `money-mom doctor`：自检环境、版本、账本目录与权限
- [ ] Claude Code 插件市场入口（`.claude-plugin/marketplace.json`）
- [ ] 逐个 agent 实测：Claude Code、Codex、Cursor、Gemini CLI、GitHub Copilot
- [ ] 为不支持 skill 的 agent 提供 `AGENTS.md` 兜底说明
- [x] GitHub Actions：Linux / macOS / Windows × Python 3.11–3.13 的测试
- [ ] GitHub Actions：打包与发布
- [x] Release 流程文档 [docs/releasing.md](docs/releasing.md)；首个预发布 `v0.1.0a1`（wheel 附在 Release 上）
- [x] 项目主页（GitHub Pages，中文默认，可切英文）与中英文 README

## M3 · 导入

- [x] 导入框架：声明式映射（编码、表头行、列、日期、正负号约定、收/支取值、跳过行、分类规则），确定性执行
- [x] 通用 CSV 导入（GBK/UTF-8、前言与汇总行、千分位、括号负数、两列金额）
- [x] 支付宝账单：通过映射文件支持（`import inspect` 帮你写）；**未内置预设**，因为没有真实导出样本，格式也常变
- [ ] 微信账单：同上，可用映射文件；内置预设需要真实样本
- [x] agent 读样本写映射（`import inspect` → `save-map` → `--dry-run`），含糊处交给用户
- [x] 导入去重（稳定的行哈希，重复导入只跳过已有的行）
- [x] 分类结果回写为新规则：待确认行按对方分组，`import rule-add` 加规则，`import recheck` 补全

## M4 · v0.2 对账与“妈妈人格”

- [x] 银行对账单逐笔比对：漏记、多记、金额不符、疑似重复扣款（CSV 用映射，PDF 由 agent 读后以 JSON 提交）
- [x] 差异清零后封存：`reconcile --assert` 写余额断言并锁定该期间（不干净则拒绝）
- [ ] 订阅与周期扣款检测，涨价与重复扣款提醒
- [ ] 小票与截图识别入账
- [ ] 人格语气档位：温柔 / 正常 / 严厉 / 佛系（只影响语气，不影响事实）
- [ ] 主动关怀与异常提醒（只提醒，不替用户做决定）

## M5 · v0.3 投资者包

- [x] 多币种：币种识别（从不靠猜）、汇率（`price` 事件、`rates update` 联网取欧洲央行每日参考汇率）、`balance --in` 与 `networth` 折算
- [ ] 汇率来源不覆盖的币种（如台币）的自动取数；更多汇率来源
- [ ] 持仓批次与成本基础，已实现与未实现盈亏
- [ ] 港美股券商流水只读导入
- [ ] 多账户净资产视图
- [ ] 与现有交易日志工具对接

## M6 · 之后

- [ ] MCP 服务（给没有 shell 的客户端）
- [ ] 手机快捷指令入口
- [ ] 可视化页面（只读）
- [ ] 英文文档与多语言账户模板
