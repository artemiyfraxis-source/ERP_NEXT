import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { supplymapRoot } from "./sibling-paths.mjs";

const root = join(import.meta.dirname, "..");
const repositoryRoot = supplymapRoot;
const sourceRepository = process.env.SUPPLYMAP_SOURCE_REPOSITORY || repositoryRoot;
const sourceRef = process.env.SUPPLYMAP_SOURCE_REF || "main";
// Основной источник шаблонов (TC-TSV-IMPORT, «решено агентом — проверить») — зеркало БД.ТК
// (SUPPLYMAP db.mjs pull/push синхронизирует его с живым листом): файл `<id>.tsv` на шаблон, ячейки
// через TAB, в ячейке экранированы обратный слэш, TAB и перевод строки, хвостовые пустые ячейки
// обрезаны, 1-я строка — заголовок; `_images.json` — якоря картинок по id. Фикстура tc-library.json
// остаётся источником только `category` (в TSV её нет) для шаблонов, которые в ней уже есть.
const mirrorPath = "_local/db/БД.ТК";
const categoryFixturePath = "_local/tooling/test/fixtures/tc-library.json";
const sourcePath = mirrorPath;
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
const git = (...args) => execFileSync(
  "git",
  ["-C", sourceRepository, ...args],
  { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
);
const sourceCommit = git("rev-parse", sourceRef).trim();
const unescapeCell = (cell) => cell.replace(/\\(\\|t|n)/g, (_, char) => {
  if (char === "t") return "\t";
  if (char === "n") return "\n";
  return "\\";
});
const parseTsv = (text) => text.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n")
  .map((line) => (line === "" ? [] : line.split("\t").map(unescapeCell)));

// Хеш источника — список блобов зеркала и фикстуры категорий (детерминированный; тот же вызов в
// test/architecture.test.mjs).
const raw = git("ls-tree", "-r", "-z", sourceCommit, "--", mirrorPath, categoryFixturePath);
const mirrorFiles = raw.split("\0").filter(Boolean).map((entry) => entry.split("\t")[1])
  .filter((path) => path.startsWith(`${mirrorPath}/`) && path.endsWith(".tsv"));
const categoryById = new Map(JSON.parse(git("show", `${sourceCommit}:${categoryFixturePath}`))
  .map((item) => [item.id, item.category]));
const fixtureOrder = [...categoryById.keys()];
const imagesById = JSON.parse(git("show", `${sourceCommit}:${mirrorPath}/_images.json`));
const byId = new Map();
for (const path of mirrorFiles) {
  const id = path.slice(mirrorPath.length + 1, -".tsv".length);
  const rows = parseTsv(git("show", `${sourceCommit}:${path}`));
  const title = String(rows[0]?.[0] ?? "").trim();
  if (!title) throw new Error(`Шаблон ${id} зеркала БД.ТК без заголовка в 1-й строке`);
  // Шаблонам вне фикстуры категорию берём из заголовка «CODE | Категория (A)».
  const category = categoryById.get(id)
    ?? title.split(" | ").slice(1).join(" | ").replace(/\s*\([AАBВ]\)$/, "").trim();
  const template = { id, title, category, rows };
  if ((imagesById[id] || []).length) template.images = imagesById[id];
  byId.set(id, template);
}
const missing = fixtureOrder.filter((id) => !byId.has(id));
if (missing.length) throw new Error(`В зеркале БД.ТК нет шаблонов фикстуры: ${missing.join(", ")}`);
// Порядок: сначала шаблоны фикстуры (как раньше), затем новые из зеркала по имени файла.
const templates = [
  ...fixtureOrder.map((id) => byId.get(id)),
  ...[...byId.keys()].filter((id) => !categoryById.has(id)).sort().map((id) => byId.get(id)),
];
if (templates.some((item) => !item.category || item.rows.length < 2)) {
  throw new Error("Зеркало БД.ТК содержит шаблон без категории или строк");
}

const sourceSha256 = createHash("sha256").update(raw).digest("hex");
const output = {
  sourceRef,
  sourceCommit,
  sourcePath,
  categorySourcePath: categoryFixturePath,
  sourceSha256,
  templates,
};
const destination = join(root, "backend", "data", "techcard-library.json");
await mkdir(dirname(destination), { recursive: true });
await writeFile(destination, `${JSON.stringify(output)}\n`);
const buildFixtures = Object.fromEntries(buildFixtureNames.map((name) => {
  const path = `${buildFixtureRoot}/${name}`;
  const content = git("show", `${sourceCommit}:${path}`);
  return [name, {
    sourcePath: path,
    sourceSha256: createHash("sha256").update(content).digest("hex"),
    payload: JSON.parse(content),
  }];
}));
const fixtureDestination = join(root, "backend", "tests", "techcards", "fixtures", "current-etk.json");
await mkdir(dirname(fixtureDestination), { recursive: true });
await writeFile(fixtureDestination, `${JSON.stringify({ sourceRef, sourceCommit, files: buildFixtures })}\n`);

// Пин в SOURCE.json техкарт (руками не править). grapherImport.sourceCommit архитектурный тест
// держит равным operationLibrary.sourceCommit — двигаем вместе, как и раньше.
const metadataPath = join(root, "backend", "app", "modules", "techcards", "SOURCE.json");
const metadata = JSON.parse(await readFile(metadataPath, "utf8"));
metadata.operationLibrary = {
  ...metadata.operationLibrary,
  sourceRef,
  sourceCommit,
  sourcePath,
  categorySourcePath: categoryFixturePath,
  sourceSha256,
  templateCount: templates.length,
  buildFixtureCount: buildFixtureNames.length,
};
metadata.grapherImport = { ...metadata.grapherImport, sourceRef, sourceCommit };
await writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`);
console.log(
  `Synced ${templates.length} operation-card templates (${templates.length - fixtureOrder.length} new vs fixture) and ${buildFixtureNames.length - 1} ETK kits from ${sourceCommit.slice(0, 8)}`,
);
