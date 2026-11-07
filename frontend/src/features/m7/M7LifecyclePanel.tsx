import { translate, type Locale } from "../../i18n/core";
import { detailText } from "../../i18n/details";
import { useI18n } from "../../i18n/context";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  CircleAlert,
  CircleCheck,
  FlaskConical,
  ShieldAlert,
  Square,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  modelStatusLabel,
  reasonLabel,
  formatUsMarketTime,
} from "../../app/presentation";
import { updateStageLabel } from "../operations/useEodUpdate";
import { createResearchApiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { M7BlindEvidencePanel } from "./M7BlindEvidencePanel";
import { M7EvidencePanel } from "./M7EvidencePanel";
import "./m7.css";
export type M7Status = components["schemas"]["M7LifecycleStatus"];
type M7Capability = components["schemas"]["M7CapabilityLifecycleStatus"];
type TrainingOperation = components["schemas"]["UpdateOperation"];
type WorkerStatus = components["schemas"]["EodWorkerStatus"];
type PartialResearch =
  | components["schemas"]["M7PartialResearchAvailable"]
  | components["schemas"]["M7PartialResearchUnavailable"];
type Bundle = M7Capability["bundle_id"];
type EvidenceTab = "development" | "blind";
type M7Horizon = 1 | 5 | 20;
const apiClient = createResearchApiClient();
export function IndependentTrainingStatus({
  summary,
}: {
  summary: M7Status["independent_training_summary"];
}) {
  const { locale } = useI18n();
  if (!summary) return null;
  if (summary.source_status === "unavailable") {
    return (
      <div role="alert" className="m7-source-state">
        {" " + translate(locale, "m7.independentTrainingSourceUnavailable")}
        <details>
          <summary>{translate(locale, "m7.originalDetails")}</summary>
          {(summary.errors ?? []).join("; ")}
        </details>
      </div>
    );
  }
  return (
    <section
      aria-label={translate(locale, "m7.independentFullTrainingResult")}
      className="m7-evidence-section"
    >
      <h3>
        {translate(locale, "m7.independentFullTraining")}
        {summary.state === "completed"
          ? translate(locale, "m7.completed")
          : modelStatusLabel(summary.state, locale)}
      </h3>
      <p>
        {summary.selected_for_research
          ? translate(locale, "m7.connectedToTheResearchDataSource")
          : translate(locale, "m7.notConnectedToTheResearchDataSourceYet")}
        {" " +
          (translate(locale, "m7.productionForecastsRemainSubjectToPublicationGates") +
            " ")}
      </p>
      <dl className="m7-drift-metrics">
        <div>
          <dt>{translate(locale, "m7.mainModelDevelopmentGates")}</dt>
          <dd>
            {translate(locale, "m7.qualitySummary", {
              passed: summary.main_passed_tasks ?? "—",
              total: summary.main_total_tasks ?? "—",
            })}
          </dd>
        </div>
        <div>
          <dt>{translate(locale, "m7.m7DirectionGate")}</dt>
          <dd>
            {summary.m7_gate_status === "failed"
              ? translate(locale, "m7.fail")
              : summary.m7_gate_status === "passed"
                ? translate(locale, "m7.passed")
                : translate(locale, "m7.noResultYet")}
          </dd>
        </div>
        <div>
          <dt>{translate(locale, "m7.trainingCompletionDate")}</dt>
          <dd>
            {summary.completed_at
              ? formatUsMarketTime(summary.completed_at, locale)
              : translate(locale, "m7.notCompleted")}
          </dd>
        </div>
        <div>
          <dt>{translate(locale, "m7.marketDataCutoffDate")}</dt>
          <dd>
            {summary.feature_as_of
              ? new Date(summary.feature_as_of).toLocaleDateString(locale, {
                  timeZone: "America/New_York",
                })
              : translate(locale, "m7.noneYet")}
          </dd>
        </div>
        <div>
          <dt>{translate(locale, "m7.developmentEvaluationCutoff")}</dt>
          <dd>{summary.development_data_cutoff ?? translate(locale, "m7.noneYet")}</dd>
        </div>
        <div>
          <dt>{translate(locale, "m7.researchModelVersion")}</dt>
          <dd>{summary.main_model_version ?? translate(locale, "m7.noneYet")}</dd>
        </div>
      </dl>
      <p>
        {translate(locale, "m7.runId")}
        {summary.run_id}
      </p>
      {(summary.m7_failed_gates ?? []).length > 0 ? (
        <p>
          {translate(locale, "m7.failedM7Gates")}
          {summary.m7_failed_gates
            ?.map((code) => `${reasonLabel(code, locale)} (${code})`)
            .join("; ")}
        </p>
      ) : null}
      <p>{translate(locale, "m7.m7LifecycleAndBlindTestRecordsBelowAre")}</p>
    </section>
  );
}
const ACTIVE_STATUSES = new Set(["queued", "running", "cancel_requested"]);
export const M7_CANDIDATE_RECIPE_VERSION =
  "candidate-recipe-seed100-targeted-v10" as const;
export async function loadM7StatusFromApi(): Promise<M7Status> {
  const { data } = await apiClient.get("/api/v1/operations/m7-status");
  if (data === undefined) throw new Error("m7 lifecycle status unavailable");
  return data;
}
async function loadLatestTraining(): Promise<TrainingOperation | null> {
  const { data } = await apiClient.get("/api/v1/operations/model-training/latest");
  return data ?? null;
}
async function loadWorker(): Promise<WorkerStatus> {
  const { data } = await apiClient.get("/api/v1/operations/model-training/worker");
  if (data === undefined) throw new Error("m7 worker status unavailable");
  return data;
}
async function loadPartialResult(
  operationId: string,
  symbol: string,
): Promise<PartialResearch> {
  const { data } = await apiClient.get(
    "/api/v1/operations/model-training/{operation_id}/partial-result",
    { params: { path: { operation_id: operationId }, query: { symbol } } },
  );
  if (data === undefined) throw new Error("m7 partial research unavailable");
  return data;
}
async function startTraining(bundles: Bundle[]): Promise<TrainingOperation> {
  const { data, error } = await apiClient.post("/api/v1/operations/model-training", {
    body: {
      candidate_recipe_version: M7_CANDIDATE_RECIPE_VERSION,
      bundles,
      trigger: "manual",
    },
  });
  if (data === undefined) throw new Error(error?.message ?? "m7 training failed");
  return data;
}
async function cancelTraining(operationId: string): Promise<TrainingOperation> {
  const { data, error } = await apiClient.post(
    "/api/v1/operations/{operation_id}/cancel",
    { params: { path: { operation_id: operationId } } },
  );
  if (data === undefined) throw new Error(error?.message ?? "m7 cancellation failed");
  return data;
}
async function promoteCapability(
  status: M7Status,
  capability: M7Capability,
): Promise<TrainingOperation> {
  if (!status.latest_training_run_id || !capability.package_sha256) {
    throw new Error("m7 promotion evidence unavailable");
  }
  const { data, error } = await apiClient.post("/api/v1/operations/model-promotion", {
    body: {
      training_run_id: status.latest_training_run_id,
      bundle_id: capability.bundle_id,
      expected_package_sha256: capability.package_sha256,
      expected_incumbent_id: capability.incumbent_id ?? null,
      approved_by: "local_user",
    },
  });
  if (data === undefined) throw new Error(error?.message ?? "m7 promotion failed");
  return data;
}
const BUNDLE_LABELS = (locale: Locale): Record<Bundle, string> => ({
  direction: translate(locale, "m7.directionProbability"),
  range_exit_20: translate(locale, "m7.20DayRangeExit"),
});
const STATE_LABELS = (locale: Locale): Record<string, string> => ({
  awaiting_manual_approval: translate(locale, "m7.awaitingManualApproval"),
  blind_collecting: translate(locale, "m7.collectingForwardBlindTestSamples"),
  collecting: translate(locale, "m7.collectingPredictionAnchors"),
  consumed: translate(locale, "m7.blindTestSealed"),
  failed: translate(locale, "m7.developmentGatesFailed"),
  frozen: translate(locale, "m7.shadowFrozen"),
  invalidated: translate(locale, "m7.blindTestInvalidated"),
  label_maturing: translate(locale, "m7.waitingFor20DayLabelsToMature"),
  not_eligible: translate(locale, "m7.notEligibleYet"),
  not_frozen: translate(locale, "m7.notFrozen"),
  not_started: translate(locale, "m7.notStarted"),
  passed: translate(locale, "m7.developmentGatesPassed"),
  ready_for_review: translate(locale, "m7.blindTestReadyForReview"),
  rejected: translate(locale, "m7.challengerRejected"),
});
function stateLabel(value: string, locale: Locale = "zh-CN") {
  return (
    STATE_LABELS(locale)[value] ?? translate(locale, "m7.unknownState", { code: value })
  );
}
function shortHash(value: string | null | undefined, locale: Locale = "zh-CN") {
  return value
    ? `${value.slice(0, 10)}…${value.slice(-6)}`
    : translate(locale, "m7.noneYet2");
}
function writeM7UrlState(
  runId: string | null,
  bundle: Bundle,
  horizon: M7Horizon,
  tab: EvidenceTab,
) {
  const url = new URL(window.location.href);
  if (runId) url.searchParams.set("m7_run", runId);
  else url.searchParams.delete("m7_run");
  url.searchParams.set("m7_bundle", bundle);
  url.searchParams.set("m7_horizon", `${horizon}`);
  url.searchParams.set("m7_tab", tab);
  window.history.replaceState(window.history.state, "", url);
}
export interface M7LifecyclePanelProps {
  actionsEnabled?: boolean;
  cancelTrainingAction?: (operationId: string) => Promise<TrainingOperation>;
  loadLatestTrainingAction?: () => Promise<TrainingOperation | null>;
  loadPartialResultAction?: (
    operationId: string,
    symbol: string,
  ) => Promise<PartialResearch>;
  loadStatus?: () => Promise<M7Status>;
  loadWorkerAction?: () => Promise<WorkerStatus>;
  startTrainingAction?: (bundles: Bundle[]) => Promise<TrainingOperation>;
  symbol?: string;
}
export function M7LifecyclePanel({
  actionsEnabled = true,
  cancelTrainingAction = cancelTraining,
  loadLatestTrainingAction = loadLatestTraining,
  loadPartialResultAction = loadPartialResult,
  loadStatus = loadM7StatusFromApi,
  loadWorkerAction = loadWorker,
  startTrainingAction = startTraining,
  symbol,
}: M7LifecyclePanelProps) {
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const [selectedBundle, setSelectedBundle] = useState<Bundle>("direction");
  const [selectedHorizon, setSelectedHorizon] = useState<M7Horizon>(1);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("development");
  const [urlFallbackNotice, setUrlFallbackNotice] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const statusQuery = useQuery({
    queryFn: loadStatus,
    queryKey: ["m7-lifecycle-status"],
    refetchInterval: 30000,
  });
  const operationQuery = useQuery({
    enabled: actionsEnabled,
    queryFn: loadLatestTrainingAction,
    queryKey: ["m7-training-latest"],
    refetchInterval: (query) =>
      ACTIVE_STATUSES.has(query.state.data?.status ?? "") ? 2000 : 30000,
  });
  const workerQuery = useQuery({
    enabled: actionsEnabled,
    queryFn: loadWorkerAction,
    queryKey: ["m7-training-worker"],
    refetchInterval: 5000,
  });
  const startMutation = useMutation({
    mutationFn: (bundles: Bundle[]) => startTrainingAction(bundles),
    onSuccess: (operation) => {
      queryClient.setQueryData(["m7-training-latest"], operation);
    },
  });
  const cancelMutation = useMutation({
    mutationFn: cancelTrainingAction,
    onSuccess: (operation) => {
      queryClient.setQueryData(["m7-training-latest"], operation);
    },
  });
  const partialQuery = useQuery({
    enabled: operationQuery.data != null && symbol != null,
    queryFn: () => loadPartialResultAction(operationQuery.data!.operation_id, symbol!),
    queryKey: ["m7-partial-result", operationQuery.data?.operation_id, symbol],
    refetchInterval: () =>
      ACTIVE_STATUSES.has(operationQuery.data?.status ?? "") ? 2000 : 30000,
  });
  const promotionMutation = useMutation({
    mutationFn: (capability: M7Capability) =>
      promoteCapability(statusQuery.data!, capability),
    onSuccess: () => {
      setConfirmed(false);
      void queryClient.invalidateQueries({ queryKey: ["m7-lifecycle-status"] });
    },
  });
  useEffect(() => {
    const status = statusQuery.data;
    if (!status) return;
    const params = new URLSearchParams(window.location.search);
    const runParam = params.get("m7_run");
    const bundleParam = params.get("m7_bundle");
    const horizonParam = params.get("m7_horizon");
    const tabParam = params.get("m7_tab");
    const hasM7UrlState = ["m7_run", "m7_bundle", "m7_horizon", "m7_tab"].some((key) =>
      params.has(key),
    );
    const bundles = new Set(status.capabilities.map((row) => row.bundle_id));
    const bundle: Bundle =
      bundleParam === "direction" || bundleParam === "range_exit_20"
        ? bundleParam
        : "direction";
    const allowedHorizons: readonly M7Horizon[] =
      bundle === "direction" ? [1, 5, 20] : [20];
    const parsedHorizon = Number(horizonParam);
    const horizon = allowedHorizons.includes(parsedHorizon as M7Horizon)
      ? (parsedHorizon as M7Horizon)
      : bundle === "direction"
        ? 1
        : 20;
    const tab: EvidenceTab =
      tabParam === "development" || tabParam === "blind" ? tabParam : "development";
    const invalid =
      (runParam !== null && runParam !== status.latest_training_run_id) ||
      (bundleParam !== null && !bundles.has(bundleParam as Bundle)) ||
      (horizonParam !== null &&
        !allowedHorizons.includes(parsedHorizon as M7Horizon)) ||
      (tabParam !== null && tabParam !== "development" && tabParam !== "blind");
    setSelectedBundle(bundle);
    setSelectedHorizon(horizon);
    setEvidenceTab(tab);
    setUrlFallbackNotice(invalid);
    if (hasM7UrlState) {
      writeM7UrlState(status.latest_training_run_id, bundle, horizon, tab);
    }
  }, [statusQuery.data]);
  if (statusQuery.isPending) {
    return (
      <section
        aria-label={translate(locale, "m7.m7TargetedModelLifecycle")}
        className="m7-panel"
        role="status"
      >
        <Activity aria-hidden="true" size={18} />
        {" " + (translate(locale, "m7.loadingM7LifecycleEvidence") + " ")}
      </section>
    );
  }
  if (!statusQuery.data) {
    return (
      <section
        aria-label={translate(locale, "m7.m7TargetedModelLifecycle")}
        className="m7-panel"
        role="alert"
      >
        <CircleAlert aria-hidden="true" size={18} />
        <div>
          <strong>{translate(locale, "m7.m7StatusSourceUnavailable")}</strong>
          <p>
            {translate(
              locale,
              "m7.existingResearchSnapshotsRemainAvailableANewChallenger",
            )}
          </p>
        </div>
      </section>
    );
  }
  const status = statusQuery.data;
  const operation = operationQuery.data;
  const operationActive = ACTIVE_STATUSES.has(operation?.status ?? "");
  const workerReady = workerQuery.data?.state === "ready";
  const workerUnavailable = operationActive && workerQuery.data != null && !workerReady;
  const currentPhaseDetail = operation?.phases.at(-1)?.detail;
  const independentCompletedAt = status.independent_training_summary?.completed_at;
  const historicalOperation =
    independentCompletedAt != null &&
    operation != null &&
    new Date(operation.updated_at) < new Date(independentCompletedAt);
  const canStart =
    actionsEnabled &&
    status.executor_configured &&
    workerReady &&
    !operationActive &&
    true;
  const startLabel = !status.executor_configured
    ? translate(locale, "m7.executorDisconnected")
    : operationActive
      ? workerUnavailable
        ? translate(locale, "m7.trainingWorkerDisconnected")
        : translate(locale, "m7.trainingRunning", {
            operation: String(operation?.progress_percent ?? 0),
          })
      : workerReady
        ? translate(locale, "m7.startDirectionTraining")
        : translate(locale, "m7.trainingWorkerNotReady");
  return (
    <section aria-labelledby="m7-lifecycle-title" className="m7-panel">
      <header className="m7-panel__header">
        <div>
          <span className="m7-kicker">
            <FlaskConical aria-hidden="true" size={15} />
            {" " + (translate(locale, "m7.m7TargetedModelsVnext") + " ")}
          </span>
          <h2 id="m7-lifecycle-title">
            {translate(locale, "m7.independentPromotionWorkbenchForTwoCapabilities")}
          </h2>
          <p>{translate(locale, "m7.directionProbabilitiesAnd20DayRangeExitAre")}</p>
        </div>
        <div
          className="m7-badges"
          aria-label={translate(locale, "m7.m7UsageBoundaries")}
        >
          <strong>{translate(locale, "m7.experimentalChallenger")}</strong>
          <strong>{translate(locale, "m7.forwardBlindTestIncomplete")}</strong>
          <strong>
            {translate(locale, "m7.unavailableAsProductionForecastsOrTradingSignals")}
          </strong>
        </div>
      </header>

      <IndependentTrainingStatus summary={status.independent_training_summary} />

      {!status.executor_configured ? (
        <div className="m7-blocker" role="alert">
          <ShieldAlert aria-hidden="true" size={18} />
          <div>
            <strong>
              {translate(locale, "m7.theRealBundleExecutorIsNotConnected")}
            </strong>
            <span>
              {" " +
                (translate(
                  locale,
                  "m7.lifecycleLeakagePreventionAndPromotionGatesAreReady",
                ) +
                  " ")}
            </span>
          </div>
        </div>
      ) : null}

      {operation?.status === "failed" ? (
        <div className="m7-blocker" role="alert">
          <CircleAlert aria-hidden="true" size={18} />
          <div>
            <strong>
              {historicalOperation
                ? translate(locale, "m7.historicalLifecycleTrainingFailed")
                : translate(locale, "m7.latestTrainingFailed")}
            </strong>
            <span>
              {operation.error_code ?? "model_training_failed"} |{" "}
              {operation.current_stage ?? "unknown_stage"}
            </span>
          </div>
        </div>
      ) : null}

      {operationActive && workerUnavailable ? (
        <div
          aria-label={translate(locale, "m7.trainingWorkerDisconnected")}
          className="m7-blocker"
          role="alert"
        >
          <CircleAlert aria-hidden="true" size={18} />
          <div>
            <strong>{translate(locale, "m7.trainingWorkerDisconnected")}</strong>
            <span>
              {translate(locale, "m7.workerStatusUnavailable")}
              {" " +
                (translate(
                  locale,
                  "m7.historicalPercentagesAreNotPresentedAsActiveTraining",
                ) +
                  " ")}
            </span>
          </div>
        </div>
      ) : operationActive ? (
        <div
          aria-label={translate(locale, "m7.liveTrainingProgress")}
          className="m7-blocker"
          role="status"
        >
          <Activity aria-hidden="true" size={18} />
          <div>
            <strong>
              {translate(locale, "m7.liveTrainingProgress") + " "}
              {operation?.progress_percent ?? 0}%
            </strong>
            <span>
              {operation?.current_stage
                ? translate(locale, "m7.liveStage", {
                    stage: updateStageLabel(operation.current_stage, locale),
                  })
                : translate(locale, "m7.waitingForTheExecutorToRecordTheFirst")}
            </span>
            {currentPhaseDetail ? (
              <details>
                <summary>{translate(locale, "m7.originalDetails")}</summary>
                {detailText(currentPhaseDetail, locale)}
              </details>
            ) : null}
            <span>
              {translate(locale, "m7.latestActivity") + " "}
              {operation?.updated_at}
            </span>
            {operation?.active_trial_id ? (
              <span>
                {translate(locale, "m7.currentTrial") + " "}
                {operation.active_trial_id}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {partialQuery.data?.availability === "available" &&
      partialQuery.data.bundle_id === selectedBundle ? (
        <section
          className="m7-partial-research"
          aria-label={translate(locale, "m7.m7PartialResearchProbabilities")}
        >
          <strong>
            {translate(
              locale,
              "m7.trainingIncompleteResearchOnlyNotProductionForecasts",
            )}
          </strong>
          <span>
            {" " + (translate(locale, "m7.contributingFolds") + " ")}
            {partialQuery.data.contributing_folds.length}/
            {partialQuery.data.required_folds}
            {" " + (translate(locale, "m7.dataThrough") + " ")}
            {partialQuery.data.as_of} | {partialQuery.data.training_devices.join(" + ")}
          </span>
          {partialQuery.data.predictions
            .filter((prediction) => prediction.horizon === selectedHorizon)
            .map((prediction) =>
              "up_probability" in prediction ? (
                <dl key={`${prediction.symbol}-${prediction.horizon}`}>
                  <div>
                    <dt>{translate(locale, "m7.down2")}</dt>
                    <dd>{(prediction.down_probability * 100).toFixed(1)}%</dd>
                  </div>
                  <div>
                    <dt>{translate(locale, "m7.flat2")}</dt>
                    <dd>{(prediction.flat_probability * 100).toFixed(1)}%</dd>
                  </div>
                  <div>
                    <dt>{translate(locale, "m7.up2")}</dt>
                    <dd>{(prediction.up_probability * 100).toFixed(1)}%</dd>
                  </div>
                </dl>
              ) : (
                <dl key={`${prediction.symbol}-${prediction.horizon}`}>
                  <div>
                    <dt>{translate(locale, "m7.20DayRangeExit")}</dt>
                    <dd>{(prediction.range_exit_probability * 100).toFixed(1)}%</dd>
                  </div>
                </dl>
              ),
            )}
        </section>
      ) : selectedBundle === "range_exit_20" && !operationActive ? (
        <p className="m7-url-notice">
          {translate(locale, "m7.secondStageTrainingHasNotStarted")}
        </p>
      ) : null}

      {urlFallbackNotice ? (
        <p className="m7-url-notice" role="status">
          {" " +
            (translate(locale, "m7.invalidM7ViewParametersShowingTheLatestValid") +
              " ")}
        </p>
      ) : null}

      <div
        aria-label={translate(locale, "m7.m7EvidenceView")}
        className="m7-view-controls"
      >
        <div
          aria-label={translate(locale, "m7.selectCapability")}
          className="m7-segmented"
        >
          {status.capabilities.map((capability) => (
            <button
              aria-pressed={selectedBundle === capability.bundle_id}
              key={capability.bundle_id}
              onClick={() => {
                const horizon = capability.bundle_id === "direction" ? 1 : 20;
                setSelectedBundle(capability.bundle_id);
                setSelectedHorizon(horizon);
                writeM7UrlState(
                  status.latest_training_run_id,
                  capability.bundle_id,
                  horizon,
                  evidenceTab,
                );
              }}
              type="button"
            >
              {BUNDLE_LABELS(locale)[capability.bundle_id]}
            </button>
          ))}
        </div>
        <div
          aria-label={translate(locale, "m7.selectHorizon")}
          className="m7-segmented"
        >
          {(selectedBundle === "direction" ? [1, 5, 20] : [20]).map((horizon) => (
            <button
              aria-pressed={selectedHorizon === horizon}
              key={horizon}
              onClick={() => {
                setSelectedHorizon(horizon as M7Horizon);
                writeM7UrlState(
                  status.latest_training_run_id,
                  selectedBundle,
                  horizon as M7Horizon,
                  evidenceTab,
                );
              }}
              type="button"
            >
              H{horizon}
            </button>
          ))}
        </div>
        <div
          aria-label={translate(locale, "m7.selectEvidence")}
          className="m7-segmented"
          role="tablist"
        >
          {(["development", "blind"] as const).map((tab) => (
            <button
              aria-selected={evidenceTab === tab}
              key={tab}
              onClick={() => {
                setEvidenceTab(tab);
                writeM7UrlState(
                  status.latest_training_run_id,
                  selectedBundle,
                  selectedHorizon,
                  tab,
                );
              }}
              role="tab"
              type="button"
            >
              {tab === "development"
                ? translate(locale, "m7.developmentEvidence")
                : translate(locale, "m7.forwardBlindTest")}
            </button>
          ))}
        </div>
      </div>

      <div className="m7-capability-grid">
        {status.capabilities.map((capability) => {
          const progress = Math.round(
            (capability.blind_available_anchors / capability.blind_required_anchors) *
              100,
          );
          return (
            <section
              aria-label={translate(locale, "m7.capability", {
                bundle_id: String(BUNDLE_LABELS(locale)[capability.bundle_id]),
              })}
              className="m7-capability"
              data-active={capability.is_active_champion}
              data-selected={selectedBundle === capability.bundle_id}
              key={capability.bundle_id}
              role="group"
            >
              <header>
                <div>
                  <span>{capability.bundle_id}</span>
                  <h3>{BUNDLE_LABELS(locale)[capability.bundle_id]}</h3>
                </div>
                <strong>
                  {capability.is_active_champion ? "M7 Champion" : "Challenger"}
                </strong>
              </header>
              <dl className="m7-state-grid">
                <div>
                  <dt>{translate(locale, "m7.development")}</dt>
                  <dd>{stateLabel(capability.development_state, locale)}</dd>
                </div>
                <div>
                  <dt>{translate(locale, "m7.shadow")}</dt>
                  <dd>{stateLabel(capability.shadow_state, locale)}</dd>
                </div>
                <div>
                  <dt>{translate(locale, "m7.blindTest")}</dt>
                  <dd>{stateLabel(capability.blind_state, locale)}</dd>
                </div>
                <div>
                  <dt>{translate(locale, "m7.promotion")}</dt>
                  <dd>{stateLabel(capability.promotion_state, locale)}</dd>
                </div>
              </dl>
              <div className="m7-progress-copy">
                <span>{translate(locale, "m7.availableAnchors")}</span>
                <strong>
                  {capability.blind_available_anchors} /{" "}
                  {capability.blind_required_anchors}
                </strong>
              </div>
              <div
                aria-label={translate(locale, "m7.blindTestProgress", {
                  bundle_id: String(BUNDLE_LABELS(locale)[capability.bundle_id]),
                  progress: String(progress),
                })}
                aria-valuemax={capability.blind_required_anchors}
                aria-valuemin={0}
                aria-valuenow={capability.blind_available_anchors}
                className="m7-progress"
                role="progressbar"
              >
                <span style={{ width: `${progress}%` }} />
              </div>
              <div className="m7-progress-copy m7-progress-copy--secondary">
                <span>
                  {translate(locale, "m7.matureLabels") + " "}
                  {capability.blind_mature_labels} / {capability.blind_required_anchors}
                </span>
                <span>
                  {translate(locale, "m7.window") + " "}
                  {capability.blind_window_id ??
                    translate(locale, "m7.notRegisteredYet")}
                </span>
              </div>
              <div className="m7-progress-meta">
                <span>
                  {translate(locale, "m7.missingAnchors") + " "}
                  {capability.blind_missing_anchors ?? 0}
                </span>
                <span>
                  {" " + (translate(locale, "m7.nextCollection") + " ")}
                  {capability.blind_next_scheduled_session ??
                    translate(locale, "m7.noPendingCollectionDate")}
                </span>
                <span>
                  {" " + (translate(locale, "m7.lastAvailable") + " ")}
                  {capability.blind_last_available_anchor ??
                    translate(locale, "m7.noAvailableAnchorsYet")}
                </span>
              </div>
              <dl className="m7-evidence-grid">
                <div>
                  <dt>{translate(locale, "m7.package")}</dt>
                  <dd>{capability.package_id ?? translate(locale, "m7.noneYet2")}</dd>
                </div>
                <div>
                  <dt>SHA-256</dt>
                  <dd title={capability.package_sha256 ?? undefined}>
                    {shortHash(capability.package_sha256, locale)}
                  </dd>
                </div>
                <div>
                  <dt>{translate(locale, "m7.inputHash")}</dt>
                  <dd title={capability.feature_input_hash ?? undefined}>
                    {shortHash(capability.feature_input_hash, locale)}
                  </dd>
                </div>
                <div>
                  <dt>{translate(locale, "m7.challengerMatrix")}</dt>
                  <dd title={capability.candidate_matrix_hash ?? undefined}>
                    {shortHash(capability.candidate_matrix_hash, locale)}
                  </dd>
                </div>
                <div>
                  <dt>{translate(locale, "m7.modelComponents")}</dt>
                  <dd>
                    {capability.model_components.length > 0
                      ? capability.model_components.join("; ")
                      : translate(locale, "m7.noneYet2")}
                  </dd>
                </div>
                <div>
                  <dt>{translate(locale, "m7.blendWeights")}</dt>
                  <dd>
                    {capability.blend_weights.length > 0
                      ? capability.blend_weights.join("; ")
                      : translate(locale, "m7.noneYet2")}
                  </dd>
                </div>
              </dl>
              {selectedBundle === capability.bundle_id ? (
                evidenceTab === "development" ? (
                  <M7EvidencePanel capability={capability} />
                ) : (
                  <M7BlindEvidencePanel
                    capability={capability}
                    horizon={selectedHorizon}
                  />
                )
              ) : null}
              <button
                disabled={
                  !confirmed ||
                  !capability.promotion_allowed ||
                  promotionMutation.isPending
                }
                onClick={() => promotionMutation.mutate(capability)}
                type="button"
              >
                {capability.promotion_allowed
                  ? translate(locale, "m7.manuallyPromoteThisCapability")
                  : translate(locale, "m7.notEligibleForPromotion")}
              </button>
            </section>
          );
        })}
      </div>

      {startMutation.error || cancelMutation.error || promotionMutation.error ? (
        <div role="alert">
          <p>{translate(locale, "m7.trainingError")}</p>
          <details>
            <summary>{translate(locale, "m7.originalDetails")}</summary>
            {String(
              startMutation.error ?? cancelMutation.error ?? promotionMutation.error,
            )}
          </details>
        </div>
      ) : null}
      <footer className="m7-controls">
        <label className="m7-confirmation">
          <input
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            type="checkbox"
          />
          {" " + (translate(locale, "m7.anchorsConfirm") + " ")}
        </label>
        <div className="m7-control-actions">
          <button
            disabled={!canStart || startMutation.isPending}
            onClick={() => startMutation.mutate(["direction"])}
            type="button"
          >
            <CircleCheck aria-hidden="true" size={16} /> {startLabel}
          </button>
          {canStart &&
          operation != null &&
          !operationActive &&
          operation.requested_bundles?.includes("direction") ? (
            <button
              disabled={startMutation.isPending}
              onClick={() => startMutation.mutate(["range_exit_20"])}
              type="button"
            >
              {" " + (translate(locale, "m7.continue20DayRangeExitTraining") + " ")}
            </button>
          ) : null}
          {operationActive && operation ? (
            <button
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate(operation.operation_id)}
              type="button"
            >
              <Square aria-hidden="true" size={14} />
              {" " + (translate(locale, "m7.cancelCurrentTraining") + " ")}
            </button>
          ) : null}
        </div>
        <small>
          {" " + (translate(locale, "m7.recipe") + " ")}
          {status.recipe_id}
          {" " + translate(locale, "m7.latestCompleteLifecycleReport")}{" "}
          {status.latest_training_run_id ?? translate(locale, "m7.noneYet2")}
        </small>
      </footer>
    </section>
  );
}
