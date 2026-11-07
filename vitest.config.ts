import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    maxWorkers: 4,
    projects: [
      {
        test: {
          name: "node",
          environment: "node",
          include: ["tooling/typescript/**/*.test.ts"],
        },
      },
      {
        plugins: [react()],
        test: {
          name: "frontend",
          environment: "jsdom",
          include: ["frontend/src/**/*.test.{ts,tsx}"],
          setupFiles: ["./frontend/src/test/setup.ts"],
        },
      },
    ],
  },
});
