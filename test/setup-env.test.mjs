import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";

const setupScript = join(import.meta.dirname, "..", "scripts", "setup-env.mjs");

function fixture(t) {
  const temporaryRoot = mkdtempSync(join(tmpdir(), "erp-setup-env-test-"));
  t.after(() => {
    assert.equal(dirname(resolve(temporaryRoot)), resolve(tmpdir()));
    assert.ok(temporaryRoot.includes("erp-setup-env-test-"));
    rmSync(temporaryRoot, { recursive: true, force: true });
  });
  const main = join(temporaryRoot, "main");
  const worktree = join(temporaryRoot, "worktree");
  const env = {
    ...process.env,
    GIT_CONFIG_GLOBAL: join(temporaryRoot, "no-global-config"),
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_TERMINAL_PROMPT: "0",
  };
  function git(cwd, ...args) {
    const result = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8", env, timeout: 15000 });
    assert.equal(result.status, 0, `git ${args.join(" ")}: ${result.stderr || result.error || result.stdout}`);
    return result.stdout.trim();
  }
  function commit(cwd, name, contents) {
    writeFileSync(join(cwd, name), contents);
    git(cwd, "add", name);
    git(cwd, "commit", "-m", `Update ${name}`);
  }
  function prepareEnvironment(cwd) {
    mkdirSync(join(cwd, "frontend", "node_modules"), { recursive: true });
    const python = join(cwd, "backend", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    mkdirSync(dirname(python), { recursive: true });
    writeFileSync(python, "fixture interpreter: must never execute\n");
    writeFileSync(join(cwd, "backend", ".venv", "pyvenv.cfg"), "version = 3.12.0\n");
    writeFileSync(join(cwd, "backend", "preview.db"), "fixture preview database\n");
  }
  function setup() {
    const result = spawnSync(process.execPath, [join(worktree, "scripts", "setup-env.mjs")], {
      cwd: worktree, encoding: "utf8", env, timeout: 15000,
    });
    assert.equal(result.status, 0, result.stderr || String(result.error));
    assert.equal(result.stderr, "");
    assert.doesNotMatch(result.stdout, /Env setup failed/);
    return result.stdout;
  }
  function state() {
    return {
      head: git(worktree, "rev-parse", "HEAD"),
      branch: git(worktree, "rev-parse", "--abbrev-ref", "HEAD"),
      status: git(worktree, "status", "--porcelain=v1"),
      diff: git(worktree, "diff", "--binary", "--no-ext-diff", "HEAD"),
    };
  }
  mkdirSync(join(main, "scripts"), { recursive: true });
  copyFileSync(setupScript, join(main, "scripts", "setup-env.mjs"));
  writeFileSync(join(main, ".gitignore"), "frontend/node_modules/\nbackend/.venv/\nbackend/preview.db\n");
  writeFileSync(join(main, "conflict.txt"), "base\n");
  git(main, "init", "--initial-branch=main");
  git(main, "config", "user.name", "Setup environment test");
  git(main, "config", "user.email", "setup-env-test@example.invalid");
  git(main, "config", "commit.gpgsign", "false");
  git(main, "config", "core.autocrlf", "false");
  git(main, "add", ".");
  git(main, "commit", "-m", "Initial fixture");
  git(main, "worktree", "add", "-b", "task", worktree, "HEAD");
  prepareEnvironment(main);
  prepareEnvironment(worktree);
  return { main, worktree, env, git, commit, setup, state };
}

test("environment setup preserves a clean worktree HEAD when local main is ahead", (t) => {
  const f = fixture(t);
  f.commit(f.main, "main-only.txt", "new local main commit\n");
  const before = f.state();
  f.setup();
  assert.deepEqual(f.state(), before);
});

test("environment setup preserves own commits and staged, unstaged and untracked work", (t) => {
  const f = fixture(t);
  f.commit(f.main, "main-only.txt", "new local main commit\n");
  f.commit(f.worktree, "own.txt", "own committed work\n");
  writeFileSync(join(f.worktree, "own.txt"), "staged work\n");
  f.git(f.worktree, "add", "own.txt");
  writeFileSync(join(f.worktree, "own.txt"), "unstaged work\n");
  writeFileSync(join(f.worktree, "untracked.txt"), "untracked work\n");
  const before = f.state();
  const stagedBefore = f.git(f.worktree, "diff", "--cached", "--binary", "--no-ext-diff");
  f.setup();
  assert.deepEqual(f.state(), before);
  assert.equal(f.git(f.worktree, "diff", "--cached", "--binary", "--no-ext-diff"), stagedBefore);
  assert.equal(readFileSync(join(f.worktree, "untracked.txt"), "utf8"), "untracked work\n");
});

test("environment setup preserves an in-progress rebase and its conflicts", (t) => {
  const f = fixture(t);
  const base = f.git(f.main, "rev-parse", "HEAD");
  f.commit(f.main, "main-only.txt", "local main advances independently\n");
  f.git(f.main, "switch", "-c", "target", base);
  f.commit(f.main, "conflict.txt", "target change\n");
  f.git(f.main, "switch", "main");
  f.commit(f.worktree, "conflict.txt", "task change\n");
  const rebase = spawnSync("git", ["-C", f.worktree, "rebase", "target"], {
    encoding: "utf8", env: f.env, timeout: 15000,
  });
  assert.equal(rebase.status, 1, rebase.stderr || rebase.stdout);
  const rebaseDirectory = f.git(f.worktree, "rev-parse", "--path-format=absolute", "--git-path", "rebase-merge");
  const rebaseState = () => Object.fromEntries(readdirSync(rebaseDirectory).sort().map((name) => [
    name, readFileSync(join(rebaseDirectory, name)),
  ]));
  const before = f.state();
  assert.match(before.status, /UU conflict.txt/);
  const metadataBefore = rebaseState();
  const conflictBefore = readFileSync(join(f.worktree, "conflict.txt"), "utf8");
  f.setup();
  assert.deepEqual(f.state(), before);
  assert.deepEqual(rebaseState(), metadataBefore);
  assert.equal(readFileSync(join(f.worktree, "conflict.txt"), "utf8"), conflictBefore);
});

test("environment setup still copies a missing preview database from the main checkout", (t) => {
  const f = fixture(t);
  const db = join(f.worktree, "backend", "preview.db");
  rmSync(db);
  const before = f.state();
  const output = f.setup();
  assert.equal(readFileSync(db, "utf8"), readFileSync(join(f.main, "backend", "preview.db"), "utf8"));
  assert.match(output, /backend\/preview\.db \(copy\)/);
  assert.deepEqual(f.state(), before);
});
