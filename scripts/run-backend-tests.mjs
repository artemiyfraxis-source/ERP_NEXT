import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

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
const result = spawnSync(
  python,
  ["-m", "pytest", "tests", "-q"],
  { cwd: join(root, "backend"), encoding: "utf8", stdio: "inherit" },
);

if (result.error) {
  console.error(`Cannot run backend tests with ${python}: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
