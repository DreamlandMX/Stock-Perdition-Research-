import { afterEach, expect, test, vi } from "vitest";
import { loadOverviewFromApi } from "./overview";

afterEach(() => vi.unstubAllGlobals());

test("shares identical pending reads but isolates snapshots and releases failures", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetch);
  const first = loadOverviewFromApi({ horizon: 5, mode: "research" });
  const firstFinish = finish;
  expect(loadOverviewFromApi({ mode: "research", horizon: 5 })).toBe(first);
  const historical = loadOverviewFromApi({
    horizon: 5,
    mode: "research",
    snapshot_id: "older",
  });
  expect(historical).not.toBe(first);
  expect(fetch).toHaveBeenCalledTimes(2);
  const failed = expect(first).rejects.toThrow();
  firstFinish(new Response("{}", { status: 503 }));
  finish(new Response("{}", { headers: { "content-type": "application/json" } }));
  await Promise.all([failed, historical]);
  const retry = loadOverviewFromApi({ horizon: 5, mode: "research" });
  expect(fetch).toHaveBeenCalledTimes(3);
  finish(new Response("{}", { headers: { "content-type": "application/json" } }));
  await retry;
});
