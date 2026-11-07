import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { SlidersHorizontal, X } from "lucide-react";
import type { components } from "../../api/generated/schema";
import type { ScreenUrlState } from "../../app/url-state";
import { assetLabel, sectorLabel } from "../../app/presentation";
import { resetScreenControls } from "./screen-url-state";
import type { ScreenOptionState } from "./option-screen-state";
type Horizon = ScreenUrlState["horizon"];
type PredictionBasis = ScreenUrlState["predictionBasis"];
type ReturnBasis = ScreenUrlState["returnBasis"];
type Regime = components["schemas"]["RegimeLabel"];
type Confidence = components["schemas"]["ScreenConfidenceFloor"];
type ServingStatus = components["schemas"]["ServingStatus"];
const REGIME_OPTIONS = (
  locale: Locale = "zh-CN",
): readonly {
  label: string;
  value: Regime;
}[] => [
  { label: translate(locale, "screen.screenfilters.rangeBound"), value: "range" },
  { label: translate(locale, "screen.screenfilters.uptrend"), value: "uptrend" },
  { label: translate(locale, "screen.screenfilters.downtrend"), value: "downtrend" },
  { label: translate(locale, "screen.screenfilters.transition"), value: "transition" },
  { label: translate(locale, "screen.screenfilters.invalidRegime"), value: "invalid" },
];
const DATA_STATUS_OPTIONS = (
  locale: Locale = "zh-CN",
): readonly {
  label: string;
  value: ServingStatus;
}[] => [
  { label: translate(locale, "screen.screenfilters.live"), value: "live" },
  { label: translate(locale, "screen.screenfilters.delayed"), value: "delayed" },
  { label: translate(locale, "screen.screenfilters.stale"), value: "stale" },
  { label: translate(locale, "screen.screenfilters.partial"), value: "partial" },
  { label: translate(locale, "screen.screenfilters.offline"), value: "offline" },
  {
    label: translate(locale, "screen.screenfilters.unavailable"),
    value: "unavailable",
  },
];
function optionalNumber(value: string) {
  return value === "" ? undefined : Number(value);
}
function optionValues(values: readonly string[], current: string | undefined) {
  return current === undefined || values.includes(current)
    ? values
    : [current, ...values];
}
export interface ScreenFiltersProps {
  assetTypes: readonly string[];
  mode: "desktop" | "mobile";
  onApply: (state: ScreenUrlState) => void;
  onClose?: () => void;
  sectors: readonly string[];
  state: ScreenUrlState;
  optionState?: ScreenOptionState;
  onOptionChange?: (state: ScreenOptionState) => void;
  onClearAll?: (state: ScreenUrlState) => void;
}
export function ScreenFilters({
  assetTypes,
  mode,
  onApply,
  onClose,
  sectors,
  state,
  optionState,
  onOptionChange,
  onClearAll,
}: ScreenFiltersProps) {
  const { locale } = useI18n();
  const [draft, setDraft] = useState(state);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (mode === "desktop") {
      setDraft(state);
      return;
    }
    setDraft((current) => ({
      ...state,
      filters: current.filters,
      horizon: current.horizon,
      page: current.page,
      predictionBasis: current.predictionBasis,
      returnBasis: current.returnBasis,
      sort: current.sort,
    }));
  }, [mode, state]);
  function commit(next: ScreenUrlState) {
    setDraft(next);
    if (mode === "desktop") {
      onApply(next);
    }
  }
  function withPageReset(next: ScreenUrlState): ScreenUrlState {
    return { ...next, page: { ...next.page, number: 1 }, warnings: [] };
  }
  function setTopLevel<K extends "horizon" | "predictionBasis" | "returnBasis">(
    key: K,
    value: ScreenUrlState[K],
  ) {
    commit(withPageReset({ ...draft, [key]: value }));
  }
  function setFilter<K extends keyof ScreenUrlState["filters"]>(
    key: K,
    value: ScreenUrlState["filters"][K],
  ) {
    commit(
      withPageReset({
        ...draft,
        filters: { ...draft.filters, [key]: value },
      }),
    );
  }
  const content = (
    <form
      className={`screen-filters screen-filters--${mode}`}
      onSubmit={(event) => {
        event.preventDefault();
        onApply(withPageReset(draft));
      }}
    >
      {mode === "mobile" ? (
        <header className="screen-filter-drawer__header">
          <h2>{translate(locale, "screen.screenfilters.filters")}</h2>
          <button
            aria-label={translate(locale, "screen.screenfilters.closeFilters")}
            onClick={onClose}
            type="button"
          >
            <X aria-hidden="true" size={20} strokeWidth={1.5} />
          </button>
        </header>
      ) : (
        <div className="screen-filters__title">
          <SlidersHorizontal aria-hidden="true" size={18} strokeWidth={1.5} />
          <span>{translate(locale, "screen.screenfilters.serverSideFilters")}</span>
        </div>
      )}

      <label>
        <span>{translate(locale, "screen.screenfilters.forecastHorizon")}</span>
        <select
          onChange={(event) =>
            setTopLevel("horizon", Number(event.target.value) as Horizon)
          }
          value={draft.horizon}
        >
          <option value="1">{translate(locale, "screen.screenfilters.1Day")}</option>
          <option value="5">{translate(locale, "screen.screenfilters.5Days")}</option>
          <option value="20">{translate(locale, "screen.screenfilters.20Days")}</option>
        </select>
      </label>
      {state.mode === "formal" ? (
        <>
          <label>
            <span>{translate(locale, "screen.screenfilters.predictionBasis")}</span>
            <select
              onChange={(event) =>
                setTopLevel("predictionBasis", event.target.value as PredictionBasis)
              }
              value={draft.predictionBasis}
            >
              <option value="executable">
                {translate(locale, "screen.screenfilters.executable")}
              </option>
              <option value="forecast">
                {translate(locale, "screen.screenfilters.modelForecast")}
              </option>
            </select>
          </label>
          <label>
            <span>{translate(locale, "screen.screenfilters.returnBasis")}</span>
            <select
              onChange={(event) =>
                setTopLevel("returnBasis", event.target.value as ReturnBasis)
              }
              value={draft.returnBasis}
            >
              <option value="executable">
                {translate(locale, "screen.screenfilters.executable")}
              </option>
              <option value="capital_gain">
                {translate(locale, "screen.screenfilters.capitalGain")}
              </option>
            </select>
          </label>
        </>
      ) : null}
      <label>
        <span>{translate(locale, "screen.screenfilters.marketRegime")}</span>
        <select
          onChange={(event) =>
            setFilter(
              "regime",
              event.target.value === "" ? undefined : (event.target.value as Regime),
            )
          }
          value={draft.filters.regime ?? ""}
        >
          <option value="">{translate(locale, "screen.screenfilters.all")}</option>
          {REGIME_OPTIONS(locale).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{translate(locale, "screen.screenfilters.sector")}</span>
        <select
          onChange={(event) => setFilter("sector", event.target.value || undefined)}
          value={draft.filters.sector ?? ""}
        >
          <option value="">{translate(locale, "screen.screenfilters.all")}</option>
          {optionValues(sectors, draft.filters.sector).map((value) => (
            <option key={value} value={value}>
              {sectorLabel(value, locale)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{translate(locale, "screen.screenfilters.assetType")}</span>
        <select
          onChange={(event) => setFilter("assetType", event.target.value || undefined)}
          value={draft.filters.assetType ?? ""}
        >
          <option value="">{translate(locale, "screen.screenfilters.all")}</option>
          {optionValues(assetTypes, draft.filters.assetType).map((value) => (
            <option key={value} value={value}>
              {assetLabel(value, locale)}
            </option>
          ))}
        </select>
      </label>
      {state.mode === "formal" ? (
        <>
          <label>
            <span>
              {translate(locale, "screen.screenfilters.minimumUpProbability")}
            </span>
            <input
              inputMode="decimal"
              max="1"
              min="0"
              onChange={(event) =>
                setFilter("minimumProbability", optionalNumber(event.target.value))
              }
              placeholder="0–1"
              step="0.01"
              type="number"
              value={draft.filters.minimumProbability ?? ""}
            />
          </label>
          <label>
            <span>{translate(locale, "screen.screenfilters.q10LowerBound")}</span>
            <input
              inputMode="decimal"
              max="1"
              min="-1"
              onChange={(event) =>
                setFilter("maximumQ10", optionalNumber(event.target.value))
              }
              placeholder="-1–1"
              step="0.01"
              type="number"
              value={draft.filters.maximumQ10 ?? ""}
            />
          </label>
          <label>
            <span>{translate(locale, "screen.screenfilters.minimumConfidence")}</span>
            <select
              onChange={(event) =>
                setFilter(
                  "confidence",
                  event.target.value === ""
                    ? undefined
                    : (event.target.value as Confidence),
                )
              }
              value={draft.filters.confidence ?? ""}
            >
              <option value="">{translate(locale, "screen.screenfilters.all")}</option>
              <option value="low">
                {translate(locale, "screen.screenfilters.lowOrHigher")}
              </option>
              <option value="medium">
                {translate(locale, "screen.screenfilters.mediumOrHigher")}
              </option>
              <option value="high">
                {translate(locale, "screen.screenfilters.highOnly")}
              </option>
            </select>
          </label>
        </>
      ) : null}
      <label>
        <span>{translate(locale, "screen.researchscreenresults.dataStatus")}</span>
        <select
          onChange={(event) =>
            setFilter(
              "dataStatus",
              event.target.value === ""
                ? undefined
                : (event.target.value as ServingStatus),
            )
          }
          value={draft.filters.dataStatus ?? ""}
        >
          <option value="">{translate(locale, "screen.screenfilters.all")}</option>
          {DATA_STATUS_OPTIONS(locale).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      {mode === "desktop" && optionState && onOptionChange && (
        <fieldset className="screen-option-filters">
          <legend>
            {translate(locale, "screen.screenfilters.optionsBatchDataFilters5DayWidth")}
          </legend>
          <label className="screen-option-checkbox">
            <input
              type="checkbox"
              checked={optionState.onlyData}
              onChange={(event) =>
                onOptionChange({ ...optionState, onlyData: event.target.checked })
              }
            />
            {translate(locale, "screen.screenfilters.optionsDataOnly")}
          </label>
          <label>
            <span>{translate(locale, "screen.screenfilters.widthRatioX")}</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={optionState.widthBelow ?? ""}
              placeholder={translate(locale, "screen.screenfilters.eG07")}
              onChange={(event) =>
                onOptionChange({
                  ...optionState,
                  widthBelow: optionalNumber(event.target.value),
                })
              }
            />
          </label>
          <span>{translate(locale, "screen.screenfilters.or")}</span>
          <label>
            <span>{translate(locale, "screen.screenfilters.widthRatioY")}</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={optionState.widthAbove ?? ""}
              placeholder={translate(locale, "screen.screenfilters.eG13")}
              onChange={(event) =>
                onOptionChange({
                  ...optionState,
                  widthAbove: optionalNumber(event.target.value),
                })
              }
            />
          </label>
          <label>
            <span>
              {translate(locale, "screen.screenfilters.ivRv20XPercentagePoints")}
            </span>
            <input
              type="number"
              step="0.1"
              value={optionState.ivRvMinimum ?? ""}
              placeholder={translate(locale, "screen.screenfilters.eG4")}
              onChange={(event) =>
                onOptionChange({
                  ...optionState,
                  ivRvMinimum: optionalNumber(event.target.value),
                })
              }
            />
          </label>
        </fieldset>
      )}
      <div className="screen-filters__actions">
        <small>
          {translate(
            locale,
            "screen.screenfilters.clearAllResetsFiltersSortingAndWindowsTheResearch",
          )}
        </small>
        <button
          onClick={() => {
            const reset = resetScreenControls(state);
            setDraft(reset);
            (onClearAll ?? onApply)(reset);
          }}
          type="button"
        >
          {translate(locale, "screen.screenfilters.clearAll")}
        </button>
        {mode === "mobile" ? (
          <button type="submit">
            {translate(locale, "screen.screenfilters.applyFilters")}
          </button>
        ) : null}
      </div>
    </form>
  );
  if (mode !== "mobile") {
    return content;
  }
  return createPortal(
    <div
      aria-label={translate(locale, "screen.screenfilters.filters")}
      aria-modal="true"
      className="screen-filter-drawer"
      onKeyDown={(event) => {
        if (event.key !== "Tab") {
          return;
        }
        const focusable = Array.from(
          dialogRef.current?.querySelectorAll<HTMLElement>(
            "button:not([disabled]), select:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
          ) ?? [],
        );
        const first = focusable[0];
        const last = focusable.at(-1);
        if (first === undefined || last === undefined) {
          return;
        }
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
      ref={dialogRef}
      role="dialog"
    >
      <div className="screen-filter-drawer__surface">{content}</div>
    </div>,
    document.body,
  );
}
