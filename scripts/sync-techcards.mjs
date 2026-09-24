import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

const root = join(import.meta.dirname, "..");
const repositoryRoot = process.env.SUPPLYMAP_SOURCE_REPOSITORY || join(root, "..", "SUPPLYMAP");
const sourceRef = process.env.TECHCARDS_SOURCE_REF || "feat/techmap-web-generator";

function git(...args) {
  return execFileSync("git", ["-C", repositoryRoot, ...args], { encoding: "utf8" });
}

const sourceCommit = git("rev-parse", sourceRef).trim();

function source(path) {
  return git("show", `${sourceCommit}:${path}`);
}

const files = [
  {
    from: "techmap/backend/app/services/coax.py",
    to: "backend/app/modules/techcards/domain/coax.py",
  },
  {
    from: "techmap/backend/app/services/harness.py",
    to: "backend/app/modules/techcards/domain/harness.py",
  },
  {
    from: "techmap/backend/tests/test_coax.py",
    to: "backend/tests/techcards/test_coax.py",
    transform: (text) => text.replaceAll("from app.services.coax", "from app.modules.techcards.domain.coax"),
  },
  {
    from: "techmap/backend/tests/test_harness.py",
    to: "backend/tests/techcards/test_harness.py",
    transform: (text) => text.replaceAll("from app.services.harness", "from app.modules.techcards.domain.harness"),
  },
  {
    from: "techmap/backend/tests/test_api.py",
    to: "backend/tests/techcards/test_api.py",
  },
  {
    from: "techmap/backend/requirements.txt",
    to: "backend/app/modules/techcards/requirements.source.txt",
  },
];

// Resolve and transform every source before touching the working tree. A missing
// upstream file therefore cannot leave a half-updated module.
const prepared = files.map((file) => {
  const content = file.transform ? file.transform(source(file.from)) : source(file.from);
  return {
    ...file,
    content,
    sha256: createHash("sha256").update(content).digest("hex"),
  };
});

function requirementIdentity(line) {
  const requirement = line.split("#", 1)[0].trim();
  if (!requirement) return null;
  const match = requirement.match(/^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)$/);
  if (!match) throw new Error(`Unsupported techcards requirement: ${requirement}`);
  return `${match[1].toLowerCase().replaceAll("_", "-")}==${match[2]}`;
}

const sourceRequirements = prepared.find(({ to }) =>
  to.endsWith("requirements.source.txt"),
).content.split(/\r?\n/).map(requirementIdentity).filter(Boolean);
const centralLock = (await readFile(join(root, "backend", "requirements.lock.txt"), "utf8"))
  .split(/\r?\n/).map(requirementIdentity).filter(Boolean);
const centralSet = new Set(centralLock);
const missingRequirements = sourceRequirements.filter((item) => !centralSet.has(item));
if (missingRequirements.length) {
  throw new Error(
    `Techcards dependencies are absent from backend/requirements.lock.txt: ${missingRequirements.join(", ")}. Update the ERP lock before syncing.`,
  );
}

const sourceMetadata = {
  sourceRef,
  sourceCommit,
  files: prepared.map(({ from, to, sha256 }) => ({ from, to, sha256 })),
};
const metadataPath = join(root, "backend", "app", "modules", "techcards", "SOURCE.json");
const outputs = [
  ...prepared.map(({ to, content }) => ({ destination: join(root, to), content })),
  { destination: metadataPath, content: `${JSON.stringify(sourceMetadata, null, 2)}\n` },
];

const originals = new Map();
try {
  for (const output of outputs) {
    await mkdir(dirname(output.destination), { recursive: true });
    try {
      originals.set(output.destination, await readFile(output.destination));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      originals.set(output.destination, null);
    }
    await writeFile(`${output.destination}.sync.tmp`, output.content);
  }
  for (const output of outputs) {
    await rename(`${output.destination}.sync.tmp`, output.destination);
  }
} catch (error) {
  for (const output of outputs) {
    await rm(`${output.destination}.sync.tmp`, { force: true });
    const original = originals.get(output.destination);
    if (original === null) await rm(output.destination, { force: true });
    else if (original !== undefined) await writeFile(output.destination, original);
  }
  throw error;
}
console.log(`Techcards synced from ${sourceRef}@${sourceMetadata.sourceCommit.slice(0, 8)}`);
