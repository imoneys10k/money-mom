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
- [ ] SQLite 缓存：由事件流重建，供 SQL 查询
- [ ] CLI：`init` / `add` / `check` / `query` / `void`
- [ ] 结构化意图（spend / income / transfer）→ 分录渲染
- [ ] propose → validate → commit 流程；待确认区与 `!` 状态（内核已支持 pending / confirm，意图层与待确认区命令待做）
- [x] 余额断言与锁定（含人工覆盖、agent 不可覆盖）
- [ ] 账户树模板（中文，国内起步）
- [ ] 导出 Beancount，并以 `bean-check` 做差分测试（开发期依赖，非运行时依赖）
- [ ] `SKILL.md` 与 agent 指令文件
- [ ] 评测集 v0 与合成 demo 账本（不含任何真实数据）
- [ ] 月报命令 `report`

## M2 · 安装与分发（贯穿各里程碑）

目标：对 AI 说一句话就能装好，且跨 agent、跨平台。

- [ ] 仓库布局：`skills/money-mom/SKILL.md`，并用 `skills-ref validate` 校验
- [ ] `INSTALL.md`：写给 agent 读的安装说明，README 里放“一句话安装”提示词
- [ ] 实测通用安装器：`npx skills add imoneys10k/money-mom` 在各 agent 上能装好
- [ ] 发布 PyPI 包 `money-mom`（引擎），skill 锁定版本调用 `uvx money-mom==x.y.z`
- [ ] 发布 npm 包 `money-mom`（轻量启动器，找到或安装 `uv` 后调用引擎）
- [ ] `money-mom init`：创建账本目录（仓库之外）
- [ ] `money-mom doctor`：自检环境、版本、账本目录与权限
- [ ] Claude Code 插件市场入口（`.claude-plugin/marketplace.json`）
- [ ] 逐个 agent 实测：Claude Code、Codex、Cursor、Gemini CLI、GitHub Copilot
- [ ] 为不支持 skill 的 agent 提供 `AGENTS.md` 兜底说明
- [x] GitHub Actions：Linux / macOS / Windows × Python 3.11–3.13 的测试
- [ ] GitHub Actions：打包与发布
- [ ] Release 流程：语义化版本、CHANGELOG、Release notes
- [ ] 项目主页（GitHub Pages）

## M3 · 导入

- [ ] 导入框架：声明式映射规则，规则确定性执行
- [ ] 通用 CSV 导入
- [ ] 支付宝账单导入
- [ ] 微信账单导入
- [ ] LLM 读样本生成映射规则（用户确认一次）
- [ ] 导入去重（导入哈希）
- [ ] 分类结果回写为新规则

## M4 · v0.2 对账与“妈妈人格”

- [ ] 银行对账单（PDF/CSV）逐笔比对：漏记、重复、金额不符
- [ ] 差异清零后自动生成余额断言
- [ ] 订阅与周期扣款检测，涨价与重复扣款提醒
- [ ] 小票与截图识别入账
- [ ] 人格语气档位：温柔 / 正常 / 严厉 / 佛系（只影响语气，不影响事实）
- [ ] 主动关怀与异常提醒（只提醒，不替用户做决定）

## M5 · v0.3 投资者包

- [ ] 多币种、汇率与价格库
- [ ] 持仓批次与成本基础，已实现与未实现盈亏
- [ ] 港美股券商流水只读导入
- [ ] 多账户净资产视图
- [ ] 与现有交易日志工具对接

## M6 · 之后

- [ ] MCP 服务（给没有 shell 的客户端）
- [ ] 手机快捷指令入口
- [ ] 可视化页面（只读）
- [ ] 英文文档与多语言账户模板
