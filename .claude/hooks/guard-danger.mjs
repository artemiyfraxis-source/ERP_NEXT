// PreToolUse (Bash|PowerShell): the gate that holds even in bypassPermissions mode. Ported from
// SUPPLYMAP's guard-hard-donts.ps1 minus the GAS rules. Blocks via stdout JSON, exit 0 always
// (a non-zero exit reads as a broken hook and the command runs).
//   - making the repo public                     -> deny (private repo, owner's data and pins)
//   - force-push / hard reset / clean / discard  -> ask  (destroys work no tree can restore)
//   - live stock import without --dry-run        -> ask  (writes the warehouse into a real DB)
//   - alembic downgrade                          -> ask  (drops columns/tables with their data)
//   - not the owner's git identity: push to main, gh pr merge, server update/deploy/ssh -> deny
//     (main auto-deploys to production; Витя's sessions stop at a PR — .claude/team/RULES.md)
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const OWNER_EMAIL = "artemiyfraxis@gmail.com";

let input;
try {
  input = JSON.parse(readFileSync(0, "utf8"));
} catch {
  process.exit(0);
}
const cmd = String(input.tool_input?.command ?? "");
if (!cmd) process.exit(0);

const decide = (permissionDecision, permissionDecisionReason) => {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision, permissionDecisionReason },
  }));
  process.exit(0);
};

if (/gh\s+repo\s+edit[^|;&]*--visibility[=\s]+public/.test(cmd)) {
  decide("deny", "Blocked by .claude/hooks/guard-danger.mjs: ERP_NEXT must stay private. Publishing it cannot be undone by flipping it back.");
}
if (/git\s+(push[^|;&]*(--force(?!-with-lease)|\s-f\b|\s\+[\w/.-]+)|reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|restore\s+(--\s+)?\.(\s|$))/.test(cmd)) {
  decide("ask", "This git command discards work that is not recoverable from the working tree (force-push / hard reset / clean / discard local changes). Confirm the target and that nothing unpushed is lost.");
}
if (/import_stock(?![^|;&]*--dry-run)/.test(cmd)) {
  decide("ask", "Live stock import (WH-IMPORT) without --dry-run writes the warehouse into the database in ERP_DATABASE_URL. The real import is the owner's cut-over step: confirm the target DB.");
}
if (/alembic(\.exe)?\s+downgrade/.test(cmd)) {
  decide("ask", "alembic downgrade drops columns/tables together with their data. Confirm the target DB is a throwaway one.");
}

const git = (args) => {
  for (const cwd of [input.cwd, process.cwd()].filter(Boolean)) {
    try {
      return execSync(`git ${args}`, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
    } catch {}
  }
  return "";
};
const touchesMain =
  /git\s+push\b[^|;&]*[\s:+](refs\/heads\/)?main(\s|$)/.test(cmd) ||
  (/git\s+push\b(?![^|;&]*\s[\w./-]+\s+[\w./:+-]+)/.test(cmd) && git("rev-parse --abbrev-ref HEAD") === "main");
const releases =
  /gh\s+pr\s+merge|gh\s+workflow\s+run\s+[^|;&]*update-server|push\.ps1|erp@|100\.96\.49\.68/.test(cmd);
if ((touchesMain || releases) && git("config user.email").toLowerCase() !== OWNER_EMAIL) {
  decide("deny", "Blocked by .claude/hooks/guard-danger.mjs: this session is not the owner's (git user.email). main auto-deploys to production, so only the owner merges, pushes main and updates the server. Push your branch vitya/<topic>, mark the PR ready and stop — see .claude/team/RULES.md.");
}
