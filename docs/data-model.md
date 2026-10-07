# 数据模型 v0

状态：**草案，实现前可改。** 改动要同步更新本文档与 `schema` 版本号。

## 目标

1. 账本是**只追加的事件流**，当前状态由事件推导，历史不可改。
2. **缺失不是零**：未知的账户、金额用 `null` 表示，且不参与余额。
3. 每条记录可追溯来源，并带置信度。
4. 人和 agent 都能直接读；能无损导出 Beancount。

## 账本目录

账本在用户自己的目录，**不在本仓库里**。默认 `~/MoneyMom/`，由 `money-mom init` 创建。

```text
~/MoneyMom/
  money-mom.toml      配置：本位币、时区、语气档位、schema 版本
  ledger/
    2026-10.jsonl     事件流，按事件的记录时间 ts 分月，只追加
  aliases.toml        账户别名（由 `money-mom alias` 维护，不属于财务事实，可手改）
  cache.sqlite        由事件流推导，可随时删除重建，不入 git
```

`money-mom.toml` 用 TOML（Python 3.11 起标准库可读），v0 含 `schema`、`base_currency`、`tone`（`gentle` / `normal` / `strict` / `zen`），以及可选的 `auto_post_confidence`（0 到 1，默认 0.9）。时间一律用系统本地时区。账本目录建议自己 `git init`，每批写入一次 commit。

## 事件通用字段

每行一个 JSON 对象（JSONL），按文件顺序为准。

| 字段 | 必填 | 说明 |
|---|---|---|
| `v` | 是 | schema 版本，整数，当前为 `1` |
| `id` | 是 | 事件 ID，在账本内唯一；字符限 `[0-9A-Za-z_-]`，长度 1 到 64。程序生成的是 ULID（按时间可排序），手写或导入的不强制 |
| `kind` | 是 | `open` / `close` / `txn` / `confirm` / `void` / `assert` / `price` |
| `ts` | 是 | 记录时间，RFC 3339 且带时区偏移 |
| `actor` | 是 | `{"type": "human" 或 "agent", "name": "..."}`，谁写入的 |
| `source` | 否 | 来源，见下 |
| `confidence` | 否 | 0 到 1；人工直接录入时为 `null` |
| `meta` | 否 | 自由键值，允许未知键 |

`source` 结构：`{"type": "chat" | "file" | "screenshot" | "import" | "manual", "ref": "说明或文件名", "sha256": "文件内容哈希（可选）"}`。`type` 为 `file` / `screenshot` / `import` 时，`ref` 不得是绝对路径（以 `/`、`~`、盘符或 `\\` 开头），避免泄露本机路径；也不得包含凭证。

规则：

- `kind` 未知则整条拒绝；`meta` 之外出现未知字段也拒绝，避免静默丢信息。
- 事件只能引用在它**之前**出现的事件。
- 同一文件内 `ts` 不得倒退。

## 金额

- 金额是**十进制字符串**，如 `"38.00"`、`"-4500.00"`，内部用 `Decimal`，不使用浮点。
- 币种 `ccy` 是字符串：法币用 ISO 4217（`CNY`、`USD`、`HKD`），其他资产用代码（如股票代码）。
- 精度按输入保留，不擅自四舍五入。

## 账户

- 层级用冒号分隔，根只能是 `Assets` / `Liabilities` / `Equity` / `Income` / `Expenses`，例如 `Expenses:Dining:Coffee`。
- 子级允许中文，例如 `Assets:招行:储蓄卡`。
- 账户必须先 `open` 才能使用。可选 `currencies` 限制该账户允许的币种。
- 交易日期必须落在账户的开户与销户日期之间；销户（`close`）要求余额为零，且不能再重新开户。
- 账户名必须是 Unicode NFC 规范形式，避免肉眼相同的两个账户。

```json
{"v":1,"id":"01J9XK0001","kind":"open","ts":"2026-10-01T09:00:00+08:00","actor":{"type":"human","name":"init"},"account":"Assets:Alipay","date":"2026-10-01","currencies":["CNY"]}
```

## 交易 `txn`

| 字段 | 必填 | 说明 |
|---|---|---|
| `date` | 是 | 记账日期，`YYYY-MM-DD` |
| `status` | 是 | `posted`（已入账）或 `pending`（待确认） |
| `narration` | 否 | 说明 |
| `payee` | 否 | 对方 |
| `postings` | 是 | 分录数组，至少 2 条 |
| `import_hash` | 否 | 导入去重用：规范化后的来源行哈希 |

分录：`{"account": "...或 null", "amount": "字符串", "ccy": "CNY"}`；预留 `cost`、`price`（持仓与汇率，M5 实现）。

**平衡规则（核心不变式）：**

- `posted` 的交易，每个币种的分录金额之和**必须恰好为 0**，且所有 `account` 非空、金额已知，否则拒绝写入。
- `pending` 的交易允许某条分录 `account` 为 `null`（分类未定）。它**不计入任何余额**，也不要求平衡。
- 跨币种的交易不用价格标注：换汇经 Equity 中转账户记成四条分录，每个币种各自平衡（见下文“换汇”）。分录里的 `cost`、`price` 字段仍保留，等投资者包实现。

```json
{"v":1,"id":"01J9XK0002","kind":"txn","ts":"2026-10-07T12:31:05+08:00","actor":{"type":"agent","name":"claude-code"},"source":{"type":"chat","ref":"午饭瑞幸 38，支付宝付的"},"confidence":0.96,"date":"2026-10-07","status":"posted","narration":"午饭","payee":"瑞幸","postings":[{"account":"Expenses:Dining:Coffee","amount":"38.00","ccy":"CNY"},{"account":"Assets:Alipay","amount":"-38.00","ccy":"CNY"}]}
{"v":1,"id":"01J9XK0003","kind":"txn","ts":"2026-10-07T12:40:10+08:00","actor":{"type":"agent","name":"claude-code"},"source":{"type":"chat","ref":"给小王转了 200"},"confidence":0.55,"date":"2026-10-07","status":"pending","payee":"小王","postings":[{"account":null,"amount":"200.00","ccy":"CNY"},{"account":"Assets:Alipay","amount":"-200.00","ccy":"CNY"}]}
```

### 意图记录的 `meta` 约定

由 `spend` / `income` / `transfer` 写入的交易带有这些 `meta` 键（自由键值，内核不校验）：

| 键 | 含义 |
|---|---|
| `intent` | `spend` / `income` / `transfer` |
| `given` | 调用者说的原话，如 `{"from": "支付宝", "category": "咖啡"}` |
| `unresolved` | 没解析成功的槽位：`slot`、`term`、`reason`（`missing` / `unknown` / `ambiguous` / `approximate` / `wrong_type` / `unavailable`）、`candidates` |

渲染规则：`spend` 是类别记借、来源记贷；`income` 是收款账户记借、收入类别记贷；`transfer` 是转入记借、转出记贷。`confirm` 按槽位补全时依据 `intent` 知道哪条分录对应哪个槽位。

## 确认、作废与更正

- **`confirm`**：把 `pending` 变为 `posted`，可带补全后的 `postings`（覆盖原值）。确认后的结果同样要通过平衡校验。
- **`void`**：作废某笔交易（`posted` 或 `pending` 均可）或某条余额断言，必须带 `reason`。作废不删除任何内容，原事件和作废事件都保留。
- **更正** = `void` 旧交易 + 追加新 `txn`。不存在“修改”事件。

```json
{"v":1,"id":"01J9XK0004","kind":"confirm","ts":"2026-10-07T12:41:30+08:00","actor":{"type":"human","name":"user"},"target":"01J9XK0003","postings":[{"account":"Assets:Receivable:小王","amount":"200.00","ccy":"CNY"},{"account":"Assets:Alipay","amount":"-200.00","ccy":"CNY"}]}
{"v":1,"id":"01J9XK0005","kind":"void","ts":"2026-10-08T09:02:00+08:00","actor":{"type":"human","name":"user"},"target":"01J9XK0002","reason":"重复录入"}
```

## 余额断言 `assert`

```json
{"v":1,"id":"01J9XK0006","kind":"assert","ts":"2026-10-08T09:10:00+08:00","actor":{"type":"human","name":"user"},"date":"2026-09-30","account":"Assets:CMB","amount":"12480.55","ccy":"CNY"}
```

- 语义：该账户在 `date` **当天结束时**的余额必须等于 `amount`（与银行对账单的口径一致）。导出 Beancount 时日期加 1 天，因为 Beancount 的断言指当天开始。
- 默认零容差，因为用的是精确十进制。
- **锁定（已确认）：** 某账户有未作废的断言后，涉及该账户的新交易、确认或作废，若日期不晚于该断言日期，一律拒绝。确需补记，须由**人**在事件的 `meta` 里写 `override_lock` 及原因；**agent 不能自行覆盖**。
- 覆盖锁定之后旧断言会不再成立，`check` 会报告。处理办法是作废旧断言（`void` 指向它）并追加新断言，这样改动留有痕迹。

## 导入的记录

`import run` 写入的交易带有：`import_hash`（形如 `imp1-` 加 32 位十六进制）、`source`（`type` 为 `import`，`ref` 是文件名，`sha256` 是文件内容哈希）、`meta.import`（`mapping`、`line`、`file`）。分类没有命中规则的行是 `pending`，对应一侧的 `account` 为 `null`；`import recheck` 之后用 `confirm` 补全，并带 `meta.recheck`。

## 汇率 `price`

```json
{"v":1,"id":"01J9XK0007","kind":"price","ts":"2026-10-07T09:00:00+08:00","actor":{"type":"human","name":"user"},"date":"2026-10-06","base":"USD","quote":"CNY","rate":"6.7046","source":{"type":"import","ref":"api.frankfurter.dev (ECB reference rate)"}}
```

- 含义：`date` 当天 1 个 `base` 值 `rate` 个 `quote`。`source` 必填，说明汇率从哪来；`ts` 即取数时间。
- 同一对币种同一天，后记的替换先记的；`void` 可以作废某条汇率。
- 折算时取不晚于所查日期的最近一天；没有直接汇率就用反向汇率，再没有就经本位币中转（`USD→HKD = USD→CNY ÷ HKD→CNY`）。不看所查日期之后的汇率。
- 汇率只用于折算和展示，**不参与借贷平衡**，也不改变任何已记的金额。

## 换汇

`exchange` 把“用 100 USD 换了 720 CNY”记成：

| 账户 | 币种 | 金额 |
|---|---|---|
| 收到的账户 | CNY | +720 |
| `Equity:汇兑` | CNY | -720 |
| `Equity:汇兑` | USD | +100 |
| 付出的账户 | USD | -100 |

每个币种的分录之和各自为零。实际成交汇率记在 `meta.exchange.rate`。

## 写入策略

以下规则只在**新写入**时检查，回放旧账时不检查：

- `actor.type` 为 `agent` 的交易必须带 `confidence`，否则拒绝（`confidence_required`）。
- 这类交易若 `status` 为 `posted` 且 `confidence` 低于 `auto_post_confidence`（默认 0.9），拒绝（`confidence_too_low`），应记为 `pending`。
- `actor` 是调用者自报的，所以这些规则防的是疏忽，不是欺骗。

## 推导：SQLite 缓存

`cache.sqlite` 只是读视图，随时可由事件流重建。核心表：`accounts`、`txns`、`postings`、`assertions`、`events`。给 agent 用的视图：

| 视图 | 内容 |
|---|---|
| `v_postings` | 已入账且未作废的分录，连同交易的日期、对方、来源、置信度 |
| `v_pending` | 待确认的交易 |
| `v_balances` | 各账户各币种的当前余额（`REAL` 求和，仅供参考；精确余额用 `money-mom balance`） |
| `v_monthly` | 按月、账户、币种汇总的发生额 |
| `v_rates` | 每对币种最新的已存汇率及其来源 |

缓存按事件流的指纹判断是否过期，过期、损坏或布局版本不同都会自动重建；缓存文件写不了时退回内存数据库。`query` 命令只接受单条只读 `SELECT`（含 `WITH`），通过只读打开、SQLite 授权器、5 秒超时和行数上限四重限制。

金额存两列：`amount_text`（精确）和 `amount`（`REAL`，便于 agent 写 `SUM`）。**余额、报表和断言校验一律用 Python `Decimal` 计算，不以 SQL 浮点结果为准**；`query` 命令按币种精度对结果取整后再返回。

## 写入流程

1. agent 提交**结构化意图**（如 `spend`），不是账本文本。
2. 脚本把意图渲染成分录，运行全部校验。
3. 置信度达到阈值且无缺失字段 → 直接 `posted`；否则写成 `pending`，等待 `confirm`。
4. 通过校验才追加到事件流，随后更新缓存；失败则整笔不写。批量写入同样全有或全无（跨 UTC 月份的批次按文件依次写入，见 `Ledger.append_many` 的说明）。
5. 写入时持有账本目录里的独占文件锁，并在锁内从磁盘重放全部事件后再校验，所以多个进程同时写也不会基于过期状态入账。
6. 便捷写入接口会把早于磁盘上最新事件的 `ts` 提升为最新事件的 `ts`（避免机器时钟回拨或多进程造成误拒）；直接追加原始事件时则严格拒绝 `ts` 倒退。

## 校验清单（`money-mom check`）

- 每个事件的字段、类型、`v` 合法，`kind` 已知，无未知字段。
- ID 唯一，引用只指向之前的事件，`ts` 不倒退。
- 使用的账户已 `open` 且未 `close`，币种符合账户限制。
- `posted` 的交易平衡、无 `null`；`pending` 不计入余额。
- `confirm` 与 `void` 的目标存在，且不重复作废。
- 所有 `assert` 与由事件流算出的余额一致。
- `import_hash` 在账本内不重复，除非显式标注。

## 导出 Beancount（对应关系）

| 本模型 | Beancount |
|---|---|
| `open` / `close` | `open` / `close` |
| `txn` posted | `*` 交易 |
| `txn` pending | `!` 交易，`null` 账户映射到 `Equity:Unresolved` |
| `assert`（当天结束） | `balance`，日期加 1 天 |
| `void` | 被作废的交易不导出，并以注释保留 ID 与原因 |
| `source`、`confidence` | 交易元数据 |

## 已确认的决定（2026-10-07）

- 对账断言后锁定历史，覆盖须由人写明原因。
- `pending` 交易不计入任何余额。
- 账户名允许中文；导出 Beancount 不兼容时由导出层做英文别名。

## 待确认的问题

- 是否给事件加哈希链（每条带上一条的哈希）以防篡改？目前靠 git 提供历史，默认不做。
- 中文账户名导出到 Beancount 是否被支持，需要实测；不支持时导出层做 ASCII 别名映射。
- 事件文件按 `ts` 分月还是按 `date` 分月（当前选 `ts`，保证文件只追加）。
- 已实现汇兑损益（需要持仓批次，留给投资者包）。
