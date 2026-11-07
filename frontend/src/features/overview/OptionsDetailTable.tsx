import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  LOOK_RANK,
  money,
  number,
  percent,
  points,
  signedPercent,
  termLabel,
  type OptionsBoardRow,
  type OptionsBoardRows,
} from "../options/options-board-rows";
import { buildMarketResearchHref, type MarketLinkState } from "./overview-terminal";

type Column = {
  key: string;
  label: string;
  value: (row: OptionsBoardRow) => number | string | null | undefined;
  render: (row: OptionsBoardRow) => ReactNode;
};
const target = (
  value: number | null,
  spot: number | null,
  tone: string,
  locale: Locale = "zh-CN",
) => (
  <>
    <span className={tone}>{number(value, 1, locale)}</span>{" "}
    <small>
      {value != null && spot ? signedPercent(value / spot - 1, undefined, locale) : "—"}
    </small>
  </>
);
const getColumns = (locale: Locale = "zh-CN"): Column[] => [
  {
    key: "s",
    label: translate(locale, "overview.OverviewPage.instrument"),
    value: (r) => r.s,
    render: (r) => <b>{r.s}</b>,
  },
  {
    key: "spot",
    label: translate(locale, "overview.OptionsMoneyTargets.spot"),
    value: (r) => r.spot,
    render: (r) => number(r.spot, undefined, locale),
  },
  {
    key: "look",
    label: translate(locale, "overview.OptionsOutlookTable.optionsOutlook"),
    value: (r) => LOOK_RANK[r.look.k],
    render: (r) => <span className={`ob-look ob-look--${r.look.k}`}>{r.look.t}</span>,
  },
  {
    key: "pu5",
    label: translate(locale, "overview.OptionsDetailTable.up5"),
    value: (r) => r.pu5,
    render: (r) => <span className="ob-up">{percent(r.pu5, undefined, locale)}</span>,
  },
  {
    key: "pd5",
    label: translate(locale, "overview.OptionsDetailTable.down5"),
    value: (r) => r.pd5,
    render: (r) => <span className="ob-down">{percent(r.pd5, undefined, locale)}</span>,
  },
  {
    key: "tail",
    label: translate(locale, "overview.OptionsDetailTable.downUpTail"),
    value: (r) => r.tail,
    render: (r) => (r.tail == null ? "—" : `${number(r.tail, undefined, locale)}×`),
  },
  {
    key: "iv",
    label: "ATM IV",
    value: (r) => r.iv,
    render: (r) => percent(r.iv, undefined, locale),
  },
  {
    key: "ivhv",
    label: translate(locale, "overview.OptionsDetailTable.iVRealized"),
    value: (r) => r.ivhv,
    render: (r) => points(r.ivhv, locale),
  },
  {
    key: "m5",
    label: translate(locale, "overview.OptionsDetailTable.5Day"),
    value: (r) => r.m5,
    render: (r) => (r.m5 == null ? "—" : `±${percent(r.m5, undefined, locale)}`),
  },
  {
    key: "m20",
    label: translate(locale, "overview.OptionsDetailTable.20Day"),
    value: (r) => r.m20,
    render: (r) => (r.m20 == null ? "—" : `±${percent(r.m20, undefined, locale)}`),
  },
  {
    key: "skew",
    label: translate(locale, "overview.OptionsDetailTable.skew"),
    value: (r) => r.skew,
    render: (r) => points(r.skew, locale),
  },
  {
    key: "term",
    label: translate(locale, "overview.OptionsDetailTable.term"),
    value: (r) => r.term,
    render: (r) => termLabel(r.term, locale),
  },
  {
    key: "prem",
    label: translate(locale, "overview.OptionsMarketTide.estimatedTurnover"),
    value: (r) => r.prem,
    render: (r) => money(r.prem, locale),
  },
  {
    key: "otmC",
    label: translate(locale, "overview.OptionsDetailTable.oTMCallShare"),
    value: (r) => r.otmC,
    render: (r) => <span className="ob-up">{percent(r.otmC, 0, locale)}</span>,
  },
  {
    key: "otmP",
    label: translate(locale, "overview.OptionsDetailTable.oTMPutShare"),
    value: (r) => r.otmP,
    render: (r) => <span className="ob-down">{percent(r.otmP, 0, locale)}</span>,
  },
  {
    key: "pc",
    label: "Put/Call",
    value: (r) => r.pc,
    render: (r) => number(r.pc, undefined, locale),
  },
  {
    key: "short7",
    label: translate(locale, "overview.OptionsDetailTable.07DayShare"),
    value: (r) => r.short7,
    render: (r) => percent(r.short7, 0, locale),
  },
  {
    key: "tgtC",
    label: translate(locale, "overview.OptionsDetailTable.premiumTarget"),
    value: (r) => r.tgtC,
    render: (r) => target(r.tgtC, r.spot, "ob-up", locale),
  },
  {
    key: "tgtP",
    label: translate(locale, "overview.OptionsDetailTable.protectionLevel"),
    value: (r) => r.tgtP,
    render: (r) => target(r.tgtP, r.spot, "ob-down", locale),
  },
];
export function OptionsDetailTable({
  rows,
  linkState,
}: {
  rows: OptionsBoardRows;
  linkState: MarketLinkState;
}) {
  const { locale } = useI18n();
  const [sort, setSort] = useState({ key: "iv", ascending: false });
  const navigate = useNavigate(),
    open = (symbol: string) =>
      void navigate(`${buildMarketResearchHref(symbol, linkState)}#options`);
  const column = getColumns(locale).find((c) => c.key === sort.key)!;
  const sorted = [...rows].sort((a, b) => {
    const left = column.value(a),
      right = column.value(b);
    if (left == null) return right == null ? a.symbol.localeCompare(b.symbol) : 1;
    if (right == null) return -1;
    const difference =
      typeof left === "number" && typeof right === "number"
        ? left - right
        : String(left).localeCompare(String(right));
    return (
      (sort.ascending ? difference : -difference) || a.symbol.localeCompare(b.symbol)
    );
  });
  return (
    <section className="ob-panel">
      <header>
        <div>
          <h3>{translate(locale, "overview.OptionsDetailTable.optionsDetailTable")}</h3>
          <p>
            {translate(
              locale,
              "overview.OptionsDetailTable.directionVolatilityPremiumClickHeadersToSortEnterOpens",
            )}
          </p>
        </div>
      </header>
      <div
        className="ob-scroll ob-detail-scroll"
        tabIndex={0}
        role="region"
        aria-label={translate(
          locale,
          "overview.OptionsDetailTable.optionsDetailTableScrollHorizontally",
        )}
      >
        <table className="ob-detail-table">
          <thead>
            <tr className="ob-table-groups">
              <th colSpan={2} />
              <th colSpan={4}>
                {translate(locale, "overview.OptionsDetailTable.direction1Month")}
              </th>
              <th colSpan={6}>
                {translate(locale, "overview.OptionsDetailTable.volatility")}
              </th>
              <th colSpan={7}>
                {translate(locale, "overview.OptionsDetailTable.premium")}
              </th>
            </tr>
            <tr>
              {getColumns(locale).map((c, i) => (
                <th
                  key={c.key}
                  className={[2, 6, 12].includes(i) ? "ob-group-start" : ""}
                  aria-sort={
                    sort.key === c.key
                      ? sort.ascending
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  <button
                    type="button"
                    onClick={() =>
                      setSort({
                        key: c.key,
                        ascending: sort.key === c.key ? !sort.ascending : false,
                      })
                    }
                  >
                    {c.label}
                    {sort.key === c.key ? (sort.ascending ? " ↑" : " ↓") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr
                key={row.symbol}
                tabIndex={0}
                onClick={() => open(row.symbol)}
                onKeyDown={(e) => e.key === "Enter" && open(row.symbol)}
              >
                {getColumns(locale).map((c, i) => (
                  <td
                    key={c.key}
                    className={[2, 6, 12].includes(i) ? "ob-group-start" : ""}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="ob-note">
        {translate(
          locale,
          "overview.OptionsDetailTable.probabilitiesAreRiskNeutralSkewUsesApproximately5OTM",
        )}
      </p>
    </section>
  );
}
