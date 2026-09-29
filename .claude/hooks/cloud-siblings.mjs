// SessionStart hook, cloud only (Claude Code on the web sets CLAUDE_CODE_REMOTE=true). Locally it exits at once.
// A cloud container clones ERP_NEXT alone; this brings in what a local session has next to it:
//   - ../SUPPLYMAP     — source of domain rules; the drift hook and architecture tests read it;
//   - ../claude-brain  — the owner's cross-project notes (locally ~/.claude/brain, the Obsidian vault).
// Then it prints the owner's global rules (brain _global/CLAUDE.md) and the brain INDEX, which a local
// session gets from ~/.claude/CLAUDE.md. Never fails the session: reports and exits 0.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { mainCheckout, supplymapRoot } from "../../scripts/sibling-paths.mjs";

if (process.env.CLAUDE_CODE_REMOTE !== "true") process.exit(0);

const brainRoot = process.env.CLAUDE_BRAIN_ROOT || join(mainCheckout, "..", "claude-brain");
const repos = [
  { name: "SUPPLYMAP", dir: supplymapRoot, full: true }, // full history: drift counts commits since each pin
  { name: "claude-brain", dir: brainRoot },
];

const git = (args, cwd) => spawnSync("git", args, { cwd, encoding: "utf8", timeout: 300_000 });
const missing = [];
for (const r of repos) {
  if (existsSync(join(r.dir, ".git"))) {
    git(["pull", "-q", "--ff-only"], r.dir); // best effort: a stale copy still beats none
    continue;
  }
  const url = `https://github.com/artemiyfraxis-source/${r.name}`;
  const res = git(["clone", "-q", ...(r.full ? [] : ["--depth", "1"]), url, r.dir]);
  if (res.status !== 0) missing.push(r.name);
}

const out = [];
if (missing.length) {
  out.push(
    `⚙ Cloud: could not clone ${missing.join(", ")} (repo not attached to this session). ` +
      `Attach each with add_repo (owner artemiyfraxis-source), then run \`node .claude/hooks/cloud-siblings.mjs\` ` +
      "and `node .claude/hooks/supplymap-drift.mjs` again.",
  );
}

const rules = join(brainRoot, "_global", "CLAUDE.md");
if (existsSync(rules)) {
  out.push(
    `Owner's global rules (from ${rules}; locally this is ~/.claude/CLAUDE.md). The brain lives at ${brainRoot}:`,
    "read INDEX.md there and pull only relevant notes. In the cloud the brain is READ-ONLY — its PowerShell sync",
    "hooks, apply-global and the Obsidian MCP do not exist here; ignore those sections. Project facts go to",
    ".claude/knowledge/memory (index MEMORY.md).",
    "",
    readFileSync(rules, "utf8").trim(),
  );
}
if (out.length) console.log(out.join("\n"));
