import { useQuery } from "@tanstack/react-query";
import { createResearchApiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { useOptionCutoff, type OptionOverviewQuery } from "./useOptionsOverview";

export type OptionBoardItem = components["schemas"]["OptionBoardItem"];
export type OptionBoardResponse = components["schemas"]["OptionBoardResponse"];
const client = createResearchApiClient({ trackConnectivity: false });

export function useOptionsBoard(query: OptionOverviewQuery = {}, enabled = true) {
  const cutoff = useOptionCutoff(
    query.latest !== undefined ? undefined : query.as_of,
    query.latest !== undefined ? undefined : query.snapshot_id,
  );
  const parameters = {
    ...query,
    as_of: query.latest === false ? query.as_of : cutoff.asOf,
  };
  return useQuery({
    queryKey: ["options-board", parameters],
    queryFn: async (): Promise<OptionBoardResponse> => {
      const { data } = await client.get("/api/v1/options/board", {
        params: {
          query: {
            ...(parameters.snapshot_id ? { snapshot_id: parameters.snapshot_id } : {}),
            ...(parameters.as_of ? { as_of: parameters.as_of } : {}),
            ...(parameters.symbol ? { symbol: parameters.symbol } : {}),
            ...(parameters.option_snapshot_id
              ? { option_snapshot_id: parameters.option_snapshot_id }
              : {}),
          },
        },
        signal: AbortSignal.timeout(parameters.symbol ? 15000 : 300000),
      });
      if (!data) throw new Error("OPTIONS_BOARD_UNAVAILABLE");
      return data as OptionBoardResponse;
    },
    enabled: enabled && cutoff.ready,
    retry: false,
    staleTime: 60000,
  });
}
