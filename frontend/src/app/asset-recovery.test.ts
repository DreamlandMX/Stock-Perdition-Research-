import { afterEach, expect, test, vi } from "vitest";
import { createMemoryRouter } from "react-router-dom";

import { installAssetRecovery } from "./asset-recovery";
const routers: ReturnType<typeof createMemoryRouter>[] = [];

afterEach(() => {
  routers.splice(0).forEach((router) => router.dispose());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.querySelector("script[data-recovery-test]")?.remove();
  sessionStorage.clear();
});

function setup(build = "a") {
  const events = new EventTarget();
  const reload = vi.fn();
  const replace = vi.fn();
  const router = createMemoryRouter([
    { path: "/", element: null },
    { path: "/research/:symbol", loader: () => new Promise(() => {}), element: null },
  ]);
  routers.push(router);
  const storage = sessionStorage;
  const script = document.createElement("script");
  script.type = "module";
  document.querySelector("script[data-recovery-test]")?.remove();
  script.src = `/assets/index-build-${build}.js`;
  script.dataset.recoveryTest = "true";
  document.head.append(script);
  vi.stubGlobal("window", {
    addEventListener: events.addEventListener.bind(events),
    sessionStorage: storage,
    location: { reload, replace },
  });
  installAssetRecovery(router);
  return { events, reload, replace, router };
}

test("reloads only once for the same entry build across document loads", () => {
  const { events, reload } = setup();
  events.dispatchEvent(new Event("vite:preloadError"));
  expect(reload).toHaveBeenCalledTimes(1);
  const nextDocument = setup();
  nextDocument.events.dispatchEvent(new Event("vite:preloadError"));
  expect(nextDocument.reload).not.toHaveBeenCalled();
});

test("allows a later build to recover without intercepting ordinary errors", () => {
  const { events, reload } = setup();
  events.dispatchEvent(new Event("error"));
  expect(reload).not.toHaveBeenCalled();
  events.dispatchEvent(new Event("vite:preloadError"));
  const nextDocument = setup("b");
  nextDocument.events.dispatchEvent(new Event("vite:preloadError"));
  expect(nextDocument.reload).toHaveBeenCalledTimes(1);
});

test("opens the pending instrument URL with its snapshot query and hash", () => {
  const { events, reload, replace, router } = setup();
  const target = "/research/US.LRCX?snapshot_id=pinned&horizon=20#evidence";
  void router.navigate(target);
  events.dispatchEvent(new Event("vite:preloadError"));
  expect(replace).toHaveBeenCalledExactlyOnceWith(target);
  expect(reload).not.toHaveBeenCalled();
});

test("leaves the error available for manual recovery if storage is blocked", () => {
  const { events, reload } = setup();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("blocked", "SecurityError");
  });
  const failure = new Event("vite:preloadError", { cancelable: true });
  events.dispatchEvent(failure);
  expect(reload).not.toHaveBeenCalled();
  expect(failure.defaultPrevented).toBe(false);
});
