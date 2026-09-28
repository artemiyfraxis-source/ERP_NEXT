// Build the local environment of this checkout if it is missing: frontend/node_modules, backend/.venv,
// backend/preview.db. Runs from the SessionStart hook, so a fresh Desktop worktree (which carries no
// gitignored files) is ready before the agent starts. Silent when everything is in place; never fails
// the session — it reports and exits 0.
import { execFileSync, spawnSync } from "node:child_process";
import { copyFileSync, existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

const root = join(import.meta.dirname, "..");
const win = process.platform === "win32";
const venvPython = join(root, "backend", ".venv", win ? "Scripts/python.exe" : "bin/python");
const done = [];

// The main checkout owns the reference environment; a worktree shares its .git.
let mainRoot = root;
try {
  const common = execFileSync("git", ["-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"], {
    encoding: "utf8",
  }).trim();
  mainRoot = dirname(common);
} catch {}

// Desktop creates a worktree from origin/main, which lags local main until the owner pushes. A fresh
// worktree (clean, no own commits) is moved onto local main; one with work in it only gets a warning.
if (mainRoot !== root) {
  const g = (...a) => spawnSync("git", ["-C", root, ...a], { encoding: "utf8" });
  if (g("merge-base", "--is-ancestor", "main", "HEAD").status !== 0) {
    const clean = !g("status", "--porcelain").stdout.trim();
    const ownCommits = Number(g("rev-list", "--count", "main..HEAD").stdout.trim() || 0);
    if (clean && ownCommits === 0 && g("rebase", "main").status === 0) {
      console.log("⚙ Worktree was based on origin/main (behind local main) — moved onto local main.");
    } else {
      g("rebase", "--abort");
      console.log("⚙ Worktree is based on an older commit than local main — run `git rebase main` before working.");
    }
  }
}

const run = (cmd, args, cwd) => {
  // npm is a .cmd shim on Windows and needs a shell; pass one command string (fixed args, no user input).
  const r = cmd === "npm"
    ? spawnSync(`npm ${args.join(" ")}`, { cwd, encoding: "utf8", shell: true })
    : spawnSync(cmd, args, { cwd, encoding: "utf8" });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")}: ${(r.stderr || r.stdout || r.error || "").toString().trim().slice(-400)}`);
};

try {
  if (!existsSync(join(root, "frontend", "node_modules"))) {
    run("npm", ["ci", "--no-audit", "--no-fund"], join(root, "frontend"));
    done.push("frontend/node_modules");
  }

  if (!existsSync(venvPython)) {
    // Base interpreter: ERP_PYTHON_BASE, else the one the main checkout's venv was built from.
    let base = process.env.ERP_PYTHON_BASE;
    const cfg = join(mainRoot, "backend", ".venv", "pyvenv.cfg");
    if (!base && existsSync(cfg)) {
      base = readFileSync(cfg, "utf8").match(/^executable\s*=\s*(.+)$/m)?.[1]?.trim();
    }
    run(base || "python", ["-m", "venv", join(root, "backend", ".venv")], root);
    run(venvPython, ["-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "requirements.lock.txt"], join(root, "backend"));
    done.push("backend/.venv");
  }

  const db = join(root, "backend", "preview.db");
  const mainDb = join(mainRoot, "backend", "preview.db");
  if (!existsSync(db) && mainRoot !== root && existsSync(mainDb)) {
    copyFileSync(mainDb, db);
    done.push("backend/preview.db (copy)");
  }

  if (done.length) console.log(`⚙ Env: built ${done.join(", ")} for this checkout.`);
} catch (err) {
  console.log(`⚙ Env setup failed — run \`node scripts/setup-env.mjs\` and read the error: ${err.message}`);
}
