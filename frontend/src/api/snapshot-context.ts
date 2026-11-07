import { queryOptions } from "@tanstack/react-query";
import { createResearchApiClient } from "./client";
import type { paths } from "./generated/schema";

type Query = NonNullable<
  paths["/api/v1/snapshot-context"]["get"]["parameters"]["query"]
>;
const client = createResearchApiClient({ trackConnectivity: false });

export async function loadSnapshotContextFromApi(query: Query, signal?: AbortSignal) {
  const symbols = [...new Set(query.symbols ?? [])];
  const { data } = await client.get("/api/v1/snapshot-context", {
    params: { query: { ...query, symbols: symbols.slice(0, 64) } },
    signal: signal ?? null,
  });
  if (!data) throw new Error("快照上下文不可用");
  const instruments = [...data.data.instruments];
  for (let offset = 64; offset < symbols.length; offset += 64) {
    const { data: batch } = await client.get("/api/v1/snapshot-context", {
      params: {
        query: {
          ...query,
          snapshot_id: data.resolved_snapshot.snapshot_id,
          as_of: data.resolved_snapshot.as_of,
          symbols: symbols.slice(offset, offset + 64),
        },
      },
      signal: signal ?? null,
    });
    if (
      !batch ||
      JSON.stringify(batch.resolved_snapshot) !== JSON.stringify(data.resolved_snapshot)
    )
      throw new Error("股票元数据快照不一致，请重新读取");
    instruments.push(...batch.data.instruments);
  }
  return { ...data, data: { ...data.data, instruments } };
}

export function snapshotContextOptions(query: Query = {}) {
  const normalized = Object.fromEntries(
    Object.entries({
      ...query,
      symbols: [...new Set(query.symbols ?? [])].sort(),
    })
      .filter(([, value]) => value != null)
      .sort(([a], [b]) => a.localeCompare(b)),
  );
  return queryOptions({
    queryKey: ["snapshot-context", normalized],
    queryFn: ({ signal }) => loadSnapshotContextFromApi(normalized, signal),
    retry: false,
    staleTime: 30_000,
  });
}
