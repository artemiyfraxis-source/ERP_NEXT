// SessionStart hook: show what changed in SUPPLYMAP since each module was last ported.
// SUPPLYMAP stays the live system (maintained in its own repo); ERP_NEXT ports its domain rules.
// Each module pins the SUPPLYMAP commit it was ported from in SOURCE.json — lag is counted from there.
// Read-only: never fetches, never writes. Silent (exit 0) when the sibling repo is missing.
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { supplymapRoot } from "../../scripts/sibling-paths.mjs";

const root = join(import.meta.dirname, "..", "..");
const supplymap = supplymapRoot;
const modulesDir = join(root, "backend", "app", "modules");

// Environment check: runs after scripts/setup-env.mjs, so anything missing here means setup failed.
const missing = [
  ["backend/.venv", join(root, "backend", ".venv")],
  ["frontend/node_modules", join(root, "frontend", "node_modules")],
].filter(([, p]) => !existsSync(p)).map(([name]) => name);
if (missing.length) {
  console.log(
    `⚙ Setup: missing ${missing.join(", ")} — ` +
      "scripts/setup-env.mjs did not finish; run `node scripts/setup-env.mjs` and fix what it reports.",
  );
}
if (!existsSync(join(supplymap, ".git")) || !existsSync(modulesDir)) process.exit(0);

const git = (...args) => execFileSync("git", ["-C", supplymap, ...args], { encoding: "utf8" }).trim();
const tryGit = (...args) => { try { return git(...args); } catch { return null; } };

// Code paths only: docs/memory commits do not change domain rules.
const code = ["--", "*.gs", "*.html", "_local/tooling/test/fixtures"];
const known = (commit) => typeof commit === "string" && /^[0-9a-f]{7,40}$/i.test(commit) &&
  tryGit("cat-file", "-e", `${commit}^{commit}`) !== null;
const onMain = (commit) => tryGit("merge-base", "--is-ancestor", commit, "main") !== null;
const behindOf = (commit) => Number(tryGit("rev-list", "--count", `${commit}..main`, ...code) ?? 0);

// Nested pins (projectCardsSourceCommit, grapherImport.sourceCommit, operationLibrary.sourceCommit, …).
const subPins = (node, top = true) =>
  !node || typeof node !== "object" ? [] : Object.entries(node).flatMap(([key, value]) =>
    typeof value === "object" ? subPins(value, false)
      : /sourcecommit$/i.test(key) && !(top && key === "sourceCommit") ? [value] : []);

const pins = [];
for (const name of readdirSync(modulesDir)) {
  const file = join(modulesDir, name, "SOURCE.json");
  if (!existsSync(file)) continue;
  const source = JSON.parse(readFileSync(file, "utf8"));
  let commit = source.sourceCommit;
  if (!known(commit)) continue;
  // A top pin on a SUPPLYMAP side branch (techcards core: feat/techmap-web-generator) is not on main, so
  // `pin..main` would count every main commit since the fork. When the module also pins main through sub-pins,
  // measure from the oldest of them — everything on main is tracked there.
  if (!onMain(commit)) {
    const mainSubs = [...new Set(subPins(source).filter((c) => known(c) && onMain(c)))];
    if (mainSubs.length) commit = mainSubs.sort((a, b) => behindOf(b) - behindOf(a))[0];
  }
  pins.push({ name, commit });
}
if (!pins.length) process.exit(0);

const lag = pins
  .map((p) => ({ ...p, behind: behindOf(p.commit) }))
  .filter((p) => p.behind > 0)
  .sort((a, b) => b.behind - a.behind);
if (!lag.length) {
  console.log("SUPPLYMAP drift: all ported modules are level with SUPPLYMAP main (code paths).");
  process.exit(0);
}

// Newest pin = the one with the fewest code commits behind; list what is new since it.
const newest = lag[lag.length - 1];
const recent = tryGit("log", "--format=%h %ad %s", "--date=format:%d.%m", "-12", `${newest.commit}..main`, ...code) || "";
console.log(
  [
    "SUPPLYMAP drift (code commits on SUPPLYMAP main not yet ported, per module):",
    ...lag.map((p) => `  ${p.name}: ${p.behind} behind (pinned ${p.commit.slice(0, 7)})`),
    `Latest SUPPLYMAP code commits since ${newest.name}'s pin:`,
    ...recent.split("\n").filter(Boolean).map((l) => `  ${l}`),
    "Port domain-rule changes into the matching module, then npm run sync:* and npm run check.",
  ].join("\n"),
);
