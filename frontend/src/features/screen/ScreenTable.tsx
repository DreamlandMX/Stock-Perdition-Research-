import { assetLabel, sectorLabel } from "../../app/presentation";
import { instrumentName } from "../../i18n/instruments";
import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3 } from "lucide-react";
import { Link } from "react-router-dom";
import type { components } from "../../api/generated/schema";
import type { ScreenUrlState } from "../../app/url-state";
import { ConfidenceLabel } from "../../components/ConfidenceLabel";
import { ProbabilityValue } from "../../components/ProbabilityValue";
import { ReturnValue } from "../../components/ReturnValue";
import { regimeLabel } from "../../app/presentation";
import { displayDataTime, displaySymbol, screenResearchHref } from "./screen-url-state";
import {
  OptionColumnHeader,
  OptionColumnValue,
  type ScreenOptionColumnsProps,
} from "./ScreenOptionColumns";
import { OPTION_COLUMNS } from "./option-screen-state";
type ScreenRow = components["schemas"]["ScreenRow"];
type SortKey = ScreenUrlState["sort"]["key"];
interface ColumnPreferences {
  capitalGain: boolean;
  intervalWidth: boolean;
  modelVersion: boolean;
  similarCases: boolean;
  options: boolean;
}
const DEFAULT_PREFERENCES: ColumnPreferences = {
  capitalGain: false,
  intervalWidth: false,
  modelVersion: false,
  similarCases: false,
  options: false,
};
const PREFERENCE_KEY = "signal-lab.screen.columns.v1";
function readPreferences(): ColumnPreferences {
  try {
    if (typeof localStorage?.getItem !== "function") {
      return DEFAULT_PREFERENCES;
    }
    const stored = localStorage.getItem(PREFERENCE_KEY);
    if (stored === null) {
      return DEFAULT_PREFERENCES;
    }
    const parsed = JSON.parse(stored) as Partial<ColumnPreferences>;
    return {
      capitalGain: parsed.capitalGain === true,
      intervalWidth: parsed.intervalWidth === true,
      modelVersion: parsed.modelVersion === true,
      similarCases: parsed.similarCases === true,
      options: parsed.options === true,
    };
  } catch {
    return DEFAULT_PREFERENCES;
  }
}
function freshness(seconds: number, locale: Locale = "zh-CN") {
  if (seconds < 60) {
    return translate(locale, "screen.screentable.seconds", { seconds: seconds });
  }
  if (seconds < 3600) {
    return translate(locale, "screen.screentable.minutes", {
      Math: Math.floor(seconds / 60),
    });
  }
  return translate(locale, "screen.screentable.hours", {
    Math: Math.floor(seconds / 3600),
  });
}
function selectedQ10(row: ScreenRow, state: ScreenUrlState) {
  return state.returnBasis === "capital_gain"
    ? row.capital_gain_q10
    : row.executable_q10;
}
function selectedQ50(row: ScreenRow, state: ScreenUrlState) {
  return state.returnBasis === "capital_gain"
    ? row.capital_gain_q50
    : row.executable_q50;
}
const columnHelper = createColumnHelper<ScreenRow>();
export interface ScreenTableProps extends Partial<ScreenOptionColumnsProps> {
  onPageChange: (page: number) => void;
  onSortChange: (sort: ScreenUrlState["sort"]) => void;
  page: components["schemas"]["ScreenPage"];
  state: ScreenUrlState;
}
export function ScreenTable({
  onPageChange,
  onSortChange,
  page,
  state,
  optionItems = [],
  optionState = {
    onlyData: false,
    widthBelow: undefined,
    widthAbove: undefined,
    ivRvMinimum: undefined,
    sort: undefined,
    direction: "desc",
  },
  onOptionSort = () => {},
}: ScreenTableProps) {
  const { locale } = useI18n();
  const [preferences, setPreferences] = useState(readPreferences);
  useEffect(() => {
    try {
      if (typeof localStorage?.setItem === "function") {
        localStorage.setItem(PREFERENCE_KEY, JSON.stringify(preferences));
      }
    } catch {
      // Display preferences are non-critical and must never block research evidence.
    }
  }, [preferences]);
  const columns = useMemo(() => {
    const selectedBasisLabel =
      state.returnBasis === "capital_gain"
        ? translate(locale, "screen.screenfilters.capitalGain")
        : translate(locale, "screen.screenfilters.executable");
    const definitions = [
      columnHelper.display({
        cell: ({ row }) => (
          <div className="screen-symbol-cell">
            <Link to={screenResearchHref(row.original, state)}>
              {displaySymbol(row.original.symbol)}
            </Link>
            <span>{instrumentName(row.original.symbol, row.original.name, locale)}</span>
          </div>
        ),
        header: translate(locale, "screen.researchscreenresults.instrument"),
        id: "symbol",
      }),
      columnHelper.display({
        cell: ({ row }) => (
          <span>
            {assetLabel(row.original.asset_type, locale)} / {sectorLabel(row.original.sector, locale)}
          </span>
        ),
        header: translate(locale, "screen.screentable.assetSector"),
        id: "asset_sector",
      }),
      columnHelper.display({
        cell: ({ row }) => regimeLabel(row.original.regime, locale),
        header: translate(locale, "screen.researchscreenresults.status"),
        id: "regime",
      }),
      columnHelper.display({
        cell: ({ row }) => (
          <ProbabilityValue value={row.original.executable_up_probability ?? null} />
        ),
        header: translate(locale, "screen.screentable.executableUpProbability"),
        id: "executable_up_probability",
      }),
      columnHelper.display({
        cell: ({ row }) => (
          <ProbabilityValue value={row.original.breakout_up_probability ?? null} />
        ),
        header: translate(locale, "screen.researchscreenresults.upwardBreakout"),
        id: "breakout_up_probability",
      }),
      columnHelper.display({
        cell: ({ row }) => (
          <ProbabilityValue value={row.original.breakout_down_probability ?? null} />
        ),
        header: translate(locale, "screen.researchscreenresults.downwardBreakout"),
        id: "breakout_down_probability",
      }),
      columnHelper.display({
        cell: ({ row }) => <ReturnValue value={selectedQ50(row.original, state)} />,
        header: `${selectedBasisLabel} q50`,
        id: "selected_q50",
      }),
      columnHelper.display({
        cell: ({ row }) => <ReturnValue value={selectedQ10(row.original, state)} />,
        header: `${selectedBasisLabel} q10`,
        id: "selected_q10",
      }),
      columnHelper.display({
        cell: ({ row }) => <ConfidenceLabel level={row.original.confidence_level} />,
        header: translate(locale, "screen.mobilescreenlist.confidence"),
        id: "confidence_level",
      }),
      columnHelper.display({
        cell: ({ row }) => (
          <span className="screen-data-time">
            <time dateTime={row.original.as_of}>
              {displayDataTime(row.original.as_of, locale)}
            </time>
            <small>
              {translate(locale, "screen.screentable.previous.withValues", {
                freshness: freshness(row.original.data_freshness_seconds, locale),
              })}
            </small>
          </span>
        ),
        header: translate(locale, "screen.screentable.dataFreshness"),
        id: "data_freshness_seconds",
      }),
    ];
    if (preferences.capitalGain && state.returnBasis !== "capital_gain") {
      definitions.push(
        columnHelper.display({
          cell: ({ row }) => <ReturnValue value={row.original.capital_gain_q50} />,
          header: translate(locale, "screen.screentable.capitalGainQ50"),
          id: "capital_gain_q50",
        }),
        columnHelper.display({
          cell: ({ row }) => <ReturnValue value={row.original.capital_gain_q10} />,
          header: translate(locale, "screen.mobilescreenlist.capitalGainQ10"),
          id: "capital_gain_q10",
        }),
      );
    }
    if (preferences.intervalWidth) {
      definitions.push(
        columnHelper.display({
          cell: ({ row }) => (
            <ProbabilityValue value={row.original.interval_width ?? null} />
          ),
          header: translate(locale, "screen.researchscreenresults.intervalWidth"),
          id: "interval_width",
        }),
      );
    }
    if (preferences.similarCases) {
      definitions.push(
        columnHelper.display({
          cell: ({ row }) => row.original.similar_case_count,
          header: translate(locale, "screen.screentable.similarCases"),
          id: "similar_case_count",
        }),
      );
    }
    if (preferences.modelVersion) {
      definitions.push(
        columnHelper.display({
          cell: ({ row }) => row.original.model_version,
          header: translate(locale, "screen.screentable.modelVersion"),
          id: "model_version",
        }),
      );
    }
    if (preferences.options) {
      definitions.push(
        ...OPTION_COLUMNS.map((column) =>
          columnHelper.display({
            id: column[0],
            header: () => (
              <OptionColumnHeader
                column={column}
                state={optionState}
                onSort={onOptionSort}
              />
            ),
            cell: ({ row }) => (
              <OptionColumnValue
                item={optionItems.find((item) => item.symbol === row.original.symbol)}
                column={column[0]}
                available={optionItems.length > 0}
              />
            ),
          }),
        ),
      );
    }
    return definitions;
  }, [preferences, state, optionItems, optionState, onOptionSort, locale]);
  const table = useReactTable({
    columns,
    data: page.items,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    pageCount: Math.max(1, Math.ceil(page.total / page.page_size)),
    state: {
      pagination: { pageIndex: state.page.number - 1, pageSize: state.page.size },
    },
  });
  function sortKeyFor(columnId: string): SortKey | null {
    if (columnId === "selected_q50") {
      return state.returnBasis === "capital_gain"
        ? "capital_gain_q50"
        : "executable_q50";
    }
    if (
      columnId === "symbol" ||
      columnId === "executable_up_probability" ||
      columnId === "breakout_up_probability" ||
      columnId === "breakout_down_probability" ||
      columnId === "capital_gain_q50" ||
      columnId === "interval_width" ||
      columnId === "similar_case_count" ||
      columnId === "confidence_level" ||
      columnId === "data_freshness_seconds"
    ) {
      return columnId;
    }
    return null;
  }
  return (
    <section className="screen-results">
      <header className="screen-results__header">
        <div>
          <p>
            {translate(locale, "screen.screenpage.total.withValues", {
              total: page.total,
              page: page.page,
            })}
          </p>
        </div>
        <details className="screen-column-picker">
          <summary>
            <Columns3 aria-hidden="true" size={18} strokeWidth={1.5} />
            {translate(locale, "screen.researchscreenresults.visibleColumns")}
          </summary>
          <fieldset>
            <legend>
              {translate(locale, "screen.screentable.optionalEvidenceColumns")}
            </legend>
            {state.returnBasis === "capital_gain" ? (
              <p className="screen-column-picker__note">
                {translate(
                  locale,
                  "screen.screentable.capitalGainQ10Q50AreAlreadyShownForThe",
                )}
              </p>
            ) : (
              <label>
                <input
                  checked={preferences.capitalGain}
                  onChange={(event) =>
                    setPreferences((current) => ({
                      ...current,
                      capitalGain: event.target.checked,
                    }))
                  }
                  type="checkbox"
                />
                {translate(locale, "screen.screentable.capitalGainBasis")}
              </label>
            )}
            {(
              [
                [
                  "intervalWidth",
                  translate(locale, "screen.researchscreenresults.intervalWidth"),
                ],
                [
                  "similarCases",
                  translate(locale, "screen.screentable.similarCaseCount"),
                ],
                ["modelVersion", translate(locale, "screen.screentable.modelVersion")],
                [
                  "options",
                  translate(locale, "screen.researchscreenresults.options6Columns"),
                ],
              ] as const
            ).map(([key, label]) => (
              <label key={key}>
                <input
                  checked={preferences[key]}
                  onChange={(event) =>
                    setPreferences((current) => ({
                      ...current,
                      [key]: event.target.checked,
                    }))
                  }
                  type="checkbox"
                />
                {label}
              </label>
            ))}
          </fieldset>
        </details>
      </header>

      <div className="screen-table-scroll">
        <table
          aria-label={translate(locale, "screen.screentable.researchScreenerResults")}
          className="screen-table"
        >
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const sortKey = sortKeyFor(header.column.id);
                  const optionColumn = OPTION_COLUMNS.find(
                    ([key]) => key === header.column.id,
                  );
                  const active = optionColumn
                    ? optionState.sort === optionColumn[0]
                    : optionState.sort === undefined &&
                      sortKey !== null &&
                      state.sort.key === sortKey;
                  const ariaSort = active
                    ? (optionColumn ? optionState.direction : state.sort.direction) ===
                      "asc"
                      ? "ascending"
                      : "descending"
                    : "none";
                  const Icon = active
                    ? state.sort.direction === "asc"
                      ? ArrowUp
                      : ArrowDown
                    : ArrowUpDown;
                  return (
                    <th
                      aria-sort={
                        sortKey === null && !optionColumn ? undefined : ariaSort
                      }
                      className={
                        header.column.id === "symbol"
                          ? "screen-table__sticky"
                          : undefined
                      }
                      key={header.id}
                      scope="col"
                    >
                      {sortKey === null ? (
                        flexRender(header.column.columnDef.header, header.getContext())
                      ) : (
                        <button
                          aria-label={translate(locale, "screen.screentable.sort", {
                            String: String(header.column.columnDef.header),
                            translate: active
                              ? state.sort.direction === "asc"
                                ? translate(locale, "screen.screentable.ascending")
                                : translate(locale, "screen.screentable.descending")
                              : translate(locale, "screen.screentable.notEnabled"),
                          })}
                          onClick={() =>
                            onSortChange({
                              direction:
                                active && state.sort.direction === "desc"
                                  ? "asc"
                                  : "desc",
                              key: sortKey,
                            })
                          }
                          type="button"
                        >
                          {flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                          <Icon aria-hidden="true" size={15} strokeWidth={1.5} />
                        </button>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={`${row.original.symbol}-${row.original.horizon}`}>
                {row.getVisibleCells().map((cell) => (
                  <td
                    className={
                      cell.column.id === "symbol" ? "screen-table__sticky" : undefined
                    }
                    key={cell.id}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <nav
        aria-label={translate(
          locale,
          "screen.researchscreenresults.screenerPagination",
        )}
        className="screen-pagination"
      >
        <button
          disabled={page.page <= 1}
          onClick={() => onPageChange(page.page - 1)}
          type="button"
        >
          {translate(locale, "screen.researchscreenresults.previous")}
        </button>
        <span>
          {translate(locale, "screen.researchscreenresults.day.withValues3", {
            page: page.page,
            Math: Math.max(1, Math.ceil(page.total / page.page_size)),
          })}
        </span>
        <button
          disabled={page.page * page.page_size >= page.total}
          onClick={() => onPageChange(page.page + 1)}
          type="button"
        >
          {translate(locale, "screen.researchscreenresults.next2")}
        </button>
      </nav>
    </section>
  );
}
