import { expect, test as base } from "@playwright/test";

interface BrowserDiagnosticsFixtures {
  browserDiagnostics: void;
  expectedConsoleErrorIncludes: string[];
}

export const test = base.extend<BrowserDiagnosticsFixtures>({
  expectedConsoleErrorIncludes: [[], { option: true }],
  browserDiagnostics: [
    async ({ expectedConsoleErrorIncludes, page }, use) => {
      const diagnostics: string[] = [];
      const onConsole = (message: import("@playwright/test").ConsoleMessage) => {
        if (message.type() === "error" || message.type() === "warning") {
          const location = message.location();
          const source = location.url.length === 0 ? "unknown" : location.url;
          diagnostics.push(
            `console.${message.type()}: ${message.text()} @ ${source}:${location.lineNumber}`,
          );
        }
      };
      const onPageError = (error: Error) => {
        diagnostics.push(`pageerror: ${error.message}`);
      };
      page.on("console", onConsole);
      page.on("pageerror", onPageError);

      await use();

      await page.unrouteAll({ behavior: "ignoreErrors" });
      page.off("console", onConsole);
      page.off("pageerror", onPageError);
      const unexpected = diagnostics.filter(
        (diagnostic) =>
          !expectedConsoleErrorIncludes.some((expected) =>
            diagnostic.includes(expected),
          ),
      );
      const missingExpected = expectedConsoleErrorIncludes.filter(
        (expected) => !diagnostics.some((diagnostic) => diagnostic.includes(expected)),
      );
      expect(
        { missingExpected, unexpected },
        "browser console and page errors",
      ).toEqual({ missingExpected: [], unexpected: [] });
    },
    { auto: true },
  ],
});

export { expect };
