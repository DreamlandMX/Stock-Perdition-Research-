import { translate, type Locale } from "../../i18n/core";
import { useCallback, useEffect, useState } from "react";
import { createResearchApiClient, ResearchApiError } from "../../api/client";
import type { components } from "../../api/generated/schema";
import {
  EOD_OPERATION_EVENT,
  EOD_UPDATE_SETTLED_EVENT,
  rememberManualEodUpdate,
} from "../../app/snapshot-refresh";
export type UpdateOperation = components["schemas"]["UpdateOperation"];
export type EodWorkerStatus = components["schemas"]["EodWorkerStatus"];
const apiClient = createResearchApiClient();
function publishOperation(operation: UpdateOperation | null) {
  window.dispatchEvent(
    new CustomEvent<UpdateOperation | null>(EOD_OPERATION_EVENT, {
      detail: operation,
    }),
  );
}
function isActive(operation: UpdateOperation | null | undefined) {
  return operation?.status === "queued" || operation?.status === "running";
}
async function loadLatestOperation(): Promise<UpdateOperation | null> {
  try {
    const { data } = await apiClient.get("/api/v1/operations/eod-update/latest");
    return data ?? null;
  } catch (error) {
    if (error instanceof ResearchApiError && error.status === 404) return null;
    throw error;
  }
}
async function loadWorkerStatus(): Promise<EodWorkerStatus> {
  const { data } = await apiClient.get("/api/v1/operations/eod-update/worker");
  if (data === undefined) throw new Error("EOD worker status unavailable");
  return data;
}
async function submitOperation(): Promise<UpdateOperation> {
  const { data } = await apiClient.post("/api/v1/operations/eod-update");
  if (data === undefined) throw new Error("EOD update response unavailable");
  return data;
}
export function useEodUpdate() {
  const [operation, setOperation] = useState<UpdateOperation | null>(null);
  const [worker, setWorker] = useState<EodWorkerStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const [latest, workerStatus] = await Promise.all([
        loadLatestOperation(),
        loadWorkerStatus(),
      ]);
      setOperation(latest);
      setWorker(workerStatus);
      publishOperation(latest);
      setError(null);
    } catch (cause) {
      setError(cause);
    } finally {
      setIsLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    const synchronize = (event: Event) => {
      setOperation((event as CustomEvent<UpdateOperation | null>).detail);
    };
    window.addEventListener(EOD_OPERATION_EVENT, synchronize);
    return () => window.removeEventListener(EOD_OPERATION_EVENT, synchronize);
  }, []);
  const active = isActive(operation);
  useEffect(() => {
    const interval = active ? 2000 : worker?.state === "ready" ? 30000 : 5000;
    const timer = window.setInterval(() => void refresh(), interval);
    return () => window.clearInterval(timer);
  }, [active, refresh, worker?.state]);
  useEffect(() => {
    if (
      operation?.status === "succeeded_data_only" ||
      operation?.status === "succeeded_forecast_ready" ||
      operation?.status === "failed"
    ) {
      window.dispatchEvent(
        new CustomEvent<UpdateOperation>(EOD_UPDATE_SETTLED_EVENT, {
          detail: operation,
        }),
      );
    }
  }, [operation?.status, operation?.updated_at]);
  const submit = useCallback(() => {
    const submittedFromSearch = window.location.search;
    setSubmitting(true);
    void submitOperation()
      .then((value) => {
        rememberManualEodUpdate(value.operation_id, submittedFromSearch);
        setOperation(value);
        publishOperation(value);
        setError(null);
      })
      .catch(setError)
      .finally(() => setSubmitting(false));
  }, []);
  return {
    error,
    isActive: active,
    isLoading,
    operation,
    submit,
    submitting,
    worker,
  };
}
const stageLabels = (
  locale: Locale,
): Record<components["schemas"]["UpdateOperationStage"], string> => ({
  runtime_preflight: translate(locale, "operations.cudaRuntimeValidation"),
  opend_preflight: translate(locale, "operations.connectToOpend"),
  incremental_collection: translate(locale, "operations.incrementalCollection"),
  data_quality: translate(locale, "operations.dataQualityChecks"),
  feature_build: translate(locale, "operations.featureAndLabelBuild"),
  market_snapshot_publication: translate(
    locale,
    "operations.marketSnapshotPublication",
  ),
  model_training: translate(locale, "operations.modelTrainingAndCalibration"),
  promotion_gate: translate(locale, "operations.outOfSampleOosAndCostGates"),
  champion_switch: translate(locale, "operations.championModelSwitch"),
  batch_inference: translate(locale, "operations.batchInference"),
  final_snapshot_publication: translate(locale, "operations.finalSnapshotPublication"),
  data_repair: translate(locale, "operations.adjustedDataRepair"),
  label_health: translate(locale, "operations.labelHealthChecks"),
  candidate_search: translate(locale, "operations.challengerSearch"),
  oos_validation: translate(locale, "operations.developmentOutOfSampleValidation"),
  promotion_window: translate(locale, "operations.oneTimePromotionWindow"),
  champion_publication: translate(locale, "operations.productionModelPublication"),
  execution_gate: translate(locale, "operations.independentExecutionGate"),
});
export const EOD_UPDATE_STAGES: readonly components["schemas"]["UpdateOperationStage"][] =
  [
    "opend_preflight",
    "incremental_collection",
    "data_quality",
    "feature_build",
    "market_snapshot_publication",
    "model_training",
    "promotion_gate",
    "champion_switch",
    "batch_inference",
    "final_snapshot_publication",
  ];

export const UPDATE_STAGE_LABELS = stageLabels("zh-CN");
export function updateStageLabel(
  stage: components["schemas"]["UpdateOperationStage"],
  locale: Locale = "zh-CN",
) {
  return stageLabels(locale)[stage];
}
