import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const root = join(import.meta.dirname, "..");
const candidates = [
  process.env.ERP_PYTHON,
  join(root, "backend", ".venv", "Scripts", "python.exe"),
  join(root, "backend", ".venv", "bin", "python"),
  "python",
].filter(Boolean);

const python = candidates.find((candidate) =>
  candidate === "python" || existsSync(candidate),
);
// ERP_TEST_SHARD=i/N (CI matrix): run only shard i of N. Test files are balanced greedily by size (a rough proxy for
// run time), so every shard gets a similar load and the union of all shards is exactly the full suite.
function shardTargets(spec) {
  const match = /^(\d+)\/(\d+)$/.exec(spec ?? "");
  if (!match) return ["tests"];
  const [index, count] = [Number(match[1]), Number(match[2])];
  if (!(count >= 1 && index >= 1 && index <= count)) throw new Error(`bad ERP_TEST_SHARD ${spec}`);
  const backend = join(root, "backend");
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/^test_.*\.py$/.test(entry.name)) files.push({ path: relative(backend, path).replaceAll("\\", "/"), size: statSync(path).size });
    }
  };
  walk(join(backend, "tests"));
  files.sort((a, b) => b.size - a.size || a.path.localeCompare(b.path));
  const loads = Array.from({ length: count }, () => ({ size: 0, files: [] }));
  for (const file of files) {
    const lightest = loads.reduce((best, load) => (load.size < best.size ? load : best));
    lightest.size += file.size;
    lightest.files.push(file.path);
  }
  return loads[index - 1].files.sort();
}

const targets = shardTargets(process.env.ERP_TEST_SHARD);
const result = spawnSync(
  python,
  // pytest-xdist: test files spread over all cores (a file stays on one worker — its module fixtures and files are shared).
  ["-m", "pytest", ...targets, "-q", "-n", "auto", "--dist", "loadfile"],
  { cwd: join(root, "backend"), encoding: "utf8", stdio: "inherit" },
);

if (result.error) {
  console.error(`Cannot run backend tests with ${python}: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
