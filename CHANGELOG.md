# Changelog

格式参考 Keep a Changelog，版本遵循语义化版本。

## [Unreleased]

### Added
- 项目立项：README、ROADMAP、设计文档、Agent 约定、MIT 许可证。
- `docs/agents.md`：各主流 agent 的 skill 加载与安装方式调研（依据官方文档）。
- `docs/data-model.md`：数据模型 v0 草案（事件流、金额、平衡规则、余额断言、SQLite 视图、Beancount 对应关系）。

- 账本内核（Python 3.11+，零运行时依赖）：严格的事件解析、借贷平衡校验、`pending` / `confirm` / `void`、余额断言与历史锁定、导入去重、只追加的按月 JSONL 存储、跨进程文件锁、整批全有或全无的写入；82 个测试。
- GitHub Actions：Linux / macOS / Windows × Python 3.11–3.13 的测试。
- 命令行 `money-mom`（也可 `python -m money_mom`）：`init` / `open` / `close` / `add` / `confirm` / `void` / `assert` / `check` / `balance` / `pending` / `accounts` / `show` / `query`；每个命令都有 `--json` 输出，退出码 0 成功、1 账本拒绝、2 用法错误；`--from-json` 支持从文件或 stdin 原子批量写入。
- SQLite 缓存（`cache.sqlite`，可随时删除）：按事件流指纹自动重建；`query` 只允许单条只读 SELECT（只读打开 + SQLite 授权器 + 5 秒超时 + 行数上限）。

- 命令行表格按显示宽度对齐，中文账户名不再错位。
- 结构化意图 `spend` / `income` / `transfer`：agent 只说业务含义，借贷方向由程序决定；账户名按全名、别名、唯一末段解析，**拿不准不猜**——未解析的名字或低于 `auto_post_confidence`（默认 0.9）的置信度记为待确认，并附候选账户；`--strict` 改为报错，`--dry-run` 只预览。
- `confirm ID --category/--from/--to` 按槽位名补全待确认的意图记录。
- `init --template cn|en`（中文与英文账户树，含别名）、`alias add|list|remove`、`resolve`。

### Changed
- 设计文档：确定 Python 标准库内核、npm 轻量启动器、Agent Skills 标准发布、引擎与 skill 分离并锁定版本。
