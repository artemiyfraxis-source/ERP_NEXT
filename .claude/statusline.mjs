// Status line: model, context fill (green <60%, yellow >=60% = start a fresh session, red >=80%),
// branch, subscription burn (5h / 7d windows) and session cost. Node, not PowerShell, so it works on
// the owner's PC, the Ubuntu server and in cloud sessions alike. Wired in .claude/settings.json.
// Keep it cheap: no git subprocess, no npm.
import { readFileSync } from "node:fs";
import { join } from "node:path";

let d;
try {
  d = JSON.parse(readFileSync(0, "utf8"));
} catch {
  process.exit(0);
}

const paint = (s, code) => `\x1b[${code}m${s}\x1b[0m`;
const pct = Math.round(d.context_window?.used_percentage ?? 0);
const parts = [d.model?.display_name ?? "", paint(`${pct}% ctx`, pct >= 80 ? "31" : pct >= 60 ? "33" : "32")];

try {
  const head = readFileSync(join(d.workspace?.current_dir ?? ".", ".git", "HEAD"), "utf8").trim();
  const m = head.match(/^ref: refs\/heads\/(.+)$/);
  parts.push(paint(m ? m[1] : head.slice(0, 7), "36"));
} catch {}

for (const [label, window] of [["5h", d.rate_limits?.five_hour], ["7d", d.rate_limits?.seven_day]]) {
  if (window?.used_percentage == null) continue;
  const used = Math.round(window.used_percentage);
  parts.push(paint(`${label}:${used}%`, used >= 80 ? "31" : used >= 50 ? "33" : "35"));
}

const cost = d.cost?.total_cost_usd;
if (cost > 0) parts.push(`$${cost.toFixed(2)}`);

process.stdout.write(parts.filter(Boolean).join(" | "));
