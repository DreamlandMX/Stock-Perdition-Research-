import { QueryClient } from "@tanstack/react-query";

import { attachPersistedViewStorage, restorePersistedViews } from "./persisted-view";

export function createAppQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        networkMode: "offlineFirst",
        refetchOnWindowFocus: false,
        retry: 1,
        staleTime: 30_000,
      },
    },
  });
}

export const appQueryClient = createAppQueryClient();
restorePersistedViews(appQueryClient);
attachPersistedViewStorage(appQueryClient);
