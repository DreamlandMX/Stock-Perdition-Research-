import { translate, type Locale } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { createResearchApiClient, ResearchApiError } from "../../api/client";
import type { components } from "../../api/generated/schema";
import {
  formatUsMarketTime,
  modelStatusLabel,
  reasonLabel,
} from "../../app/presentation";
import { DataStatusBanner } from "../../components/DataStatusBanner";
import { snapshotQueryFromSearch } from "../../app/snapshot-refresh";
import { EconomicBacktestChart } from "./EconomicBacktestChart";
import { FoldMetricsTable } from "./FoldMetricsTable";
import { IntervalCoverageChart } from "./IntervalCoverageChart";
import { OptionBacktestsPanel } from "./OptionBacktestsPanel";
import { ModelComparisonTable } from "./ModelComparisonTable";
import { ReliabilityChart } from "./ReliabilityChart";
import { SurvivalCalibrationChart } from "./SurvivalCalibrationChart";
import {
  buildBacktestRequest,
  buildModelRequest,
  decodeBacktestUrlState,
  encodeBacktestUrlState,
  resolveBacktestUrlState,
  selectBacktestEvidence,
  type BacktestQuery,
  type BacktestUrlState,
  type ModelQuery,
} from "./backtest-url-state";
import "./backtests.css";
type BacktestResponse = components["schemas"]["ResearchResponse_BacktestReadModel_"];
type ModelResponse = components["schemas"]["ResearchResponse_ModelMatrixReadModel_"];
type BacktestReadModel = components["schemas"]["BacktestReadModel"];
type DiagnosticSlice = components["schemas"]["DiagnosticSliceEvidence"];
export type BacktestLoader = (query: BacktestQuery) => Promise<BacktestResponse>;
export type ModelLoader = (query: ModelQuery) => Promise<ModelResponse>;
const apiClient = createResearchApiClient();
async function loadBacktestFromApi(query: BacktestQuery) {
  const { data } = await apiClient.get("/api/v1/backtests", { params: { query } });
  if (data === undefined) {
    throw new Error("backtest response unavailable");
  }
  return data;
}
async function loadModelsFromApi(query: ModelQuery) {
  const { data } = await apiClient.get("/api/v1/models", { params: { query } });
  if (data === undefined) {
    throw new Error("model response unavailable");
  }
  return data;
}
function queryKey(name: string, query: BacktestQuery | ModelQuery) {
  return [name, query] as const;
}
function optionalInput(value: string) {
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}
interface SliceControlsProps {
  foldIds: readonly string[];
  onChange: (state: BacktestUrlState) => void;
  state: BacktestUrlState;
}
function SliceControls({ foldIds, onChange, state }: SliceControlsProps) {
  const { locale } = useI18n();
  const setValue = <K extends keyof BacktestUrlState>(
    key: K,
    value: BacktestUrlState[K],
  ) => onChange({ ...state, [key]: value, warnings: [] });
  const setVersion = (key: keyof BacktestUrlState["versions"], value: string) =>
    onChange({
      ...state,
      versions: { ...state.versions, [key]: optionalInput(value) },
      warnings: [],
    });
  return (
    <details className="backtest-slices" open>
      <summary>{translate(locale, "backtests.reproducibleDiagnosticSlices")}</summary>
      <div className="backtest-slices__grid">
        <label>
          {" " + (translate(locale, "backtests.modelVersion") + " ")}
          <input
            onChange={(event) => setVersion("modelVersion", event.target.value)}
            value={state.versions.modelVersion ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.strategyVersion") + " ")}
          <input
            onChange={(event) => setVersion("strategyVersion", event.target.value)}
            value={state.versions.strategyVersion ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.universeVersion") + " ")}
          <input
            onChange={(event) => setVersion("universeVersion", event.target.value)}
            value={state.versions.universeVersion ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.splitVersion") + " ")}
          <input
            onChange={(event) => setVersion("splitVersion", event.target.value)}
            value={state.versions.splitVersion ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.evaluationWindow") + " ")}
          <input
            onChange={(event) =>
              setValue("evaluationWindowId", optionalInput(event.target.value))
            }
            value={state.evaluationWindowId ?? ""}
          />
        </label>
        <label>
          {translate(locale, "backtests.outerFold")}
          <select
            onChange={(event) => setValue("foldId", optionalInput(event.target.value))}
            value={state.foldId ?? ""}
          >
            <option value="">{translate(locale, "backtests.allOuterFolds")}</option>
            {foldIds.map((foldId) => (
              <option key={foldId} value={foldId}>
                {foldId}
              </option>
            ))}
          </select>
        </label>
        <label>
          {" " + (translate(locale, "backtests.startDate") + " ")}
          <input
            onChange={(event) => setValue("from", optionalInput(event.target.value))}
            type="date"
            value={state.from ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.endDate") + " ")}
          <input
            onChange={(event) => setValue("to", optionalInput(event.target.value))}
            type="date"
            value={state.to ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.horizon") + " ")}
          <select
            onChange={(event) =>
              setValue("horizon", Number(event.target.value) as 1 | 5 | 20)
            }
            value={state.horizon}
          >
            <option value="1">{translate(locale, "backtests.1Day")}</option>
            <option value="5">{translate(locale, "backtests.5Days")}</option>
            <option value="20">{translate(locale, "backtests.20Days")}</option>
          </select>
        </label>
        <label>
          {" " + (translate(locale, "backtests.task") + " ")}
          <input
            onChange={(event) => setValue("task", optionalInput(event.target.value))}
            value={state.task ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.assetGroup") + " ")}
          <input
            onChange={(event) =>
              setValue("assetGroup", optionalInput(event.target.value))
            }
            value={state.assetGroup ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.sector") + " ")}
          <input
            onChange={(event) => setValue("sector", optionalInput(event.target.value))}
            value={state.sector ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.marketRegime") + " ")}
          <input
            onChange={(event) => setValue("regime", optionalInput(event.target.value))}
            value={state.regime ?? ""}
          />
        </label>
        <label>
          {" " + (translate(locale, "backtests.confidenceSlice") + " ")}
          <select
            onChange={(event) =>
              setValue(
                "confidenceSlice",
                optionalInput(
                  event.target.value,
                ) as BacktestUrlState["confidenceSlice"],
              )
            }
            value={state.confidenceSlice ?? ""}
          >
            <option value="">
              {translate(locale, "backtests.allConfidenceSlices")}
            </option>
            <option value="high">{translate(locale, "backtests.high")}</option>
            <option value="medium">{translate(locale, "backtests.medium")}</option>
            <option value="low">{translate(locale, "backtests.low")}</option>
            <option value="abstain">{translate(locale, "backtests.abstain")}</option>
          </select>
        </label>
        <label>
          {" " + (translate(locale, "backtests.costAssumption") + " ")}
          <input
            onChange={(event) =>
              setValue("costScenario", optionalInput(event.target.value))
            }
            value={state.costScenario ?? ""}
          />
        </label>
      </div>
    </details>
  );
}
function formatMetric(metric: DiagnosticSlice["metrics"][number], locale: Locale) {
  return metric.value === null
    ? `${metric.name}${locale === "en-US" ? ": " : "："}${metric.unavailable_reason}`
    : `${metric.name} ${metric.value.toFixed(3)}`;
}
interface ConfidenceSlicesProps {
  selected: string | undefined;
  slices: readonly DiagnosticSlice[];
}
function ConfidenceSlices({ selected, slices }: ConfidenceSlicesProps) {
  const { locale } = useI18n();
  const confidenceSlices = slices.filter((slice) => slice.dimension === "confidence");
  return (
    <section
      aria-label={translate(locale, "backtests.confidenceSliceDiagnostics")}
      className="backtest-panel"
    >
      <header>
        <h2>{translate(locale, "backtests.confidenceSlicesAndSampleEvidence")}</h2>
        <p>
          {translate(locale, "backtests.sampleCountsAndSkillComeDirectlyFromVersioned")}
        </p>
      </header>
      <div className="backtest-table-scroll">
        <table
          aria-label={translate(locale, "backtests.confidenceSliceSampleEvidence")}
          className="backtest-data-table"
        >
          <thead>
            <tr>
              <th scope="col">{translate(locale, "backtests.slice")}</th>
              <th scope="col">{translate(locale, "backtests.sampleEvidence")}</th>
              <th scope="col">{translate(locale, "backtests.skillEvidence")}</th>
              <th scope="col">{translate(locale, "backtests.selection")}</th>
            </tr>
          </thead>
          <tbody>
            {["high", "medium", "low", "abstain"].map((label) => {
              const slice = confidenceSlices.find((item) => item.value === label);
              return (
                <tr aria-current={selected === label ? "true" : undefined} key={label}>
                  <th scope="row">
                    {translate(
                      locale,
                      label === "high"
                        ? "backtests.high"
                        : label === "medium"
                          ? "backtests.medium"
                          : label === "low"
                            ? "backtests.low"
                            : "backtests.abstain",
                    )}
                  </th>
                  <td>
                    {slice?.sample_count.toLocaleString(locale) ??
                      translate(locale, "backtests.contractSliceMissing")}
                  </td>
                  <td>
                    {slice?.metrics.map((metric) => (
                      <span className="backtest-metric" key={metric.name}>
                        {formatMetric(metric, locale)}
                      </span>
                    )) ?? translate(locale, "backtests.contractMetricMissing")}
                  </td>
                  <td>
                    {selected === label
                      ? translate(locale, "backtests.currentUrlSlice")
                      : translate(locale, "backtests.notSelected")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
function percentage(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}
function GovernanceEvidence({ data }: { data: BacktestReadModel }) {
  const { locale } = useI18n();
  const dsr = data.deflated_sharpe;
  const pbo = data.pbo;
  const trials = data.trial_ledger;
  return (
    <section
      aria-label={translate(locale, "backtests.researchGovernanceEvidence")}
      className="backtest-panel backtest-audit"
    >
      <header>
        <h2>
          {translate(
            locale,
            "backtests.trialsStatisticalCorrectionsAndEvaluationWindows",
          )}
        </h2>
        <p>
          {translate(
            locale,
            "backtests.insufficientEvidenceRetainsItsOriginalStatusItIs",
          )}
        </p>
      </header>
      <div className="backtest-governance-grid">
        <article>
          <h3>{translate(locale, "backtests.deflatedSharpe")}</h3>
          <strong title={dsr.status}>{modelStatusLabel(dsr.status, locale)}</strong>
          <p>
            {" " + (translate(locale, "backtests.observedSharpe") + " ")}
            {dsr.observed_sharpe.toFixed(3)}
            {" " + translate(locale, "backtests.expectedMaximumSharpe")}{" "}
            {dsr.expected_maximum_sharpe.toFixed(3)}
          </p>
          <p>
            {" " + translate(locale, "backtests.estimatedProbabilityOfTrueSharpe")}{" "}
            {dsr.probability_sharpe_is_genuine == null
              ? modelStatusLabel(dsr.status, locale)
              : percentage(dsr.probability_sharpe_is_genuine)}
          </p>
          <p>
            {" " + (translate(locale, "backtests.trialsAttempted") + " ")}
            {dsr.attempted_trials}
            {" " +
              translate(locale, "backtests.successfulTrialsWithSharpeRecords")}{" "}
            {dsr.successful_trials_with_sharpe}
            {" " + (translate(locale, "backtests.observations") + " ")}
            {dsr.observation_count}
          </p>
        </article>
        <article>
          <h3>{translate(locale, "backtests.overfittingProbability")}</h3>
          <strong title={pbo.status}>{modelStatusLabel(pbo.status, locale)}</strong>
          <p>
            {" " + translate(locale, "backtests.probabilityOfOverfitting")}{" "}
            {pbo.probability == null
              ? modelStatusLabel(pbo.status, locale)
              : percentage(pbo.probability)}
          </p>
          <p>
            {" " + (translate(locale, "backtests.blocks") + " ")}
            {pbo.block_count}
            {" " + (translate(locale, "backtests.combinations") + " ")}
            {pbo.combination_count}
          </p>
        </article>
      </div>
      <div className="backtest-table-scroll">
        <table
          aria-label={translate(locale, "backtests.trialRegistry")}
          className="backtest-data-table"
        >
          <thead>
            <tr>
              <th scope="col">{translate(locale, "backtests.registered")}</th>
              <th scope="col">{translate(locale, "backtests.succeeded")}</th>
              <th scope="col">{translate(locale, "backtests.failed")}</th>
              <th scope="col">{translate(locale, "backtests.aborted")}</th>
              <th scope="col">{translate(locale, "backtests.rejected")}</th>
              <th scope="col">{translate(locale, "backtests.running")}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                {translate(locale, "backtests.registered")}
                {trials.registered}
              </td>
              <td>
                {translate(locale, "backtests.succeeded")}
                {trials.succeeded}
              </td>
              <td>
                {translate(locale, "backtests.failed")}
                {trials.failed}
              </td>
              <td>
                {translate(locale, "backtests.aborted")}
                {trials.aborted}
              </td>
              <td>
                {translate(locale, "backtests.rejected")}
                {trials.rejected}
              </td>
              <td>
                {translate(locale, "backtests.running")}
                {trials.running}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="backtest-table-scroll">
        <table
          aria-label={translate(locale, "backtests.evaluationWindowConsumption")}
          className="backtest-data-table"
        >
          <thead>
            <tr>
              <th scope="col">{translate(locale, "backtests.window")}</th>
              <th scope="col">{translate(locale, "backtests.role")}</th>
              <th scope="col">{translate(locale, "backtests.scope")}</th>
              <th scope="col">{translate(locale, "backtests.consumptionCount")}</th>
              <th scope="col">{translate(locale, "backtests.consumedAt")}</th>
            </tr>
          </thead>
          <tbody>
            {data.evaluation_windows.map((window) => (
              <tr key={window.window_id}>
                <th scope="row">{window.window_id}</th>
                <td>{window.role}</td>
                <td>{window.scope}</td>
                <td>{window.consumption_count}</td>
                <td title={window.consumed_at ?? undefined}>
                  {window.consumed_at == null
                    ? window.consumption_count > 0
                      ? translate(locale, "backtests.consumedTimeNotProvided")
                      : translate(locale, "backtests.notConsumed")
                    : formatUsMarketTime(window.consumed_at, locale)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="backtest-evidence-scope">
        <p>
          {" " + translate(locale, "backtests.evidenceScope")}
          <strong>{data.evidence_scope}</strong>
          {" " + (translate(locale, "backtests.reason") + " ")}
          <span title={data.research_only_reasons.join(", ")}>
            {data.research_only_reasons
              .map((value) => reasonLabel(value, locale))
              .join(locale === "en-US" ? ", " : "、") || translate(locale, "backtests.reasonNotProvided")}
          </span>
        </p>
        <p>
          {" " + translate(locale, "backtests.survivorshipBias")}
          {data.survivorship_limited
            ? translate(locale, "backtests.limited")
            : translate(locale, "backtests.notMarked")}{" "}
          |{" "}
          <span title={data.survivorship_limitation_reason ?? undefined}>
            {data.survivorship_limitation_reason == null
              ? translate(locale, "backtests.reasonNotProvided")
              : reasonLabel(data.survivorship_limitation_reason, locale)}
          </span>
        </p>
      </div>
    </section>
  );
}
function SensitivityEvidence({ data }: { data: BacktestReadModel }) {
  const { locale } = useI18n();
  return (
    <div className="backtest-sensitivity-grid">
      <div className="backtest-table-scroll">
        <table
          aria-label={translate(locale, "backtests.costSensitivity")}
          className="backtest-data-table"
        >
          <thead>
            <tr>
              <th scope="col">{translate(locale, "backtests.oneWayCost")}</th>
              <th scope="col">{translate(locale, "backtests.estimatedTotalCost")}</th>
              <th scope="col">{translate(locale, "backtests.netPortfolioReturn")}</th>
            </tr>
          </thead>
          <tbody>
            {data.cost_sensitivity.map((item) => (
              <tr key={item.per_side_bps}>
                <th scope="row">{item.per_side_bps} bps</th>
                <td>{percentage(item.estimated_total_cost)}</td>
                <td>{percentage(item.net_portfolio_return)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="backtest-table-scroll">
        <table
          aria-label={translate(locale, "backtests.capacitySensitivity")}
          className="backtest-data-table"
        >
          <thead>
            <tr>
              <th scope="col">{translate(locale, "backtests.portfolioSize")}</th>
              <th scope="col">{translate(locale, "backtests.foldCount")}</th>
              <th scope="col">
                {translate(locale, "backtests.opportunitiesAboveCapacity")}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.capacity_sensitivity.map((item) => (
              <tr key={item.portfolio_scale}>
                <th scope="row">{item.portfolio_scale.toFixed(1)}×</th>
                <td>{item.fold_count}</td>
                <td>{item.capacity_exceeded_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
export interface BacktestsPageProps {
  loadBacktest?: BacktestLoader;
  loadModels?: ModelLoader;
}
export function BacktestsPage({
  loadBacktest = loadBacktestFromApi,
  loadModels = loadModelsFromApi,
}: BacktestsPageProps) {
  const { locale } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const urlState = useMemo(() => decodeBacktestUrlState(searchParams), [searchParams]);
  const backtestRequest = useMemo(() => buildBacktestRequest(urlState), [urlState]);
  const modelRequest = useMemo(() => buildModelRequest(urlState), [urlState]);
  const backtestKey = queryKey("backtests", backtestRequest);
  const modelKey = queryKey("backtest-models", modelRequest);
  const backtestQuery = useQuery({
    queryFn: () => loadBacktest(backtestRequest),
    queryKey: backtestKey,
  });
  const modelQuery = useQuery({
    queryFn: () => loadModels(modelRequest),
    queryKey: modelKey,
  });
  const selectedEvidence = useMemo(
    () =>
      backtestQuery.data === undefined
        ? undefined
        : selectBacktestEvidence(backtestQuery.data.data, urlState),
    [backtestQuery.data, urlState.foldId, urlState.from, urlState.to],
  );
  useEffect(() => {
    if (backtestQuery.data === undefined) {
      return;
    }
    const nextState = resolveBacktestUrlState(
      urlState,
      backtestQuery.data.resolved_snapshot,
      backtestQuery.data.data,
    );
    const nextParams = encodeBacktestUrlState(nextState);
    if (nextParams.toString() === searchParams.toString()) {
      return;
    }
    queryClient.setQueryData(
      queryKey("backtests", buildBacktestRequest(nextState)),
      backtestQuery.data,
    );
    if (modelQuery.data !== undefined) {
      queryClient.setQueryData(
        queryKey("backtest-models", buildModelRequest(nextState)),
        modelQuery.data,
      );
    }
    setSearchParams(nextParams, { replace: true });
  }, [
    backtestQuery.data,
    modelQuery.data,
    queryClient,
    searchParams,
    setSearchParams,
    urlState,
  ]);
  if (backtestQuery.isPending) {
    return (
      <section
        aria-label={translate(locale, "backtests.loadingBacktestDiagnostics")}
        className="backtest-loading"
        role="status"
      >
        <RefreshCw aria-hidden="true" size={20} strokeWidth={1.5} />
        <div>
          <h1>{translate(locale, "backtests.backtestingCalibrationDiagnostics")}</h1>
          <p>{translate(locale, "backtests.loadingIndependentOutOfSampleEvidence")}</p>
        </div>
      </section>
    );
  }
  if (backtestQuery.data === undefined) {
    const error = backtestQuery.error ?? modelQuery.error;
    const apiError = error instanceof ResearchApiError ? error : undefined;
    const missingEvidence = apiError?.code === "backtest_not_found";
    return (
      <section
        aria-label={
          missingEvidence
            ? translate(locale, "backtests.noMatchingBacktestForThisSnapshot")
            : translate(locale, "backtests.backtestDiagnosticsUnavailable")
        }
        className={missingEvidence ? "backtests-page backtest-empty" : "backtest-error"}
        role={missingEvidence ? "status" : "alert"}
      >
        {missingEvidence ? null : (
          <TriangleAlert aria-hidden="true" size={20} strokeWidth={1.5} />
        )}
        <div>
          <h1>
            {missingEvidence
              ? translate(locale, "backtests.backtestingCalibration")
              : translate(locale, "backtests.backtestDiagnosticsUnavailable")}
          </h1>
          <OptionBacktestsPanel />
          <p>
            {missingEvidence
              ? translate(
                  locale,
                  "backtests.noBacktestMatchesTheSelectedUniverseAndWindow",
                )
              : translate(locale, "backtests.theReadOnlyApiDidNotReturnComplete")}
          </p>
          <details className="research-governance">
            <summary>
              {translate(locale, "backtests.diagnosticDetailsAndQuery")}
            </summary>
            <p>
              {translate(locale, "backtests.query")}
              {searchParams.toString() ||
                translate(locale, "backtests.defaultResearchWindow")}
            </p>
            {apiError ? (
              <p>
                {" " + translate(locale, "backtests.error")}
                {apiError.code}
                {" " + translate(locale, "backtests.requestId")}
                {apiError.requestId ?? translate(locale, "backtests.notProvided")}
              </p>
            ) : null}
          </details>
          <Link
            to={`/health?${new URLSearchParams(
              Object.entries(snapshotQueryFromSearch(searchParams.toString())).filter(
                (entry): entry is [string, string] => typeof entry[1] === "string",
              ),
            ).toString()}`}
          >
            {" " +
              (translate(locale, "backtests.viewModelsAndDataHealthForThisSnapshot") +
                " ")}
          </Link>
          {missingEvidence ? (
            <>
              <p>
                {translate(
                  locale,
                  "backtests.selectAHistoricalSnapshotWithBacktestEvidenceOlder",
                )}
              </p>
              <label>
                {" " + translate(locale, "backtests.historicalSnapshotId")}{" "}
                <input
                  aria-label={translate(
                    locale,
                    "backtests.historicalSnapshotIdWithEvidence",
                  )}
                  name="historical_snapshot"
                  placeholder={translate(locale, "backtests.enterAKnownSnapshotId")}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" || !event.currentTarget.value.trim())
                      return;
                    const next = new URLSearchParams(searchParams);
                    [
                      "as_of",
                      "revision_no",
                      "dataset_revision",
                      "feature_set_version",
                      "universe_version",
                      "model_version",
                      "calibration_version",
                      "split_version",
                      "strategy_version",
                    ].forEach((key) => next.delete(key));
                    next.set("snapshot_id", event.currentTarget.value.trim());
                    setSearchParams(next);
                  }}
                />
              </label>
              <small>
                {translate(locale, "backtests.pressEnterToQueryASavedSnapshot")}
              </small>
            </>
          ) : null}
          <p>
            {translate(
              locale,
              "backtests.serviceConnectivityOnTheHealthPageShowsCurrent",
            )}
          </p>
          {!missingEvidence && apiError?.retryable !== false ? (
            <button
              onClick={() => {
                void backtestQuery.refetch();
                void modelQuery.refetch();
              }}
              type="button"
            >
              {" " + (translate(locale, "backtests.retryReadOnlyDiagnostics") + " ")}
            </button>
          ) : null}
          {modelQuery.data !== undefined ? (
            <section
              aria-label={translate(locale, "backtests.independentModelEvidence")}
            >
              <h2>{translate(locale, "backtests.availableModelTaskEvidence")}</h2>
              <p>
                {" " +
                  (translate(
                    locale,
                    "backtests.thisDoesNotMeanTheMissingBacktestPassed",
                  ) +
                    " ")}
                {modelQuery.data.resolved_snapshot.snapshot_id}
              </p>
              <ModelComparisonTable rows={modelQuery.data.data.rows} />
            </section>
          ) : null}
        </div>
      </section>
    );
  }
  const response = backtestQuery.data;
  const models = modelQuery.data?.data.rows ?? [];
  const evidence = selectedEvidence;
  if (evidence === undefined) {
    return null;
  }
  const noChampion = models.some((row) => row.champion_version === null);
  const foldIds = response.data.fold_metrics.map((fold) => fold.fold_id);
  const selectedDiagnosticSlices = response.data.diagnostic_slices.filter((slice) => {
    if (slice.dimension === "confidence") {
      return urlState.confidenceSlice === slice.value;
    }
    if (slice.dimension === "sector") {
      return urlState.sector === slice.value;
    }
    if (slice.dimension === "regime") {
      return urlState.regime === slice.value;
    }
    return false;
  });
  const selectedSampleEvidence =
    urlState.foldId !== undefined
      ? evidence.folds.reduce((total, fold) => total + fold.sample_count, 0)
      : selectedDiagnosticSlices.length === 1
        ? selectedDiagnosticSlices[0]?.sample_count
        : selectedDiagnosticSlices.length > 1
          ? undefined
          : response.data.sample_count;
  return (
    <section
      aria-label={translate(locale, "backtests.backtestingCalibrationDiagnostics")}
      className="backtests-page"
    >
      <header className="backtests-page__header">
        <div>
          <h1>{translate(locale, "backtests.backtestingCalibrationDiagnostics")}</h1>
          <p>
            {translate(
              locale,
              "backtests.checkFailuresInIndividualIndependentOutOfSample",
            )}
          </p>
        </div>
        <div
          aria-label={translate(locale, "backtests.researchLimitations")}
          className="backtests-page__badges"
        >
          <span>{translate(locale, "backtests.researchOnly")}</span>
          <span>{translate(locale, "backtests.subjectToSurvivorshipBias")}</span>
        </div>
      </header>

      <DataStatusBanner
        lastSuccessfulAt={response.resolved_snapshot.as_of}
        status={response.serving_status}
      />

      {noChampion ? (
        <section
          aria-label={translate(locale, "backtests.noChampionCandidate")}
          className="backtest-governance-alert"
          role="alert"
        >
          <TriangleAlert aria-hidden="true" size={20} strokeWidth={1.5} />
          <div>
            <strong>no_champion_candidate</strong>
            <p>
              {translate(locale, "backtests.independentOutOfSampleGatesDidNotSupport")}
            </p>
          </div>
        </section>
      ) : null}

      {urlState.warnings.length > 0 ? (
        <section
          aria-label={translate(locale, "backtests.urlParameterWarnings")}
          className="backtest-url-warnings"
          role="status"
        >
          {urlState.warnings.map((item) => (
            <p key={`${item.parameter}-${item.rejectedValue}`}>
              {translate(locale, "backtests.invalidUrlParameter", {
                parameter: item.parameter,
                value: item.rejectedValue,
              })}
            </p>
          ))}
        </section>
      ) : null}

      <SliceControls
        foldIds={foldIds}
        onChange={(state) => setSearchParams(encodeBacktestUrlState(state))}
        state={urlState}
      />

      <section
        aria-label={translate(locale, "backtests.diagnosticContext")}
        className="backtest-context"
      >
        <strong>
          {translate(locale, "backtests.totalSamplesN")}
          {response.data.sample_count.toLocaleString(locale)}
        </strong>
        <span>
          {translate(locale, "backtests.model") + " "}
          {response.data.model_version}
        </span>
        <span>
          {translate(locale, "backtests.strategy") + " "}
          {response.data.strategy_version}
        </span>
        <span>{translate(locale, "backtests.splitVersion")} {response.data.split_version}</span>
        <span>
          {translate(locale, "backtests.cost") + " "}
          {response.data.cost_scenario}
        </span>
        <span>
          {" " + (translate(locale, "backtests.currentSliceSamples") + " ")}
          {selectedSampleEvidence?.toLocaleString(locale) ??
            translate(locale, "backtests.noIntersectionSampleCount")}
        </span>
      </section>

      <section
        aria-label={translate(locale, "backtests.independentOutOfSampleOuterFolds")}
        className="backtest-panel backtest-panel--priority"
      >
        <header>
          <div>
            <h2>{translate(locale, "backtests.independentOutOfSampleOuterFolds")}</h2>
          </div>
          <p>
            {translate(
              locale,
              "backtests.theWorstWindowPrecedesAggregateEconomicsEveryFold",
            )}
          </p>
        </header>
        <FoldMetricsTable folds={evidence.folds} />
      </section>

      <div className="backtest-chart-grid">
        <ReliabilityChart bins={response.data.calibration_bins} />
        <IntervalCoverageChart
          calibration={response.data.interval_calibration}
          folds={evidence.folds}
        />
        <SurvivalCalibrationChart
          folds={evidence.folds}
          points={response.data.survival_calibration}
        />
      </div>

      <ConfidenceSlices
        selected={urlState.confidenceSlice}
        slices={response.data.diagnostic_slices}
      />

      <GovernanceEvidence data={response.data} />
      <OptionBacktestsPanel />

      <section
        aria-label={translate(locale, "backtests.modelComparison")}
        className="backtest-panel"
      >
        <header>
          <h2>{translate(locale, "backtests.baselineChallengerChampionComparison")}</h2>
          <p>
            {translate(locale, "backtests.allTasksRemainVisibleEvenWithoutAChampion")}
          </p>
        </header>
        {modelQuery.data !== undefined ? (
          <ModelComparisonTable rows={models} />
        ) : (
          <div
            role="status"
            aria-label={translate(locale, "backtests.modelComparisonStatus")}
          >
            {modelQuery.isPending
              ? translate(
                  locale,
                  "backtests.modelEvidenceIsLoadingBacktestResultsRemainIndependently",
                )
              : translate(
                  locale,
                  "backtests.modelEvidenceFailedToLoadChampionStatusCannot",
                )}
            {!modelQuery.isPending &&
            !(
              modelQuery.error instanceof ResearchApiError &&
              !modelQuery.error.retryable
            ) ? (
              <button type="button" onClick={() => void modelQuery.refetch()}>
                {" " + (translate(locale, "backtests.retryModelEvidence") + " ")}
              </button>
            ) : null}
          </div>
        )}
      </section>

      <section
        aria-label={translate(locale, "backtests.aggregateEconomicDiagnostics")}
        className="backtest-panel"
      >
        <header>
          <h2>{translate(locale, "backtests.aggregateEconomicDiagnostics")}</h2>
          <p>
            {translate(
              locale,
              "backtests.aggregateResultsDoNotOverrideFoldFailuresOnly",
            )}
          </p>
        </header>
        <SensitivityEvidence data={response.data} />
        <EconomicBacktestChart points={evidence.economicSeries} />
      </section>
    </section>
  );
}
