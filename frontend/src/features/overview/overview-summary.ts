import { translate, type Locale } from "../../i18n/core";
import type { components } from "../../api/generated/schema";
import { getRegimeLabels } from "./RegimeDistribution";

type OverviewReadModel = components["schemas"]["OverviewReadModel"];

function percentage(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

export function buildOverviewFactSummary(
  data: OverviewReadModel,
  locale: Locale = "zh-CN",
) {
  let advancers = 0;
  let decliners = 0;
  let unchanged = 0;
  for (const proxy of data.proxies) {
    if (proxy.session_return > 0) advancers += 1;
    else if (proxy.session_return < 0) decliners += 1;
    else unchanged += 1;
  }

  const dominant = data.regime_distribution.reduce<
    OverviewReadModel["regime_distribution"][number] | null
  >(
    (current, item) =>
      current === null || item.fraction > current.fraction ? item : current,
    null,
  );
  const proxyClause =
    data.proxies.length === 0
      ? translate(locale, "overview.overviewSummary.noBroadMarketProxyPrices")
      : translate(locale, "overview.overviewSummary.marketProxiesUpDown", {
          advancers: advancers,
          decliners: decliners,
          value:
            unchanged > 0
              ? translate(locale, "overview.overviewSummary.unchanged", {
                  unchanged: unchanged,
                })
              : "",
        });
  const regimeClause =
    dominant === null
      ? translate(
          locale,
          "overview.overviewSummary.completeRegimeDistributionUnavailable",
        )
      : translate(locale, "overview.overviewSummary.hasTheLargestShare", {
          value: getRegimeLabels(locale)[dominant.regime],
          percentage: percentage(dominant.fraction),
        });

  return translate(locale, "overview.overviewSummary.marketBreadth", {
    proxyClause: proxyClause,
    regimeClause: regimeClause,
    percentage: percentage(data.market_breadth),
  });
}
