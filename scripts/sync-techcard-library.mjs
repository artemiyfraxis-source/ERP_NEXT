import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { supplymapRoot } from "./sibling-paths.mjs";

const root = join(import.meta.dirname, "..");
const repositoryRoot = supplymapRoot;
const sourceRepository = process.env.SUPPLYMAP_SOURCE_REPOSITORY || repositoryRoot;
const sourceRef = process.env.SUPPLYMAP_SOURCE_REF || "origin/main";
const sourcePath = "_local/tooling/test/fixtures/tc-library.json";
const buildFixtureRoot = "_local/tooling/test/fixtures/etk";
const buildFixtureNames = [
  "db.json",
  "kit-345К.2-КБ1.json",
  "kit-591К.5-КБ2.json",
  "kit-708К.2-КБ1.json",
  "kit-708К.5-КБ1.json",
  "kit-720К.-КБ1.json",
  "kit-720К.1.2-КБ1.json",
  "kit-729К.2-КБ1.json",
];
const sourceCommit = execFileSync(
  "git",
  ["-C", sourceRepository, "rev-parse", sourceRef],
  { encoding: "utf8" },
).trim();
const raw = execFileSync(
  "git",
  ["-C", sourceRepository, "show", `${sourceCommit}:${sourcePath}`],
  { encoding: "utf8", maxBuffer: 10 * 1024 * 1024 },
);
const templates = JSON.parse(raw);
if (!Array.isArray(templates) || templates.some((item) =>
  !item || typeof item.id !== "string" || typeof item.title !== "string" || !Array.isArray(item.rows)
)) {
  throw new Error("SUPPLYMAP techcard fixture has an unsupported format");
}
const ids = new Set(templates.map(({ id }) => id));
if (ids.size !== templates.length) throw new Error("SUPPLYMAP techcard fixture contains duplicate ids");

const output = {
  sourceRef,
  sourceCommit,
  sourcePath,
  sourceSha256: createHash("sha256").update(raw).digest("hex"),
  templates,
};
const destination = join(root, "backend", "data", "techcard-library.json");
await mkdir(dirname(destination), { recursive: true });
await writeFile(destination, `${JSON.stringify(output)}\n`);
const buildFixtures = Object.fromEntries(buildFixtureNames.map((name) => {
  const path = `${buildFixtureRoot}/${name}`;
  const content = execFileSync(
    "git",
    ["-C", sourceRepository, "show", `${sourceCommit}:${path}`],
    { encoding: "utf8", maxBuffer: 10 * 1024 * 1024 },
  );
  return [name, {
    sourcePath: path,
    sourceSha256: createHash("sha256").update(content).digest("hex"),
    payload: JSON.parse(content),
  }];
}));
const fixtureDestination = join(root, "backend", "tests", "techcards", "fixtures", "current-etk.json");
await mkdir(dirname(fixtureDestination), { recursive: true });
await writeFile(fixtureDestination, `${JSON.stringify({ sourceRef, sourceCommit, files: buildFixtures })}\n`);
console.log(
  `Synced ${templates.length} operation-card templates and ${buildFixtureNames.length - 1} ETK kits from ${sourceCommit.slice(0, 8)}`,
);
