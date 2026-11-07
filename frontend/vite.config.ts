import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: import.meta.dirname,
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:18766",
      },
    },
  },
  preview: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:18766",
      },
    },
  },
  build: {
    outDir: "../dist/frontend",
    emptyOutDir: true,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "echarts",
              test: /node_modules[\\/](echarts|zrender)[\\/]/,
            },
          ],
        },
      },
    },
  },
});
