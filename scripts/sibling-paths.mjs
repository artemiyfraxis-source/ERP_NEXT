// Sibling repos (SUPPLYMAP, ERP_RECOVERED) sit next to the MAIN checkout. A Desktop worktree lives
// under .claude/worktrees/<name>, so "../SUPPLYMAP" from it points nowhere. Resolve through git's
// common dir, which is the same for the main checkout and every worktree. Env vars still override.
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";

const repoRoot = join(import.meta.dirname, "..");
let main = repoRoot;
try {
  main = dirname(
    execFileSync("git", ["-C", repoRoot, "rev-parse", "--path-format=absolute", "--git-common-dir"], {
      encoding: "utf8",
    }).trim(),
  );
} catch {}

export const mainCheckout = main;
export const supplymapRoot = process.env.SUPPLYMAP_SOURCE_REPOSITORY || join(main, "..", "SUPPLYMAP");
export const recoveredRoot = process.env.ERP_RECOVERED_ROOT || join(main, "..", "ERP_RECOVERED");
