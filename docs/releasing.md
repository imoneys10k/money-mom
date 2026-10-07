# 发布流程

给维护者（人或 agent）。一次性的账号设置见 [publishing.md](publishing.md)。

发布是不可撤销的，所以流程的重点是：**先在本地把一切对齐，再打标签；标签之后只由流水线构建并发布，而且每个仓库发布前要人批准。**

## 发布前（本地，按顺序）

1. **测试全绿**：`PYTHONPATH=src:tests python -m unittest discover -s tests`，`cd npm && node --test`，GitHub Actions 全绿。
2. **改版本号**，下面几处必须一致（`tests/test_docs.py` 和流水线都会检查）：
   - `pyproject.toml` 的 `version` 与 `src/money_mom/__init__.py` 的 `__version__`（预发布写 `0.1.0a3`）；
   - `npm/package.json` 的 `moneyMomVersion`（同上）和 `version`（npm 写法 `0.1.0-alpha.3`）；
   - `skills/money-mom/SKILL.md` 的 `metadata.version`；
   - `README.md`、`README.en.md`、`README.pypi.md`、`INSTALL.md`、`site/index.html` 里提到的版本号。
3. **写发布说明**：`docs/release-notes/vX.Y.Z.md`（流水线用它创建 GitHub Release）；把 `CHANGELOG.md` 的 `[Unreleased]` 切成版本号和日期。
4. **README 定稿再构建。** wheel 的元数据包含 `README.pypi.md`，它或 `src/`、`pyproject.toml`、`LICENSE` 一变，哈希就变。
5. **构建并取哈希**：

   ```bash
   rm -rf dist && uv build
   uvx twine check dist/*
   shasum -a 256 dist/*.whl
   ```

   wheel 是可复现的：同一份源码再构建一次哈希应当相同（流水线会在 GitHub 的机器上再核对一次）。
6. **把哈希写进两处**：`INSTALL.md`（出现两处）和发布说明里的 `__WHEEL_SHA256__`。
7. **演练（不需要账号）**：
   - npm 包：`cd npm && npm pack`，把 tarball 装进一个临时项目，设 `UV_FIND_LINKS=<dist 目录>` 后运行 `node_modules/.bin/money-mom --version`，应当输出新版本（PyPI 上还没有它时，find-links 提供本地 wheel）。
   - 在 Actions 页面运行 `release` 工作流，选 `build-only`：完整验证和构建，什么都不发布。
   - 有 TestPyPI 账号时选 `testpypi`，彩排一次真实上传。
8. **提交并推送**，等 `main` 的 CI 全绿。

## 发布

9. **打标签**（需要明确同意，因为之后不可撤销）：

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

10. 流水线 `release.yml` 依次：核对版本、标签、校验和 → 跑全部测试 → 构建一次 → 暂停等批准 `pypi` → 发布到 PyPI → 暂停等批准 `npm` → 发布到 npm → 创建 GitHub Release（预发布版本会自动标记为 pre-release）。**任何一项核对不通过，什么都不会发布。**

## 发布后

11. **在隔离环境验证真实发布物**（临时 `HOME`、`UV_TOOL_DIR`、`UV_CACHE_DIR`）：

    ```bash
    uv tool install "money-mom==X.Y.Z" && money-mom --version
    npx -y money-mom@X.Y.Z --version
    ```

    再按 `INSTALL.md` 走一遍，并确认从上一个版本升级也能成功。
12. 确认从 PyPI 下载的 wheel 的哈希与 `INSTALL.md` 里写的一致。
13. 把 README、INSTALL、主页里的安装方式更新为已经真正可用的那些，再提交。

## 重新生成 banner 与分享图

源文件在 `assets/src/`，用无头 Chrome 渲染（`?theme=dark` 出深色版）：

```bash
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CH" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1600,420 \
  --virtual-time-budget=2000 --screenshot=assets/banner-light.png "file://$PWD/assets/src/banner.html?theme=light"
```

分享图 `og.png` 是 1200×630、倍率 1，渲染后复制到 `site/og.png`。
