import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  authorizeLegacyRequest,
  createLegacyBridge,
  filterLegacyResponseHeaders,
  filterNavigation,
  isAllowedLegacyApi,
  legacyRoute,
  legacyRouteScope,
  verifyLegacyGrant,
} from "../legacy-bridge.mjs";

const secret = "test-legacy-bridge-secret-with-32-bytes";

function grant(scope, mode = "view", iat = 1_795_000_000) {
  const payload = Buffer.from(JSON.stringify({
    aud: "erp-legacy-bridge",
    exp: iat + 120,
    iat,
    mode,
    scope,
    sub: "10000000-0000-0000-0000-000000000001",
  })).toString("base64url");
  const signature = createHmac("sha256", secret).update(payload).digest("base64url");
  return `${payload}.${signature}`;
}

test("legacy bridge only permits auth, projects, and calculation APIs", () => {
  for (const [method, path] of [
    ["POST", "/api/auth/login"],
    ["GET", "/api/auth/me"],
    ["GET", "/api/projects?limit=20"],
    ["GET", "/api/projects/134%D0%9A.1/requests"],
    ["GET", "/api/calc/134%D0%9A.1"],
    ["GET", "/api/calc/591%D0%BA.3"],
    ["GET", "/api/docs/project/134%D0%9A.1"],
    ["GET", "/api/customers?q=test"],
  ]) {
    assert.equal(isAllowedLegacyApi(path, method), true, `${method} ${path}`);
  }
  for (const [method, path] of [
    ["GET", "/api/payments"],
    ["GET", "/api/tz"],
    ["POST", "/api/stock/batches"],
    ["GET", "/api/purchase"],
    ["GET", "/api/admin/users"],
    ["GET", "/api/projects-secret"],
    ["POST", "/api/auth/change-password"],
    ["DELETE", "/api/projects"],
    ["GET", "/api/calc/future-endpoint"],
  ]) {
    assert.equal(isAllowedLegacyApi(path, method), false, `${method} ${path}`);
  }
  assert.equal(legacyRouteScope("/api/projects", "GET"), "projects");
  assert.equal(legacyRouteScope("/api/calc/134%D0%9A.1", "GET"), "calculation");
  assert.equal(legacyRoute("/api/projects", "GET").minimumMode, "view");
  assert.equal(legacyRoute("/api/projects", "POST").minimumMode, "edit");
});

test("legacy navigation response contains only the granted module", () => {
  const filtered = filterNavigation({
    nav: [
      { code: "projects", name: "Проекты" },
      { code: "calc", name: "Расчёт и КП" },
      { code: "payments", name: "Платежи" },
    ],
    blocks: [
      { key: "work", items: [{ code: "projects" }, { code: "payments" }] },
      { key: "stock", items: [{ code: "stock" }] },
    ],
  }, "projects");

  assert.deepEqual(filtered.nav.map(({ code }) => code), ["projects"]);
  assert.deepEqual(filtered.blocks, [
    { key: "work", items: [{ code: "projects" }] },
  ]);
});

test("legacy grants are signed, scoped, short-lived, and fail closed", () => {
  const token = grant("projects");
  assert.equal(verifyLegacyGrant(token, secret, 1_795_000_001)?.scope, "projects");
  assert.equal(verifyLegacyGrant(token, secret, 1_795_000_001)?.mode, "view");
  assert.equal(verifyLegacyGrant(token, secret, 1_795_000_121), null);
  assert.equal(verifyLegacyGrant(`${token}x`, secret, 1_795_000_001), null);
  assert.equal(verifyLegacyGrant(grant("payments"), secret, 1_795_000_001), null);
  assert.equal(verifyLegacyGrant(grant("projects", "owner"), secret, 1_795_000_001), null);
  assert.equal(verifyLegacyGrant(token, "short", 1_795_000_001), null);
});

test("view grants cannot mutate and scopes cannot cross", () => {
  const now = 1_795_000_001;
  const view = grant("projects", "view");
  const edit = grant("projects", "edit");
  assert.equal(authorizeLegacyRequest(view, secret, "/api/projects", "GET", now).ok, true);
  assert.equal(authorizeLegacyRequest(view, secret, "/api/projects", "POST", now).status, 403);
  assert.equal(authorizeLegacyRequest(edit, secret, "/api/projects", "POST", now).ok, true);
  assert.equal(
    authorizeLegacyRequest(edit, secret, "/api/calc/134%D0%9A.1", "GET", now).status,
    403,
  );
});

test("legacy response headers cannot set cookies, CORS, redirects, or browser policy", () => {
  assert.deepEqual(filterLegacyResponseHeaders({
    "access-control-allow-origin": "*",
    "content-disposition": 'attachment; filename="quote.xlsx"',
    "content-type": "application/octet-stream",
    location: "https://attacker.test",
    "set-cookie": ["session=legacy"],
    "content-security-policy": "default-src *",
  }), {
    "cache-control": "no-store",
    "content-disposition": 'attachment; filename="quote.xlsx"',
    "content-type": "application/octet-stream",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
  });
});

test("bootstrap tickets are origin-bound, independent, and single-use", async (context) => {
  const publicDir = await mkdtemp(join(tmpdir(), "erp-legacy-bridge-"));
  await writeFile(
    join(publicDir, "index.html"),
    '<!doctype html><script type="module">window.ready=true</script>',
  );
  const server = createLegacyBridge({
    publicDir,
    upstream: new URL("http://127.0.0.1:1"),
    secret,
    parentOrigin: "http://127.0.0.1:8021",
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  context.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    await rm(publicDir, { recursive: true, force: true });
  });
  const address = server.address();
  assert.equal(typeof address, "object");
  const base = `http://127.0.0.1:${address.port}`;
  const token = grant("projects", "view", Math.floor(Date.now() / 1000));
  const exchange = (origin = "http://127.0.0.1:8021") => fetch(`${base}/_erp_bridge/exchange`, {
    method: "POST",
    headers: { Origin: origin, "X-ERP-Legacy-Grant": token },
  });

  assert.equal((await exchange("http://attacker.test")).status, 403);
  const guard = await (await fetch(`${base}/_erp_bridge/guard.js`)).text();
  assert.match(guard, /event\.origin === allowedParentOrigin/);
  assert.match(guard, /http:\/\/127\.0\.0\.1:8021/);
  const firstExchange = await exchange();
  const secondExchange = await exchange();
  assert.equal(firstExchange.status, 200);
  assert.equal(secondExchange.status, 200);
  const first = await firstExchange.json();
  const second = await secondExchange.json();
  assert.notEqual(first.ticket, second.ticket);

  const firstPage = await fetch(`${base}/login?section=projects&ticket=${first.ticket}`);
  const secondPage = await fetch(`${base}/login?section=projects&ticket=${second.ticket}`);
  assert.equal(firstPage.status, 200);
  assert.equal(secondPage.status, 200);
  assert.match(await firstPage.text(), /__ERP_LEGACY_GRANT__/);
  assert.equal((await fetch(`${base}/login?section=projects&ticket=${first.ticket}`)).status, 401);
  assert.equal((await fetch(`${base}/login?section=projects`)).status, 401);

  const boundedTickets = [];
  for (let index = 0; index < 10; index += 1) {
    const response = await exchange();
    boundedTickets.push((await response.json()).ticket);
  }
  assert.equal(
    (await fetch(`${base}/login?section=projects&ticket=${boundedTickets[0]}`)).status,
    401,
  );
  assert.equal(
    (await fetch(`${base}/login?section=projects&ticket=${boundedTickets[1]}`)).status,
    401,
  );
  for (const ticket of boundedTickets.slice(2)) {
    assert.equal((await fetch(`${base}/login?section=projects&ticket=${ticket}`)).status, 200);
  }
});
