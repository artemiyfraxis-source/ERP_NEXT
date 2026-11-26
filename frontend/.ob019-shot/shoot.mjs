import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(execSync('npm root -g').toString().trim(), 'playwright'));
const phase = process.argv[2] || 'before';
const out = fileURLToPath(new URL('../../docs/screens/ob019-ux2-1123/', import.meta.url));
const browser = await chromium.launch({ headless: true });
const results = [];
for (const [tag, width, height] of [['27', 2560, 1440], ['16', 1536, 864], ['tablet', 1400, 900]]) {
  for (const state of tag === 'tablet' && phase === 'after' ? ['normal', 'offline', 'alarm', 'running', 'finished'] : ['normal']) {
    const context = await browser.newContext({ viewport: { width, height }, hasTouch: tag === 'tablet', locale: 'ru-RU' });
    // Local static assets only. fetch in the harness is replaced before the card renders.
    await context.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:5197/.ob019-shot/index.html?state=${state}`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Что режем' }).waitFor();
    await page.waitForTimeout(500);
    const findings = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      windowScroll: [...document.querySelectorAll('.page-window')].map(el => el.scrollWidth - el.clientWidth),
      text: document.body.innerText, posts: window.__mockPosts, size: [...document.querySelectorAll('.machine-controls button, .machine-pull-modes button')].map(el => ({ text: el.textContent, height: el.getBoundingClientRect().height })) }));
    const name = `${phase}-${state}-${tag}.png`;
    await page.screenshot({ path: path.join(out, name), fullPage: true });
    if (tag === 'tablet' && state === 'normal') {
      if (phase === 'after') {
        await page.getByRole('region', { name: 'Маркировка', exact: true }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(out, `${phase}-marking-tablet.png`) });
      }
      await page.locator('.page-window').evaluate(el => el.scrollTop = el.scrollHeight);
      await page.screenshot({ path: path.join(out, `${phase}-bottom-tablet.png`) });
      if (phase === 'after') {
        await page.getByText('Скорости', { exact: true }).click();
        await page.getByText('Скорости', { exact: true }).evaluate(el => el.scrollIntoView({ block: 'start' }));
        await page.screenshot({ path: path.join(out, `${phase}-speeds-tablet.png`) });
      }
    }
    if (phase === 'after' && state === 'finished') {
      await page.getByRole('button', { name: 'Новый тираж', exact: true }).click();
      await page.getByRole('dialog', { name: 'Новый тираж', exact: true }).waitFor();
      await page.screenshot({ path: path.join(out, `${phase}-new-run-confirm-tablet.png`) });
      if ((await page.evaluate(() => window.__mockPosts)).length) throw new Error('Opening a confirmation must not POST');
    }
    results.push({ name, errors, ...findings });
    console.log(name, JSON.stringify({ errors, scroll: findings.scroll, posts: findings.posts }));
    await context.close();
  }
}
fs.writeFileSync(path.join(out, `${phase}-checks.json`), JSON.stringify(results, null, 2));
await browser.close();

