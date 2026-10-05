import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

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
