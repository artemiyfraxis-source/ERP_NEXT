// PreToolUse (Bash|PowerShell): the gate that holds even in bypassPermissions mode. Ported from
// SUPPLYMAP's guard-hard-donts.ps1 minus the GAS rules. Blocks via stdout JSON, exit 0 always
// (a non-zero exit reads as a broken hook and the command runs).
//   - making the repo public                     -> deny (private repo, owner's data and pins)
//   - force-push / hard reset / clean / discard  -> ask  (destroys work no tree can restore)
//   - live stock import without --dry-run        -> ask  (writes the warehouse into a real DB)
//   - alembic downgrade                          -> ask  (drops columns/tables with their data)
//   - not the owner's git identity: direct push to main, push.ps1, server ssh -> deny; unknown identity also
//     can't merge PRs or request a server update (main auto-deploys; Витя releases via PR — .claude/team/RULES.md)
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const OWNER_EMAIL = "artemiyfraxis@gmail.com";
const TEAM_EMAILS = [OWNER_EMAIL, "tektra.real.tektra@gmail.com"];
const TEAM_LOGINS = ["artemiyfraxis-source", "h4styq"];

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
const ownerOnly = touchesMain || /push\.ps1|erp@|100\.96\.49\.68/.test(cmd);
const release = /gh\s+pr\s+merge|gh\s+workflow\s+run\s+[^|;&]*update-server/.test(cmd);
if (ownerOnly || release) {
  const email = git("config user.email").toLowerCase();
  if (email !== OWNER_EMAIL) {
    if (ownerOnly) {
      decide("deny", "Blocked by .claude/hooks/guard-danger.mjs: direct push to main, push.ps1 and server ssh are the owner's. main auto-deploys to production — merge a PR with green CI instead, see .claude/team/RULES.md «Releasing».");
    }
    let team = TEAM_EMAILS.includes(email);
    if (!team) {
      try {
        team = TEAM_LOGINS.includes(execSync("gh api user --jq .login", { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 10000 }).trim());
      } catch {}
    }
    if (!team) {
      decide("deny", "Blocked by .claude/hooks/guard-danger.mjs: neither git user.email nor the gh login is a known developer of this repo, and merging or updating the server releases to production.");
    }
  }
}
