#!/usr/bin/env node
"use strict";

// A thin launcher: `npx money-mom ...` runs the pinned Money Mom engine (a Python package on PyPI) through uv.
// It installs nothing itself. If uv is missing it only prints how to get it, and never runs an installer.

const { spawnSync } = require("node:child_process");
const pkg = require("../package.json");

const engine = pkg.moneyMomVersion;
const UV_HELP = `money-mom runs on Python through uv, which is not installed here.
Install uv, then run this command again:

  macOS / Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh
  Windows:        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

More options: https://docs.astral.sh/uv/getting-started/installation/
`;

function fail(message, code) {
  process.stderr.write(message.endsWith("\n") ? message : message + "\n");
  process.exit(code);
}

const probe = spawnSync("uvx", ["--version"], { stdio: "ignore" });
if (probe.error || probe.status !== 0) {
  fail(UV_HELP, 127);
}

const result = spawnSync(
  "uvx",
  ["--quiet", "--from", `money-mom==${engine}`, "money-mom", ...process.argv.slice(2)],
  { stdio: "inherit" },
);

if (result.error) {
  fail(`could not run uvx: ${result.error.message}`, 127);
}
if (result.signal) {
  process.kill(process.pid, result.signal);
} else {
  process.exit(result.status === null ? 1 : result.status);
}
