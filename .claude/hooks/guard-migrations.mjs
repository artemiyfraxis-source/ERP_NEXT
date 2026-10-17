// PreToolUse (Edit|Write|MultiEdit): refuse to change an Alembic migration that is already on
// origin/main. Those have run on the owner's real.db and on the server; a fix is a NEW migration
// after the current head. Brand-new migrations on a branch pass. Blocks via stdout JSON, exit 0.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { relative, resolve } from "node:path";

let input;
try {
  input = JSON.parse(readFileSync(0, "utf8"));
} catch {
  process.exit(0);
}
const filePath = input.tool_input?.file_path;
if (!filePath) process.exit(0);

const root = resolve(import.meta.dirname, "..", "..");
const rel = relative(root, resolve(filePath)).split("\\").join("/");
if (!/^backend\/migrations\/versions\/[^/]+\.py$/.test(rel)) process.exit(0);

try {
  execFileSync("git", ["-C", root, "cat-file", "-e", `origin/main:${rel}`], { stdio: "ignore" });
} catch {
  process.exit(0); // not on main yet: a migration of this branch, editing is fine
}
process.stdout.write(JSON.stringify({
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason:
      `${rel} is already on main and has run on real databases. Do not edit it: add a new Alembic migration after the current head.`,
  },
}));
