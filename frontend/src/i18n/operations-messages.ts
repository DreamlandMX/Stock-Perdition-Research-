export const operationsMessages = {
  "operations.publication.progress": { zh: "{step}：{completed}/{total} 步", en: "{step}: {completed}/{total} steps" },
  "operations.publication.prepare": { zh: "验证快照和模型", en: "Verifying snapshot and models" },
  "operations.publication.formal": { zh: "正式模型推理", en: "Production model inference" },
  "operations.publication.verified": { zh: "已验证研究模型推理", en: "Verified research inference" },
  "operations.publication.candidate": { zh: "候选模型推理", en: "Candidate model inference" },
  "operations.publication.tables": { zh: "构建研究数据表", en: "Building research tables" },
  "operations.publication.materialize": { zh: "校验并写入快照", en: "Verifying and writing snapshot" },
  "operations.eodUpdateDidNotCompletePleaseRetry": {
    zh: "收盘更新未完成，请重试。",
    en: "EOD update did not complete. Please retry.",
  },
  "operations.updateWorkerDisconnected": {
    zh: "更新执行器未连接",
    en: "Update worker disconnected",
  },
  "operations.waitingForUpdate": {
    zh: "等待更新",
    en: "Waiting for update",
  },
  "operations.updateFailedRetry": {
    zh: "更新失败，重试",
    en: "Update failed; retry",
  },
  "operations.runEodUpdate": {
    zh: "一键收盘更新",
    en: "Run EOD update",
  },
  "operations.taskQueuedButTheBackgroundWorkerIsDisconnected": {
    zh: "任务已排队，但后台执行器未连接；请重新启动本地服务",
    en: "Task queued, but the background worker is disconnected. Restart local services.",
  },
  "operations.theBackgroundUpdateWorkerIsDisconnectedRestartLocal": {
    zh: "后台更新执行器未连接；请重新启动本地服务",
    en: "The background update worker is disconnected. Restart local services.",
  },
  "operations.incrementallyCollectDataBuildFeaturesApplyTrainingGates": {
    zh: "增量采集、构建特征、训练门禁并发布最新研究快照",
    en: "Incrementally collect data, build features, apply training gates and publish the latest research snapshot",
  },
  "operations.noCompleteDataYet": {
    zh: "尚无完整数据",
    en: "No complete data yet",
  },
  "operations.opendDailyBarsAreIncompleteRetryingAutomaticallyAttempt": {
    zh: "OpenD 日线尚未齐全，正在自动重试（第 {retryAttempt} 次）；当前完整至 {actual_cutoff}，目标 {target_session}。",
    en: "OpenD daily bars are incomplete. Retrying automatically (attempt {retryAttempt}); complete through {actual_cutoff}, target {target_session}.",
  },
  "operations.checkUpdateDetailsBeforeSubmittingAgain": {
    zh: "请检查更新详情后重新提交。",
    en: "Check update details before submitting again.",
  },
  "operations.updateIncompleteTargetCompleteThroughPreviousDataIs": {
    zh: "更新未完成：目标 {target_session}，当前完整至 {actual_cutoff}。旧数据已保留。{recommendation}",
    en: "Update incomplete: target {target_session}, complete through {actual_cutoff}. Previous data is retained. {recommendation}",
  },
  "operations.validationCompleteTheLatestSessionRemainsWeekendsAnd": {
    zh: "校验完成：当前最新交易日仍为 {actual_cutoff}；周末或休市日没有新增收盘数据，数值可能不变。",
    en: "Validation complete: the latest session remains {actual_cutoff}. Weekends and market holidays add no closing data, so values may be unchanged.",
  },
  "operations.updateCompleteMarketDataAdvancedFromToPrices": {
    zh: "更新完成：行情已从 {previous} 更新到 {actual_cutoff}，价格与研究预测已刷新。",
    en: "Update complete: market data advanced from {previous} to {actual_cutoff}. Prices and research forecasts have refreshed.",
  },
  "operations.eodUpdateResult": {
    zh: "收盘更新结果",
    en: "EOD update result",
  },
  "operations.viewUpdateDetails": {
    zh: "查看更新详情",
    en: "View update details",
  },
  "operations.dismissEodUpdateNotice": {
    zh: "关闭收盘更新提示",
    en: "Dismiss EOD update notice",
  },
  "operations.updateFailed": {
    zh: "更新失败",
    en: "Update failed",
  },
  "operations.queued": {
    zh: "等待执行",
    en: "Queued",
  },
  "operations.running": {
    zh: "正在执行",
    en: "Running",
  },
  "operations.dataUpdatedModelDidNotPassGates": {
    zh: "数据已更新，模型未通过门禁",
    en: "Data updated; model did not pass gates",
  },
  "operations.dataAndForecastsUpdated": {
    zh: "数据与预测已更新",
    en: "Data and forecasts updated",
  },
  "operations.storedDataHasNotReachedTheTargetSession": {
    zh: "实际落库日期尚未达到目标交易日",
    en: "Stored data has not reached the target session",
  },
  "operations.dailyBarsAreIncompleteForTheCurrentUniverse": {
    zh: "当前数据集的日线标的尚未全部完成",
    en: "Daily bars are incomplete for the current universe",
  },
  "operations.insufficientOpendHistoryQuotaForAddedSymbols": {
    zh: "新增标的所需的 OpenD 历史额度不足",
    en: "Insufficient OpenD history quota for added symbols",
  },
  "operations.opendDidNotReturnVerifiableHistoryQuotaDetails": {
    zh: "OpenD 未返回可核验的历史额度明细",
    en: "OpenD did not return verifiable history quota details",
  },
  "operations.datasetVersionAndUniverseBindingDoNotMatch": {
    zh: "数据集版本与标的池绑定不匹配",
    en: "Dataset version and universe binding do not match",
  },
  "operations.liveDataStorageIsLockedByAnotherProcess": {
    zh: "实时数据存储正被其他进程占用",
    en: "Live data storage is locked by another process",
  },
  "operations.anotherLiveDataTaskIsRunning": {
    zh: "另一项实时数据任务正在运行",
    en: "Another live data task is running",
  },
  "operations.opendPreflightFailed": {
    zh: "OpenD 预检未通过",
    en: "OpenD preflight failed",
  },
  "operations.theUpdatePipelineDidNotComplete": {
    zh: "更新流水线未能完成",
    en: "The update pipeline did not complete",
  },
  "operations.marketAndRuleSnapshotOnly": {
    zh: "仅行情与规则快照",
    en: "Market and rule snapshot only",
  },
  "operations.forecastSnapshotReady": {
    zh: "预测快照已就绪",
    en: "Forecast snapshot ready",
  },
  "operations.currentChampionRetained": {
    zh: "保留现有主模型",
    en: "Current Champion retained",
  },
  "operations.dataAndArtifactsUnchanged": {
    zh: "数据与制品没有变化",
    en: "Data and artifacts unchanged",
  },
  "operations.challengerPromoted": {
    zh: "候选模型已晋级",
    en: "Challenger promoted",
  },
  "operations.waiting": {
    zh: "等待",
    en: "Waiting",
  },
  "operations.wroteItemsIncrementalRequestsBlockedFinalCoverageDepends": {
    zh: "写入 {collection} 项；{collection2} 项增量请求受阻（最终覆盖由质量检查确认）",
    en: "Wrote {collection} items; {collection2} incremental requests blocked (final coverage depends on quality checks)",
  },
  "operations.backgroundWorkerHeartbeatLost": {
    zh: "后台执行器心跳已中断",
    en: "Background worker heartbeat lost",
  },
  "operations.backgroundWorkerDisconnected": {
    zh: "后台执行器未连接",
    en: "Background worker disconnected",
  },
  "operations.backgroundWorkerDisconnectedNoNewUpdateTaskWas": {
    zh: "后台执行器未连接，未创建新的更新任务。请重新启动本地服务。",
    en: "Background worker disconnected. No new update task was created. Restart local services.",
  },
  "operations.taskStatusIsTemporarilyUnavailable": {
    zh: "任务状态暂时无法读取。",
    en: "Task status is temporarily unavailable.",
  },
  "operations.incrementalMarketDataQualityChecksFeatureUpdatesInference": {
    zh: "增量行情、质量检查、特征更新、现有 Champion 推理与快照发布。",
    en: "Incremental market data, quality checks, feature updates, inference with the current Champion, and snapshot publication.",
  },
  "operations.targetSession": {
    zh: "目标交易日",
    en: "Target session",
  },
  "operations.storedThrough": {
    zh: "实际落库",
    en: "Stored through",
  },
  "operations.snapshotVersion": {
    zh: "快照版本",
    en: "Snapshot version",
  },
  "operations.modelVersion": {
    zh: "模型版本",
    en: "Model version",
  },
  "operations.automaticBackfillAttempt": {
    zh: "正在自动补采（第",
    en: "Automatic backfill (attempt",
  },
  "operations.completeThrough": {
    zh: "当前完整至",
    en: "Complete through",
  },
  "operations.target": {
    zh: "； 目标",
    en: "; Target",
  },
  "operations.nextRetry": {
    zh: "； 下次重试",
    en: "; Next retry",
  },
  "operations.taskQueuedForSecondsWithoutAWorkerRestarting": {
    zh: "任务已排队 {queuedSeconds} 秒，但没有执行器领取。重新启动本地服务后会自动继续。",
    en: "Task queued for {queuedSeconds} seconds without a worker. Restarting local services resumes it automatically.",
  },
  "operations.eodUpdatesAreUnavailableRestartLocalServicesTo": {
    zh: "一键更新暂不可提交；重新启动本地服务会恢复执行器。",
    en: "EOD updates are unavailable. Restart local services to restore the worker.",
  },
  "operations.restoringTheLatestUpdateTask": {
    zh: "正在恢复最近更新任务…",
    en: "Restoring the latest update task…",
  },
  "operations.noRunYetUseTheButtonAfterThe": {
    zh: "尚未运行。美股收盘且 OpenD 数据可用后点击按钮即可。",
    en: "No run yet. Use the button after the US market closes and OpenD data is available.",
  },
  "operations.queuedWaitingForTheBackgroundWorker": {
    zh: "已排队，等待后台执行器恢复",
    en: "Queued; waiting for the background worker",
  },
  "operations.unknownStatus": {
    zh: "状态未知",
    en: "Unknown status",
  },
  "operations.task": {
    zh: "任务",
    en: "Task",
  },
  "operations.stageFinished": {
    zh: " ； 阶段已结束",
    en: "; Stage finished",
  },
  "operations.aFinishedStageOnlyMeansItWasProcessed": {
    zh: "阶段结束仅表示流程已处理；门禁是否通过、是否跳过，请查看阶段说明与最终结果。",
    en: "A finished stage only means it was processed. Check stage details and the final result for gates and skipped work.",
  },
  "operations.failureReason": {
    zh: "失败原因：",
    en: "Failure reason:",
  },
  "operations.fixTheFailedStageBeforeSubmittingAgain": {
    zh: "请修复失败阶段后重新提交。",
    en: "Fix the failed stage before submitting again.",
  },
  "operations.cudaRuntimeValidation": {
    zh: "CUDA 运行环境验证",
    en: "CUDA runtime validation",
  },
  "operations.connectToOpend": {
    zh: "连接 OpenD",
    en: "Connect to OpenD",
  },
  "operations.incrementalCollection": {
    zh: "增量采集",
    en: "Incremental collection",
  },
  "operations.dataQualityChecks": {
    zh: "数据质量检查",
    en: "Data quality checks",
  },
  "operations.featureAndLabelBuild": {
    zh: "特征与标签构建",
    en: "Feature and label build",
  },
  "operations.marketSnapshotPublication": {
    zh: "行情快照发布",
    en: "Market snapshot publication",
  },
  "operations.modelTrainingAndCalibration": {
    zh: "模型训练与校准",
    en: "Model training and calibration",
  },
  "operations.outOfSampleOosAndCostGates": {
    zh: "样本外（OOS）与成本门禁",
    en: "Out-of-sample (OOS) and cost gates",
  },
  "operations.championModelSwitch": {
    zh: "主模型（Champion）切换",
    en: "Champion model switch",
  },
  "operations.batchInference": {
    zh: "批量推理",
    en: "Batch inference",
  },
  "operations.finalSnapshotPublication": {
    zh: "最终快照发布",
    en: "Final snapshot publication",
  },
  "operations.adjustedDataRepair": {
    zh: "复权数据修复",
    en: "Adjusted data repair",
  },
  "operations.labelHealthChecks": {
    zh: "标签健康检查",
    en: "Label health checks",
  },
  "operations.challengerSearch": {
    zh: "候选模型搜索",
    en: "Challenger search",
  },
  "operations.developmentOutOfSampleValidation": {
    zh: "开发期样本外验证",
    en: "Development out-of-sample validation",
  },
  "operations.oneTimePromotionWindow": {
    zh: "一次性晋升窗口",
    en: "One-time promotion window",
  },
  "operations.productionModelPublication": {
    zh: "正式模型发布",
    en: "Production model publication",
  },
  "operations.independentExecutionGate": {
    zh: "独立交易门禁",
    en: "Independent execution gate",
  },
  "operations.originalDetails": {
    zh: "原始详情",
    en: "Original details",
  },
  "operations.phaseDetails": {
    zh: "阶段结果请查看原始详情。",
    en: "See original details for the stage result.",
  },
  "operations.retryAttempt": {
    zh: "正在自动补采（第 {attempt} 次）",
    en: "Automatic backfill (attempt {attempt})",
  },
  "operations.retryStatus": {
    zh: "当前完整至 {cutoff}；目标 {target}；下次重试 {time}",
    en: "Complete through {cutoff}; target {target}; next retry {time}",
  },
  "operations.dataOnlyNotice": {
    zh: "行情已从 {previous} 更新到 {cutoff}；模型未通过门禁，尚未发布新的正式预测。",
    en: "Market data advanced from {previous} to {cutoff}. The model did not pass its gates; no new production forecasts were published.",
  },
} as const;
