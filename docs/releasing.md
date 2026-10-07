# 发布流程

给维护者（人或 agent）。每次发布都按这个顺序，别跳步。

1. **测试全绿**：`python -m unittest discover -s tests`，以及 GitHub Actions 全绿。
2. **改版本号**：`pyproject.toml` 的 `version` 与 `src/money_mom/__init__.py` 的 `__version__` 保持一致（预发布用 `0.1.0a1` 这种写法）。
3. **README 定稿再构建**：`pyproject.toml` 把 `README.md` 打进了 wheel 的元数据，README 一变哈希就变。
4. **构建并取哈希**：

   ```bash
   rm -rf dist && uv build
   shasum -a 256 dist/*.whl
   ```

   wheel 是可复现的：同一份源码再构建一次，哈希应当相同。
5. **把哈希写进 `INSTALL.md`**（文中出现两处：校验命令里的和单独列出的那一行），并把文中的版本号、下载地址同步更新。`skills/money-mom/SKILL.md` 的 `metadata.version` 也要同步。
6. **写 CHANGELOG**：把 `[Unreleased]` 改成版本号和日期。
7. **提交并推送**，等 CI 全绿。
8. **打标签并发布**，附上构建好的 wheel（必须是第 4 步那一个）：

   ```bash
   git tag -a v0.1.0a1 -m "v0.1.0a1"
   git push origin v0.1.0a1
   gh release create v0.1.0a1 dist/*.whl --prerelease --title "v0.1.0a1" --notes-file <说明文件>
   ```

9. **验证发布物**：从 Release 下载 wheel，确认哈希与 `INSTALL.md` 一致；在隔离环境（临时 `HOME`、`UV_TOOL_DIR`）里按 `INSTALL.md` 走一遍。

## 重新生成 banner 与分享图

源文件在 `assets/src/`，用无头 Chrome 渲染（`?theme=dark` 出深色版）：

```bash
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CH" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1600,420 \
  --virtual-time-budget=2000 --screenshot=assets/banner-light.png "file://$PWD/assets/src/banner.html?theme=light"
```

分享图 `og.png` 是 1200×630、倍率 1，渲染后复制到 `site/og.png`。
