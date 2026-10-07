# Changelog

格式参考 Keep a Changelog，版本遵循语义化版本。

## [Unreleased]

### Added
- 项目立项：README、ROADMAP、设计文档、Agent 约定、MIT 许可证。
- `docs/agents.md`：各主流 agent 的 skill 加载与安装方式调研（依据官方文档）。
- `docs/data-model.md`：数据模型 v0 草案（事件流、金额、平衡规则、余额断言、SQLite 视图、Beancount 对应关系）。

- 账本内核（Python 3.11+，零运行时依赖）：严格的事件解析、借贷平衡校验、`pending` / `confirm` / `void`、余额断言与历史锁定、导入去重、只追加的按月 JSONL 存储、跨进程文件锁、整批全有或全无的写入；82 个测试。
- GitHub Actions：Linux / macOS / Windows × Python 3.11–3.13 的测试。

### Changed
- 设计文档：确定 Python 标准库内核、npm 轻量启动器、Agent Skills 标准发布、引擎与 skill 分离并锁定版本。
