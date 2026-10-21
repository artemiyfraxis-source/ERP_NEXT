import { defineConfig, type Plugin } from "vitest/config";
import legacy from "@vitejs/plugin-legacy";
import react from "@vitejs/plugin-react";

// Цех в Москве: даты и время в тестах — по Москве, иначе CI (UTC) показывает другое время, чем экран у людей.
declare const process: { env: Record<string, string | undefined> };
process.env.TZ = "Europe/Moscow";

const RESOLVE_CHECK = 'if(!import.meta.resolve)throw Error("import.meta.resolve not supported")';

/** The ERP's CSP is `script-src 'self'`: no inline scripts, no data: modules. @vitejs/plugin-legacy needs both — four
 *  inline loader scripts in index.html and a `data:` module at the top of every modern chunk that throws where
 *  `import.meta.resolve` is missing (Chrome 70 on the ТСД), so that such a browser runs the legacy bundle instead. The
 *  same behaviour without either: the loaders become small files next to the bundle, the data: module becomes the same
 *  check as the chunk's first statement. A plugin update that changes its inline code fails the build here. */
function legacyWithoutInlineScripts(): Plugin {
  const detect = `import.meta.url;import("_").catch(()=>1);(async function*(){})().next();if(import.meta.resolve)window.__vite_is_modern_browser=true`;
  return {
    name: "erp:legacy-without-inline-scripts",
    enforce: "post",
    apply: "build",
    generateBundle(_options, bundle) {
      // `import'data:text/javascript,"<chunk>";<check>';` → `<check>;` (the chunk name only kept the data: URLs unique).
      const head = "import'data:text/javascript,";
      for (const file of Object.values(bundle)) {
        if (file.type !== "chunk" || !file.code.startsWith(head)) continue;
        const end = file.code.indexOf(`${RESOLVE_CHECK}';`);
        const between = end < 0 ? "" : file.code.slice(head.length, end);
        if (end < 0 || !(between === "" || (between.startsWith('"') && between.endsWith('";') && !between.includes("'")))) {
          this.error(`${file.fileName}: незнакомая проверка plugin-legacy — сверить с CSP`);
        }
        file.code = `${RESOLVE_CHECK};${file.code.slice(end + RESOLVE_CHECK.length + 2)}`;
      }
      const page = bundle["index.html"];
      if (!page || page.type !== "asset" || typeof page.source !== "string") return;
      const emit = (name: string, source: string) =>
        `/${this.getFileName(this.emitFile({ type: "asset", name, source }))}`;
      const html = page.source.replace(/<script([^>]*)>([^<]+)<\/script>/g, (_tag, attributes: string, code: string) => {
        if (code.startsWith("import'data:text/javascript,") && code.includes("window.__vite_is_modern_browser=true")) {
          return `<script type="module" src="${emit("legacy-detect.js", detect)}"></script>`;
        }
        if (code.startsWith("!function(){if(window.__vite_is_modern_browser)return;")) {
          return `<script type="module" src="${emit("legacy-fallback.js", code)}"></script>`;
        }
        if (code.startsWith('!function(){var e=document,t=e.createElement("script");if(!("noModule"in t)')) {
          return `<script nomodule src="${emit("legacy-safari-nomodule.js", code)}"></script>`;
        }
        if (attributes.includes('id="vite-legacy-entry"') && code.startsWith("System.import(")) {
          return `<script${attributes} src="${emit("legacy-entry.js", code)}"></script>`;
        }
        this.error(`index.html: встроенный скрипт не пройдёт CSP (script-src 'self'): ${code.slice(0, 80)}`);
      });
      page.source = html;
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    // ТСД склада (AUTOID / SEUIC, Android 8.1) — Chrome 70: the default target (2023+ browsers) gave it a white screen in
    // Roller_Buyer. Same settings as there: browsers without import.meta.resolve (Chrome 70 among them) run the legacy
    // SystemJS bundle with polyfills by usage; current browsers keep the modern one.
    legacy({ targets: ["chrome >= 49", "android >= 5"], modernTargets: ["chrome >= 64", "android >= 64"], modernPolyfills: true }),
    legacyWithoutInlineScripts(),
  ],
  build: { cssTarget: "chrome64" },
  server: {
    host: "127.0.0.1",
    port: 5176,
    proxy: {
      "/api": "http://127.0.0.1:8021",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    // Тяжёлые экраны (сетки на 100+ строк, производство) в полном параллельном прогоне не укладываются в 5 с.
    testTimeout: 15_000,
  },
});
