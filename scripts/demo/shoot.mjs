#!/usr/bin/env node
// Screenshots of the running ERP at the owner's two screens (27" monitor, 12.7" tablet) + a text lint.
//   node scripts/demo/shoot.mjs projects stock "card=?section=projects&project=614К.1"
//   env: ERP_URL (default http://127.0.0.1:8021), ERP_LOGIN/ERP_PASSWORD (preview), SHOTS_DIR (default /tmp/erp-shots)
// An argument is a section id (see frontend/src/navigation.ts) or name=?query. Writes <name>-27.png and
// <name>-tablet.png, then prints what the lint found on the visible text: English identifiers, raw codes,
// «БЕЗ КОДА», undefined/NaN, and a «Загрузка…» that never went away. Read the PNGs — exit 0 is not a verdict.
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try { return require("playwright"); } catch { /* fall through to the global install */ }
  const root = execSync("npm root -g").toString().trim();
  return require(path.join(root, "playwright"));
}
const { chromium } = loadPlaywright();

const BASE = process.env.ERP_URL || "http://127.0.0.1:8021";
const OUT = process.env.SHOTS_DIR || "/tmp/erp-shots";
const VIEWPORTS = [
  { tag: "27", viewport: { width: 2560, height: 1440 }, hasTouch: false },
  // 12.7" tablet in landscape: ~1400 CSS px, touch → the app's (pointer: coarse) layout.
  { tag: "tablet", viewport: { width: 1400, height: 900 }, hasTouch: true, isMobile: false },
];
const LEAKS = [
  [/\b[a-z]+(?:[A-Z][a-z0-9]*)+\b/g, "camelCase identifier"],
  [/\b[A-Z]{2,}(?:_[A-Za-z0-9]+)+\b/g, "raw code with underscores"],
  [/БЕЗ КОДА|без кода/g, "«без кода» placeholder"],
  [/\b(?:undefined|null|NaN|\[object Object\])\b/g, "JS value in text"],
];

const targets = process.argv.slice(2).map((arg) => {
  const eq = arg.indexOf("=");
  if (eq > 0 && arg.slice(eq + 1).startsWith("?")) return { name: arg.slice(0, eq), query: arg.slice(eq + 1) };
  return { name: arg, query: `?section=${encodeURIComponent(arg)}` };
});
if (!targets.length) {
  console.error("usage: node scripts/demo/shoot.mjs <section|name=?query> ...");
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
let problems = 0;
for (const vp of VIEWPORTS) {
  const context = await browser.newContext({ viewport: vp.viewport, hasTouch: vp.hasTouch, isMobile: vp.isMobile, locale: "ru-RU" });
  const login = await context.request.post(`${BASE}/api/auth/login`, {
    data: { login: process.env.ERP_LOGIN || "preview", password: process.env.ERP_PASSWORD || "Preview-2026!" },
  });
  if (!login.ok()) throw new Error(`login failed: ${login.status()} ${await login.text()}`);
  const page = await context.newPage();
  for (const target of targets) {
    await page.goto(`${BASE}/${target.query}`, { waitUntil: "networkidle" });
    let loading = true;
    for (let i = 0; i < 20 && loading; i += 1) {
      loading = await page.getByText(/Загрузка…|Читаю|Загружаю/).first().isVisible().catch(() => false);
      if (loading) await page.waitForTimeout(500);
    }
    // The shell scrolls inside .workspace, so fullPage alone stops at the viewport: grow the viewport to
    // the workspace's height for the shot (the layout itself stays the real one), then restore it.
    const { sideways, tall } = await page.evaluate(() => {
      const ws = document.querySelector(".workspace");
      return ws
        ? { sideways: ws.scrollWidth - ws.clientWidth, tall: ws.scrollHeight - ws.clientHeight }
        : { sideways: 0, tall: 0 };
    });
    if (tall > 0) await page.setViewportSize({ width: vp.viewport.width, height: Math.min(vp.viewport.height + tall, 12000) });
    const file = path.join(OUT, `${target.name}-${vp.tag}.png`);
    await page.screenshot({ path: file, fullPage: true });
    if (tall > 0) await page.setViewportSize(vp.viewport);
    const text = await page.locator("body").innerText();
    const found = [];
    if (loading) found.push("still loading after 10 s");
    for (const [re, label] of LEAKS) {
      const hits = [...new Set(text.match(re) || [])].slice(0, 8);
      if (hits.length) found.push(`${label}: ${hits.join(", ")}`);
    }
    if (sideways > 1) found.push(`page scrolls sideways by ${sideways}px`);
    problems += found.length;
    console.log(`${file}${found.length ? `\n  ! ${found.join("\n  ! ")}` : "  ok"}`);
  }
  await context.close();
}
await browser.close();
console.log(problems ? `${problems} text/layout finding(s) — fix or justify each` : "no text/layout findings (still look at the PNGs)");
