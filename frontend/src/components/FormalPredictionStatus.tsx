import { translate, type Locale } from "../i18n/core";
import { useI18n } from "../i18n/context";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, CircleSlash2, Play, RefreshCw } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { createResearchApiClient } from "../api/client";
import type { components } from "../api/generated/schema";
import { formatUsMarketTime, reasonLabel, taskLabel } from "../app/presentation";
import "./formal-prediction-status.css";
import { OriginalDetails } from "./OriginalDetails";
type ResolvedSnapshot = components["schemas"]["ResolvedSnapshot"];
type ModelResponse = components["schemas"]["ResearchResponse_ModelMatrixReadModel_"];
type TrainingOperation = components["schemas"]["UpdateOperation"];
type TrainingReadiness = components["schemas"]["ModelTrainingReadiness"];
type WorkerStatus = components["schemas"]["EodWorkerStatus"];
type ModelRow = components["schemas"]["ModelTaskMatrixRow"];
const apiClient = createResearchApiClient();
const ACTIVE_OPERATION_STATUSES = new Set(["queued", "running"]);
const TRAINING_PHASES = (locale: Locale) =>
  [
    ["data_repair", translate(locale, "common.shared.dataRepair")],
    ["label_health", translate(locale, "common.shared.labelHealth")],
    ["candidate_search", translate(locale, "common.shared.candidateSearch")],
    ["oos_validation", translate(locale, "common.shared.outOfSampleValidation")],
    ["promotion_window", translate(locale, "common.shared.promotionWindow")],
    ["champion_publication", translate(locale, "common.shared.championPublication")],
  ] as const;
async function loadFormalPredictionStatus(): Promise<ModelResponse> {
  const { data } = await apiClient.get("/api/v1/models", {
    params: { query: {} },
  });
  if (data === undefined) throw new Error("model matrix unavailable");
  return data;
}
async function loadLatestTraining(): Promise<TrainingOperation | null> {
  const { data } = await apiClient.get("/api/v1/operations/model-training/latest");
  return data ?? null;
}
async function loadTrainingWorker(): Promise<WorkerStatus> {
  const { data } = await apiClient.get("/api/v1/operations/model-training/worker");
  if (data === undefined) throw new Error("model worker unavailable");
  return data;
}
async function loadLatestPromotion(): Promise<TrainingOperation | null> {
  const { data } = await apiClient.get("/api/v1/operations/model-promotion/latest");
  return data ?? null;
}
async function loadPromotionWorker(): Promise<WorkerStatus> {
  const { data } = await apiClient.get("/api/v1/operations/model-promotion/worker");
  if (data === undefined) throw new Error("model promotion worker unavailable");
  return data;
}
async function loadTrainingReadiness(): Promise<TrainingReadiness> {
  const { data } = await apiClient.get("/api/v1/operations/model-training/readiness");
  if (data === undefined) throw new Error("model training readiness unavailable");
  return data;
}
async function submitTraining(recipeVersion: string): Promise<TrainingOperation> {
  const { data, error } = await apiClient.post("/api/v1/operations/model-training", {
    body: { candidate_recipe_version: recipeVersion, trigger: "manual" },
  });
  if (data === undefined) {
    throw new Error(error?.message ?? "model training submit failed");
  }
  return data;
}
async function submitPromotion(): Promise<TrainingOperation> {
  const { data, error } = await apiClient.post("/api/v1/operations/model-promotion");
  if (data === undefined) {
    throw new Error(error?.message ?? "model promotion submit failed");
  }
  return data;
}
function taskName(row: ModelRow, locale: Locale = "zh-CN") {
  return translate(locale, "common.shared.tradingDays", {
    task: String(taskLabel(row.task, locale)),
    horizon: String(row.horizon),
  });
}
function gateDetail(row: ModelRow, locale: Locale = "zh-CN") {
  const details: string[] = [];
  if (row.baseline_wins != null && row.fold_count != null) {
    details.push(
      translate(locale, "common.shared.statisticalBaselineWins", {
        baseline_wins: String(row.baseline_wins),
        fold_count: String(row.fold_count),
      }),
    );
  }
  if (row.pinball_wins != null && row.fold_count != null) {
    details.push(
      translate(locale, "common.shared.pinballLossWins", {
        pinball_wins: String(row.pinball_wins),
        fold_count: String(row.fold_count),
      }),
    );
  }
  if (row.coverage_windows_passed != null && row.fold_count != null) {
    details.push(
      translate(locale, "common.shared.intervalCoveragePasses", {
        coverage_windows_passed: String(row.coverage_windows_passed),
        fold_count: String(row.fold_count),
      }),
    );
  }
  if (row.calibration_gate_passed === false)
    details.push(translate(locale, "common.shared.calibrationGateFailed"));
  if (row.stability_gate_passed === false)
    details.push(translate(locale, "common.shared.stabilityGateFailed"));
  if (row.promotion_window_passed === false)
    details.push(translate(locale, "common.shared.promotionWindowFailed"));
  return details.join("; ");
}
function failureLabel(code: string, locale: Locale = "zh-CN") {
  return (
    {
      calibration_gate_failed: translate(
        locale,
        "common.shared.calibrationErrorExceedsTheLimit",
      ),
      label_degenerate: translate(
        locale,
        "common.shared.labelClassProportionsDegenerated",
      ),
      promotion_window_already_consumed: translate(
        locale,
        "common.shared.oneTimePromotionWindowConsumed",
      ),
      promotion_window_failed: translate(
        locale,
        "common.shared.oneTimePromotionWindowNotWon",
      ),
      stability_gate_failed: translate(
        locale,
        "common.shared.crossWindowStabilityBelowTheThreshold",
      ),
      statistical_gate_failed: translate(locale, "common.shared.statisticalGateFailed"),
      unexplained_corporate_action_return: translate(
        locale,
        "common.shared.unexplainedCorporateActionReturnJump",
      ),
    }[code] ?? code
  );
}
function trainingDetail(detail: string | null | undefined, locale: Locale = "zh-CN") {
  if (!detail) return translate(locale, "common.shared.backgroundTrainingIsRunning");
  return detail.replace(
    /forecast_direction|breakout|return_fwd|mfe|mae|range_exit/g,
    (task) => taskLabel(task, locale),
  );
}
function phaseState(
  phase: ReturnType<typeof TRAINING_PHASES>[number][0],
  operation: TrainingOperation | null | undefined,
  summary: ModelResponse["data"]["candidate_summary"],
  snapshot: ResolvedSnapshot,
) {
  const operationPhase = operation?.phases?.find((item) => item.stage === phase);
  if (operationPhase?.status === "running") return "running";
  if (
    phase === "promotion_window" &&
    (summary?.promotion_window_status === "failed" ||
      summary?.promotion_window_status === "consumed")
  ) {
    return "failed";
  }
  if (operationPhase?.status === "completed") return "completed";
  if (phase === "data_repair" && summary?.data_quality_status === "passed") {
    return "completed";
  }
  if (phase === "label_health" && summary?.label_health_status === "passed") {
    return "completed";
  }
  if (phase === "candidate_search" && summary != null) return "completed";
  if (phase === "oos_validation" && summary != null) return "completed";
  if (phase === "promotion_window" && summary?.promotion_window_status === "passed") {
    return "completed";
  }
  if (
    phase === "champion_publication" &&
    snapshot.prediction_readiness === "forecast_ready"
  ) {
    return "completed";
  }
  return "pending";
}
export interface FormalPredictionStatusProps {
  snapshot: ResolvedSnapshot;
}
export function FormalPredictionStatus({ snapshot }: FormalPredictionStatusProps) {
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryFn: loadFormalPredictionStatus,
    queryKey: ["formal-prediction-status", snapshot.snapshot_id],
  });
  const operationQuery = useQuery({
    queryFn: loadLatestTraining,
    queryKey: ["model-training-latest"],
    refetchInterval: (result) =>
      ACTIVE_OPERATION_STATUSES.has(result.state.data?.status ?? "") ? 2000 : false,
  });
  const workerQuery = useQuery({
    queryFn: loadTrainingWorker,
    queryKey: ["model-training-worker"],
    refetchInterval: 5000,
  });
  const promotionQuery = useQuery({
    queryFn: loadLatestPromotion,
    queryKey: ["model-promotion-latest"],
    refetchInterval: (result) =>
      ACTIVE_OPERATION_STATUSES.has(result.state.data?.status ?? "") ? 2000 : false,
  });
  const promotionWorkerQuery = useQuery({
    queryFn: loadPromotionWorker,
    queryKey: ["model-promotion-worker"],
    refetchInterval: 5000,
  });
  const readinessQuery = useQuery({
    queryFn: loadTrainingReadiness,
    queryKey: ["model-training-readiness"],
    refetchInterval: 60000,
  });
  const mutation = useMutation({
    mutationFn: submitTraining,
    onSuccess: (operation) => {
      queryClient.setQueryData(["model-training-latest"], operation);
      void queryClient.invalidateQueries({ queryKey: ["model-training-readiness"] });
    },
  });
  const promotionMutation = useMutation({
    mutationFn: submitPromotion,
    onSuccess: (promotion) => {
      queryClient.setQueryData(["model-promotion-latest"], promotion);
    },
  });
  const summary = query.data?.data.candidate_summary;
  const rows = query.data?.data.rows ?? [];
  const passedRows = rows.filter((row) => row.gate_passed === true);
  const failedRows = rows.filter((row) => row.gate_passed !== true);
  const passed = summary?.passed_tasks ?? passedRows.length;
  const total = summary?.total_tasks ?? (rows.length || 17);
  const progress = Math.round((passed / Math.max(total, 1)) * 100);
  const operation = operationQuery.data;
  const operationActive = ACTIVE_OPERATION_STATUSES.has(operation?.status ?? "");
  const operationFailed = operation?.status === "failed";
  const promotionOperation = promotionQuery.data;
  const promotionActive = ACTIVE_OPERATION_STATUSES.has(
    promotionOperation?.status ?? "",
  );
  const forecastReady = snapshot.prediction_readiness === "forecast_ready";
  const executionReady = snapshot.execution_readiness === "ready";
  const workerReady = workerQuery.data?.state === "ready";
  const promotionWorkerReady = promotionWorkerQuery.data?.state === "ready";
  const readiness = readinessQuery.data;
  const candidateAction = readiness?.candidate_training_action;
  const nextRecipeVersion = readiness?.next_candidate_recipe_version;
  const nextGeneration = nextRecipeVersion === "candidate-recipe-v2" ? 2 : 1;
  const trainingReady =
    readiness?.can_submit_candidate_training === true &&
    candidateAction === "available" &&
    nextRecipeVersion != null;
  const promotionReady = readiness?.can_submit_promotion === true;
  const anchorProgress = readiness
    ? Math.round(
        (readiness.anchor_sessions_collected / readiness.required_anchor_sessions) *
          100,
      )
    : 0;
  const labelProgress = readiness
    ? Math.round(
        (readiness.fully_labeled_sessions / readiness.required_fully_labeled_sessions) *
          100,
      )
    : 0;
  useEffect(() => {
    if (
      operation?.status !== "succeeded_data_only" &&
      operation?.status !== "succeeded_forecast_ready" &&
      operation?.status !== "failed"
    ) {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["model-training-readiness"] });
    void queryClient.invalidateQueries({
      queryKey: ["formal-prediction-status", snapshot.snapshot_id],
    });
  }, [operation?.status, queryClient, snapshot.snapshot_id]);
  const submissionMessage =
    mutation.data?.submission_disposition === "created"
      ? operation?.status === "succeeded_data_only" ||
        operation?.status === "succeeded_forecast_ready"
        ? translate(locale, "common.shared.trainingRoundCompleted", {
            candidate_generation: String(mutation.data.candidate_generation ?? 2),
            passed: String(passed),
            total: String(total),
          })
        : operation?.status === "failed"
          ? translate(locale, "common.shared.trainingRoundFailed", {
              candidate_generation: String(mutation.data.candidate_generation ?? 2),
            })
          : translate(locale, "common.shared.trainingRoundStarted", {
              nextGeneration: String(
                mutation.data.candidate_generation ?? nextGeneration,
              ),
            })
      : mutation.data?.submission_disposition === "reused_active"
        ? translate(locale, "common.shared.connectedToRunningTraining")
        : mutation.data?.submission_disposition === "reused_completed"
          ? translate(
              locale,
              "common.shared.thisRecipeHasCompletedNoDuplicateRunStarted",
            )
          : null;
  return (
    <section
      aria-label={translate(locale, "common.shared.formalForecastPublicationStatus")}
      className="formal-prediction-status"
      data-ready={forecastReady}
      role="status"
    >
      <header>
        {forecastReady ? (
          <CircleCheck aria-hidden="true" size={22} strokeWidth={1.5} />
        ) : (
          <CircleSlash2 aria-hidden="true" size={22} strokeWidth={1.5} />
        )}
        <div>
          <span>
            {"" +
              (translate(
                locale,
                "common.shared.formalForecastGatesCompleteChampionFromTheSame",
              ) +
                "")}
          </span>
          <h2>
            {forecastReady
              ? translate(locale, "common.shared.formalForecastsPublished")
              : translate(locale, "common.shared.formalForecastsNotYetPublished")}
          </h2>
          <p>
            {snapshot.activation_basis === "manual_override"
              ? translate(locale, "common.manualPublished", { passed, total })
              : forecastReady
                ? executionReady
                  ? translate(
                      locale,
                      "common.shared.all17ForecastTasksPassedTheSameBatch",
                    )
                  : translate(
                      locale,
                      "common.shared.all17ForecastTasksPassedAndWerePublished",
                    )
                : translate(
                    locale,
                    "common.shared.verifiedResearchRemainsAvailableWithoutBeingLabeledFormal",
                  )}
          </p>
        </div>
        <strong>
          {query.isPending
            ? translate(locale, "common.shared.loading")
            : translate(locale, "common.shared.latestDevelopment", {
                passed: String(passed),
                total: String(total),
              })}
        </strong>
      </header>

      <div
        aria-label={translate(locale, "common.shared.formalForecastGateProgress", {
          progress: String(progress),
        })}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={progress}
        className="formal-prediction-status__progress"
        role="progressbar"
      >
        <span style={{ width: `${progress}%` }} />
      </div>

      <div className="formal-prediction-status__actions">
        <div>
          <strong>
            {operationActive
              ? translate(locale, "common.shared.candidateModelTraining", {
                  operation: String(operation?.progress_percent ?? 0),
                })
              : operationFailed
                ? translate(locale, "common.shared.trainingRoundFailed2", {
                    operation: String(
                      operation?.candidate_generation ?? nextGeneration,
                    ),
                  })
                : candidateAction === "exhausted"
                  ? translate(
                      locale,
                      "common.shared.round2CompletedCurrentRecipeExhausted",
                    )
                  : candidateAction === "active"
                    ? translate(
                        locale,
                        "common.shared.candidateTrainingSubmittedSynchronizingOperationStatus",
                      )
                    : candidateAction === "frozen"
                      ? translate(
                          locale,
                          "common.shared.candidateFrozenAwaitingFormalPromotionBlindEvaluation",
                        )
                      : readiness?.latest_candidate_recipe_version
                        ? translate(locale, "common.shared.round1Completed", {
                            candidate_passed_tasks: String(
                              readiness.candidate_passed_tasks,
                            ),
                            candidate_total_tasks: String(
                              readiness.candidate_total_tasks,
                            ),
                          })
                        : translate(
                            locale,
                            "common.shared.candidateTrainingAvailableNow",
                          )}
          </strong>
          <span>
            {operationActive
              ? translate(locale, "common.shared.operationStartedWorker", {
                  operation: String(
                    trainingDetail(operation?.phases.at(-1)?.detail, locale),
                  ),
                  operation2: String(
                    operation?.operation_id ??
                      translate(locale, "common.shared.loading"),
                  ),
                  operation3: String(
                    operation?.candidate_recipe_version ??
                      translate(locale, "common.shared.loadingRecipe"),
                  ),
                  created_at: String(
                    operation
                      ? formatUsMarketTime(
                          operation.started_at ?? operation.created_at,
                          locale,
                        )
                      : translate(locale, "common.shared.loading"),
                  ),
                  data: String(
                    workerReady && workerQuery.data?.last_heartbeat
                      ? translate(locale, "common.shared.workerHeartbeat", {
                          last_heartbeat: String(
                            formatUsMarketTime(workerQuery.data.last_heartbeat, locale),
                          ),
                        })
                      : translate(locale, "common.shared.workerHeartbeatUnavailable"),
                  ),
                })
              : operationFailed
                ? translate(
                    locale,
                    "common.shared.repairTheCauseBeforeRetryingTheSameFinite",
                    {
                      operation: String(
                        operation?.error_code ??
                          translate(locale, "common.shared.trainingFailed"),
                      ),
                    },
                  )
                : candidateAction === "exhausted"
                  ? translate(locale, "common.shared.round2OfTheFiniteRecipeIsComplete")
                  : candidateAction === "active"
                    ? translate(
                        locale,
                        "common.shared.aCandidateTrainingOperationIsAlreadyQueuedOr",
                      )
                    : candidateAction === "frozen"
                      ? translate(
                          locale,
                          "common.shared.candidateIsFrozenAndWillNotBeOverwritten",
                          {
                            readiness: String(
                              readiness?.frozen_candidate_version ?? "",
                            ),
                          },
                        )
                      : nextRecipeVersion === "candidate-recipe-v2"
                        ? translate(
                            locale,
                            "common.shared.round2OptimizationIsAvailableRetrainAll17",
                          )
                        : translate(
                            locale,
                            "common.shared.developmentTrainingUsesDataThroughJune292026",
                          )}
          </span>
        </div>
        <button
          disabled={
            !workerReady || operationActive || mutation.isPending || !trainingReady
          }
          onClick={() => {
            if (nextRecipeVersion) mutation.mutate(nextRecipeVersion);
          }}
          type="button"
        >
          {operationActive || mutation.isPending ? (
            <RefreshCw aria-hidden="true" size={15} />
          ) : (
            <Play aria-hidden="true" size={15} />
          )}
          {operationActive
            ? translate(locale, "common.shared.trainingInProgress")
            : candidateAction === "exhausted"
              ? translate(locale, "common.shared.currentRecipeTrainingCompleted")
              : candidateAction === "active"
                ? translate(locale, "common.shared.trainingInProgress")
                : candidateAction === "frozen"
                  ? translate(locale, "common.shared.candidateFrozen")
                  : translate(locale, "common.shared.startCandidateOptimizationRound", {
                      nextGeneration: String(nextGeneration),
                    })}
        </button>
      </div>
      {operationFailed ? (
        <OriginalDetails value={operation?.diagnostic_detail} />
      ) : null}
      {submissionMessage ? (
        <p className="formal-prediction-status__submission">
          {submissionMessage}
          {mutation.data
            ? translate(locale, "common.shared.operation", {
                operation_id: String(mutation.data.operation_id),
                candidate_recipe_version: String(
                  mutation.data.candidate_recipe_version ?? "",
                ),
              })
            : ""}
        </p>
      ) : null}
      {mutation.isError ? (
        <div className="formal-prediction-status__error">
          {" " + (translate(locale, "common.shared.trainingSubmissionFailed") + "")}
          {translate(locale, "common.requestFailed")}
          <OriginalDetails value={mutation.error} />
        </div>
      ) : null}

      {readiness ? (
        <section
          aria-label={translate(locale, "common.shared.nextFormalPromotionProgress")}
          className="formal-prediction-status__next-window"
        >
          <header>
            <div>
              <strong>
                {"" +
                  (translate(
                    locale,
                    "common.shared.formalPromotionBlindEvaluationAwaitingIndependentData",
                  ) +
                    "")}
              </strong>
              <span>
                {" " +
                  (translate(locale, "common.shared.independentOutOfSampleWindow") +
                    " ")}
                {readiness.window_start}—{readiness.window_end}
              </span>
            </div>
            <div className="formal-prediction-status__promotion-control">
              <b>
                {promotionReady
                  ? translate(locale, "common.shared.readyForBlindEvaluation")
                  : translate(locale, "common.shared.collectingAutomatically")}
              </b>
              <button
                disabled={
                  !promotionWorkerReady ||
                  !promotionReady ||
                  promotionActive ||
                  promotionMutation.isPending
                }
                onClick={() => promotionMutation.mutate()}
                type="button"
              >
                {promotionActive
                  ? translate(locale, "common.shared.blindEvaluationInProgress")
                  : translate(
                      locale,
                      "common.shared.startFormalPromotionBlindEvaluation",
                    )}
              </button>
            </div>
          </header>
          <div className="formal-prediction-status__evidence-progress">
            <div>
              <span>
                {" " + (translate(locale, "common.shared.tradingDaysInWindow") + " ")}
                <strong>
                  {readiness.anchor_sessions_collected}/
                  {readiness.required_anchor_sessions}
                </strong>
              </span>
              <progress max={100} value={anchorProgress} />
            </div>
            <div>
              <span>
                {" " + (translate(locale, "common.shared.complete20DayLabels") + " ")}
                <strong>
                  {readiness.fully_labeled_sessions}/
                  {readiness.required_fully_labeled_sessions}
                </strong>
              </span>
              <progress max={100} value={labelProgress} />
            </div>
          </div>
          <p>
            {" " + (translate(locale, "common.shared.expectedEvidenceCompletion") + "")}
            {readiness.label_complete_through}
            {" " +
              (translate(
                locale,
                "common.shared.afterCloseEarliestAutomaticBlindEvaluation",
              ) +
                " ")}
            {formatUsMarketTime(readiness.automatic_training_at, locale)}
            {"" +
              (translate(
                locale,
                "common.shared.blindEvaluationLoadsOnlyTheFrozenCandidateWithout",
              ) +
                " ")}
          </p>
          {promotionMutation.isError ? (
            <div className="formal-prediction-status__error">
              {" " +
                (translate(
                  locale,
                  "common.shared.promotionBlindEvaluationSubmissionFailed",
                ) +
                  "")}
              {translate(locale, "common.requestFailed")}
              <OriginalDetails value={promotionMutation.error} />
            </div>
          ) : null}
        </section>
      ) : null}

      <ol
        aria-label={translate(locale, "common.shared.formalForecastTrainingStages")}
        className="formal-prediction-status__phases"
      >
        {TRAINING_PHASES(locale).map(([phase, label]) => {
          const state = phaseState(phase, operation, summary, snapshot);
          return (
            <li data-state={state} key={phase}>
              {state === "completed" ? (
                <CircleCheck aria-hidden="true" size={15} />
              ) : state === "running" ? (
                <RefreshCw aria-hidden="true" size={15} />
              ) : state === "failed" ? (
                <CircleSlash2 aria-hidden="true" size={15} />
              ) : (
                <CircleDashed aria-hidden="true" size={15} />
              )}
              <span>{label}</span>
            </li>
          );
        })}
      </ol>

      <dl className="formal-prediction-status__availability">
        <div>
          <dt>{"" + (translate(locale, "common.shared.expectedOpeningDate") + "")}</dt>
          <dd>
            {readiness
              ? translate(
                  locale,
                  "common.shared.candidateTrainingIsAvailableTodayFormalPublicationRequires",
                  { label_complete_through: String(readiness.label_complete_through) },
                )
              : translate(
                  locale,
                  "common.shared.loadingTheNextOutOfSampleEvidenceSchedule",
                )}
          </dd>
        </div>
        <div>
          <dt>{"" + (translate(locale, "common.shared.trainingTriggers") + "")}</dt>
          <dd>
            {"" +
              (translate(
                locale,
                "common.shared.candidateTrainingCanBeTriggeredManuallyNowBlind",
              ) +
                "")}
          </dd>
        </div>
        <div>
          <dt>
            {"" + (translate(locale, "common.shared.publicationAndTrading") + "")}
          </dt>
          <dd>
            {"" +
              (translate(
                locale,
                "common.shared.1717PassesPublishForecastsAutomaticallySeparateTrading",
              ) +
                "")}
          </dd>
        </div>
      </dl>

      {query.isPending ? (
        <div className="formal-prediction-status__loading">
          <RefreshCw aria-hidden="true" size={16} />
          {" " + (translate(locale, "common.shared.loadingTaskGates") + " ")}
        </div>
      ) : query.isError ? (
        <p className="formal-prediction-status__error">
          {" " +
            (translate(
              locale,
              "common.shared.gateDetailsAreUnavailableFormalForecastsRemainIn",
            ) +
              " ")}
        </p>
      ) : (
        <div className="formal-prediction-status__columns">
          <section>
            <h3>
              {"" + (translate(locale, "common.shared.forecastGatesPassed") + "")}
              {passedRows.length}{locale === "en-US" ? ")" : "）"}
            </h3>
            <ul>
              {passedRows.map((row) => (
                <li key={`passed-${row.task}-${row.horizon}`}>
                  <CircleCheck aria-hidden="true" size={15} />
                  {taskName(row, locale)}
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h3>
              {"" + (translate(locale, "common.shared.forecastGatesNotPassed") + "")}
              {failedRows.length}{locale === "en-US" ? ")" : "）"}
            </h3>
            <ul>
              {failedRows.map((row) => (
                <li key={`failed-${row.task}-${row.horizon}`}>
                  {taskName(row, locale)}
                  <small>
                    {[
                      ...(row.failure_codes ?? []).map((code) =>
                        failureLabel(code, locale),
                      ),
                      row.unavailable_reason == null
                        ? translate(
                            locale,
                            "common.shared.candidateModelDidNotPassGates",
                          )
                        : reasonLabel(row.unavailable_reason, locale),
                      gateDetail(row, locale),
                    ]
                      .filter(Boolean)
                      .join("; ")}
                  </small>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      <footer>
        <span>
          {" " + (translate(locale, "common.shared.latestEvaluation") + " ")}
          {summary == null
            ? translate(locale, "common.shared.noEvaluationRecord")
            : formatUsMarketTime(summary.generated_at, locale)}
          {summary?.failure_codes?.length
            ? `; ${summary.failure_codes.map((code) => failureLabel(code, locale)).join("; ")}`
            : ""}
        </span>
        <Link to="/backtests">
          {"" + (translate(locale, "common.shared.viewBacktestsAndGateDetails") + "")}
        </Link>
      </footer>
    </section>
  );
}
