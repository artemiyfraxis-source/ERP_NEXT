import { createReadStream, existsSync, statSync } from "node:fs";
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, isAbsolute, join, normalize, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { recoveredRoot } from "./scripts/sibling-paths.mjs";

const allowedScopes = new Set(["projects", "calculation"]);
const sectionByScope = { projects: "projects", calculation: "calc" };
const maxBootstrapTickets = 1_024;
const maxTicketsPerPrincipal = 8;
const legacyResponseHeaderAllowlist = new Set([
  "accept-ranges",
  "content-disposition",
  "content-encoding",
  "content-range",
  "content-type",
  "etag",
  "last-modified",
  "retry-after",
  "www-authenticate",
]);
const routeRules = [
  ["auth", /^(POST) \/api\/auth\/(login|login-pin)$/],
  ["auth", /^(GET) \/api\/auth\/me$/],
  ["projects", /^(GET|POST) \/api\/projects$/],
  ["projects", /^(POST) \/api\/projects\/(extract|import)$/],
  ["projects", /^(GET|POST) \/api\/projects\/customers$/],
  ["projects", /^(PATCH) \/api\/projects\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)$/],
  ["projects", /^(POST) \/api\/projects\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/status$/],
  ["projects", /^(GET) \/api\/projects\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/requests$/],
  ["calculation", /^(GET) \/api\/calc\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)$/],
  ["calculation", /^(POST) \/api\/calc\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/import$/],
  ["calculation", /^(GET) \/api\/calc\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/(materials|techcards|history|qty-sheet)$/],
  ["calculation", /^(POST) \/api\/calc\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/assemblies$/],
  ["calculation", /^(PATCH|DELETE) \/api\/calc\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/assemblies\/[^/]+$/],
  ["calculation", /^(POST) \/api\/calc\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/assemblies\/[^/]+\/(move|generate-route)$/],
  ["calculation", /^(PUT) \/api\/calc\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*)\/assemblies\/[^/]+\/(bom|steps)$/],
  ["calculation", /^(GET|POST) \/api\/calc\/(operations|material-types)$/],
  ["calculation", /^(GET) \/api\/calc\/(nomenclature|equipment\/list|params\/list)$/],
  ["calculation", /^(POST) \/api\/calc\/(equipment|equipment\/sync|params)$/],
  ["calculation", /^(PUT) \/api\/calc\/(operations|equipment)\/[^/]+$/],
  ["calculation", /^(PUT|DELETE) \/api\/calc\/material-types\/[^/]+$/],
  ["calculation", /^(PUT) \/api\/calc\/material-types\/[^/]+\/priority$/],
  ["calculation", /^(GET|POST) \/api\/customers$/],
  ["calculation", /^(PATCH) \/api\/customers\/[^/]+$/],
  ["calculation", /^(POST) \/api\/customers\/[^/]+\/(hide|show)$/],
  ["calculation", /^(POST) \/api\/docs$/],
  ["calculation", /^(GET) \/api\/docs\/(?:terms|project\/(?:\d+[Кк](?:\.\d+)?|\d+\.[Кк]\d*))$/],
  ["calculation", /^(PATCH|DELETE) \/api\/docs\/[^/]+$/],
  ["calculation", /^(PUT) \/api\/docs\/[^/]+\/lines$/],
  ["calculation", /^(GET) \/api\/docs\/[^/]+\/export$/],
];

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

export function legacyRoute(rawUrl, method = "GET") {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(rawUrl, "http://bridge").pathname);
  } catch {
    return null;
  }
  const normalizedMethod = method.toUpperCase();
  const request = `${normalizedMethod} ${pathname}`;
  const scope = routeRules.find(([, pattern]) => pattern.test(request))?.[0];
  if (!scope) return null;
  return {
    scope,
    minimumMode: scope === "auth" || normalizedMethod === "GET" ? "view" : "edit",
  };
}

export function legacyRouteScope(rawUrl, method = "GET") {
  return legacyRoute(rawUrl, method)?.scope ?? null;
}

export function isAllowedLegacyApi(rawUrl, method = "GET") {
  return legacyRouteScope(rawUrl, method) !== null;
}

export function verifyLegacyGrant(token, secret, now = Math.floor(Date.now() / 1000)) {
  if (typeof secret !== "string" || Buffer.byteLength(secret) < 32) return null;
  const [encoded, supplied, extra] = String(token || "").split(".");
  if (!encoded || !supplied || extra) return null;
  try {
    const expected = createHmac("sha256", secret).update(encoded).digest();
    const signature = Buffer.from(supplied, "base64url");
    if (signature.length !== expected.length || !timingSafeEqual(signature, expected)) return null;
    const claims = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    if (
      claims.aud !== "erp-legacy-bridge" ||
      !allowedScopes.has(claims.scope) ||
      !["view", "edit"].includes(claims.mode) ||
      typeof claims.sub !== "string" ||
      !Number.isInteger(claims.iat) ||
      !Number.isInteger(claims.exp) ||
      claims.iat > now + 30 ||
      claims.exp <= now ||
      claims.exp - claims.iat > 180
    ) return null;
    return claims;
  } catch {
    return null;
  }
}

export function authorizeLegacyRequest(
  token,
  secret,
  rawUrl,
  method,
  now = Math.floor(Date.now() / 1000),
) {
  const route = legacyRoute(rawUrl, method);
  if (!route) return { ok: false, status: 403, detail: "Legacy module is outside the ERP Next allowlist" };
  const claims = verifyLegacyGrant(token, secret, now);
  if (!claims) return { ok: false, status: 401, detail: "Native ERP grant is missing or expired" };
  if (route.scope !== "auth" && route.scope !== claims.scope) {
    return { ok: false, status: 403, detail: "Native ERP grant does not include this module" };
  }
  if (route.minimumMode === "edit" && claims.mode !== "edit") {
    return { ok: false, status: 403, detail: "Native ERP grant is read-only" };
  }
  return { ok: true, claims };
}

export function filterNavigation(payload, scope) {
  if (!payload || typeof payload !== "object") return payload;
  const section = sectionByScope[scope];
  const filtered = { ...payload };
  if (Array.isArray(payload.nav)) {
    filtered.nav = payload.nav.filter((item) => item?.code === section);
  }
  if (Array.isArray(payload.blocks)) {
    filtered.blocks = payload.blocks
      .map((block) => ({
        ...block,
        items: Array.isArray(block?.items)
          ? block.items.filter((item) => item?.code === section)
          : [],
      }))
      .filter((block) => block.items.length > 0);
  }
  return filtered;
}

function bridgeGuardScript(parentOrigin) {
  return `(() => {
    const allowedParentOrigin = ${JSON.stringify(parentOrigin)};
    let grant = window.__ERP_LEGACY_GRANT__ || "";
    delete window.__ERP_LEGACY_GRANT__;
    addEventListener("message", (event) => {
      if (
        event.source === parent &&
        event.origin === allowedParentOrigin &&
        event.data?.type === "erp:legacy-grant" &&
        typeof event.data.grant === "string"
      ) grant = event.data.grant;
    });
    let scope = "";
    try {
      const encoded = grant.split(".")[0];
      const padded = encoded + "=".repeat((4 - encoded.length % 4) % 4);
      scope = JSON.parse(atob(padded.replaceAll("-", "+").replaceAll("_", "/"))).scope;
    } catch {}
    const section = scope === "calculation" ? "calc" : "projects";
    const label = scope === "calculation" ? "Расчёт и КП" : "Проекты";
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
      const target = new URL(typeof input === "string" ? input : input.url, location.href);
      if (target.origin === location.origin && target.pathname.startsWith("/api/")) {
        const headers = new Headers(init.headers);
        headers.set("X-ERP-Legacy-Grant", grant);
        return originalFetch(input, { ...init, headers });
      }
      return originalFetch(input, init);
    };
    const safeUrl = (value) => {
      const url = new URL(value ?? location.href, location.href);
      url.searchParams.delete("grant");
      url.hash = "";
      const requested = url.searchParams.get("section");
      if (requested !== section) {
        url.searchParams.set("section", section);
        url.searchParams.delete("project");
        url.searchParams.delete("tab");
      }
      return url;
    };
    const originalPush = history.pushState.bind(history);
    const originalReplace = history.replaceState.bind(history);
    history.pushState = (state, unused, url) => originalPush(state, unused, safeUrl(url));
    history.replaceState = (state, unused, url) => originalReplace(state, unused, safeUrl(url));
    const clean = () => {
      for (const item of document.querySelectorAll(".nav-item")) {
        if (item.textContent.trim() !== label) item.remove();
      }
      for (const group of document.querySelectorAll(".nav-group")) {
        if (!group.querySelector(".nav-item")) group.remove();
      }
    };
    originalReplace(history.state, "", safeUrl(location.href));
    addEventListener("popstate", () => {
      const safe = safeUrl(location.href);
      if (safe.href !== location.href) originalReplace(history.state, "", safe);
    });
    new MutationObserver(clean).observe(document.documentElement, { childList: true, subtree: true });
    addEventListener("DOMContentLoaded", clean);
  })();`;
}

function sendJson(res, status, value, extraHeaders = {}) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "cache-control": "no-store",
    "content-length": Buffer.byteLength(body),
    "content-type": "application/json; charset=utf-8",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    ...extraHeaders,
  });
  res.end(body);
}

export function filterLegacyResponseHeaders(headers) {
  const filtered = {
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
  };
  for (const [name, value] of Object.entries(headers)) {
    if (legacyResponseHeaderAllowlist.has(name.toLowerCase()) && value !== undefined) {
      filtered[name.toLowerCase()] = value;
    }
  }
  return filtered;
}

function proxyApi(req, res, upstream, scope) {
  const requester = upstream.protocol === "https:" ? httpsRequest : httpRequest;
  const headers = { host: upstream.host, origin: upstream.origin, referer: `${upstream.origin}/` };
  for (const name of ["accept", "authorization", "content-length", "content-type", "user-agent"]) {
    if (req.headers[name] !== undefined) headers[name] = req.headers[name];
  }
  const proxy = requester(
    {
      protocol: upstream.protocol,
      hostname: upstream.hostname,
      port: upstream.port,
      method: req.method,
      path: req.url,
      headers,
    },
    (upstreamResponse) => {
      const status = upstreamResponse.statusCode || 502;
      const responseHeaders = filterLegacyResponseHeaders(upstreamResponse.headers);
      if (new URL(req.url || "/", "http://bridge").pathname !== "/api/auth/me") {
        res.writeHead(status, responseHeaders);
        upstreamResponse.pipe(res);
        return;
      }
      const chunks = [];
      upstreamResponse.on("data", (chunk) => chunks.push(chunk));
      upstreamResponse.on("end", () => {
        try {
          const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          delete responseHeaders["content-length"];
          const body = JSON.stringify(filterNavigation(payload, scope));
          responseHeaders["content-length"] = Buffer.byteLength(body);
          res.writeHead(status, responseHeaders);
          res.end(body);
        } catch {
          sendJson(res, 502, { detail: "Legacy auth response is not valid JSON" });
        }
      });
    },
  );
  proxy.on("error", (error) => {
    sendJson(res, 502, { detail: `Legacy upstream unavailable: ${error.message}` });
  });
  req.pipe(proxy);
}

async function serveIndex(res, indexPath, parentOrigin, grant) {
  const html = await readFile(indexPath, "utf8");
  const bootstrap = `<script>window.__ERP_LEGACY_GRANT__=${JSON.stringify(grant)};const u=new URL(location.href);u.searchParams.delete("ticket");history.replaceState(history.state,"",u);</script>`;
  const guarded = html.replace(
    '<script type="module"',
    `${bootstrap}<script src="/_erp_bridge/guard.js"></script><script type="module"`,
  );
  const scriptHashes = [...guarded.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
    .map((match) => `'sha256-${createHash("sha256").update(match[1]).digest("base64")}'`);
  res.writeHead(200, {
    "cache-control": "no-store",
    "content-security-policy": [
      "default-src 'self'",
      "base-uri 'self'",
      "object-src 'none'",
      "form-action 'self'",
      `frame-ancestors ${parentOrigin}`,
      "connect-src 'self'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      `script-src 'self' ${scriptHashes.join(" ")}`,
      "style-src 'self' 'unsafe-inline'",
    ].join("; "),
    "content-type": contentTypes[".html"],
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
  });
  res.end(guarded);
}

export function createLegacyBridge({
  publicDir,
  upstream,
  secret,
  parentOrigin = "http://127.0.0.1:8021",
}) {
  const indexPath = join(publicDir, "index.html");
  const bootstrapTickets = new Map();
  const ticketsByPrincipal = new Map();
  const deleteBootstrap = (ticket) => {
    const bootstrap = bootstrapTickets.get(ticket);
    if (!bootstrap) return;
    bootstrapTickets.delete(ticket);
    const principalTickets = ticketsByPrincipal.get(bootstrap.principal);
    principalTickets?.delete(ticket);
    if (principalTickets?.size === 0) ticketsByPrincipal.delete(bootstrap.principal);
  };
  const evictOldest = () => {
    const ticket = bootstrapTickets.keys().next().value;
    if (ticket) deleteBootstrap(ticket);
  };
  const discardExpired = (now) => {
    while (bootstrapTickets.size > 0) {
      const [ticket, bootstrap] = bootstrapTickets.entries().next().value;
      if (bootstrap.expiresAt > now) break;
      deleteBootstrap(ticket);
    }
  };
  const consumeBootstrap = (requestUrl) => {
    const ticket = requestUrl.searchParams.get("ticket");
    const bootstrap = bootstrapTickets.get(ticket);
    if (ticket) deleteBootstrap(ticket);
    const requestedScope = requestUrl.searchParams.get("section") === "calc"
      ? "calculation"
      : "projects";
    if (
      !bootstrap ||
      bootstrap.expiresAt <= Date.now() ||
      bootstrap.scope !== requestedScope ||
      !verifyLegacyGrant(bootstrap.grant, secret)
    ) return null;
    return bootstrap.grant;
  };
  return createServer(async (req, res) => {
    const requestUrl = new URL(req.url || "/", "http://bridge");
    if (requestUrl.pathname === "/_erp_bridge/health") {
      sendJson(res, 200, { ok: true, modules: ["projects", "calculation"] });
      return;
    }
    if (requestUrl.pathname === "/_erp_bridge/exchange") {
      const origin = req.headers.origin;
      const corsHeaders = {
        "access-control-allow-headers": "X-ERP-Legacy-Grant",
        "access-control-allow-methods": "POST, OPTIONS",
        "access-control-allow-origin": parentOrigin,
        vary: "Origin",
      };
      if (origin !== parentOrigin) {
        sendJson(res, 403, { detail: "Bootstrap origin is not allowed" });
        return;
      }
      if (req.method === "OPTIONS") {
        res.writeHead(204, corsHeaders);
        res.end();
        return;
      }
      if (req.method !== "POST") {
        sendJson(res, 405, { detail: "Method not allowed" }, corsHeaders);
        return;
      }
      const grant = req.headers["x-erp-legacy-grant"];
      const claims = verifyLegacyGrant(grant, secret);
      if (!claims) {
        sendJson(res, 401, { detail: "Native ERP grant is missing or expired" }, corsHeaders);
        return;
      }
      const now = Date.now();
      discardExpired(now);
      const principal = JSON.stringify([claims.sub, claims.scope]);
      let principalTickets = ticketsByPrincipal.get(principal);
      if (!principalTickets) {
        principalTickets = new Set();
        ticketsByPrincipal.set(principal, principalTickets);
      }
      while (principalTickets.size >= maxTicketsPerPrincipal) {
        deleteBootstrap(principalTickets.values().next().value);
      }
      while (bootstrapTickets.size >= maxBootstrapTickets) evictOldest();
      if (!ticketsByPrincipal.has(principal)) {
        principalTickets = new Set();
        ticketsByPrincipal.set(principal, principalTickets);
      }
      const ticket = randomBytes(24).toString("base64url");
      bootstrapTickets.set(ticket, {
        grant,
        principal,
        scope: claims.scope,
        subject: claims.sub,
        expiresAt: now + 30_000,
      });
      principalTickets.add(ticket);
      sendJson(res, 200, { ticket }, corsHeaders);
      return;
    }
    if (requestUrl.pathname === "/_erp_bridge/guard.js") {
      res.writeHead(200, {
        "cache-control": "no-store",
        "content-type": contentTypes[".js"],
        "referrer-policy": "no-referrer",
        "x-content-type-options": "nosniff",
      });
      res.end(bridgeGuardScript(parentOrigin));
      return;
    }
    if (requestUrl.pathname.startsWith("/api/")) {
      const authorization = authorizeLegacyRequest(
        req.headers["x-erp-legacy-grant"],
        secret,
        req.url,
        req.method,
      );
      if (!authorization.ok) {
        sendJson(res, authorization.status, { detail: authorization.detail });
        return;
      }
      proxyApi(req, res, upstream, authorization.claims.scope);
      return;
    }

    let decodedPath;
    try {
      decodedPath = decodeURIComponent(requestUrl.pathname);
    } catch {
      sendJson(res, 400, { detail: "Malformed URL path" });
      return;
    }
    const relativePath = normalize(decodedPath).replace(/^[/\\]+/, "");
    const candidate = join(publicDir, relativePath || "index.html");
    const candidateRelative = relative(publicDir, candidate);
    const inside = !candidateRelative.startsWith("..") && !isAbsolute(candidateRelative);
    if (inside && existsSync(candidate) && statSync(candidate).isFile()) {
      if (candidate === indexPath) {
        const grant = consumeBootstrap(requestUrl);
        if (!grant) {
          sendJson(res, 401, { detail: "Bootstrap ticket is missing, expired, or out of scope" });
          return;
        }
        await serveIndex(res, indexPath, parentOrigin, grant);
      } else {
        res.writeHead(200, {
          "cache-control": "no-store",
          "content-type": contentTypes[extname(candidate)] || "application/octet-stream",
          "referrer-policy": "no-referrer",
          "x-content-type-options": "nosniff",
        });
        createReadStream(candidate).pipe(res);
      }
      return;
    }
    if (!existsSync(indexPath)) {
      sendJson(res, 503, { detail: "Legacy public snapshot not found" });
      return;
    }
    const grant = consumeBootstrap(requestUrl);
    if (!grant) {
      sendJson(res, 401, { detail: "Bootstrap ticket is missing, expired, or out of scope" });
      return;
    }
    await serveIndex(res, indexPath, parentOrigin, grant);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const port = Number(process.env.ERP_LEGACY_BRIDGE_PORT || 5177);
  const publicDir = resolve(
    process.env.ERP_LEGACY_PUBLIC_DIR ||
      join(recoveredRoot, "public"),
  );
  const upstream = new URL(process.env.ERP_LEGACY_UPSTREAM || "http://127.0.0.1:5175");
  const secret = process.env.ERP_LEGACY_BRIDGE_SECRET || "";
  if (Buffer.byteLength(secret) < 32) {
    throw new Error("Set ERP_LEGACY_BRIDGE_SECRET to at least 32 bytes");
  }
  const configuredParent = new URL(
    process.env.ERP_NEXT_ORIGIN || "http://127.0.0.1:8021",
  );
  if (
    !["http:", "https:"].includes(configuredParent.protocol) ||
    configuredParent.username ||
    configuredParent.password
  ) throw new Error("ERP_NEXT_ORIGIN must be an HTTP(S) origin without credentials");
  const server = createLegacyBridge({
    publicDir,
    upstream,
    secret,
    parentOrigin: configuredParent.origin,
  });
  server.listen(port, "127.0.0.1", () => {
    console.log(`ERP legacy bridge: http://localhost:${port}`);
  });
}
