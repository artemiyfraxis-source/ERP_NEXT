// Module boundaries of the ERP itself. SOURCE.json files are frozen provenance records (what each module was ported
// from and which invariants it owns); nothing here reads the sibling SUPPLYMAP checkout (owner 05.10.2026).
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";

const root = join(import.meta.dirname, "..");
const moduleMap = JSON.parse(await readFile(join(root, "module-map.json"), "utf8"));

function requirementIdentity(line) {
  const requirement = line.split("#", 1)[0].trim();
  if (!requirement) return null;
  const match = requirement.match(/^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)$/);
  assert.ok(match, `unsupported pinned requirement: ${requirement}`);
  return `${match[1].toLowerCase().replaceAll("_", "-")}==${match[2]}`;
}

function requirementName(line) {
  return requirementIdentity(line)?.split("==", 1)[0] ?? null;
}

test("every module has one explicit owner", () => {
  const ids = moduleMap.modules.map(({ id }) => id);
  assert.equal(new Set(ids).size, ids.length);
  for (const module of moduleMap.modules) {
    assert.equal(module.owner, "supplymap");
  }
});

test("System ID is retained only as a legacy identifier", () => {
  assert.equal(moduleMap.architecture, "modular-monolith");
  assert.equal(moduleMap.legacyIdField, "legacy_id");
});

test("techcards keeps its bundled library consistent and its dependencies in the central lock", async () => {
  const metadata = JSON.parse(
    await readFile(join(root, "backend", "app", "modules", "techcards", "SOURCE.json"), "utf8"),
  );
  const techcardsRequirements = (await readFile(
    join(root, "backend", "app", "modules", "techcards", "requirements.source.txt"),
    "utf8",
  )).split(/\r?\n/).map(requirementName).filter(Boolean);
  const centralRequirements = new Set((await readFile(
    join(root, "backend", "requirements.lock.txt"),
    "utf8",
  )).split(/\r?\n/).map(requirementName).filter(Boolean));
  assert.equal(
    techcardsRequirements.every((requirement) => centralRequirements.has(requirement)),
    true,
    "techcards dependency packages must be included in the central ERP lock",
  );

  const apiSource = await readFile(
    join(root, "backend", "app", "modules", "techcards", "api.py"),
    "utf8",
  );
  assert.equal(apiSource.includes("..services"), false);

  const operationLibrary = JSON.parse(await readFile(
    join(root, "backend", "data", "techcard-library.json"),
    "utf8",
  ));
  const operationSourceCommit = metadata.operationLibrary.sourceCommit;
  assert.equal(operationLibrary.sourceCommit, operationSourceCommit);
  assert.equal(operationLibrary.sourceSha256, metadata.operationLibrary.sourceSha256);
  assert.equal(operationLibrary.templates.length, metadata.operationLibrary.templateCount);
  const buildFixtures = JSON.parse(await readFile(
    join(root, "backend", "tests", "techcards", "fixtures", "current-etk.json"),
    "utf8",
  ));
  assert.equal(buildFixtures.sourceCommit, operationSourceCommit);
  assert.equal(Object.keys(buildFixtures.files).length, metadata.operationLibrary.buildFixtureCount);
  const techcardsModule = moduleMap.modules.find(({ id }) => id === "techcards");
  assert.equal(
    techcardsModule.implementedStage,
    "persistent-project-cards-native-etk-builder-cut-crimp-splices-connector-housings-operation-library-terminal-norms-and-grapher-import",
  );
  assert.equal(techcardsModule.routePrefixes.includes("/api/techcards"), true);
  assert.equal(metadata.grapherImport.sourceCommit, operationSourceCommit);
  const grapherSource = await readFile(join(root, "backend", "app", "modules", "techcards", "grapher_import.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(grapherSource.includes(forbidden), false, `grapher import imports ${forbidden}`);
  assert.equal(grapherSource.includes("def read_export"), true);
});

test("requests domain stays independent and records its port provenance", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "requests");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");

  const domainSource = await readFile(join(moduleRoot, "domain.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient", "modules.projects"])
    assert.equal(domainSource.includes(forbidden), false, `requests domain imports ${forbidden}`);

  const appSource = await readFile(join(root, "backend", "app", "main.py"), "utf8");
  assert.equal(appSource.includes("requests_api.router"), true);
});

test("procurement owns its status machine and records its port provenance", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "procurement");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");

  const requestsDomain = await readFile(
    join(root, "backend", "app", "modules", "requests", "domain.py"),
    "utf8",
  );
  assert.equal(requestsDomain.includes("class ProcurementStatus"), false);
  assert.equal(requestsDomain.includes("revision=\"A\""), false);

  const procurementDomain = await readFile(join(moduleRoot, "domain.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "modules.requests"])
    assert.equal(
      procurementDomain.includes(forbidden),
      false,
      `procurement domain imports ${forbidden}`,
    );
  assert.equal(procurementDomain.includes("class ProcurementStatus"), true);
  // Rule: a line's default revision is A (a retransfer passes the next letter, RQ-3).
  assert.match(procurementDomain, /revision(?:\s*:\s*str)?\s*=\s*"A"/);
});

test("identity records its port provenance and avoids legacy token storage", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "identity");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");

  const middleware = await readFile(join(moduleRoot, "middleware.py"), "utf8");
  const domain = await readFile(join(moduleRoot, "domain.py"), "utf8");
  assert.equal(middleware.includes('SESSION_COOKIE = "erp_session"'), true);
  assert.equal(middleware.includes("localStorage"), false);
  assert.equal(domain.includes("PasswordHasher"), true);
  assert.equal(domain.includes("Type.ID"), true);
});

test("receiving and warehouse own native traceability rules", async () => {
  for (const name of ["receiving", "warehouse"]) {
    const moduleRoot = join(root, "backend", "app", "modules", name);
    const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
    assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");
    const domain = await readFile(join(moduleRoot, "domain.py"), "utf8");
    for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
      assert.equal(domain.includes(forbidden), false, `${name} domain imports ${forbidden}`);
  }
  const appSource = await readFile(join(root, "backend", "app", "main.py"), "utf8");
  assert.equal(appSource.includes("receiving_api.router"), true);
  assert.equal(appSource.includes("warehouse_api.router"), true);
});

test("fulfillment records its source and stays independent of transport and persistence", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "fulfillment");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");
  assert.deepEqual(metadata.invariants, [
    "request-exists-without-stock",
    "human-binds-specific-lot",
    "requirement-and-reservation-are-distinct",
    "whole-kit-is-issued-atomically",
    "warehouse-lot-selection-displays-article-before-description",
    "issue-journals-one-writeoff-row-per-issued-position",
  ]);

  const domain = await readFile(join(moduleRoot, "domain.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(domain.includes(forbidden), false, `fulfillment domain imports ${forbidden}`);

  const appSource = await readFile(join(root, "backend", "app", "main.py"), "utf8");
  assert.equal(appSource.includes("fulfillment_api.router"), true);
});

test("production records its implemented execution stage", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "production");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");
  assert.equal(metadata.implementedStage, "production-traceability-stage-6-plan-manual-kit-marks-and-auto-scheduler-step-2");

  const domain = await readFile(join(moduleRoot, "domain.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(domain.includes(forbidden), false, `production domain imports ${forbidden}`);
  assert.equal(domain.includes("class OperationStatus"), false);

  const traceability = await readFile(join(moduleRoot, "traceability.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(traceability.includes(forbidden), false, `production traceability imports ${forbidden}`);
  assert.equal(traceability.includes("def box_check_digit"), true);

  const genealogy = await readFile(join(moduleRoot, "genealogy.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(genealogy.includes(forbidden), false, `production genealogy imports ${forbidden}`);

  const plan = await readFile(join(moduleRoot, "plan.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(plan.includes(forbidden), false, `production plan imports ${forbidden}`);
  assert.equal(plan.includes("KIT_STAGES"), true);
  assert.equal(plan.includes("def summarize_journal"), true);

  const scheduling = await readFile(join(moduleRoot, "scheduling.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(scheduling.includes(forbidden), false, `production scheduling imports ${forbidden}`);
  assert.equal(scheduling.includes("def schedule_variants"), true);
  assert.equal(scheduling.includes("def chain_start"), true);

  const appSource = await readFile(join(root, "backend", "app", "main.py"), "utf8");
  assert.equal(appSource.includes("production_api.router"), true);
  assert.equal(appSource.includes("production_plan_api.router"), true);
});

test("shipping keeps finished goods and shipment gates in one native context", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "shipping");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");
  assert.equal(metadata.implementedStage, "finished-goods-and-shipment-workflow");

  const domain = await readFile(join(moduleRoot, "domain.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(domain.includes(forbidden), false, `shipping domain imports ${forbidden}`);
  assert.equal(domain.includes("requested"), true);
  assert.equal(domain.includes("picked"), true);

  const appSource = await readFile(join(root, "backend", "app", "main.py"), "utf8");
  assert.equal(appSource.includes("shipping_api.router"), true);
});

test("references separates editable maps from the versioned technology snapshot", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "references");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");
  assert.equal(metadata.invariants.includes("technology-catalog-is-a-versioned-snapshot"), true);
  assert.equal(metadata.implementedStage, "technology-schema-v14-operation-and-terminal-norm-kernel");
  assert.equal(metadata.invariants.includes("terminal-norm-prefers-exact-wire-section"), true);

  const domain = await readFile(join(moduleRoot, "domain.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(domain.includes(forbidden), false, `references domain imports ${forbidden}`);

  const technology = await readFile(join(moduleRoot, "technology.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(technology.includes(forbidden), false, `references technology imports ${forbidden}`);
  assert.equal(technology.includes("TECH_SNAPSHOT_SCHEMA_VERSION = 14"), true);

  const appSource = await readFile(join(root, "backend", "app", "main.py"), "utf8");
  assert.equal(appSource.includes("references_api.router"), true);
});

test("operations replaces the owner tick with append-only audit and reliable jobs", async () => {
  const moduleRoot = join(root, "backend", "app", "modules", "operations");
  const metadata = JSON.parse(await readFile(join(moduleRoot, "SOURCE.json"), "utf8"));
  assert.match(metadata.sourceCommit, /^[0-9a-f]{40}$/, "SOURCE.json keeps the commit the module was ported from");
  assert.equal(metadata.invariants.includes("minute-owner-tick-is-not-recreated"), true);

  const domain = await readFile(join(moduleRoot, "domain.py"), "utf8");
  for (const forbidden of ["fastapi", "sqlalchemy", "googleapiclient"])
    assert.equal(domain.includes(forbidden), false, `operations domain imports ${forbidden}`);

  const worker = await readFile(join(root, "backend", "app", "outbox_worker.py"), "utf8");
  assert.equal(worker.includes("dispatch_next_notification"), true);
  assert.equal(worker.includes("everyMinutes"), false);
});
