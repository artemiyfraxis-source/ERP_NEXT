import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { recoveredRoot } from "./sibling-paths.mjs";

const recoveryRoot = recoveredRoot;
const sourcePath = join(
  recoveryRoot,
  "reconstruction",
  "contracts",
  "observed-openapi.json",
);
const outputPath = join(import.meta.dirname, "..", "contracts", "legacy-projects.openapi.json");

const ownedPrefixes = [
  "/api/projects",
  "/api/calc",
  "/api/docs",
  "/api/customers",
];

const source = JSON.parse(await readFile(sourcePath, "utf8"));
const paths = Object.fromEntries(
  Object.entries(source.paths).filter(([path]) =>
    ownedPrefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`)),
  ),
);

const contract = {
  ...source,
  info: {
    ...source.info,
    title: "Fraxis ERP legacy Projects and Calculation API",
    description:
      "Observed compatibility contract for the legacy-owned Projects and Calculation modules. Other ERP modules must not be implemented from this contract.",
  },
  paths,
  "x-module-owner": "recovered-erp",
  "x-owned-prefixes": ownedPrefixes,
  "x-source-contract": "ERP_RECOVERED/reconstruction/contracts/observed-openapi.json",
};

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(contract, null, 2)}\n`);
console.log(`Legacy Projects contract: ${Object.keys(paths).length} paths`);
