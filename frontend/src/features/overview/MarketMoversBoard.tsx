import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { Link, useLocation } from "react-router-dom";

import { sectorLabel } from "../../app/presentation";
import { sparklinePath, historyDates } from "./overview-history";
import type { DailyHistories } from "./overview-history";
import {
  buildMarketResearchHref,
  formatReturn,
  marketReturnValue,
  symbolLabel,
  type MarketLinkState,
  type MarketPoint,
  type MarketReturnHorizon,
} from "./overview-terminal";

interface MoverList {
  key: string;
  note: string;
  points: MarketPoint[];
  title: string;
  tone: "up" | "down" | "accent";
  value: (point: MarketPoint) => string;
}

function top(
  points: readonly MarketPoint[],
  value: (point: MarketPoint) => number | null,
  direction: "asc" | "desc",
) {
  return points
    .filter((point) => value(point) !== null)
    .sort((left, right) => {
      const difference = (value(right) ?? 0) - (value(left) ?? 0);
      return (
        (direction === "desc" ? difference : -difference) ||
        left.symbol.localeCompare(right.symbol)
      );
    })
    .slice(0, 5);
}

export function MarketMoversBoard({
  histories,
  horizon,
  linkState,
  points,
}: {
  histories?: DailyHistories;
  horizon: MarketReturnHorizon;
  linkState: MarketLinkState;
  points: readonly MarketPoint[];
}) {
  const { locale } = useI18n();
  const location = useLocation();
  const returnOf = (point: MarketPoint) => marketReturnValue(point, horizon);
  const gainers = top(points, returnOf, "desc").filter(
    (point) => (returnOf(point) ?? 0) > 0,
  );
  const losers = top(points, returnOf, "asc").filter(
    (point) => (returnOf(point) ?? 0) < 0,
  );
  const lists: MoverList[] = [
    {
      key: "gainers",
      note: translate(locale, "overview.MarketMoversBoard.dayReturn", {
        horizon: horizon,
      }),
      points: gainers,
      title: translate(locale, "overview.MarketMoversBoard.top5Gainers"),
      tone: "up",
      value: (point) => formatReturn(returnOf(point), locale),
    },
    {
      key: "losers",
      note: translate(locale, "overview.MarketMoversBoard.dayReturn", {
        horizon: horizon,
      }),
      points: losers,
      title: translate(locale, "overview.MarketMoversBoard.top5Losers"),
      tone: "down",
      value: (point) => formatReturn(returnOf(point), locale),
    },
    {
      key: "volume",
      note: translate(locale, "overview.MarketMoversBoard.volumeZ20"),
      points: top(
        points,
        (point) => (point.volume_z_20 == null ? null : Math.abs(point.volume_z_20)),
        "desc",
      ),
      title: translate(locale, "overview.MarketMoversBoard.top5VolumeAnomalies"),
      tone: "accent",
      value: (point) =>
        point.volume_z_20 == null ? "—" : `Z ${point.volume_z_20.toFixed(2)}`,
    },
  ];
  return (
    <section
      aria-label={translate(locale, "overview.MarketMoversBoard.top5MarketFocus")}
      className="ov-movers"
    >
      {lists.map((list) => (
        <section
          aria-label={list.title}
          className={`ov-movers__list ov-movers__list--${list.tone}`}
          key={list.key}
        >
          <header>
            <h3>{list.title}</h3>
            <span>
              {list.note} {translate(locale, "overview.MarketMoversBoard.all")}{" "}
              {points.length}{" "}
              {translate(locale, "overview.SectorRegimeMatrix.instruments")}
            </span>
          </header>
          {list.points.length === 0 ? (
            <p className="ov-empty">
              {translate(
                locale,
                "overview.MarketMoversBoard.noComparableEvidenceCurrently",
              )}
            </p>
          ) : (
            <ol>
              {list.points.map((point, index) => (
                <li key={point.symbol}>
                  <span className="ov-movers__rank">{index + 1}</span>
                  <Link
                    state={{ returnTo: `${location.pathname}${location.search}` }}
                    to={buildMarketResearchHref(point.symbol, linkState)}
                  >
                    <strong>{symbolLabel(point.symbol)}</strong>
                    <small>{sectorLabel(point.sector, locale)}</small>
                  </Link>
                  {histories?.[point.symbol]?.length ? (
                    <svg
                      className="ov-mover-spark"
                      viewBox="0 0 64 20"
                      aria-label={translate(
                        locale,
                        "overview.OpportunityTable.last30DailyCloses",
                        { symbol: symbolLabel(point.symbol) },
                      )}
                    >
                      <path
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.4"
                        d={sparklinePath(
                          histories[point.symbol]!.slice(-30),
                          64,
                          20,
                          historyDates(histories),
                        )}
                      />
                    </svg>
                  ) : null}
                  <span className="ov-movers__value">{list.value(point)}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      ))}
    </section>
  );
}
