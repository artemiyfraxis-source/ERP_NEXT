#!/usr/bin/env node
// Shots of «ОТК → Замеры КСВН» and of the printed «Паспорт кабельной сборки» on the running demo.
//   ERP_URL=http://127.0.0.1:8064 SHOTS_DIR=docs/screens/coax-vswr-1022 node scripts/demo/shoot-vswr.mjs
import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try { return require("playwright"); } catch { /* global install */ }
  return require(path.join(execSync("npm root -g").toString().trim(), "playwright"));
}
const { chromium } = loadPlaywright();
const BASE = process.env.ERP_URL || "http://127.0.0.1:8021";
const OUT = process.env.SHOTS_DIR || "/tmp/erp-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
for (const vp of [{ tag: "16", width: 1536, height: 864 }, { tag: "27", width: 2560, height: 1440 }]) {
  const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, locale: "ru-RU" });
  const login = await context.request.post(`${BASE}/api/auth/login`, { data: { login: "preview", password: "Preview-2026!" } });
  if (!login.ok()) throw new Error(`login ${login.status()}`);
  const page = await context.newPage();
  await page.goto(`${BASE}/?section=quality`, { waitUntil: "networkidle" });
  const select = page.getByLabel("Задание");
  await select.selectOption({ label: "ПЗ-0001 · Жгут питания стойки · 100 шт" });
  await page.getByText(/Норма ≤/).waitFor();
  await page.waitForTimeout(400);
  const panel = page.locator(".vswr-section");
  await panel.screenshot({ path: path.join(OUT, `vswr-section-${vp.tag}.png`) });
  if (vp.tag === "16") {
    // Окно загрузки: форма, затем результат.
    await page.getByRole("button", { name: "Загрузить замер" }).first().click();
    await page.getByRole("dialog").waitFor();
    await page.screenshot({ path: path.join(OUT, "vswr-upload-form-16.png") });
    await page.getByLabel("Файл замера").setInputFiles(path.resolve("backend/tests/production/fixtures/arinst_vna_dl.s1p"));
    await page.getByRole("dialog").getByRole("button", { name: "Загрузить замер" }).click();
    await page.getByRole("dialog").getByRole("img").waitFor();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, "vswr-upload-result-16.png") });
  }
  await context.close();
}

// Паспорт: JSON с сервера; HTML из него строит frontend/src/coaxPassportPrint.ts (см. render-passport в README шагов) и
// снимает лист A4 тем же chromium.
const context = await browser.newContext({ viewport: { width: 794, height: 1123 }, locale: "ru-RU" });
const login = await context.request.post(`${BASE}/api/auth/login`, { data: { login: "preview", password: "Preview-2026!" } });
if (!login.ok()) throw new Error(`login ${login.status()}`);
const page = await context.newPage();
await page.goto(`${BASE}/?section=quality`, { waitUntil: "networkidle" });
const html = await page.evaluate(async () => {
  const tasks = (await (await fetch("/api/production/tasks")).json()).tasks;
  const task = tasks.find((item) => item.number.endsWith("0001"));
  const passport = await (await fetch(`/api/production/vswr/tasks/${task.id}/serials/3/passport`)).json();
  return { passport };
});
writeFileSync(path.join(OUT, "passport-3.json"), JSON.stringify(html.passport));
await browser.close();
console.log("shots in", OUT);
