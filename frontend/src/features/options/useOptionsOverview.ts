import { useQuery } from "@tanstack/react-query";
import { createResearchApiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { snapshotContextOptions } from "../../api/snapshot-context";
import "./options-shared.css";

export type OptionOverviewItem = components["schemas"]["OptionOverviewItem"];
export type OptionOverviewResponse = components["schemas"]["OptionOverviewResponse"];
export type OptionOverviewQuery = {
  latest?: boolean | undefined;
  snapshot_id?: string | undefined;
  as_of?: string | undefined;
  symbol?: string | undefined;
  option_snapshot_id?: string | undefined;
};
const client = createResearchApiClient({ trackConnectivity: false });

export function useOptionCutoff(asOf?: string, snapshotId?: string) {
  const needsIdentity = Boolean(snapshotId && asOf);
  const latest = useQuery({
    ...snapshotContextOptions(),
    enabled: needsIdentity,
  });
  return {
    asOf:
      snapshotId && snapshotId === latest.data?.resolved_snapshot.snapshot_id
        ? undefined
        : asOf,
    ready: !needsIdentity || (latest.isSuccess && !latest.isFetching) || latest.isError,
  };
}

export function useOptionAsOf(asOf?: string, snapshotId?: string) {
  return useOptionCutoff(asOf, snapshotId).asOf;
}

export function useOptionsOverview(query: OptionOverviewQuery = {}, enabled = true) {
  const cutoff = useOptionCutoff(
    query.latest !== undefined ? undefined : query.as_of,
    query.latest !== undefined ? undefined : query.snapshot_id,
  );
  query = { ...query, as_of: query.latest === false ? query.as_of : cutoff.asOf };
  return useQuery({
    queryKey: ["options-overview", query],
    queryFn: async () => {
      const { data } = await client.get("/api/v1/options/overview", {
        params: {
          query: {
            ...(query.snapshot_id ? { snapshot_id: query.snapshot_id } : {}),
            ...(query.as_of ? { as_of: query.as_of } : {}),
            ...(query.symbol ? { symbol: query.symbol } : {}),
            ...(query.option_snapshot_id
              ? { option_snapshot_id: query.option_snapshot_id }
              : {}),
          },
        },
        signal: AbortSignal.timeout(query.symbol ? 15000 : 300000),
      });
      if (!data) throw new Error("OPTIONS_OVERVIEW_UNAVAILABLE");
      return data;
    },
    enabled: enabled && cutoff.ready,
    retry: false,
    staleTime: 60000,
  });
}

export function useOptionsCollection(
  query: { as_of?: string | undefined } = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["options-collection", query],
    queryFn: async () => {
      const { data } = await client.get("/api/v1/options/collection", {
        params: { query: query.as_of ? { as_of: query.as_of } : {} },
        signal: AbortSignal.timeout(45000),
      });
      if (!data) throw new Error("OPTIONS_COLLECTION_UNAVAILABLE");
      return data;
    },
    enabled,
    retry: false,
    staleTime: 60000,
  });
}

export function useOptionsExperiments(
  query: { as_of?: string | undefined; snapshot_id?: string | undefined } = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["options-experiments", query],
    queryFn: async () => {
      const { data } = await client.get("/api/v1/options/experiments", {
        params: {
          query: {
            ...(query.as_of ? { as_of: query.as_of } : {}),
            ...(query.snapshot_id ? { snapshot_id: query.snapshot_id } : {}),
          },
        },
        signal: AbortSignal.timeout(300000),
      });
      if (!data) throw new Error("OPTIONS_EXPERIMENTS_UNAVAILABLE");
      return data;
    },
    enabled,
    retry: false,
    staleTime: 60000,
  });
}
