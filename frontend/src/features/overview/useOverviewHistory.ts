import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { HISTORY_BATCH_SIZE, loadOverviewHistoryFromApi } from "../../api/overview";
import type { components } from "../../api/generated/schema";
import { normalizeHistoryBars, type DailyHistories } from "./overview-history";

type Snapshot = components["schemas"]["ResolvedSnapshot"];
interface HistoryState {
  key: string;
  histories: DailyHistories;
  failed: Record<string, string>;
  completed: number;
}

export function useOverviewHistory(
  snapshot: Snapshot | undefined,
  symbols: readonly string[],
) {
  const queryClient = useQueryClient();
  const key = JSON.stringify([snapshot, [...new Set(symbols)]]);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<HistoryState>({
    key: "",
    histories: {},
    failed: {},
    completed: 0,
  });
  useEffect(() => {
    if (!snapshot) return;
    let active = true,
      next = 0;
    const unique = [...new Set(symbols)];
    const buffer: HistoryState = { key, histories: {}, failed: {}, completed: 0 };
    setState({ ...buffer, histories: {}, failed: {} });
    let timer: ReturnType<typeof setTimeout> | undefined;
    const publish = () => {
      const flush = () => {
        timer = undefined;
        if (active)
          setState({
            ...buffer,
            histories: { ...buffer.histories },
            failed: { ...buffer.failed },
          });
      };
      if (buffer.completed === unique.length) {
        clearTimeout(timer);
        flush();
      } else if (timer === undefined) timer = setTimeout(flush, 500);
    };
    const worker = async () => {
      while (active && next < unique.length) {
        const symbol = unique[next++]!;
        try {
          const data = await queryClient.fetchQuery({
            queryKey: ["overview-history", snapshot, symbol],
            queryFn: ({ signal }) =>
              loadOverviewHistoryFromApi(snapshot, symbol, signal),
            staleTime: Infinity,
            retry: 1,
          });
          if (active) {
            buffer.completed++;
            buffer.histories[symbol] = normalizeHistoryBars(data.bars, snapshot.as_of);
            publish();
          }
        } catch (error) {
          if (active) {
            buffer.completed++;
            buffer.failed[symbol] =
              error instanceof Error ? error.message : "OVERVIEW_HISTORY_UNAVAILABLE";
            publish();
          }
        }
      }
    };
    void Promise.all(
      Array.from({ length: Math.min(HISTORY_BATCH_SIZE * 2, unique.length) }, worker),
    );
    // Completed requests may fill their own cache; old scopes cannot update this view.
    return () => {
      active = false;
      clearTimeout(timer);
    };
    // Snapshot identity and ordered symbols are serialized into key.
  }, [key, attempt, queryClient]);
  return useMemo(
    () => ({
      histories: state.key === key ? state.histories : {},
      failed: state.key === key ? state.failed : {},
      completed: state.key === key ? state.completed : 0,
      total: new Set(symbols).size,
      pending:
        snapshot !== undefined &&
        (state.key !== key || state.completed < new Set(symbols).size),
      retry: () => setAttempt((value) => value + 1),
    }),
    [state, key, snapshot, symbols],
  );
}
