# 发布到 PyPI 与 npm：一次性设置

发布**不可撤销**：同一个版本号上传后永远不能再上传一次。所以这里的原则是：账号、密码、令牌只由你亲手处理，我（或任何 agent）从不接触；发布前有人工批准关卡；发布的就是流水线里构建的那一份文件。

## 分工

| 已经做好（在仓库里） | 需要你做 |
|---|---|
| PyPI 元数据与专用说明 `README.pypi.md`，`twine check` 通过 | 注册 PyPI，登记“可信发布者”（A） |
| npm 启动器包（只有 4 个文件，通过 uv 运行同版本引擎，自己不安装任何东西），带测试 | 注册 npm，把发布令牌存进 GitHub（C） |
| 发布流水线 `.github/workflows/release.yml`：版本、标签、校验和不一致就拒绝发布；构建一次，到处发布同一份文件 | 在 GitHub 上点“批准”（每个仓库一次） |
| GitHub 环境 `pypi`、`npm`、`testpypi`，并把你设为必需批准人 | 对我说“可以发布” |
| 用真实的 `uvx` 和真实打包出的 npm 包做过端到端演练 | |

## A. PyPI（约 5 分钟）

1. 注册 <https://pypi.org/account/register/>，并按提示开启两步验证。
2. 登录后进入账号设置，左侧栏的 **Publishing**（这个入口在账号层面，因为项目还不存在），在 “Add a new pending publisher” 里选 **GitHub**，填：

   | 字段 | 填 |
   |---|---|
   | PyPI Project Name | `money-mom` |
   | Owner | `imoneys10k` |
   | Repository name | `money-mom` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. 注意：“待定发布者”**不会为你保留名字**，谁先真正发布谁拥有；而且名字被别人抢先注册后，你的待定发布者会失效。所以登记后请尽快发布。
4. 不需要创建任何 API 令牌，也不要创建。可信发布是流水线向 PyPI 临时换取身份。

## B. TestPyPI 彩排（推荐，约 5 分钟）

TestPyPI 是独立的练习站，账号和包名与正式 PyPI 无关，正好用来演练整套流程而不留下后果。

1. 注册 <https://test.pypi.org/account/register/>（和 PyPI 是两个独立账号），开启两步验证。
2. 同样在 **Publishing** 里添加待定发布者，字段与 A 相同，**只有 Environment name 填 `testpypi`**。
3. 告诉我，我触发 `release` 工作流的 `testpypi` 彩排（它只上传到 TestPyPI，不碰正式站）。你需要在 GitHub 上批准 `testpypi` 环境。

## C. npm（约 10 分钟）

npm 的“可信发布”（不用令牌）要求**包已经存在**，所以**第一次发布必须用令牌**，之后可以切换并删除令牌。

1. 注册 <https://www.npmjs.com/signup>，开启两步验证。
2. 在网站的 Access Tokens 页面创建 **Granular Access Token**（命令行不能创建这种令牌）：
   - 权限选 **Read and write**；
   - 包的范围选能覆盖新包的那一项（包还不存在，所以没法只限定它，通常是 “All packages”）；
   - **有效期设短，比如 7 天**；
   - 如果页面上有“允许在没有两步验证的情况下发布（bypass 2FA）”之类的选项，为了让 CI 能发布需要勾上。

   页面上选项的确切文字可能和我描述的略有出入，以页面为准；我没能读到 npm 官方文档里这一页的最新内容，这一步的细节我没有核实。
3. 把令牌存进 GitHub 的 `npm` 环境密钥。**在你自己的终端**运行：

   ```bash
   gh secret set NPM_TOKEN --env npm --repo imoneys10k/money-mom
   ```

   它会提示你粘贴令牌。令牌不会出现在聊天里，也不会经过我。

## D. 批准关卡

仓库里的 `pypi`、`npm`、`testpypi` 环境已把你设为必需批准人。流水线跑到发布那一步会**暂停**，等你在 GitHub 的 Actions 页面打开这次运行，点 **Review deployments**，选环境，点 **Approve and deploy**。你可以先看一眼构建结果和校验和再批准；不批准就什么都不会发布。

## 发布当天

1. 我确认 `main` 的测试全绿、INSTALL.md 里的校验和等于构建出的 wheel，然后（在你说“可以发布”之后）打标签 `v0.1.0a3`。
2. 流水线：构建并校验 → 暂停等你批准 `pypi` → 发布到 PyPI → 创建 GitHub Release（发布说明取自仓库里的 `docs/release-notes/`）。npm 单独发布：PyPI 上线后，在 Actions 页面运行 `release` 工作流，`target` 选 `npm`，再批准 `npm` 环境。
3. 我在隔离环境里验证 `uv tool install "money-mom==0.1.0a3"` 和 `npx money-mom --version` 真的能用，然后把 README、INSTALL 和主页更新为这两条更简单的安装方式。

预发布版本（带 `a`、`b`、`rc`）默认不会被 `pip install money-mom` 装上，要写明确的版本号或加 `--pre`；这是预期行为，文档里写的都是带版本号的命令。

## 之后：npm 改用可信发布

第一次发布成功后：

1. 在 npmjs.com 打开包 `money-mom` → Settings → **Trusted Publisher** → GitHub Actions，填 Owner `imoneys10k`、Repository `money-mom`、Workflow `release.yml`、Environment `npm`。
2. 删除 GitHub 的环境密钥 `NPM_TOKEN`，并在 npm 上撤销那个令牌。
3. 之后的发布不再需要任何令牌（流水线会自动改用 OIDC，并附带来源证明）。可信发布要求 npm CLI 11.5.1 或更新，流水线里已经安装。

## 出了问题怎么办

- **发布后发现版本有问题**：PyPI 不允许覆盖同一个版本。在项目的 Manage 页面对该版本点 **Yank**，安装器就会跳过它；修好后发一个新版本号。npm 用 `npm deprecate money-mom@<版本> "原因"`（npm 只允许在发布后很短的时间内撤回，之后只能弃用）。
- **名字被别人抢先注册**：在流水线失败时才会发现，没有任何东西被发布。需要换名字（比如 npm 的作用域包 `@imoneys10k/money-mom`）并相应修改文档。
- **令牌泄露**：立刻在 npm 撤销，换一个新的，再重新设置 GitHub 环境密钥。
- **想先看看会发生什么**：运行 `release` 工作流（Actions 页面 → release → Run workflow）选 `build-only`，它会完整地验证和构建，但什么都不发布。
