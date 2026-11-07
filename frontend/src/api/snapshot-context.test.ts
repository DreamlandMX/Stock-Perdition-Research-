import { beforeEach, expect, test, vi } from "vitest";
import { loadSnapshotContextFromApi } from "./snapshot-context";

type RequestOptions = {
  params: { query: { symbols: string[]; snapshot_id?: string } };
};
const api = vi.hoisted(() => ({
  get: vi.fn<(path: string, options: RequestOptions) => Promise<unknown>>(),
}));
vi.mock("./client", () => ({ createResearchApiClient: () => api }));
beforeEach(() => vi.clearAllMocks());

test("metadata over 64 symbols is batched and pinned to one saved snapshot", async () => {
  const symbols = Array.from({ length: 130 }, (_, i) => `US.S${i}`);
  api.get.mockImplementation((_path, { params }) =>
    Promise.resolve({
      data: {
        resolved_snapshot: { snapshot_id: "saved", as_of: "2026-10-30T20:00:00Z" },
        data: {
          symbol_count: 204,
          instruments: params.query.symbols.map((symbol: string) => ({ symbol })),
        },
      },
    }),
  );
  const result = await loadSnapshotContextFromApi({ symbols });
  expect(api.get).toHaveBeenCalledTimes(3);
  expect(api.get.mock.calls.map((call) => call[1].params.query.symbols.length)).toEqual(
    [64, 64, 2],
  );
  expect(api.get.mock.calls[1]?.[1].params.query.snapshot_id).toBe("saved");
  expect(result.data.instruments.map((i) => i.symbol)).toEqual(symbols);
});

test("metadata rejects a changed snapshot instead of mixing batches", async () => {
  api.get.mockResolvedValueOnce({
    data: { resolved_snapshot: { snapshot_id: "a" }, data: { instruments: [] } },
  });
  api.get.mockResolvedValueOnce({
    data: { resolved_snapshot: { snapshot_id: "b" }, data: { instruments: [] } },
  });
  await expect(
    loadSnapshotContextFromApi({
      symbols: Array.from({ length: 65 }, (_, i) => `US.S${i}`),
    }),
  ).rejects.toThrow("快照");
});
