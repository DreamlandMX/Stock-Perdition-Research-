import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { optionQuery } from "./options-state";
import { createResearchApiClient } from "../../api/client";
import { formatUsMarketTime } from "../../app/presentation";
import { useOptionCutoff } from "../options/useOptionsOverview";
const client = createResearchApiClient({ trackConnectivity: false });
export function ResearchOptionBadge({ symbol }: { symbol: string }) {
  const { locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const cutoff = useOptionCutoff(
    params.get("as_of") ?? undefined,
    params.get("snapshot_id") ?? undefined,
  );
  const selection = new URLSearchParams(params);
  if (!cutoff.asOf) selection.delete("as_of");
  const selected = optionQuery(selection);
  const query = {
    ...(selected.as_of ? { as_of: selected.as_of } : {}),
    ...(selected.snapshot_id ? { snapshot_id: selected.snapshot_id } : {}),
  };
  const result = useQuery({
    queryKey: ["options-header", symbol, query],
    queryFn: async () => {
      const { data } = await client.get("/api/v1/instruments/{symbol}/options/header", {
        params: { path: { symbol }, query },
        signal: AbortSignal.timeout(15000),
      });
      if (!data) throw new Error("Options snapshot response unavailable");
      return data;
    },
    enabled: cutoff.ready,
    retry: false,
    staleTime: 60000,
  });
  const snapshot = result.data?.data;
  const timestamp = snapshot?.market_as_of ?? snapshot?.captured_at;
  return (
    <button
      className="research-option-badge"
      type="button"
      onClick={() => {
        setParams(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("option_tab", "sources");
            return next;
          },
          { flushSync: true, preventScrollReset: true },
        );
        document.getElementById("options")?.scrollIntoView({ block: "start" });
      }}
    >
      {result.isPending
        ? translate(locale, "research.researchoptionbadge.loadingOptionsSnapshot")
        : result.isError
          ? translate(
              locale,
              "research.researchoptionbadge.unableToLoadOptionsSnapshot",
            )
          : snapshot?.snapshot_id
            ? translate(locale, "research.optionspanel.options", {
                translate: snapshot.market_as_of
                  ? translate(locale, "research.optionspanel.marketData")
                  : translate(locale, "research.optionspanel.collection"),
                marketAsOf: timestamp
                  ? formatUsMarketTime(timestamp, locale)
                  : translate(locale, "research.pricehistorychart.timeUnconfirmed"),
                translate2: snapshot.complete
                  ? translate(locale, "research.optionspanel.complete")
                  : translate(locale, "research.optionspanel.partialCoverage"),
              })
            : translate(locale, "research.optionanalyticspanel.noSavedOptionsSnapshot")}
    </button>
  );
}
