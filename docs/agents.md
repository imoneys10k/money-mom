# 各 AI agent 的加载与安装方式

核实日期：2026-10-07。除“未核实”部分外，内容均来自各家官方文档（链接见文末）。这些产品更新很快，落地前以官方文档为准。

## 结论

1. **`SKILL.md`（Agent Skills 开放标准）是最大公约数。** 它最初由 Anthropic 提出并开放，官方客户端清单里已有 Claude Code、Codex、Cursor、GitHub Copilot、Gemini CLI、OpenCode、Goose、Kiro、Roo Code 等几十个。我们只做**一份 skill**，不为每个 agent 单独适配。
2. **`.agents/skills/` 是跨厂商共用的目录。** Codex、Cursor、Gemini CLI、GitHub Copilot 都会读 `~/.agents/skills/`（用户级）和 `.agents/skills/`（项目级）。Claude Code 读自己的 `~/.claude/skills/`。所以放两份就覆盖这些主流产品。
3. **有现成的通用安装器。** `npx skills add <owner>/<repo>`（Vercel Labs 出品，非各 agent 官方）支持 78 个 agent，并有非交互参数，适合让 AI 一条命令装好。
4. **`AGENTS.md` 是不支持 skill 的 agent 的兜底。** 它由 Linux Foundation 旗下的 Agentic AI Foundation 托管，24 个以上的工具会读它。

## 各 agent 速查

| Agent | skill 目录（用户级 / 项目级） | 原生安装方式 |
|---|---|---|
| Claude Code | `~/.claude/skills/` / `.claude/skills/` | 插件市场：仓库放 `.claude-plugin/marketplace.json`，用户执行 `claude plugin marketplace add <owner>/<repo>`，再 `claude plugin install <插件名>@<市场名>`；也可手动放目录 |
| Codex | `~/.agents/skills/` / `.agents/skills/`（及父目录、仓库根） | 自动发现目录；`$skill-installer` 安装官方精选；官方说“插件用于分发 skill” |
| Cursor | `~/.agents/skills/`、`~/.cursor/skills/` / `.agents/skills/`、`.cursor/skills/` | 放目录；或打包成插件（`.cursor-plugin/marketplace.json`）从 GitHub 仓库导入。官方称 skill 不能单独导入，只能经插件 |
| Gemini CLI | `~/.gemini/skills/`、`~/.agents/skills/` / `.gemini/skills/`、`.agents/skills/` | `gemini skills install <git 仓库地址> --consent`；扩展（extension）也可内含 skill |
| GitHub Copilot | `~/.copilot/skills/`、`~/.agents/skills/` / `.github/skills/`、`.claude/skills/`、`.agents/skills/` | 放目录；覆盖 Copilot CLI、VS Code 与 JetBrains 的 agent 模式、cloud agent |
| 其他（OpenCode、Goose、Kiro、Roo Code 等） | 见各自文档 | 多数可经 `npx skills add` 安装 |

## SKILL.md 格式要点（来自规范）

- 必填 `name`：小写字母、数字、连字符，最长 64，**必须与所在目录名一致**；必填 `description`：最长 1024，要写清“做什么、何时用”。
- 可选 `license`、`compatibility`（环境要求，最长 500）、`metadata`、`allowed-tools`（实验性，各家支持不一）。
- 目录可含 `scripts/`、`references/`、`assets/`；`SKILL.md` 控制在 500 行以内，细节拆到 `references/`，引用只下探一层。
- 采用渐进加载：启动时只读 `name` 和 `description`，命中任务才读正文。
- 官方提供校验库 `skills-ref validate <目录>`。

Claude Code 另有扩展字段（如 `disable-model-invocation`、`context: fork`），其他 agent 未必认；**我们的 `SKILL.md` 只用规范里的通用字段**，保证可移植。

## 对 Money Mom 的设计含义

1. **仓库布局：** skill 放在 `skills/money-mom/SKILL.md`，通用安装器和多数 agent 都能发现。
2. **引擎与 skill 分离：** skill 只是“使用说明”，真正的记账引擎是 PyPI 包 `money-mom`，由 skill 指示 agent 通过 `uvx money-mom ...` 调用。这样 skill 小、引擎可独立发版。
3. **锁定版本：** 记账涉及财务数据，skill 里应调用**固定版本**的引擎（`uvx money-mom==x.y.z`），避免静默升级带来行为变化。
4. **不预批准宽泛的 shell 权限：** 不在 `allowed-tools` 里放通配的 `Bash`，让各 agent 保留自己的授权提示。
5. **一句话安装的流程（写进 `INSTALL.md`，给 agent 读）：**
   1. 判断自己是哪个 agent；
   2. 运行 `npx skills add imoneys10k/money-mom -g -a <agent> -y`，失败则把 `skills/money-mom/` 复制到对应目录；
   3. 确认有 `uv`，没有就征得用户同意后安装；
   4. `uvx money-mom init` 创建账本目录（在仓库之外）；
   5. `uvx money-mom doctor` 自检，并告诉用户可以开始说话记账。
6. **Claude Code 额外提供插件市场入口**，其余 agent 走通用安装器或手动复制。

## 未核实 / 待办

- `npx skills add imoneys10k/money-mom` 要等仓库里有真正的 `SKILL.md` 后才能实测。
- Codex 与 Cursor 的插件清单细节、Windsurf、Cline、Aider 的 skill 或指令加载方式，本次没有读官方文档。
- 各 agent 的 MCP 配置方式没有从官方文档核实，等做 MCP 服务（M6）时再查。
- `compatibility` 字段在各 agent 里是否被解析，规范说“多数 skill 不需要”，实测前不依赖它。

## 来源

- Agent Skills 概览与规范：https://agentskills.io/home 、https://agentskills.io/specification
- Claude Code skills：https://code.claude.com/docs/en/skills
- Claude Code 插件市场：https://code.claude.com/docs/en/plugin-marketplaces
- Codex skills：https://learn.chatgpt.com/docs/build-skills （由 https://developers.openai.com/codex/skills 重定向）
- Cursor skills：https://cursor.com/docs/context/skills
- GitHub Copilot agent skills：https://docs.github.com/en/copilot/concepts/agents/about-agent-skills
- Gemini CLI skills：https://geminicli.com/docs/cli/skills/
- 通用安装器 `npx skills`：https://github.com/vercel-labs/skills
- AGENTS.md：https://agents.md
