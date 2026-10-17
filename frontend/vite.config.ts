import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Цех в Москве: даты и время в тестах — по Москве, иначе CI (UTC) показывает другое время, чем экран у людей.
declare const process: { env: Record<string, string | undefined> };
process.env.TZ = "Europe/Moscow";

export default defineConfig({
  plugins: [react()],
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
