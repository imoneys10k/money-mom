"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const root = path.join(__dirname, "..");
const launcher = path.join(root, "bin", "money-mom.js");
const pkg = require("../package.json");
const windows = process.platform === "win32";

// A directory that holds only `node` (and, optionally, a fake uvx), used as the whole PATH.
function sandbox(fakeUvx) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mm-launcher-"));
  fs.symlinkSync(process.execPath, path.join(dir, "node"));
  if (fakeUvx) {
    const file = path.join(dir, "uvx");
    fs.writeFileSync(file, `#!/usr/bin/env node\n${fakeUvx}\n`);
    fs.chmodSync(file, 0o755);
  }
  return dir;
}

function run(dir, args, options = {}) {
  return spawnSync(process.execPath, [launcher, ...args], {
    env: { PATH: dir, HOME: dir },
    encoding: "utf8",
    ...options,
  });
}

const record = (file) => `
const fs = require("node:fs");
if (process.argv[2] === "--version") { process.stdout.write("uvx 0.0.0\\n"); process.exit(0); }
fs.writeFileSync(${JSON.stringify(file)}, JSON.stringify(process.argv.slice(2)));
`;

test("runs the pinned engine through uvx and passes every argument through", { skip: windows }, () => {
  const marker = path.join(os.tmpdir(), `mm-argv-${process.pid}.json`);
  const dir = sandbox(record(marker));
  const result = run(dir, ["spend", "5 USD", "--from", "cash", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(fs.readFileSync(marker, "utf8")), [
    "--quiet", "--from", `money-mom==${pkg.moneyMomVersion}`, "money-mom", "spend", "5 USD", "--from", "cash", "--json",
  ]);
});

test("arguments with spaces, quotes and Chinese text arrive unchanged", { skip: windows }, () => {
  const marker = path.join(os.tmpdir(), `mm-argv2-${process.pid}.json`);
  const dir = sandbox(record(marker));
  const odd = ["spend", "38", "--payee", "瑞幸 \"咖啡\"", "--narration", "a'b $HOME `x`"];
  assert.equal(run(dir, odd).status, 0);
  assert.deepEqual(JSON.parse(fs.readFileSync(marker, "utf8")).slice(4), odd);
});

test("the exit code of the engine is the exit code of the launcher", { skip: windows }, () => {
  for (const code of [0, 1, 2, 7]) {
    const dir = sandbox(`if (process.argv[2] === "--version") process.exit(0); process.exit(${code});`);
    assert.equal(run(dir, ["check"]).status, code);
  }
});

test("standard input and output pass straight through", { skip: windows }, () => {
  const dir = sandbox(`
    if (process.argv[2] === "--version") process.exit(0);
    let data = ""; process.stdin.on("data", (c) => data += c);
    process.stdin.on("end", () => { process.stdout.write("got:" + data); process.stderr.write("note"); });
  `);
  const result = run(dir, ["add", "--from-json", "-"], { input: '[{"x":1}]' });
  assert.equal(result.stdout, 'got:[{"x":1}]');
  assert.equal(result.stderr, "note");
});

test("without uv it only explains how to install it, and runs nothing", { skip: windows }, () => {
  const dir = sandbox(null);
  const result = run(dir, ["doctor"]);
  assert.equal(result.status, 127);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, /uv/);
  assert.match(result.stderr, /astral\.sh\/uv\/install\.sh/);
  assert.match(result.stderr, /install\.ps1/);
  assert.match(result.stderr, /docs\.astral\.sh/);
  assert.deepEqual(fs.readdirSync(dir).sort(), ["node"]); // it created nothing and installed nothing
});

test("a uvx that does not work is treated like a missing one", { skip: windows }, () => {
  const dir = sandbox(`process.exit(3);`);
  const result = run(dir, ["doctor"]);
  assert.equal(result.status, 127);
  assert.match(result.stderr, /not installed/);
});

test("package.json is what npm needs", () => {
  assert.equal(pkg.name, "money-mom");
  assert.deepEqual(pkg.bin, { "money-mom": "bin/money-mom.js" });
  assert.ok(fs.statSync(launcher).isFile());
  if (!windows) assert.ok(fs.statSync(launcher).mode & 0o111, "the launcher must be executable");
  assert.ok(fs.readFileSync(launcher, "utf8").startsWith("#!/usr/bin/env node"));
  assert.equal(pkg.repository.url, "git+https://github.com/imoneys10k/money-mom.git"); // provenance compares this exactly
  assert.equal(pkg.repository.directory, "npm");
  assert.equal(pkg.license, "MIT");
  assert.ok(pkg.files.includes("bin"));
  assert.equal(pkg.dependencies, undefined, "the launcher must have no dependencies");
  for (const file of ["README.md", "LICENSE"]) assert.ok(fs.existsSync(path.join(root, file)), file);
});

test("the npm version and the engine version it pins are the same release", () => {
  const match = /^(\d+\.\d+\.\d+)-(alpha|beta|rc)\.(\d+)$/.exec(pkg.version);
  const letter = { alpha: "a", beta: "b", rc: "rc" };
  const expected = match ? `${match[1]}${letter[match[2]]}${match[3]}` : pkg.version;
  assert.equal(pkg.moneyMomVersion, expected);
});
