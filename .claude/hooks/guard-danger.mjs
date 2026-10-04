// PreToolUse (Bash|PowerShell): the gate that holds even in bypassPermissions mode. Ported from
// SUPPLYMAP's guard-hard-donts.ps1 minus the GAS rules. Blocks via stdout JSON, exit 0 always
// (a non-zero exit reads as a broken hook and the command runs).
//   - making the repo public                     -> deny (private repo, owner's data and pins)
//   - force-push / hard reset / clean / discard  -> ask  (destroys work no tree can restore)
//   - live stock import without --dry-run        -> ask  (writes the warehouse into a real DB)
//   - alembic downgrade                          -> ask  (drops columns/tables with their data)
import { readFileSync } from "node:fs";

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
