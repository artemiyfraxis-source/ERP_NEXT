#!/usr/bin/env node
// «Обход всех разделов» на 16" (1536×864): грузит каждый раздел и каждую его вкладку и ищет то, что не помещается:
// горизонтальный скролл страницы, элементы за правым краем, кнопки в две строки, таблицы шире своей рамки без прокрутки.
//   ERP_URL=http://127.0.0.1:8033 [WIDTH=1536] [SHOTS_DIR=…] node scripts/demo/audit-fit.mjs [section …]
// Код выхода 1, если что-то найдено. Скриншоты — <раздел>[-вкладка]-<ширина>.png.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { execSync } from "node:child_process";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try { return require("playwright"); } catch { /* global install */ }
  return require(path.join(execSync("npm root -g").toString().trim(), "playwright"));
}
const { chromium } = loadPlaywright();
const BASE = process.env.ERP_URL || "http://127.0.0.1:8021";
const OUT = process.env.SHOTS_DIR || "/tmp/erp-fit";
const WIDTH = Number(process.env.WIDTH || 1536);
const HEIGHT = WIDTH >= 2000 ? 1440 : WIDTH >= 1500 ? 864 : 900;
const SECTIONS = ["cabinet", "projects", "techcards", "references", "tctemplates", "production", "myops", "quality",
  "newrequest", "myrequests", "requests", "procurement", "receiving", "stock", "issues", "handout", "shipping",
  "prodplan", "staff", "access", "notifications", "operations", "card"];
const wanted = process.argv.slice(2);
const sections = wanted.length ? wanted : SECTIONS;
mkdirSync(OUT, { recursive: true });

/** Runs in the page: returns a list of fit problems. */
function probe() {
  const out = [];
  const vw = document.documentElement.clientWidth;
  const ws = document.querySelector(".workspace");
  if (ws && ws.scrollWidth - ws.clientWidth > 1) out.push(`страница прокручивается вбок на ${ws.scrollWidth - ws.clientWidth}px`);
  const sb = document.querySelector(".sidebar");
  if (sb && sb.scrollHeight - sb.clientHeight > 1) out.push(`меню обрезано: прокрутка ${sb.scrollHeight - sb.clientHeight}px`);
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (p.classList.contains("workspace")) break;
      if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true;
    }
    return false;
  };
  const label = (el) => `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""} «${(el.innerText || "").trim().slice(0, 30).replace(/\s+/g, " ")}»`;
  const seen = new Set();
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(el).visibility === "hidden") continue;
    if (r.right > vw + 1 && !clipped(el) && !el.closest(".sidebar")) {
      const k = label(el);
      if (!seen.has(k) && out.length < 12) { seen.add(k); out.push(`за правым краем (+${Math.round(r.right - vw)}px): ${k}`); }
    }
    if (el.tagName === "BUTTON" && /(primary|secondary|small|danger)/.test(el.className) && el.innerText.trim() && !el.closest("table") && !el.closest(".sidebar")) {
      const rg = document.createRange();
      rg.selectNodeContents(el);
      const tops = new Set([...rg.getClientRects()].map((q) => Math.round(q.top / 4)));
      if (tops.size > 1) { const k = label(el); if (!seen.has(k) && out.length < 20) { seen.add(k); out.push(`кнопка в две строки: ${k}`); } }
    }
  }
  for (const t of document.querySelectorAll("table")) {
    const wrap = t.parentElement;
    if (t.offsetWidth > wrap.clientWidth + 1 && !/(auto|scroll)/.test(getComputedStyle(wrap).overflowX) && !clipped(t)) out.push(`таблица шире рамки без прокрутки: ${t.offsetWidth} > ${wrap.clientWidth}`);
  }
  for (const d of document.querySelectorAll(".plan-drawer, [role=dialog], .modal, .dialog")) {
    const r = d.getBoundingClientRect();
    if (r.width && (r.right > vw + 1 || r.bottom > innerHeight + 1)) out.push(`панель/диалог не помещается: ${label(d)} ${Math.round(r.right)}×${Math.round(r.bottom)}`);
  }
  return out;
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, locale: "ru-RU" });
const login = await context.request.post(`${BASE}/api/auth/login`, {
  data: { login: process.env.ERP_LOGIN || "preview", password: process.env.ERP_PASSWORD || "Preview-2026!" },
});
if (!login.ok()) throw new Error(`login failed ${login.status()}`);
const page = await context.newPage();
let bad = 0;
async function settle() {
  await page.waitForLoadState("networkidle").catch(() => {});
  for (let i = 0; i < 20; i += 1) {
    if (!(await page.getByText(/Загрузка…|Читаю|Загружаю/).first().isVisible().catch(() => false))) break;
    await page.waitForTimeout(400);
  }
}
async function report(name) {
  const found = await page.evaluate(probe);
  const { tall } = await page.evaluate(() => { const w = document.querySelector(".workspace"); return { tall: w ? w.scrollHeight - w.clientHeight : 0 }; });
  if (tall > 0) await page.setViewportSize({ width: WIDTH, height: Math.min(HEIGHT + tall, 6000) });
  await page.screenshot({ path: path.join(OUT, `${name}-${WIDTH}.png`), fullPage: true });
  if (tall > 0) await page.setViewportSize({ width: WIDTH, height: HEIGHT });
  bad += found.length;
  console.log(`${name}${found.length ? "" : "  ok"}${found.map((f) => `\n  ! ${f}`).join("")}`);
}
for (const s of sections) {
  await page.goto(`${BASE}/?section=${s}`, { waitUntil: "networkidle" });
  await settle();
  await report(s);
  // Вкладки: группы из ≥2 кнопок, одна из которых выбрана (class on/active/selected или aria-selected/aria-pressed).
  const marked = await page.evaluate(() => {
    const isOn = (b) => b.getAttribute("aria-selected") === "true" || b.getAttribute("aria-pressed") === "true" || /(on|active|selected)/.test(b.className);
    const groups = [];
    document.querySelectorAll(".workspace *").forEach((g) => {
      const kids = [...g.children].filter((c) => c.tagName === "BUTTON");
      if (kids.length >= 2 && kids.length === g.children.length && kids.some(isOn) && !g.closest("table") && !g.closest(".column-menu")) {
        groups.push(kids.map((k, i) => ({ i, on: isOn(k), t: k.innerText.trim().slice(0, 24) })));
      }
    });
    return groups.slice(0, 6);
  });
  let gi = 0;
  for (const group of marked) {
    const picked = [];
    for (const k of group.filter((x) => !x.on).slice(0, 8)) {
      picked.push(k);
      await page.goto(`${BASE}/?section=${s}`, { waitUntil: "networkidle" });
      await settle();
      const btn = page.locator(".workspace button", { hasText: k.t }).first();
      if (!(await btn.isVisible().catch(() => false))) continue;
      await btn.click().catch(() => {});
      await settle();
      await report(`${s}-g${gi}-${k.t.replace(/[^\p{L}\p{N}]+/gu, "_")}`);
    }
    gi += 1;
  }
}
// Диалоги и боковые панели: [раздел, текст кнопки, имя снимка].
const DIALOGS = [
  ["stock", "Внести позиции", "stock-add"], ["stock", "Переместить", "stock-move"], ["stock", "Лист перемещения", "stock-sheet"],
  ["receiving", "Принять поставку", "receiving-accept"], ["receiving", "Возврат поставщику", "receiving-return"],
  ["production", "Запустить задание", "production-launch"], ["production", "Автоплан", "production-autoplan"],
  ["production", "Календарь цеха", "production-calendar"], ["production", "Компетенции", "production-skills"],
  ["access", "Исключения по сотрудникам", "access-exceptions"], ["access", "Токены API", "access-tokens"],
  ["staff", "Добавить", "staff-add"], ["newrequest", "Из Excel", "newrequest-excel"], ["shipping", "Создать", "shipping-create"],
];
if (!wanted.length) {
  for (const [s, text, name] of DIALOGS) {
    await page.goto(`${BASE}/?section=${s}`, { waitUntil: "networkidle" });
    await settle();
    const btn = page.locator(".workspace button, .workspace a", { hasText: text }).first();
    if (!(await btn.isVisible().catch(() => false))) { console.log(`${name}  (кнопки «${text}» нет)`); continue; }
    await btn.click().catch(() => {});
    await settle();
    await report(name);
  }
}
await browser.close();
console.log(bad ? `${bad} находок` : "всё помещается");
process.exit(bad ? 1 : 0);
