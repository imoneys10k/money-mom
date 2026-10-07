# Changelog

格式参考 Keep a Changelog，版本遵循语义化版本。

## [Unreleased]

### Added
- 月报 `money-mom report [--month YYYY-MM] [--in CCY] [--top N]`：收入、支出、结余与储蓄率，分类占比及与上月的变化，最大几笔支出，净资产月初到月末的变化，并列出待确认笔数、当月核对过余额的账户和过期汇率。只读。当月未结束时按“月初至今”计算并明确提示；折算用期末（或今天）的已存汇率，缺汇率的币种列在 `missing`、不计入折算数字并标记 `partial`；转账、换汇不算收支；没有上月数据时对比字段为 `null`，不是 0。

## [0.1.0a3] - 2026-10-07

账单导入与对账；第一个可以从 PyPI 和 npm 安装的版本。

### Added
- 账单导入 `money-mom import`：用声明式映射（TOML）描述一种 CSV 的编码、表头行、列、日期格式、正负号约定、收/支取值、要跳过的行和分类规则；`inspect` 探测并给出建议（含糊处不替你决定）、`save-map` 校验后保存、`run` 导入（规则命中的行入账，其余待确认并按对方分组）、`rule-add` / `rule-list` / `recheck` 把一次回答变成规则并补全历史。重复导入安全（稳定的行哈希），整批全有或全无，被拒绝的行按账单行号指出；agent 导入必须带置信度。**没有内置银行预设**（没有真实样本，格式也常变）。
- 打包：PyPI 元数据（SPDX 许可证、关键字、分类、链接，专用的 `README.pypi.md`，带 `py.typed`，精简的 sdist）；npm 包 `money-mom` 只是一个启动器，通过 uv 运行同版本的引擎，自己不安装任何东西，缺少 uv 时只打印指引；发布流水线 `release.yml`（可信发布，无需保存令牌，PyPI 与 npm 各有人工批准关卡）。
- 对账 `money-mom reconcile`：按导入哈希、再按金额加日期容差一对一配对，列出漏记、多记、金额不符和疑似重复扣款，核对期末余额；`--assert` 只封存完全干净的结果（锁定该期间）；PDF 由 agent 读后以 `--from-json` 提交。

## [0.1.0a2] - 2026-10-07

多币种：币种识别、汇率折算、换汇。**程序现在有一个会联网的命令** `rates update`，其余仍然离线。

### Added
- 换汇 `money-mom exchange 100 USD 720 CNY --from 美元户 --to 银行卡`：经 `Equity:汇兑`（模板已含；旧账本用 `open Equity:汇兑` 添加）记成四条分录，每个币种各自仍然平衡；实际成交汇率记在 meta 里，不取数也不假设；`--give-ccy` / `--get-ccy` 可避开 shell 对 `$` 的转义；同样支持待确认、`--strict`、`--dry-run` 和按槽位补全。
- 汇率与折算：新增 `price` 事件（带来源，只追加，同日后记的替换先记的，可作废）；`money-mom rates update` 是**唯一会联网的命令**，从 Frankfurter（欧洲央行每日参考汇率，约 30 种货币，不含台币，不是实时行情）取每种外币兑本位币的汇率，请求里只有币种代码和日期；取数失败时一个都不写，不支持的币种单独报告；`rates set` 手动记录（必须写来源）、`rates list`；`balance --in CNY` 和 `networth` 用已存的汇率折算：只用不晚于所查日期的汇率、先加总再四舍五入、缺汇率的币种明确列出而不是悄悄丢掉、超过 7 天的汇率标为过期。SQLite 缓存新增 `prices` 表和 `v_rates` 视图。
- 币种识别（不联网）：金额可以带币种，如 `5美元`、`HK$200`、`USD 5`、`1,200元`；`$`、`¥` 这类有歧义的符号从不靠猜，先看账户允许的币种，再看账本里在用的币种（非本位币的推断记为待确认），仍不确定就报错并列出候选。新增 `money-mom currency` 命令预览识别结果，`doctor` 显示账本在用的币种。

## [0.1.0a1] - 2026-10-07

首个预发布版本。账本内核、命令行、SQLite 查账、意图层、skill 与安装说明可用；还没有发布到 PyPI / npm。

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

- `skills/money-mom/SKILL.md`（含命令与 SQL 参考）与 `INSTALL.md`：agent 读了就能装、能用；安装包经 SHA-256 校验。
- `money-mom doctor`：安装与账本健康检查；没有账本不算错误。
- 写入策略：agent 记的每笔交易必须带置信度，低于 `auto_post_confidence` 不能直接入账（只对新写入生效，不影响回放旧账）。
- 中英文 README、GitHub Pages 主页（中文默认、可切英文、自动适配深色模式）、banner 与分享图。
- CI：skill 校验与占位符检查；Pages 自动部署。

### Changed
- 设计文档：确定 Python 标准库内核、npm 轻量启动器、Agent Skills 标准发布、引擎与 skill 分离并锁定版本。
