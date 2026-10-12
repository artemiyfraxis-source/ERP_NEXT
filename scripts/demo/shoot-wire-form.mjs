#!/usr/bin/env node
// Shots of «База данных → Монтажные провода» (a row opened into its form) and «Многожильные кабели» (grouped) at 27" and 16".
//   ERP_URL=http://127.0.0.1:8031 SHOTS_DIR=... node scripts/demo/shoot-wire-form.mjs
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = (() => { try { return require("playwright"); } catch { return require(path.join(execSync("npm root -g").toString().trim(), "playwright")); } })();
const BASE = process.env.ERP_URL || "http://127.0.0.1:8031";
const OUT = process.env.SHOTS_DIR || "/tmp/erp-shots";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
for (const vp of [{ tag: "27", width: 2560, height: 1440 }, { tag: "16", width: 1536, height: 864 }]) {
  const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, locale: "ru-RU" });
  await context.request.post(`${BASE}/api/auth/login`, { data: { login: "preview", password: "Preview-2026!" } });
  const page = await context.newPage();
  await page.goto(`${BASE}/?section=references`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Монтажные провода/ }).first().click();
  await page.waitForTimeout(800);
  const row = process.env.WIRE_ROW || "UL1061";
  await page.locator("tbody tr:not(.group-row)").filter({ hasText: row }).first().click();
  await page.waitForTimeout(600);
  await page.locator(".cable-editor").first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(OUT, `wire-form-${vp.tag}.png`), fullPage: false });
  await page.getByRole("button", { name: /Многожильные кабели/ }).first().click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, `cables-grouped-${vp.tag}.png`), fullPage: false });
  await context.close();
}
await browser.close();
console.log("ok", OUT);
