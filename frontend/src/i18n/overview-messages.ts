export const overviewMessages = {
  "overview.optionsVerdict.spyDown": {
    zh: "SPY 跌超 5% 的概率 {probability}。",
    en: "SPY probability of falling more than 5%: {probability}.",
  },
  "overview.optionsVerdict.spyUp": {
    zh: "涨超 5% 的概率 {probability}。",
    en: "Probability of rising more than 5%: {probability}.",
  },
  "overview.optionsVerdict.tailRatio": {
    zh: "下跌概率约为上涨的 {ratio} 倍。",
    en: "The downside probability is about {ratio} times the upside probability.",
  },
  "overview.optionsVerdict.zeroUpside": {
    zh: "上涨尾概率为零，不计算倍数。",
    en: "Upside tail probability is zero, so no ratio is calculated.",
  },
  "overview.optionsVerdict.callPremium": {
    zh: "价外 Call 估算成交额 {amount}。",
    en: "Estimated OTM Call turnover: {amount}.",
  },
  "overview.optionsVerdict.putPremium": {
    zh: "价外 Put 估算成交额 {amount}。",
    en: "Estimated OTM Put turnover: {amount}.",
  },
  "overview.optionsVerdict.otmCallShare": {
    zh: "{symbols} 的价外资金至少 {share} 进入 Call。",
    en: "At least {share} of OTM premium in {symbols} is in Calls.",
  },
  "overview.optionsVerdict.putCallAboveOne": {
    zh: "Put/Call > 1：{symbols}。",
    en: "Put/Call > 1: {symbols}.",
  },
  "overview.optionsVerdict.largestSkew": {
    zh: "偏斜最大：{symbols}，资金进入保护型合约。",
    en: "Largest skew: {symbols}; premium flows to protective contracts.",
  },
  "overview.optionsVerdict.highestIv": {
    zh: "IV 最高：{symbols}。",
    en: "Highest IV: {symbols}.",
  },
  "overview.optionsVerdict.invertedTerms": {
    zh: "{count} 个标的期限倒挂。",
    en: "Instruments with inverted term structures: {count}.",
  },
  "overview.optionsVerdict.cheaperOptions": {
    zh: "{symbols} 的期权偏便宜。",
    en: "Relatively cheaper options: {symbols}.",
  },
  "overview.optionsVerdict.marketCallPremium": {
    zh: "全市场 {share} 的估算成交额在 Call，成交额最大的是 {symbols}。",
    en: "Calls account for {share} of market-wide estimated turnover; the largest turnover is in {symbols}.",
  },
  "overview.ranking.upwardBreakout": {
    zh: "规则型向上突破优先",
    en: "Rule-based upward breakouts first",
  },
  "overview.ranking.relativeStrength": {
    zh: "趋势和20日相对强弱排序",
    en: "Ranked by trend and 20-day relative strength",
  },
  "overview.ranking.lowerReturnScenario": {
    zh: "收益q10（偏弱收益情景）优先",
    en: "q10 returns (weak return scenario) first",
  },
  "overview.ranking.medianReturnScenario": {
    zh: "收益q50（中位收益情景）优先",
    en: "q50 returns (median return scenario) first",
  },
  "overview.ranking.unsupported": {
    zh: "该排序依据暂无可用说明，请查看原始详情。",
    en: "An explanation for this ranking basis is unavailable. See original details.",
  },
  "overview.insight.weakSector": {
    zh: "{breadth}；{sector}板块逆势走弱",
    en: "{breadth}; the {sector} sector is weakening against the trend",
  },
  "overview.insight.weakSectorDespiteBreadth": {
    zh: "{breadth}，但{sector}板块逆势走弱",
    en: "{breadth}, but the {sector} sector is weakening against the trend",
  },
  "overview.brief.marketContext": {
    zh: "{insight}。{facts}",
    en: "{insight}. {facts}",
  },
  "overview.brief.sectorDrivers": {
    zh: "{leaders}；20 日正收益宽度 {breadth}%，较上次 {change}",
    en: "{leaders}; 20-day positive-return breadth {breadth}%; change from previous {change}.",
  },
  "overview.brief.unusualMoves": {
    zh: "{up} 个向上突破、{down} 个向下突破、{volume} 个量能异常；{regime} 个状态切换",
    en: "{up} upward breakouts, {down} downward breakouts, {volume} volume anomalies; {regime} regime changes.",
  },
  "overview.manualScenarios": {
    zh: "人工启用情景（不可执行）",
    en: "Manually activated scenarios (not executable)",
  },
  "overview.blockedScenarios": {
    zh: "已发布模型情景（不可执行）",
    en: "Published model scenarios (not executable)",
  },
  "overview.executionBlocked": { zh: "执行已阻断", en: "Execution blocked" },
  "overview.scenarioEvidenceRanking": {
    zh: "模型情景；不可执行证据排序",
    en: "Model scenarios; non-executable evidence ranking",
  },
  "overview.noPublishedScenarios": {
    zh: "当前快照没有已发布模型情景。",
    en: "No model scenarios have been published for this snapshot.",
  },
  "overview.scenarioUpProbability": {
    zh: "情景上涨概率",
    en: "Scenario up probability",
  },
  "overview.scenarioQ10": { zh: "情景 q10 下行", en: "Scenario q10 downside" },
  "overview.scenarioQ50": { zh: "情景 q50 中位数", en: "Scenario q50 median" },
  "overview.brief.blockedReadiness": {
    zh: "模型情景 {scenarios}；研究排序 {research}；模型任务覆盖 {coverage}，非准确率。执行已阻断。",
    en: "Model scenarios {scenarios}; research ranking {research}; model task coverage {coverage}, not accuracy. Execution blocked.",
  },
  "overview.rawPriceJumps": {
    zh: "原始价格跳变（{count}）",
    en: "Raw price jumps ({count})",
  },
  "overview.status.ready": { zh: "已就绪", en: "Ready" },
  "overview.status.degraded": { zh: "已降级", en: "Degraded" },
  "overview.status.blocked": { zh: "已阻断", en: "Blocked" },
  "overview.status.dataOnly": { zh: "仅研究数据", en: "Research data only" },
  "overview.status.forecastReady": { zh: "预测已就绪", en: "Forecast ready" },
  "overview.AnomalyWorkbench.insufficientHistoryToCompareCandidateChanges": {
    zh: "历史不足，无法比较候选变化。",
    en: "Insufficient history to compare candidate changes.",
  },
  "overview.AnomalyWorkbench.theCandidateSetIsUnchanged": {
    zh: "候选集合未发生变化。",
    en: "The candidate set is unchanged.",
  },
  "overview.OverviewHistoryPanels.newEntries": {
    zh: "新进入",
    en: "New entries",
  },
  "overview.AnomalyWorkbench.exited": {
    zh: "已退出",
    en: "Exited",
  },
  "overview.AnomalyWorkbench.rankUnchanged": {
    zh: "排名持平",
    en: "Rank unchanged",
  },
  "overview.AnomalyWorkbench.rank": {
    zh: "排名 {value}{value2}",
    en: "Rank {value}{value2}",
  },
  "overview.AnomalyWorkbench.breakout": {
    zh: "突破",
    en: "Breakout",
  },
  "overview.OverviewHistoryPanels.volume": {
    zh: "量能",
    en: "Volume",
  },
  "overview.AnomalyWorkbench.transition": {
    zh: "切换",
    en: "Transition",
  },
  "overview.AnomalyWorkbench.channelExtreme": {
    zh: "通道极值",
    en: "Channel extreme",
  },
  "overview.AnomalyWorkbench.highVolatility": {
    zh: "高波动",
    en: "High volatility",
  },
  "overview.OverviewHistoryPanels.unusualVolume": {
    zh: "量能异常",
    en: "Unusual volume",
  },
  "overview.AnomalyWorkbench.upperChannel": {
    zh: "上通道",
    en: "Upper channel",
  },
  "overview.AnomalyWorkbench.lowerChannel": {
    zh: "下通道",
    en: "Lower channel",
  },
  "overview.SectorMarketMap.regimeChange": {
    zh: "状态切换",
    en: "Regime change",
  },
  "overview.MarketStructureTerminal.current": {
    zh: "当前",
    en: "Current",
  },
  "overview.OverviewHistoryPanels.previousCompatibleSnapshot": {
    zh: "上个兼容快照",
    en: "Previous compatible snapshot",
  },
  "overview.AnomalyWorkbench.anomalyRadarAndEvidenceCoverage": {
    zh: "异常雷达与证据覆盖",
    en: "Anomaly radar and evidence coverage",
  },
  "overview.AnomalyWorkbench.researchPriority": {
    zh: "研究优先级",
    en: "Research priority",
  },
  "overview.AnomalyWorkbench.anomalyRadarConditionOverlapAndEvidenceCoverage": {
    zh: "异常雷达、条件交集与证据覆盖",
    en: "Anomaly radar, condition overlap and evidence coverage",
  },
  "overview.AnomalyWorkbench.multiFactorAnomaliesRankByBreakout2Volume2": {
    zh: "多因子异常按突破 2、量能 2、切换 1、通道极值 1、高波动 1 排序。",
    en: "Multi-factor anomalies rank by breakout 2, volume 2, transition 1, channel extreme 1 and high volatility 1.",
  },
  "overview.AnomalyWorkbench.eachAxisIsAPercentageOfAllInstrumentsMissing": {
    zh: "各轴均为占全体标的百分比；缺失比较值不解释为零。",
    en: "Each axis is a percentage of all instruments; missing comparisons do not mean zero.",
  },
  "overview.AnomalyWorkbench.viewAnomalyRadarData": {
    zh: "查看异常雷达数据",
    en: "View anomaly radar data",
  },
  "overview.AnomalyWorkbench.anomalyRadar": {
    zh: "异常雷达",
    en: "Anomaly radar",
  },
  "overview.AnomalyWorkbench.anomalyRadarData": {
    zh: "异常雷达数据",
    en: "Anomaly radar data",
  },
  "overview.AnomalyWorkbench.condition": {
    zh: "条件",
    en: "Condition",
  },
  "overview.OverviewHistoryPanels.previous": {
    zh: "上次",
    en: "Previous",
  },
  "overview.SectorHealthTerminal.missing": {
    zh: "缺失",
    en: "Missing",
  },
  "overview.SectorMarketMap.insufficientHistory": {
    zh: "历史不足",
    en: "Insufficient history",
  },
  "overview.AnomalyWorkbench.top10MultiFactorAnomalies": {
    zh: "多因子异常 Top 10",
    en: "Top 10 multi-factor anomalies",
  },
  "overview.AnomalyWorkbench.points": {
    zh: "分",
    en: "points",
  },
  "overview.AnomalyWorkbench.overlappingAnomalyConditions": {
    zh: "异常条件交集",
    en: "Overlapping anomaly conditions",
  },
  "overview.AnomalyWorkbench.noOverlappingConditionsCurrently": {
    zh: "当前没有多条件重叠。",
    en: "No overlapping conditions currently.",
  },
  "overview.AnomalyWorkbench.formalCoverageAndQualityGates": {
    zh: "正式覆盖与门禁",
    en: "Formal coverage and quality gates",
  },
  "overview.AnomalyWorkbench.formalModeExcludesCandidateRankingsCurrentRuleEvidence": {
    zh: "正式模式不展示候选排名。当前规则证据",
    en: "Formal mode excludes candidate rankings. Current rule evidence",
  },
  "overview.AnomalyWorkbench.comparable": {
    zh: "，可比较",
    en: ", comparable",
  },
  "overview.AnomalyWorkbench.experimentalCandidateChanges": {
    zh: "实验候选变化",
    en: "Experimental candidate changes",
  },
  "overview.AnomalyWorkbench.verifiedCandidateChanges": {
    zh: "已验证候选变化",
    en: "Verified candidate changes",
  },
  "overview.AnomalyWorkbench.evidenceCoverage": {
    zh: "证据覆盖",
    en: "Evidence coverage",
  },
  "overview.AnomalyWorkbench.coverageStatisticsUnavailable": {
    zh: "覆盖统计不可用。",
    en: "Coverage statistics unavailable.",
  },
  "overview.AnomalyWorkbench.allInstruments": {
    zh: "全量标的",
    en: "All instruments",
  },
  "overview.AnomalyWorkbench.comparable2": {
    zh: "可比较",
    en: "Comparable",
  },
  "overview.OpportunityTable.1520DayReturns": {
    zh: "1 / 5 / 20 日收益",
    en: "1 / 5 / 20-day returns",
  },
  "overview.AnomalyWorkbench.aTRChannelVolume": {
    zh: "ATR / 通道 / 量能",
    en: "ATR / channel / volume",
  },
  "overview.AnomalyWorkbench.intradayEvidence": {
    zh: "盘中证据",
    en: "Intraday evidence",
  },
  "overview.CoverageStatusPanel.dataAndModelCoverageStatus": {
    zh: "数据与模型覆盖状态",
    en: "Data and model coverage status",
  },
  "overview.CoverageStatusPanel.dataAndModelCoverage": {
    zh: "数据与模型覆盖",
    en: "Data and model coverage",
  },
  "overview.CoverageStatusPanel.dataCoverageModelTaskCoverageAndAccuracyAreDifferent": {
    zh: "数据覆盖、模型任务覆盖与准确率是三件不同的事；本页不展示准确率。",
    en: "Data coverage, model task coverage and accuracy are different measures. This page does not report accuracy.",
  },
  "overview.CoverageStatusPanel.viewModelsData": {
    zh: "查看模型数据页",
    en: "View Models & Data",
  },
  "overview.CoverageStatusPanel.optionsCollection": {
    zh: "期权采集：",
    en: "Options collection:",
  },
  "overview.CoverageStatusPanel.noAutomaticRunsRecorded": {
    zh: "尚无自动运行记录",
    en: "No automatic runs recorded",
  },
  "overview.CoverageStatusPanel.completeBatchesTradingDaysAccumulated": {
    zh: "{value} ； 完整批次 {completeSymbols}/{value2}；已积累 {historyDays} 个交易日",
    en: "{value}; complete batches {completeSymbols}/{value2}; {historyDays} trading days accumulated",
  },
  "overview.CoverageStatusPanel.loadingOrTemporarilyUnavailable": {
    zh: "正在读取或暂不可用",
    en: "Loading or temporarily unavailable",
  },
  "overview.CoverageStatusPanel.viewCollectionMatrix": {
    zh: "查看采集矩阵 →",
    en: "View collection matrix →",
  },
  "overview.CoverageStatusPanel.dataCoverage": {
    zh: "数据覆盖",
    en: "Data coverage",
  },
  "overview.CoverageStatusPanel.noCrossSectionalCoverageEvidenceInThisSnapshot": {
    zh: "当前快照没有横截面覆盖证据",
    en: "No cross-sectional coverage evidence in this snapshot",
  },
  "overview.OpportunityTable.ruleEvidence": {
    zh: "规则证据",
    en: "Rule evidence",
  },
  "overview.CoverageStatusPanel.sessionReturn": {
    zh: "当日收益",
    en: "Session return",
  },
  "overview.CoverageStatusPanel.5DayReturn": {
    zh: "5 日收益",
    en: "5-day return",
  },
  "overview.CoverageStatusPanel.20DayReturn": {
    zh: "20 日收益",
    en: "20-day return",
  },
  "overview.SectorMarketMap.volumeZ20": {
    zh: "量能 Z20",
    en: "Volume Z20",
  },
  "overview.OpportunityTable.intradayData": {
    zh: "盘中数据",
    en: "Intraday data",
  },
  "overview.OverviewHeaderBand.modelTaskCoverage": {
    zh: "模型任务覆盖",
    en: "Model task coverage",
  },
  "overview.OverviewHeaderBand.noEvaluationEvidence": {
    zh: "暂无评估证据",
    en: "No evaluation evidence",
  },
  "overview.CoverageStatusPanel.noChallengerEvaluationsRecordedTaskPassCountsAreUnavailable":
    {
      zh: "尚无候选模型评估记录；不显示任何任务通过数。",
      en: "No challenger evaluations recorded; task pass counts are unavailable.",
    },
  "overview.CoverageStatusPanel.allQualityGatesPassed": {
    zh: "全部门禁已通过",
    en: "All quality gates passed",
  },
  "overview.CoverageStatusPanel.gatesNotPassed": {
    zh: "未通过 {count} 项",
    en: "{count} gates not passed",
  },
  "overview.CoverageStatusPanel.tasksPassingTheQualityGatesThisIsCoverageNot": {
    zh: "已通过门禁的任务数；{value}。这是覆盖，不是准确率。",
    en: "Tasks passing the quality gates; {value}. This is coverage, not accuracy.",
  },
  "overview.CoverageStatusPanel.modelVersion": {
    zh: "模型版本",
    en: "Model version",
  },
  "overview.OverviewPage.noChampionModel": {
    zh: "暂无主模型（Champion）",
    en: "No Champion model",
  },
  "overview.CoverageStatusPanel.calibrationVersion": {
    zh: "校准版本",
    en: "Calibration version",
  },
  "overview.OverviewPage.notCalibrated": {
    zh: "尚未校准",
    en: "Not calibrated",
  },
  "overview.overviewInsights.formalForecastCoverage": {
    zh: "正式预测覆盖",
    en: "Formal forecast coverage",
  },
  "overview.CoverageStatusPanel.theCompleteChampionModelIsReady": {
    zh: "完整主模型已就绪。",
    en: "The complete Champion model is ready.",
  },
  "overview.CoverageStatusPanel.researchDataIsReadyFormalForecastsAwaitAComplete": {
    zh: "研究数据已就绪，正式预测等待完整主模型；研究结果不可执行。",
    en: "Research data is ready. Formal forecasts await a complete Champion model; research results are not executable.",
  },
  "overview.SectorRegimeMatrix.instruments": {
    zh: "个",
    en: "instruments",
  },
  "overview.CoverageStatusPanel.snapshotVersion": {
    zh: "快照版本",
    en: "Snapshot version",
  },
  "overview.OverviewPage.forecastBatch": {
    zh: "预测批次",
    en: "Forecast batch",
  },
  "overview.CoverageStatusPanel.datasetRevision": {
    zh: "数据修订",
    en: "Dataset revision",
  },
  "overview.CoverageStatusPanel.featureSet": {
    zh: "特征集",
    en: "Feature set",
  },
  "overview.CoverageStatusPanel.universe": {
    zh: "标的池",
    en: "Universe",
  },
  "overview.CoverageStatusPanel.inferenceTime": {
    zh: "本次推理耗时",
    en: "Inference time",
  },
  "overview.OverviewPage.seconds": {
    zh: "秒",
    en: "seconds",
  },
  "overview.EvidenceInspector.unresolvedCorporateAction": {
    zh: "公司行动未解决",
    en: "Unresolved corporate action",
  },
  "overview.EvidenceInspector.insufficientDataQuality": {
    zh: "数据质量不足",
    en: "Insufficient data quality",
  },
  "overview.EvidenceInspector.inconsistentFeatureArtifacts": {
    zh: "特征工件不一致",
    en: "Inconsistent feature artifacts",
  },
  "overview.EvidenceInspector.insufficientCalibrationSamples": {
    zh: "校准样本不足",
    en: "Insufficient calibration samples",
  },
  "overview.EvidenceInspector.insufficientSimilarCases": {
    zh: "相似案例不足",
    en: "Insufficient similar cases",
  },
  "overview.EvidenceInspector.intervalTooWide": {
    zh: "区间过宽",
    en: "Interval too wide",
  },
  "overview.EvidenceInspector.excessiveModelDisagreement": {
    zh: "模型分歧过大",
    en: "Excessive model disagreement",
  },
  "overview.EvidenceInspector.noChampionModelAvailable": {
    zh: "无可用冠军模型",
    en: "No Champion model available",
  },
  "overview.EvidenceInspector.outOfDistributionSample": {
    zh: "分布外样本",
    en: "Out-of-distribution sample",
  },
  "overview.EvidenceInspector.insufficientCalibrationQuality": {
    zh: "校准质量不足",
    en: "Insufficient calibration quality",
  },
  "overview.EvidenceInspector.inconsistentDataStructure": {
    zh: "数据结构不一致",
    en: "Inconsistent data structure",
  },
  "overview.EvidenceInspector.staleData": {
    zh: "数据过期",
    en: "Stale data",
  },
  "overview.EvidenceInspector.overviewEvidenceInspector": {
    zh: "总览证据检查器",
    en: "Overview evidence inspector",
  },
  "overview.EvidenceInspector.currentMarketAssessment": {
    zh: "当前市场判断",
    en: "Current market assessment",
  },
  "overview.EvidenceInspector.marketBreadth": {
    zh: "市场宽度",
    en: "Market breadth",
  },
  "overview.EvidenceInspector.instrumentsAboveTheShortTermMovingAverage": {
    zh: "标的位于短期均线之上",
    en: "Instruments above the short-term moving average",
  },
  "overview.EvidenceInspector.whyNoAssessmentYet": {
    zh: "为什么暂不判断",
    en: "Why no assessment yet",
  },
  "overview.EvidenceInspector.abstain": {
    zh: "暂不判断",
    en: "Abstain",
  },
  "overview.EvidenceInspector.noForecastRefusalReasonsCurrently": {
    zh: "当前没有拒绝预测原因",
    en: "No forecast refusal reasons currently",
  },
  "overview.EvidenceInspector.dataConfidence": {
    zh: "数据可信度",
    en: "Data confidence",
  },
  "overview.EvidenceInspector.determinedByFreshnessCompletenessAndPointInTimeEligibility":
    {
      zh: "由新鲜度、完整性与点时资格共同决定",
      en: "Determined by freshness, completeness and point-in-time eligibility",
    },
  "overview.RegimeDistribution.downtrend": {
    zh: "趋势偏空",
    en: "Downtrend",
  },
  "overview.RegimeDistribution.regimeUndetermined": {
    zh: "状态不可判定",
    en: "Regime undetermined",
  },
  "overview.RegimeDistribution.range": {
    zh: "震荡区间",
    en: "Range",
  },
  "overview.RegimeDistribution.directionUnconfirmed": {
    zh: "方向待确认",
    en: "Direction unconfirmed",
  },
  "overview.SectorMarketMap.uptrend": {
    zh: "趋势偏多",
    en: "Uptrend",
  },
  "overview.OverviewHistoryPanels.topGainers": {
    zh: "领涨",
    en: "Top gainers",
  },
  "overview.OverviewHistoryPanels.topLosers": {
    zh: "领跌",
    en: "Top losers",
  },
  "overview.OverviewPulse.ruleBreakout": {
    zh: "规则突破",
    en: "Rule breakout",
  },
  "overview.OverviewHistoryPanels.upwardBreakout": {
    zh: "向上突破",
    en: "Upward breakout",
  },
  "overview.OverviewHistoryPanels.downwardBreakout": {
    zh: "向下突破",
    en: "Downward breakout",
  },
  "overview.MarketMapTerminal.marketMapInstruments": {
    zh: "{count} 标的市场地图",
    en: "Market map: {count} instruments",
  },
  "overview.MarketMapTerminal.fullCrossSection": {
    zh: "全量横截面",
    en: "Full cross-section",
  },
  "overview.MarketMapTerminal.instrumentMarketMap": {
    zh: "标的市场地图",
    en: "Instrument market map",
  },
  "overview.MarketMapTerminal.equalAreaCellsDoNotRepresentMarketCapWeights": {
    zh: "等面积单元，不代表市值权重；颜色超出固定区间时仅截断着色。",
    en: "Equal-area cells do not represent market-cap weights. Colors are clipped at fixed limits.",
  },
  "overview.SectorMarketMap.mapReturnHorizon": {
    zh: "地图收益周期",
    en: "Map return horizon",
  },
  "overview.SectorMarketMap.dayReturn": {
    zh: "日收益",
    en: "-day return",
  },
  "overview.SectorRegimeMatrix.sector": {
    zh: "行业",
    en: "Sector",
  },
  "overview.MarketMapTerminal.allSectors": {
    zh: "全部行业",
    en: "All sectors",
  },
  "overview.MarketMapTerminal.fixedMapColorScale": {
    zh: "地图固定色域",
    en: "Fixed map color scale",
  },
  "overview.MarketMapTerminal.lossCap": {
    zh: "跌幅上限 −",
    en: "Loss cap −",
  },
  "overview.MarketMapTerminal.gainCap": {
    zh: "涨幅上限 +",
    en: "Gain cap +",
  },
  "overview.MarketMapTerminal.dayReturnAnomalyScoreOpenInstrumentResearch": {
    zh: "{symbol}，{value}，{horizon} 日收益 {returnValue}，异常分 {score}，进入研究页",
    en: "{symbol}, {value}, {horizon}-day return {returnValue}, anomaly score {score}, open Instrument Research",
  },
  "overview.SectorMarketMap.vol": {
    zh: "量",
    en: "Vol",
  },
  "overview.SectorMarketMap.break": {
    zh: "突↑",
    en: "Break↑",
  },
  "overview.SectorMarketMap.break2": {
    zh: "突↓",
    en: "Break↓",
  },
  "overview.SectorMarketMap.shift": {
    zh: "切",
    en: "Shift",
  },
  "overview.MarketMapTerminal.collapseToTheTop20Anomalies": {
    zh: "收起至异常优先 20 个",
    en: "Collapse to the top 20 anomalies",
  },
  "overview.MarketMapTerminal.showAllInstruments": {
    zh: "展开全部 {count} 个标的",
    en: "Show all {count} instruments",
  },
  "overview.MarketMapTerminal.fiveMarketFocusLists": {
    zh: "五类市场焦点",
    en: "Five market focus lists",
  },
  "overview.MarketMapTerminal.dayReturn": {
    zh: "日收益 ；",
    en: "-day return;",
  },
  "overview.MarketMapTerminal.samples": {
    zh: "个样本",
    en: "samples",
  },
  "overview.MarketMoversBoard.noComparableEvidenceCurrently": {
    zh: "当前无可比较证据",
    en: "No comparable evidence currently",
  },
  "overview.MarketMapTerminal.sectorsUseTheSelectedSnapshotSPublishedClassificationUnclassified":
    {
      zh: "行业沿用所选快照的发布分类；未分类",
      en: "Sectors use the selected snapshot's published classification; unclassified",
    },
  "overview.MarketMapTerminal.eTFsFormASeparateAssetGroupSectorsAreNot": {
    zh: "。ETF 单独列为资产组，不按公司名称推测行业。",
    en: ". ETFs form a separate asset group. Sectors are not inferred from company names.",
  },
  "overview.MarketMoversBoard.dayReturn": {
    zh: "{horizon} 日收益",
    en: "{horizon}-day return",
  },
  "overview.MarketMoversBoard.top5Gainers": {
    zh: "领涨 Top 5",
    en: "Top 5 gainers",
  },
  "overview.MarketMoversBoard.top5Losers": {
    zh: "领跌 Top 5",
    en: "Top 5 losers",
  },
  "overview.MarketMoversBoard.volumeZ20": {
    zh: "|量能 Z20|",
    en: "|Volume Z20|",
  },
  "overview.MarketMoversBoard.top5VolumeAnomalies": {
    zh: "量能异常 Top 5",
    en: "Top 5 volume anomalies",
  },
  "overview.MarketMoversBoard.top5MarketFocus": {
    zh: "市场焦点 Top 5",
    en: "Top 5 market focus",
  },
  "overview.MarketMoversBoard.all": {
    zh: "； 全部",
    en: "; all",
  },
  "overview.OpportunityTable.last30DailyCloses": {
    zh: "{symbol}近30日收盘",
    en: "{symbol}: last 30 daily closes",
  },
  "overview.MarketProxyStrip.weak": {
    zh: "偏弱",
    en: "Weak",
  },
  "overview.MarketProxyStrip.strong": {
    zh: "偏强",
    en: "Strong",
  },
  "overview.MarketProxyStrip.uSBroadMarketProxies": {
    zh: "美国大盘代理",
    en: "US broad-market proxies",
  },
  "overview.MarketProxyStrip.marketPricesAndRuleResearch": {
    zh: "大盘行情与规则研究",
    en: "Market prices and rule research",
  },
  "overview.MarketProxyStrip.modelForecastsForThisHorizonAreUnpublishedCardsShow": {
    zh: "当前周期模型预测未发布；卡片展示真实行情和规则状态。",
    en: "Model forecasts for this horizon are unpublished. Cards show observed prices and rule states.",
  },
  "overview.MarketProxyStrip.verifiedComponentsAvailableForProxiesAtThisHorizonExpand":
    {
      zh: "{modelCoverage}/{count} 个代理有该周期的已验证组件，展开查看；全部内容不可执行。",
      en: "Verified components available for {modelCoverage}/{count} proxies at this horizon. Expand to view; all content is non-executable.",
    },
  "overview.MarketProxyStrip.forecastHorizon": {
    zh: "预测周期",
    en: "Forecast horizon",
  },
  "overview.MarketProxyStrip.next": {
    zh: "未来",
    en: "Next",
  },
  "overview.MarketProxyStrip.tradingDays": {
    zh: "个交易日",
    en: "trading days",
  },
  "overview.MarketProxyStrip.previousMarketProxy": {
    zh: "上一个大盘代理",
    en: "Previous market proxy",
  },
  "overview.MarketProxyStrip.nextMarketProxy": {
    zh: "下一个大盘代理",
    en: "Next market proxy",
  },
  "overview.MarketProxyStrip.regimeProbability": {
    zh: "状态概率",
    en: "Regime probability",
  },
  "overview.MarketProxyStrip.researchDirection": {
    zh: "研究方向",
    en: "Research direction",
  },
  "overview.MarketProxyStrip.directionModelUnpublished": {
    zh: "方向模型尚未发布",
    en: "Direction model unpublished",
  },
  "overview.MarketProxyStrip.marketCloseSamePriceBasisAsTrend": {
    zh: "市场收盘 ； 与趋势同口径",
    en: "Market close; same price basis as trend",
  },
  "overview.MarketProxyStrip.noCurrentPrice": {
    zh: "无当前价",
    en: "No current price",
  },
  "overview.MarketProxyStrip.sessionChange": {
    zh: "当日变动",
    en: "Session change",
  },
  "overview.MarketProxyStrip.modelResearchDetails": {
    zh: "模型研究详情 ；",
    en: "Model research details;",
  },
  "overview.MarketProxyStrip.componentEvidenceAvailable": {
    zh: "有组件证据",
    en: "Component evidence available",
  },
  "overview.OpportunityTable.unpublished": {
    zh: "未发布",
    en: "Unpublished",
  },
  "overview.MarketProxyStrip.researchForecastOfTheCloseTradingDaysAhead": {
    zh: "未来第 {horizon} 个交易日收盘价研究预测",
    en: "Research forecast of the close {horizon} trading days ahead",
  },
  "overview.MarketProxyStrip.tradingDayForecastMedianPrice": {
    zh: "个交易日预测中位价",
    en: "-trading-day forecast median price",
  },
  "overview.MarketProxyStrip.modelReferencePriceAndQ50AreMarked": {
    zh: "模型参考价与 q50 均已标记",
    en: "Model reference price and q50 are marked",
  },
  "overview.MarketProxyStrip.q10Q90IntervalUnavailable": {
    zh: "q10–q90 区间暂不可用",
    en: "q10–q90 interval unavailable",
  },
  "overview.MarketStructureTerminal.20DayPositiveReturns": {
    zh: "20日正收益",
    en: "20-day positive returns",
  },
  "overview.OverviewPulse.returnDispersion": {
    zh: "收益离散度",
    en: "Return dispersion",
  },
  "overview.MarketStructureTerminal.20DayReturn": {
    zh: "20日收益",
    en: "20-day return",
  },
  "overview.OpportunityTable.volumeZ": {
    zh: "成交量 Z",
    en: "Volume Z",
  },
  "overview.MarketStructureTerminal.fourMarketStructureCharts": {
    zh: "市场结构四联图",
    en: "Four market structure charts",
  },
  "overview.MarketStructureTerminal.structureEvidence": {
    zh: "结构证据",
    en: "Structure evidence",
  },
  "overview.MarketStructureTerminal.distributionsTransitionsTimeAndAnomalyPositionsUseTheSame":
    {
      zh: "分布、迁移、时间和异常位置使用同一兼容快照口径。",
      en: "Distributions, transitions, time and anomaly positions use the same compatible snapshot basis.",
    },
  "overview.MarketStructureTerminal.marketStructureChartGrid": {
    zh: "市场结构图表网格",
    en: "Market structure chart grid",
  },
  "overview.MarketStructureTerminal.instrumentsAcross10FixedEqualWidthBins": {
    zh: "{count} 个标的，固定 10 个等宽区间。",
    en: "{count} instruments across 10 fixed equal-width bins.",
  },
  "overview.MarketStructureTerminal.viewReturnDistributionData": {
    zh: "查看收益分布数据",
    en: "View return distribution data",
  },
  "overview.MarketStructureTerminal.dayReturnDistribution": {
    zh: "{horizon} 日收益分布",
    en: "{horizon}-day return distribution",
  },
  "overview.MarketStructureTerminal.dayReturnDistributionData": {
    zh: "{horizon} 日收益分布数据",
    en: "{horizon}-day return distribution data",
  },
  "overview.MarketStructureTerminal.interval": {
    zh: "区间",
    en: "Interval",
  },
  "overview.MarketStructureTerminal.count": {
    zh: "数量",
    en: "Count",
  },
  "overview.MarketStructureTerminal.compare": {
    zh: "比较 {snapshotId}",
    en: "Compare {snapshotId}",
  },
  "overview.MarketStructureTerminal.viewRegimeTransitionData": {
    zh: "查看状态迁移数据",
    en: "View regime transition data",
  },
  "overview.MarketStructureTerminal.regimeTransitions": {
    zh: "状态迁移",
    en: "Regime transitions",
  },
  "overview.MarketStructureTerminal.regimeTransitionData": {
    zh: "状态迁移数据",
    en: "Regime transition data",
  },
  "overview.MarketStructureTerminal.5CompatibleSnapshotsMissingValuesAreNotFilledWith":
    {
      zh: "{count}/5 个兼容快照；缺失项不补零。",
      en: "{count}/5 compatible snapshots; missing values are not filled with zero.",
    },
  "overview.MarketStructureTerminal.viewFiveSnapshotData": {
    zh: "查看五快照数据",
    en: "View five-snapshot data",
  },
  "overview.MarketStructureTerminal.fiveSnapshotTimeline": {
    zh: "五快照时间轴",
    en: "Five-snapshot timeline",
  },
  "overview.MarketStructureTerminal.fiveSnapshotData": {
    zh: "五快照数据",
    en: "Five-snapshot data",
  },
  "overview.MarketStructureTerminal.date": {
    zh: "日期",
    en: "Date",
  },
  "overview.MarketStructureTerminal.breadth": {
    zh: "宽度",
    en: "Breadth",
  },
  "overview.MarketStructureTerminal.dispersion": {
    zh: "离散度",
    en: "Dispersion",
  },
  "overview.MarketStructureTerminal.onlyBreakoutsTransitionsOrInstrumentsWithZ2Are": {
    zh: "仅标注突破、切换或 |Z|≥2 的标的。",
    en: "Only breakouts, transitions or instruments with |Z|≥2 are marked.",
  },
  "overview.MarketStructureTerminal.viewReturnAndVolumeData": {
    zh: "查看收益与量能数据",
    en: "View return and volume data",
  },
  "overview.MarketStructureTerminal.20DayReturnVolumeZ": {
    zh: "20 日收益 × 成交量 Z",
    en: "20-day return × volume Z",
  },
  "overview.MarketStructureTerminal.returnAndVolumeData": {
    zh: "收益与量能数据",
    en: "Return and volume data",
  },
  "overview.OverviewPage.instrument": {
    zh: "标的",
    en: "Instrument",
  },
  "overview.MarketStructureTerminal.volumeZ": {
    zh: "量能 Z",
    en: "Volume Z",
  },
  "overview.MarketTrendSparkline.trendDataUnavailable": {
    zh: "{symbol} 趋势：数据不可用",
    en: "{symbol} trend: data unavailable",
  },
  "overview.MarketTrendSparkline.trendObservations": {
    zh: "{symbol} 趋势：{directionLabel}，{count} 个观测点",
    en: "{symbol} trend: {directionLabel}, {count} observations",
  },
  "overview.OverviewPulse.noData": {
    zh: "无数据",
    en: "No data",
  },
  "overview.OpportunityTable.dayModelTaskHasNotPassedTheGatesOr": {
    zh: "{horizon} 日模型任务未通过门禁或尚未发布",
    en: "{horizon}-day model task has not passed the gates or is unpublished",
  },
  "overview.OpportunityTable.researchTasksPublishedFormalForecastsRemainSubjectToPage":
    {
      zh: "已发布 {count} 个研究任务；正式预测仍以页面级门禁为准",
      en: "{count} research tasks published; formal forecasts remain subject to page-level gates",
    },
  "overview.OpportunityTable.rankingBasis": {
    zh: "排序依据",
    en: "Ranking basis",
  },
  "overview.OpportunityTable.noRankingBasisSuppliedForThisSnapshot": {
    zh: "当前快照未提供排序依据。",
    en: "No ranking basis supplied for this snapshot.",
  },
  "overview.OpportunityTable.20DayATROfPrice": {
    zh: "20 日 ATR（占价格）",
    en: "20-day ATR (% of price)",
  },
  "overview.OpportunityTable.volumeZScore": {
    zh: "成交量 Z 分数",
    en: "Volume Z-score",
  },
  "overview.SectorMarketMap.channelPosition": {
    zh: "通道位置",
    en: "Channel position",
  },
  "overview.OpportunityTable.channelWidth": {
    zh: "通道宽度",
    en: "Channel width",
  },
  "overview.OpportunityTable.available": {
    zh: "可用",
    en: "Available",
  },
  "overview.OpportunityTable.evidenceTime": {
    zh: "证据时间",
    en: "Evidence time",
  },
  "overview.OpportunityTable.modelAvailability": {
    zh: "模型可用性",
    en: "Model availability",
  },
  "overview.SectorMarketMap.viewInstrumentResearch": {
    zh: "查看标的研究",
    en: "View Instrument Research",
  },
  "overview.OpportunityTable.experimentalCandidatesHaveNotPassedModelQualityGatesFor": {
    zh: "实验候选尚未通过模型门禁，仅供研究。",
    en: "Experimental candidates have not passed model quality gates. For research only.",
  },
  "overview.OpportunityTable.modelPublishedButNoProbabilityGeneratedForThisRun": {
    zh: "模型已发布，但本次未生成概率",
    en: "Model published, but no probability generated for this run",
  },
  "overview.OpportunityTable.unpublishedDayBreakoutModelHasNotPassedGates": {
    zh: "未发布（{horizon}日突破模型未通过门禁）",
    en: "Unpublished ({horizon}-day breakout model has not passed gates)",
  },
  "overview.OpportunityTable.notApplicable1DayTaskDisabled": {
    zh: "不适用（1日任务不启用）",
    en: "Not applicable (1-day task disabled)",
  },
  "overview.OpportunityTable.noChallengerRangeExitTaskForThisHorizon": {
    zh: "该周期无候选退出任务",
    en: "No challenger range-exit task for this horizon",
  },
  "overview.OpportunityTable.challengerOutputNotGenerated": {
    zh: "候选输出未生成",
    en: "Challenger output not generated",
  },
  "overview.OpportunityTable.notApplicableCurrentRegimeIsNotRange": {
    zh: "不适用（当前非震荡）",
    en: "Not applicable (current regime is not range)",
  },
  "overview.OpportunityTable.ruleBasedUpwardBreakout": {
    zh: "规则向上突破",
    en: "Rule-based upward breakout",
  },
  "overview.OpportunityTable.ruleBasedDownwardBreakout": {
    zh: "规则向下突破",
    en: "Rule-based downward breakout",
  },
  "overview.OpportunityTable.ranksHighlyUnderTheCurrentSortNoRuleBreakout": {
    zh: "按当前排序进入前列，未触发规则突破",
    en: "Ranks highly under the current sort; no rule breakout triggered",
  },
  "overview.OpportunityTable.todaySMovesAndResearchLeads": {
    zh: "今日异动与研究线索",
    en: "Today's moves and research leads",
  },
  "overview.OpportunityTable.crossSectionalRuleEvidenceFirst10DesktopRows": {
    zh: "横截面规则证据 ； 桌面首显 10 条",
    en: "Cross-sectional rule evidence; first 10 desktop rows",
  },
  "overview.OpportunityTable.noResearchInstrumentsMeetTheRuleRankingConditionsIn": {
    zh: "当前快照没有满足规则排序条件的研究标的。",
    en: "No research instruments meet the rule-ranking conditions in this snapshot.",
  },
  "overview.OpportunityTable.noResearchCandidates": {
    zh: "暂无研究候选",
    en: "No research candidates",
  },
  "overview.OpportunityTable.topResearchCandidates": {
    zh: "Top 研究候选",
    en: "Top research candidates",
  },
  "overview.OpportunityTable.rank": {
    zh: "排名",
    en: "Rank",
  },
  "overview.OpportunityTable.whyItAppearsToday": {
    zh: "为什么今天出现",
    en: "Why it appears today",
  },
  "overview.OpportunityTable.trend30Days": {
    zh: "走势 ； 30日",
    en: "Trend; 30 days",
  },
  "overview.OpportunityTable.research": {
    zh: "研究入口",
    en: "Research",
  },
  "overview.OpportunityTable.collapse": {
    zh: "收起",
    en: "Collapse",
  },
  "overview.OpportunityTable.expand": {
    zh: "展开",
    en: "Expand",
  },
  "overview.OpportunityTable.researchEvidence": {
    zh: "{value}{value2}研究证据",
    en: "{value}{value2} research evidence",
  },
  "overview.OpportunityTable.1Day": {
    zh: "1日",
    en: "1 day",
  },
  "overview.OpportunityTable.5Days": {
    zh: "5日",
    en: "5 days",
  },
  "overview.OpportunityTable.20Days": {
    zh: "20日",
    en: "20 days",
  },
  "overview.OverviewPage.research": {
    zh: "研究",
    en: "Research",
  },
  "overview.OpportunityTable.compactResearchOpportunities": {
    zh: "紧凑研究机会",
    en: "Compact research opportunities",
  },
  "overview.OpportunityTable.collapseFullEvidence": {
    zh: "收起完整证据",
    en: "Collapse full evidence",
  },
  "overview.OpportunityTable.viewFullEvidence": {
    zh: "查看完整证据",
    en: "View full evidence",
  },
  "overview.OpportunityTable.experimentalCandidatePreview": {
    zh: "实验室候选预览",
    en: "Experimental candidate preview",
  },
  "overview.OpportunityTable.qualityGatesNotPassedExperimentalResearchOnly": {
    zh: "未通过门禁，仅供实验研究",
    en: "Quality gates not passed; experimental research only",
  },
  "overview.OpportunityTable.rankedByObservedRuleEvidenceAndVerifiedModelTasks": {
    zh: "真实规则证据与已验证模型任务排序；不可执行",
    en: "Ranked by observed rule evidence and verified model tasks; non-executable",
  },
  "overview.OpportunityTable.upProbabilityMeasuresTheClosingDirectionAtTheEnd": {
    zh: "“上涨概率”判断窗口末收盘方向；“突破概率”判断窗口内是否越过此前 20 日通道；“震荡退出”只对当前为震荡状态的标的计算。",
    en: "“Up probability” measures the closing direction at the end of the window. “Breakout probability” measures whether price crosses the preceding 20-day channel during the window. “Range exit” applies only to instruments currently in a range.",
  },
  "overview.OpportunityTable.noChallengerTaskOutputAvailableInThisSnapshot": {
    zh: "当前快照没有可展示的候选任务输出。",
    en: "No challenger task output available in this snapshot.",
  },
  "overview.OpportunityTable.thisSnapshotFallsBackToRuleRankingNoResearch": {
    zh: "当前快照将自动退化为规则排序；暂无满足条件的研究标的。",
    en: "This snapshot falls back to rule ranking. No research instruments currently meet the conditions.",
  },
  "overview.OpportunityTable.currentRuleRegime": {
    zh: "当前规则状态",
    en: "Current rule regime",
  },
  "overview.OpportunityTable.latestPrice": {
    zh: "最新价",
    en: "Latest price",
  },
  "overview.OpportunityTable.returnsOverThePast520TradingDays": {
    zh: "过去 5 / 20 个交易日收益",
    en: "Returns over the past 5 / 20 trading days",
  },
  "overview.OpportunityTable.dayChallengerUpProbability": {
    zh: "{displayedHorizon} 日候选上涨概率",
    en: "{displayedHorizon}-day challenger up probability",
  },
  "overview.OpportunityTable.forecast": {
    zh: "{closeHorizon}预测",
    en: "{closeHorizon} forecast",
  },
  "overview.OpportunityTable.dayChallengerUpwardDownwardBreakoutProbability": {
    zh: "{displayedHorizon} 日候选向上 / 向下突破概率",
    en: "{displayedHorizon}-day challenger upward / downward breakout probability",
  },
  "overview.OpportunityTable.potentialHighLow": {
    zh: "{window}可能高位 / 低位",
    en: "{window} potential high / low",
  },
  "overview.OpportunityTable.dayChallengerRangeExitRangeOnly": {
    zh: "{displayedHorizon} 日候选震荡退出（仅震荡）",
    en: "{displayedHorizon}-day challenger range exit (range only)",
  },
  "overview.OpportunityTable.breakoutProbabilityOverTheNext20TradingDaysUp": {
    zh: "未来 20 个交易日内突破概率（上 / 下）",
    en: "Breakout probability over the next 20 trading days (up / down)",
  },
  "overview.OpportunityTable.failedTasks": {
    zh: "失败任务",
    en: "Failed tasks",
  },
  "overview.OpportunityTable.currentMarketRuleRegimeDoesNotIndicateFutureForecast": {
    zh: "当前市场规则状态，不代表未来预测方向",
    en: "Current market rule regime; does not indicate future forecast direction",
  },
  "overview.SectorMarketMap.5Days": {
    zh: "5 日",
    en: "5 days",
  },
  "overview.SectorMarketMap.20Days": {
    zh: "20 日",
    en: "20 days",
  },
  "overview.OpportunityTable.up": {
    zh: "上",
    en: "Up",
  },
  "overview.OpportunityTable.down": {
    zh: "下",
    en: "Down",
  },
  "overview.OpportunityTable.high": {
    zh: "高",
    en: "High",
  },
  "overview.OpportunityTable.low": {
    zh: "低",
    en: "Low",
  },
  "overview.OpportunityTable.directionForecast": {
    zh: "方向预测",
    en: "Direction forecast",
  },
  "overview.OpportunityTable.breakoutForecast": {
    zh: "突破预测",
    en: "Breakout forecast",
  },
  "overview.OpportunityTable.rangeExit": {
    zh: "震荡退出",
    en: "Range exit",
  },
  "overview.OpportunityTable.maximumAdverseExcursion": {
    zh: "最大不利波动",
    en: "Maximum adverse excursion",
  },
  "overview.OpportunityTable.unknownVersion": {
    zh: "版本未知",
    en: "Unknown version",
  },
  "overview.OpportunityTable.channelPosition": {
    zh: "通道位置 {value}",
    en: "Channel position {value}",
  },
  "overview.OpportunityTable.dayChallengerUpProbability2": {
    zh: "日候选上涨概率",
    en: "-day challenger up probability",
  },
  "overview.OpportunityTable.dayChallengerRangeExit": {
    zh: "日候选震荡退出",
    en: "-day challenger range exit",
  },
  "overview.OpportunityTable.dayChallengerUpwardBreakoutProbability": {
    zh: "日候选向上突破概率",
    en: "-day challenger upward breakout probability",
  },
  "overview.OpportunityTable.medianScenarioPriceQ50": {
    zh: "中位情景价（q50）",
    en: "Median scenario price (q50)",
  },
  "overview.OpportunityTable.priceIntervalQ10Q90": {
    zh: "价区间（q10–q90）",
    en: "Price interval (q10–q90)",
  },
  "overview.OpportunityTable.potentialHighLow2": {
    zh: "可能高 / 低",
    en: "Potential high / low",
  },
  "overview.OpportunityTable.breakoutOverTheNext20TradingDaysUpDown": {
    zh: "未来 20 个交易日内突破（上 / 下）",
    en: "Breakout over the next 20 trading days (up / down)",
  },
  "overview.OpportunityTable.topOpportunitiesExecutableEvidenceRanking": {
    zh: "Top 机会 ； 可执行证据排序",
    en: "Top opportunities; executable evidence ranking",
  },
  "overview.OpportunityTable.q10WeakReturnScenarioReceivesTheSameProminenceAs": {
    zh: "q10（偏弱收益情景）与上涨概率同等展示",
    en: "q10 (weak return scenario) receives the same prominence as up probability",
  },
  "overview.OpportunityTable.noResearchInstrumentsPassDataCalibrationAndAbstentionGates":
    {
      zh: "当前快照没有通过数据、校准和拒绝判断门控的研究标的。",
      en: "No research instruments pass data, calibration and abstention gates in this snapshot.",
    },
  "overview.OpportunityTable.noExecutableOpportunities": {
    zh: "暂无可执行机会",
    en: "No executable opportunities",
  },
  "overview.OpportunityTable.status": {
    zh: "状态",
    en: "Status",
  },
  "overview.OpportunityTable.executableUpProbability": {
    zh: "可成交上涨概率",
    en: "Executable up probability",
  },
  "overview.OpportunityTable.executableQ10Downside": {
    zh: "可成交 q10 下行",
    en: "Executable q10 downside",
  },
  "overview.OpportunityTable.executableQ50Median": {
    zh: "可成交 q50 中位数",
    en: "Executable q50 median",
  },
  "overview.OpportunityTable.intervalWidth": {
    zh: "区间宽度",
    en: "Interval width",
  },
  "overview.OpportunityTable.confidenceLevel": {
    zh: "置信等级",
    en: "Confidence level",
  },
  "overview.OpportunityTable.dataTime": {
    zh: "数据时间",
    en: "Data time",
  },
  "overview.OpportunityTable.topOpportunityEvidence": {
    zh: "移动端 Top 机会证据",
    en: "Top opportunity evidence",
  },
  "overview.OpportunityTable.evidenceSummary": {
    zh: "{symbol} 证据摘要",
    en: "{symbol} evidence summary",
  },
  "overview.OpportunityTable.upProbability": {
    zh: "上涨概率",
    en: "Up probability",
  },
  "overview.OpportunityTable.q10Downside": {
    zh: "q10 下行",
    en: "q10 downside",
  },
  "overview.OptionResearchCard.openResearch": {
    zh: "打开 {s} 研究页",
    en: "Open {s} research",
  },
  "overview.OptionResearchCard.stockSnapshotClose": {
    zh: "股票快照收盘价",
    en: "Stock snapshot close",
  },
  "overview.OptionResearchCard.optionsCalculationReferencePrice": {
    zh: "期权计算参考价",
    en: "Options calculation reference price",
  },
  "overview.OptionResearchCard.turnover": {
    zh: "成交额",
    en: "Turnover",
  },
  "overview.OptionResearchCard.candlesImpliedIntervalsAndVolumeByStrike": {
    zh: "{s} K 线、隐含区间与行权价成交分布",
    en: "{s} candles, implied intervals and volume by strike",
  },
  "overview.OptionResearchCard.last30DailyCandlesNext10Day6880": {
    zh: "最近 30 日 K 线；未来 10 日 68% / 80% 隐含区间；右侧 Put / Call 成交量",
    en: "Last 30 daily candles; next 10-day 68% / 80% implied intervals; Put / Call volume on the right",
  },
  "overview.OptionResearchCard.savedStockDailyBarsOptionsDataUnavailable": {
    zh: "已保存的股票日线；期权数据尚不可用",
    en: "Saved stock daily bars; options data unavailable",
  },
  "overview.OptionResearchCard.viewCardSourcesAndPrices": {
    zh: "查看小卡来源与价格",
    en: "View card sources and prices",
  },
  "overview.OptionResearchCard.optionsMarketDateSnapshot": {
    zh: "期权市场日：{marketDate}；快照：{snapshotId}。",
    en: "Options market date: {marketDate}; snapshot: {snapshotId}.",
  },
  "overview.OptionResearchCard.stockSnapshotCutoff": {
    zh: "股票快照截止：{asOf}；{why}。",
    en: "Stock snapshot cutoff: {asOf}; {why}.",
  },
  "overview.OptionResearchCard.noDailyBarsForThisCutoffDateOnlyThe": {
    zh: "尚无该截止日期的日线，图中仅显示已保存的行权价分布。",
    en: "No daily bars for this cutoff date; only the saved strike distribution is shown.",
  },
  "overview.OptionResearchCard.tradingDaysOfDailyBarsLoaded": {
    zh: "已读取 {count} 个交易日日线。",
    en: "{count} trading days of daily bars loaded.",
  },
  "overview.OptionResearchCard.dateStrike": {
    zh: "日期 / 行权价",
    en: "Date / strike",
  },
  "overview.OptionResearchCard.closeCallVolume": {
    zh: "收盘 / Call 量",
    en: "Close / Call volume",
  },
  "overview.OptionResearchCard.putVolume": {
    zh: "Put 量",
    en: "Put volume",
  },
  "overview.OptionResearchCard.dataGap": {
    zh: "数据缺口",
    en: "Data gap",
  },
  "overview.OptionResearchCard.noEligibleOptionsSnapshotForThisInstrument": {
    zh: "该标的尚无合格期权快照。",
    en: "No eligible options snapshot for this instrument.",
  },
  "overview.OptionResearchCard.1MonthUpGt5": {
    zh: "1 月 涨>5%",
    en: "1 month up >5%",
  },
  "overview.OptionResearchCard.downGt5": {
    zh: "； 跌>5%",
    en: "; down >5%",
  },
  "overview.OptionResearchCard.premiumTarget": {
    zh: "资金 目标",
    en: "Premium target",
  },
  "overview.OptionResearchCard.protection": {
    zh: "/ 保护",
    en: "/ protection",
  },
  "overview.OptionResearchCard.putPeak": {
    zh: "Put 峰值",
    en: "Put peak",
  },
  "overview.OptionResearchCard.callPeak": {
    zh: "； Call 峰值",
    en: "; Call peak",
  },
  "overview.OptionResearchCard.modelUnpublished": {
    zh: "模型未发布",
    en: "Model unpublished",
  },
  "overview.OptionsBoard.universe": {
    zh: "全站",
    en: "Universe:",
  },
  "overview.OptionsBoard.instrumentsSnapshotsAvailable": {
    zh: "个标的，快照可用",
    en: "instruments; snapshots available",
  },
  "overview.OptionsBoard.awaitingCollection": {
    zh: "，待采集",
    en: ", awaiting collection",
  },
  "overview.OptionsBoard.collectionFailedOrDataUnavailable": {
    zh: "，采集失败或数据不可用",
    en: ", collection failed or data unavailable",
  },
  "overview.OptionsBoard.aggregatesIncludeOnlyInstrumentsWithEligibleData": {
    zh: "。 汇总仅计入有合格数据的标的。",
    en: ". Aggregates include only instruments with eligible data.",
  },
  "overview.OptionsBoard.optionsMarketDate": {
    zh: "期权市场日：",
    en: "Options market date:",
  },
  "overview.OptionsBoard.snapshotAvailable": {
    zh: "快照可用",
    en: "Snapshot available",
  },
  "overview.OptionsBoard.dataUnavailable": {
    zh: "数据不可用",
    en: "Data unavailable",
  },
  "overview.OptionsBoard.savedSnapshotReadOnlyResearch": {
    zh: "； 已保存快照，只读研究。",
    en: "; saved snapshot, read-only research.",
  },
  "overview.OptionsBoard.instrumentOptionsPulse": {
    zh: "标的期权脉搏",
    en: "instrument Options Pulse",
  },
  "overview.OptionsBoard.candlesImpliedIntervalsAndVolumeByStrikeClickTo": {
    zh: "K 线、隐含区间与行权价成交分布 ； 点击打开研究页",
    en: "Candles, implied intervals and volume by strike; click to open research",
  },
  "overview.OptionsBoard.optionsCardSorting": {
    zh: "期权小卡排序",
    en: "Options card sorting",
  },
  "overview.OptionsBoard.byIV": {
    zh: "按 IV",
    en: "By IV",
  },
  "overview.OptionsBoard.byTurnover": {
    zh: "按成交额",
    en: "By turnover",
  },
  "overview.OptionsBoard.by5DayInterval": {
    zh: "按 5 日区间",
    en: "By 5-day interval",
  },
  "overview.OptionsBoard.byPutCall": {
    zh: "按 Put/Call",
    en: "By Put/Call",
  },
  "overview.OptionsBoard.default": {
    zh: "默认",
    en: "Default",
  },
  "overview.OptionsBoard.searchInstruments": {
    zh: "搜索标的",
    en: "Search instruments",
  },
  "overview.OptionsBoard.searchOptionsInstruments": {
    zh: "搜索期权标的",
    en: "Search options instruments",
  },
  "overview.OptionsBoard.tickerOrName": {
    zh: "股票代码或名称",
    en: "Ticker or name",
  },
  "overview.OptionsBoard.matches": {
    zh: "匹配",
    en: "Matches:",
  },
  "overview.OptionsBoard.page": {
    zh: "个，第",
    en: "; page",
  },
  "overview.OptionsBoard.ofResults": {
    zh: "页",
    en: "of results",
  },
  "overview.OptionsBoard.previousPage": {
    zh: "上一页",
    en: "Previous page",
  },
  "overview.OptionsBoard.nextPage": {
    zh: "下一页",
    en: "Next page",
  },
  "overview.OptionsBoard.noMatchingInstruments": {
    zh: "没有匹配的标的。",
    en: "No matching instruments.",
  },
  "overview.OptionsBoard.instrumentOptionsCards": {
    zh: "标的期权卡片",
    en: "Instrument options cards",
  },
  "overview.OptionsBoard.cardGainLossColorsUseSavedDailyBarsPurple": {
    zh: "小卡上涨 / 下跌颜色来自已保存日线；紫色为期权隐含区间。概率为风险中性概率，资金目标 / 保护位为估算成交额加权行权价；不表示预测或主动买卖方向。",
    en: "Card gain / loss colors use saved daily bars; purple marks options implied intervals. Probabilities are risk-neutral. Premium targets / protection levels are estimated premium-weighted strikes; they do not indicate forecasts or buyer / seller direction.",
  },
  "overview.OptionsBoard.contractsWithIncompleteQuotesOrVolume": {
    zh: "报价或成交量不完整的合约共",
    en: "Contracts with incomplete quotes or volume:",
  },
  "overview.OptionsBoard.excludedFromEstimatedTurnover": {
    zh: "个，未计入估算成交额。",
    en: "; excluded from estimated turnover.",
  },
  "overview.OptionsBoard.viewInstrumentDataLimitsAndSources": {
    zh: "查看各标的数据限制与来源",
    en: "View instrument data limits and sources",
  },
  "overview.OptionsBoard.marketDateUnconfirmed": {
    zh: "市场日未确认",
    en: "Market date unconfirmed",
  },
  "overview.OptionsBoard.noSnapshot": {
    zh: "无快照",
    en: "No snapshot",
  },
  "overview.OptionsBoard.dataAvailable": {
    zh: "； 数据可用",
    en: "; data available",
  },
  "overview.OptionsMoneyTargets.spot": {
    zh: "现价",
    en: "Spot",
  },
  "overview.OptionsOutlookTable.optionsOutlook": {
    zh: "期权的看法",
    en: "Options outlook",
  },
  "overview.OptionsDetailTable.up5": {
    zh: "涨 >5%",
    en: "Up >5%",
  },
  "overview.OptionsDetailTable.down5": {
    zh: "跌 >5%",
    en: "Down >5%",
  },
  "overview.OptionsDetailTable.downUpTail": {
    zh: "尾部 跌/涨",
    en: "Down / up tail",
  },
  "overview.OptionsDetailTable.iVRealized": {
    zh: "IV − 实际",
    en: "IV − realized",
  },
  "overview.OptionsDetailTable.5Day": {
    zh: "5 日 ±",
    en: "5-day ±",
  },
  "overview.OptionsDetailTable.20Day": {
    zh: "20 日 ±",
    en: "20-day ±",
  },
  "overview.OptionsDetailTable.skew": {
    zh: "偏斜",
    en: "Skew",
  },
  "overview.OptionsDetailTable.term": {
    zh: "期限",
    en: "Term",
  },
  "overview.OptionsMarketTide.estimatedTurnover": {
    zh: "估算成交额",
    en: "Estimated turnover",
  },
  "overview.OptionsDetailTable.oTMCallShare": {
    zh: "价外 Call 占比",
    en: "OTM Call share",
  },
  "overview.OptionsDetailTable.oTMPutShare": {
    zh: "价外 Put 占比",
    en: "OTM Put share",
  },
  "overview.OptionsDetailTable.07DayShare": {
    zh: "0–7 天占比",
    en: "0–7-day share",
  },
  "overview.OptionsDetailTable.premiumTarget": {
    zh: "资金目标",
    en: "Premium target",
  },
  "overview.OptionsDetailTable.protectionLevel": {
    zh: "保护位",
    en: "Protection level",
  },
  "overview.OptionsDetailTable.optionsDetailTable": {
    zh: "期权明细表",
    en: "Options detail table",
  },
  "overview.OptionsDetailTable.directionVolatilityPremiumClickHeadersToSortEnterOpens":
    {
      zh: "方向 / 波动 / 资金 ； 点击表头排序 ； 回车打开研究页",
      en: "Direction / volatility / premium; click headers to sort; Enter opens research",
    },
  "overview.OptionsDetailTable.optionsDetailTableScrollHorizontally": {
    zh: "期权明细表，可横向滚动",
    en: "Options detail table; scroll horizontally",
  },
  "overview.OptionsDetailTable.direction1Month": {
    zh: "方向 ； 1 个月",
    en: "Direction; 1 month",
  },
  "overview.OptionsDetailTable.volatility": {
    zh: "波动",
    en: "Volatility",
  },
  "overview.OptionsDetailTable.premium": {
    zh: "资金",
    en: "Premium",
  },
  "overview.OptionsDetailTable.probabilitiesAreRiskNeutralSkewUsesApproximately5OTM": {
    zh: "概率为风险中性概率；偏斜为约 5% 价外口径；空值显示“—”，排序时放末尾。期权专用模型尚未发布。",
    en: "Probabilities are risk-neutral; skew uses approximately 5% OTM contracts. Missing values show “—” and sort last. Options-specific models have not been published.",
  },
  "overview.OptionsDivergenceScatter.modelWidthImpliedWidthModelImplied": {
    zh: "{symbol}<br/>模型宽度 {percentage}<br/>隐含宽度 {percentage2}<br/>模型 / 隐含 {number}",
    en: "{symbol}<br/>Model width {percentage}<br/>Implied width {percentage2}<br/>Model / implied {number}",
  },
  "overview.OptionsDivergenceScatter.model80Width": {
    zh: "模型 80% 宽度（%）",
    en: "Model 80% width (%)",
  },
  "overview.OptionsDivergenceScatter.options80Width": {
    zh: "期权 80% 宽度（%）",
    en: "Options 80% width (%)",
  },
  "overview.OptionsDivergenceScatter.equalWidth": {
    zh: "等宽",
    en: "Equal width",
  },
  "overview.OptionsDivergenceScatter.optionsScatterView": {
    zh: "期权散点视图",
    en: "Options scatter view",
  },
  "overview.OptionsDivergenceScatter.fearVsProtection": {
    zh: "恐慌 vs 防守",
    en: "Fear vs protection",
  },
  "overview.OptionsDivergenceScatter.modelIntervalDivergence": {
    zh: "模型区间分歧",
    en: "Model interval divergence",
  },
  "overview.OptionsDivergenceScatter.optionsDivergenceHorizon": {
    zh: "期权分歧期限",
    en: "Options divergence horizon",
  },
  "overview.SectorHealthTerminal.days": {
    zh: "日",
    en: " days",
  },
  "overview.OptionsDivergenceScatter.modelVsMarketIntervalWidthDivergence": {
    zh: "模型 vs 市场：区间宽度分歧",
    en: "Model vs market: interval width divergence",
  },
  "overview.OptionsDivergenceScatter.aboveOptionsPriceGreaterVolatilityBelowModelIntervalsAre":
    {
      zh: "上方：期权定价波动更大；下方：模型区间更宽。点击标的查看研究证据。",
      en: "Above: options price greater volatility. Below: model intervals are wider. Click an instrument for research evidence.",
    },
  "overview.OptionsDivergenceScatter.viewIntervalWidthsAndInstruments": {
    zh: "查看区间宽度与标的",
    en: "View interval widths and instruments",
  },
  "overview.OptionsDivergenceScatter.modelWidth": {
    zh: "模型宽度",
    en: "Model width",
  },
  "overview.OptionsDivergenceScatter.impliedWidth": {
    zh: "隐含宽度",
    en: "Implied width",
  },
  "overview.OptionsPulseTable.widthRatio": {
    zh: "宽度比",
    en: "Width ratio",
  },
  "overview.OptionsDivergenceScatter.requiresModelQuantilesAndOptionsSnapshotsCoveringTheSame":
    {
      zh: "需要同期限可用模型分位数与有期限覆盖的期权快照；模型拒绝预测或期限不可插值时留空。",
      en: "Requires model quantiles and options snapshots covering the same horizon. Blank when the model abstains or interpolation is unavailable.",
    },
  "overview.OptionsDivergenceScatter.aTMIVAnnualized": {
    zh: "ATM IV（年化 %）",
    en: "ATM IV (annualized %)",
  },
  "overview.OptionsDivergenceScatter.putCallVolumeRatio": {
    zh: "Put/Call 成交比",
    en: "Put/Call volume ratio",
  },
  "overview.OptionsDivergenceScatter.xATMIVYPutCallVolumeRatioHigher": {
    zh: "X = ATM IV；Y = Put/Call 成交比。较高 IV 表示波动定价较高，成交比不代表买卖方向。",
    en: "X = ATM IV; Y = Put/Call volume ratio. Higher IV means higher priced volatility; the volume ratio does not indicate buyer / seller direction.",
  },
  "overview.OptionsDivergenceScatter.viewIVAndPutCallValues": {
    zh: "查看 IV 与 Put/Call 数值",
    en: "View IV and Put/Call values",
  },
  "overview.OptionsDivergenceScatter.noSnapshotsHaveBothValidIVAndVolumeRatios": {
    zh: "没有同时具备有效 IV 与成交比的快照。",
    en: "No snapshots have both valid IV and volume ratios.",
  },
  "overview.OptionsIntentMix.07Days": {
    zh: "0–7 天",
    en: "0–7 days",
  },
  "overview.OptionsExpiryMix.830Days": {
    zh: "8–30 天",
    en: "8–30 days",
  },
  "overview.OptionsExpiryMix.3160Days": {
    zh: "31–60 天",
    en: "31–60 days",
  },
  "overview.OptionsExpiryMix.over60Days": {
    zh: "60 天以上",
    en: "Over 60 days",
  },
  "overview.OptionsExpiryMix.noValidVolume": {
    zh: "无有效成交",
    en: "No valid volume",
  },
  "overview.OptionsExpiryMix.contracts": {
    zh: "{number} 张",
    en: "{number} contracts",
  },
  "overview.OptionsExpiryMix.volumeByExpiry": {
    zh: "到期日成交分布",
    en: "Volume by expiry",
  },
  "overview.OptionsExpiryMix.nearTermOrLongerDatedPositioningByVolume": {
    zh: "短线押注还是远月布局 ； 按成交量",
    en: "Near-term or longer-dated positioning; by volume",
  },
  "overview.OptionsIntentMix.total": {
    zh: "合计",
    en: "Total",
  },
  "overview.OptionsIntentMix.noAvailableSnapshot": {
    zh: "无可用快照",
    en: "No available snapshot",
  },
  "overview.OptionsExpiryMix.calendarDTEIsMeasuredFromEachSnapshotSMarket": {
    zh: "自然日 DTE 从各快照市场日期计算，不使用采集日期。",
    en: "Calendar DTE is measured from each snapshot's market date, not collection date.",
  },
  "overview.OptionsFlowTable.largestContracts": {
    zh: "大额合约榜",
    en: "Largest contracts",
  },
  "overview.OptionsFlowTable.instruments": {
    zh: "{count} 个标的",
    en: "{count} instruments",
  },
  "overview.OptionsFlowTable.topEstimatedTurnover": {
    zh: "按估算成交额前",
    en: "Top estimated turnover:",
  },
  "overview.OptionsFlowTable.excluded": {
    zh: "名 ； 已排除",
    en: "; excluded:",
  },
  "overview.OptionsFlowTable.deepITMContracts": {
    zh: "个深度价内合约",
    en: "deep ITM contracts",
  },
  "overview.OptionsFlowTable.contractTypeFilter": {
    zh: "合约类型筛选",
    en: "Contract type filter",
  },
  "overview.OptionsIntentMix.all": {
    zh: "全部",
    en: "All",
  },
  "overview.OptionsFlowTable.largeContractInstrument": {
    zh: "大额合约标的",
    en: "Large-contract instrument",
  },
  "overview.OptionsFlowTable.allInstruments": {
    zh: "全部标的",
    en: "All instruments",
  },
  "overview.OptionsFlowTable.excludeDeepITMGt10": {
    zh: "排除深度价内（>10%）",
    en: "Exclude deep ITM (>10%)",
  },
  "overview.OptionsFlowTable.type": {
    zh: "类型",
    en: "Type",
  },
  "overview.OptionsImpliedDistribution.strike": {
    zh: "行权价",
    en: "Strike",
  },
  "overview.OptionsFlowTable.expiry": {
    zh: "到期",
    en: "Expiry",
  },
  "overview.OptionsFlowTable.moneyness": {
    zh: "价内外",
    en: "Moneyness",
  },
  "overview.OptionsFlowTable.volume": {
    zh: "成交量",
    en: "Volume",
  },
  "overview.OptionsFlowTable.midpoint": {
    zh: "中间价",
    en: "Midpoint",
  },
  "overview.OptionsPulseTable.days": {
    zh: "天",
    en: " days",
  },
  "overview.OptionsFlowTable.iTM": {
    zh: "价内",
    en: "ITM",
  },
  "overview.OptionsFlowTable.aTM": {
    zh: "平值",
    en: "ATM",
  },
  "overview.OptionsFlowTable.oTM": {
    zh: "价外",
    en: "OTM",
  },
  "overview.OptionsFlowTable.noEligibleContractsMatchTheCurrentFilters": {
    zh: "当前筛选没有合格的合约。",
    en: "No eligible contracts match the current filters.",
  },
  "overview.OptionsFlowTable.theTop20CallsAndPutsByEstimatedTurnover": {
    zh: "每标的 Call / Put 各保留成交额前 20 后合并排序；并非全链逐笔成交榜。成交额是估算值，不能判断主动买卖方向。",
    en: "The top 20 Calls and Puts by estimated turnover are retained per instrument, then ranked together. This is not a full-chain trade tape. Turnover is estimated and cannot identify buyer / seller direction.",
  },
  "overview.OptionsImpliedDistribution.down5": {
    zh: "跌超 5%",
    en: "Down >5%",
  },
  "overview.OptionsImpliedDistribution.within5": {
    zh: "±5% 内",
    en: "Within ±5%",
  },
  "overview.OptionsImpliedDistribution.up5": {
    zh: "涨超 5%",
    en: "Up >5%",
  },
  "overview.OptionsImpliedDistribution.spot": {
    zh: "现价 {number}",
    en: "Spot {number}",
  },
  "overview.OptionsImpliedDistribution.down52": {
    zh: "跌超 5%：{percentage}",
    en: "Down >5%: {percentage}",
  },
  "overview.OptionsImpliedDistribution.up52": {
    zh: "涨超 5%：{percentage}",
    en: "Up >5%: {percentage}",
  },
  "overview.OptionsImpliedDistribution.impliedPriceDistribution": {
    zh: "{s}隐含价格分布",
    en: "{s} implied price distribution",
  },
  "overview.OptionsImpliedDistribution.impliedDistributionHorizon": {
    zh: "隐含分布期限",
    en: "Implied distribution horizon",
  },
  "overview.OptionsImpliedDistribution.1Week": {
    zh: "1 周",
    en: "1 week",
  },
  "overview.OptionsOutlookTable.1Month": {
    zh: "1 月",
    en: "1 month",
  },
  "overview.OptionsImpliedDistribution.impliedPriceDistribution2": {
    zh: "{s} ； 隐含价格分布",
    en: "{s}; implied price distribution",
  },
  "overview.OptionsImpliedDistribution.expiresDaysProbabilityPer1PriceBin": {
    zh: "{expiration} 到期（{dte} 天）； 每 1% 价格区间的概率",
    en: "Expires {expiration} ({dte} days); probability per 1% price bin",
  },
  "overview.OptionsImpliedDistribution.viewProbabilityCurveAndFitSources": {
    zh: "查看概率曲线与拟合来源",
    en: "View probability curve and fit sources",
  },
  "overview.OptionsImpliedDistribution.cubicIVSmileFitFitPoints": {
    zh: "三次 IV 微笑拟合 ； 拟合点",
    en: "Cubic IV smile fit; fit points",
  },
  "overview.OptionsImpliedDistribution.probabilityAboveThisPrice": {
    zh: "高于此价概率",
    en: "Probability above this price",
  },
  "overview.OptionsImpliedDistribution.fittedIV": {
    zh: "拟合 IV",
    en: "Fitted IV",
  },
  "overview.OptionsImpliedDistribution.impliedPriceDistribution3": {
    zh: "； 隐含价格分布",
    en: "; implied price distribution",
  },
  "overview.OptionsImpliedDistribution.insufficientEligibleVolatilitySmilePointsForThisHorizonImplied":
    {
      zh: "该期限缺少合格的波动率微笑点，无法给出隐含分布。",
      en: "Insufficient eligible volatility smile points for this horizon; implied distribution unavailable.",
    },
  "overview.OptionsImpliedDistribution.80Interval": {
    zh: "80% 区间",
    en: "80% interval",
  },
  "overview.OptionsImpliedDistribution.downGt5DownGt10": {
    zh: "跌 >5% ； 跌 >10%",
    en: "Down >5%; down >10%",
  },
  "overview.OptionsImpliedDistribution.upGt5UpGt10": {
    zh: "涨 >5% ； 涨 >10%",
    en: "Up >5%; up >10%",
  },
  "overview.OptionsImpliedDistribution.premiumProtectionTargetDashed": {
    zh: "资金 保护 / 目标（虚线）",
    en: "Premium protection / target (dashed)",
  },
  "overview.OptionsImpliedDistribution.riskNeutralProbabilitiesNotForecastsGridLimitedToSpot":
    {
      zh: "风险中性概率，不是预测。网格限于现价 ±20%，不外推。",
      en: "Risk-neutral probabilities, not forecasts. Grid limited to spot ±20%; no extrapolation.",
    },
  "overview.OptionsImpliedDistribution.largeFitError": {
    zh: "拟合误差较大。",
    en: "Large fit error.",
  },
  "overview.OptionsImpliedMoveChart.outsideTheOptionsImplied1DayInterval": {
    zh: "<br/>超出期权隐含 1 日区间",
    en: "<br/>Outside the options implied 1-day interval",
  },
  "overview.OptionsImpliedMoveChart.previousSession1RealizedReturnSnapshot": {
    zh: "{symbol} ； {session}<br/>前一交易日 ±1σ：±{percentage}<br/>实际收益：{percentage2}{value}<br/>快照：{time}",
    en: "{symbol}; {session}<br/>Previous session ±1σ: ±{percentage}<br/>Realized return: {percentage2}{value}<br/>Snapshot: {time}",
  },
  "overview.OptionsImpliedMoveChart.implied1About68": {
    zh: "隐含 ±1σ（约 68%）",
    en: "Implied ±1σ (about 68%)",
  },
  "overview.OptionsImpliedMoveChart.realized1DayReturn": {
    zh: "实际 1 日收益",
    en: "Realized 1-day return",
  },
  "overview.OptionsImpliedMoveChart.1DayRealizedMoveVsImpliedInterval": {
    zh: "1 日实际涨跌 vs 隐含区间",
    en: "1-day realized move vs implied interval",
  },
  "overview.OptionsImpliedMoveChart.previousSessionPreCloseSnapshotsPairedWithTheNext":
    {
      zh: "前一交易日收盘前快照配对随后一日真实收益；±1σ 约 68%，不是 80% 区间。",
      en: "Previous-session pre-close snapshots paired with the next session's realized return; ±1σ is about 68%, not an 80% interval.",
    },
  "overview.OptionsImpliedMoveChart.viewPairedValuesAndSources": {
    zh: "查看配对数值与来源",
    en: "View paired values and sources",
  },
  "overview.OptionsImpliedMoveChart.returnDate": {
    zh: "收益日期",
    en: "Return date",
  },
  "overview.OptionsImpliedMoveChart.implied1": {
    zh: "隐含 ±1σ",
    en: "Implied ±1σ",
  },
  "overview.OptionsImpliedMoveChart.realizedReturn": {
    zh: "实际收益",
    en: "Realized return",
  },
  "overview.OptionsImpliedMoveChart.sourceSnapshot": {
    zh: "来源快照",
    en: "Source snapshot",
  },
  "overview.OptionsImpliedMoveChart.requiresAPreviousSessionPreCloseSnapshotAndThe": {
    zh: "需要前一交易日收盘前快照与随后一日已收盘收益，当前没有可配对样本。",
    en: "Requires a previous-session pre-close snapshot and the next session's completed return. No paired samples currently.",
  },
  "overview.OptionsIntentMix.oTMCall": {
    zh: "价外 Call",
    en: "OTM Call",
  },
  "overview.OptionsIntentMix.iTMCall": {
    zh: "价内 Call",
    en: "ITM Call",
  },
  "overview.OptionsIntentMix.oTMPut": {
    zh: "价外 Put",
    en: "OTM Put",
  },
  "overview.OptionsIntentMix.iTMPut": {
    zh: "价内 Put",
    en: "ITM Put",
  },
  "overview.OptionsIntentMix.intentMetricsUnavailable": {
    zh: "意图指标缺失",
    en: "Intent metrics unavailable",
  },
  "overview.OptionsIntentMix.wherePremiumFlows": {
    zh: "资金在干嘛",
    en: "Where premium flows",
  },
  "overview.OptionsIntentMix.contractTypesReceivingPremiumEstimatedTurnover": {
    zh: "钱进了哪类合约 ； 按估算成交额",
    en: "Contract types receiving premium; estimated turnover",
  },
  "overview.OptionsIntentMix.premiumIntentWindow": {
    zh: "资金意图窗口",
    en: "Premium intent window",
  },
  "overview.OptionsIntentMix.over30Days": {
    zh: "30 天以上",
    en: "Over 30 days",
  },
  "overview.OptionsIntentMix.withoutTradePricesBuyerSellerDirectionIsUnknownOnly": {
    zh: "没有成交价，分不清买卖方向，只能按合约类型推断。成交额 = 成交量 × 买卖中间价 × 合约乘数；价内合约还可能用于替代持股或套利。",
    en: "Without trade prices, buyer / seller direction is unknown; only contract types can be compared. Turnover = volume × bid/ask midpoint × contract multiplier. ITM contracts may also substitute stock holdings or support arbitrage.",
  },
  "overview.OptionsIvHvRank.iVRealizedVolatility": {
    zh: "IV − 实际波动",
    en: "IV − realized volatility",
  },
  "overview.OptionsIvHvRank.optionsPricingAnnualizedIVVsRecent20DayRealized": {
    zh: "期权贵不贵 ； 年化 IV 与近 20 日实际波动比较",
    en: "Options pricing; annualized IV vs recent 20-day realized volatility",
  },
  "overview.OptionsIvHvRank.relativelyCheaperOptions": {
    zh: "← 期权偏便宜",
    en: "← Relatively cheaper options",
  },
  "overview.OptionsIvHvRank.relativelyExpensiveOptions": {
    zh: "期权偏贵 →",
    en: "Relatively expensive options →",
  },
  "overview.OptionsIvHvRank.iVRealizedVolatility2": {
    zh: "IV ； 实际波动",
    en: "IV; realized volatility",
  },
  "overview.OptionsIvHvRank.realized": {
    zh: "； 实际",
    en: "; realized",
  },
  "overview.OptionsIvHvRank.realizedVolatilityRequires21ClosesBeforeTheSameMarket": {
    zh: "实际波动需要同一市场日期之前 21 个收盘价，样本标准差 × √252。较贵不等于应卖出，较便宜不等于应买入。",
    en: "Realized volatility requires 21 closes before the same market date; sample standard deviation × √252. Expensive does not imply sell; cheap does not imply buy.",
  },
  "overview.OptionsMarketTide.optionsMarketAggregateUnavailable": {
    zh: "尚无可用的期权市场汇总。",
    en: "Options market aggregate unavailable.",
  },
  "overview.OptionsMarketTide.strongCallTilt": {
    zh: "明显偏 Call",
    en: "Strong Call tilt",
  },
  "overview.OptionsMarketTide.slightCallTilt": {
    zh: "略偏 Call",
    en: "Slight Call tilt",
  },
  "overview.OptionsMarketTide.balanced": {
    zh: "均衡",
    en: "Balanced",
  },
  "overview.OptionsMarketTide.slightPutTilt": {
    zh: "略偏 Put",
    en: "Slight Put tilt",
  },
  "overview.OptionsMarketTide.strongPutTilt": {
    zh: "明显偏 Put",
    en: "Strong Put tilt",
  },
  "overview.OptionsMarketTide.marketTemperature": {
    zh: "市场温度条",
    en: "Market temperature",
  },
  "overview.OptionsMarketTide.totalOptionsVolume": {
    zh: "期权总成交",
    en: "Total options volume",
  },
  "overview.OptionsMarketTide.contracts": {
    zh: "张",
    en: "contracts",
  },
  "overview.OptionsMarketTide.largest": {
    zh: "最大",
    en: "Largest",
  },
  "overview.OptionsMarketTide.estimatedPremiumFlow": {
    zh: "成交额流向 ；",
    en: "Estimated premium flow;",
  },
  "overview.OptionsMarketTide.overallPutCall": {
    zh: "整体 Put/Call",
    en: "Overall Put/Call",
  },
  "overview.OptionsMarketTide.putVolumeCallVolume": {
    zh: "Put 量 ÷ Call 量",
    en: "Put volume ÷ Call volume",
  },
  "overview.OptionsMarketTide.averageATMIV": {
    zh: "平均 ATM IV",
    en: "Average ATM IV",
  },
  "overview.OptionsMarketTide.07DayVolumeShare": {
    zh: "0–7 天成交占比",
    en: "0–7-day volume share",
  },
  "overview.OptionsMarketTide.mostlyNearTermContracts": {
    zh: "短线合约为主",
    en: "Mostly near-term contracts",
  },
  "overview.OptionsMarketTide.moreBalancedExpiryMix": {
    zh: "期限分布较均衡",
    en: "More balanced expiry mix",
  },
  "overview.OptionsMoneyTargets.wherePremiumConcentrates": {
    zh: "资金押注的价位",
    en: "Where premium concentrates",
  },
  "overview.OptionsMoneyTargets.oTMPremiumWeightedStrikesProtectionTarget": {
    zh: "价外成交额加权行权价 ； 保护 / 目标",
    en: "OTM premium-weighted strikes; protection / target",
  },
  "overview.OptionsMoneyTargets.premiumTargetWindow": {
    zh: "资金目标窗口",
    en: "Premium target window",
  },
  "overview.OptionsMoneyTargets.expiresWithin1Week": {
    zh: "1 周内到期",
    en: "Expires within 1 week",
  },
  "overview.OptionsMoneyTargets.expiresWithin1Month": {
    zh: "1 月内到期",
    en: "Expires within 1 month",
  },
  "overview.OptionsMoneyTargets.protectionTarget": {
    zh: "{s} ； 保护 {number}（{value}）； 目标 {number2}（{value2}）",
    en: "{s}; protection {number} ({value}); target {number2} ({value2})",
  },
  "overview.OptionsMoneyTargets.protectionTarget2": {
    zh: "保护 / 目标",
    en: "Protection / target",
  },
  "overview.OptionsMoneyTargets.theAxisSpans5OutOfRangePointsClamp": {
    zh: "横轴 ±5%，超出范围夹在边缘，百分比保留真实值。1 月窗口 = 35 个自然日内到期；这是资金聚集价位，不是预测目标价。",
    en: "The axis spans ±5%. Out-of-range points clamp to the edge; percentages retain actual values. The 1-month window includes expiry within 35 calendar days. These are premium concentration levels, not forecast price targets.",
  },
  "overview.OptionsMoveLadder.impliedMoveIntervalComparison": {
    zh: "隐含波动区间对比",
    en: "Implied move interval comparison",
  },
  "overview.OptionsMoveLadder.5DayBright20DayFaint80IntervalsSame": {
    zh: "5 日（亮）/ 20 日（淡）； 80% 区间 ； 同刻度",
    en: "5-day (bright) / 20-day (faint); 80% intervals; same scale",
  },
  "overview.OptionsMoveLadder.impliedIntervalSorting": {
    zh: "隐含区间排序",
    en: "Implied interval sorting",
  },
  "overview.OptionsMoveLadder.sortBy": {
    zh: "按",
    en: "Sort by",
  },
  "overview.OptionsMoveLadder.days": {
    zh: "{value} 日 {number}–{number2}",
    en: "{value} days {number}–{number2}",
  },
  "overview.OptionsMoveLadder.5Day20Day": {
    zh: "5 日 / 20 日",
    en: "5-day / 20-day",
  },
  "overview.OptionsMoveLadder.usesExistingImpliedMovesFromTheSameSnapshotMissing": {
    zh: "沿用同快照的已有 implied_moves；缺失时不补算、不外推。",
    en: "Uses existing implied_moves from the same snapshot; missing values are not recalculated or extrapolated.",
  },
  "overview.OptionsOutlookTable.optionsOutlook2": {
    zh: "期权对未来的展望",
    en: "Options outlook",
  },
  "overview.OptionsOutlookTable.1Week1MonthRiskNeutralIntervalsClickFor": {
    zh: "1 周 / 1 月风险中性区间 ； 单击看分布，双击或回车进研究页",
    en: "1-week / 1-month risk-neutral intervals; click for distribution, double-click or Enter for research",
  },
  "overview.OptionsOutlookTable.1Week80Interval": {
    zh: "1 周 80% 区间",
    en: "1-week 80% interval",
  },
  "overview.OptionsOutlookTable.1Month80Interval": {
    zh: "1 月 80% 区间",
    en: "1-month 80% interval",
  },
  "overview.OverviewHeaderBand.down": {
    zh: "跌",
    en: "Down",
  },
  "overview.OverviewHeaderBand.up": {
    zh: "涨",
    en: "Up",
  },
  "overview.OptionsOutlookTable.5": {
    zh: "超 5%",
    en: ">5%",
  },
  "overview.OptionsOutlookTable.probabilityDown10ProbabilityUp10": {
    zh: "跌超 10% 概率 ÷ 涨超 10% 概率",
    en: "Probability down >10% ÷ probability up >10%",
  },
  "overview.OptionsOutlookTable.tailRatio": {
    zh: "尾部比",
    en: "Tail ratio",
  },
  "overview.OptionsOutlookTable.largeFitError": {
    zh: "；拟合误差较大",
    en: "; large fit error",
  },
  "overview.OptionsOutlookTable.largeFitError2": {
    zh: "拟合误差较大",
    en: "Large fit error",
  },
  "overview.OptionsOutlookTable.classificationRules": {
    zh: "判断规则：",
    en: "Classification rules:",
  },
  "overview.OptionsOutlookTable.probabilityDown1013ProbabilityUp10And": {
    zh: "跌超 10% 概率 ≥ 涨超 10% 的 1.3 倍且偏斜 ≥ 4 点；",
    en: "Probability down >10% ≥ 1.3× probability up >10% and skew ≥ 4 points;",
  },
  "overview.OptionsOutlookTable.probabilitiesOfUpAndDown10Both17": {
    zh: "涨、跌超 10% 的概率都 ≥ 17%；",
    en: "Probabilities of up and down >10% both ≥17%;",
  },
  "overview.OptionsOutlookTable.oTMCallPremiumShare60AndProbabilityUp5": {
    zh: "价外成交额 ≥ 60% 在 Call 且涨超 5% 概率不低于跌超 5%；其余",
    en: "OTM Call premium share ≥60% and probability up >5% ≥ probability down >5%; otherwise",
  },
  "overview.OptionsOutlookTable.probabilityDerivativeOfOptionPriceWithRespectToStrike":
    {
      zh: "。概率 = 各行权价期权价格对行权价求导（−∂C/∂K），r 取 0；这是市场定价隐含的风险中性概率，不是预测。单击看分布，双击进研究页。缺失判断指标时显示“指标不足”。",
      en: ". Probability = derivative of option price with respect to strike (−∂C/∂K), with r = 0. These are market-implied risk-neutral probabilities, not forecasts. Click for distribution; double-click for research. Missing classification inputs show “Insufficient metrics”.",
    },
  "overview.OptionsPulseTable.25Skew": {
    zh: "25Δ 偏斜",
    en: "25Δ skew",
  },
  "overview.OptionsPulseTable.termStructure": {
    zh: "期限结构",
    en: "Term structure",
  },
  "overview.OptionsPulseTable.5DayImplied80": {
    zh: "5 日隐含 80%",
    en: "5-day implied 80%",
  },
  "overview.OptionsPulseTable.model5DayQ10Q90": {
    zh: "模型 5 日 q10–q90",
    en: "Model 5-day q10–q90",
  },
  "overview.OptionsPulseTable.data": {
    zh: "数据",
    en: "Data",
  },
  "overview.OptionsPulseTable.aTMIVOverTheLast20AccumulatedTradingDays": {
    zh: "最近 20 个已积累交易日 ATM IV",
    en: "ATM IV over the last 20 accumulated trading days",
  },
  "overview.OptionsPulseTable.10InstrumentOptionsPulse": {
    zh: "10 标的期权脉搏",
    en: "10-instrument Options Pulse",
  },
  "overview.OptionsPulseTable.defaultsToModelMarketIntervalDivergenceRankingIVIs": {
    zh: "默认按模型与市场区间分歧排序；IV 为年化 %，差值为百分点。历史不足时显示已积累天数。",
    en: "Defaults to model / market interval divergence ranking. IV is annualized %; differences are percentage points. Accumulated days are shown when history is insufficient.",
  },
  "overview.OptionsPulseTable.noCompleteSnapshot": {
    zh: "尚无完整快照",
    en: "No complete snapshot",
  },
  "overview.OptionsPulseTable.partialCoverage": {
    zh: "部分覆盖 ；",
    en: "Partial coverage;",
  },
  "overview.OptionsPulseTable.coverageTradingDaysAccumulated": {
    zh: "{value} ； 覆盖 {percentage} ； {value2}已积累 {historyDays} 个交易日",
    en: "{value}; coverage {percentage}; {value2}{historyDays} trading days accumulated",
  },
  "overview.OptionsPulseTable.slopeThresholdCalendarDay": {
    zh: "斜率阈值 ±{termSlopeThreshold} / 自然日",
    en: "Slope threshold ±{termSlopeThreshold} / calendar day",
  },
  "overview.OptionsPulseTable.symmetric80Interval": {
    zh: "对称 80% 区间",
    en: "Symmetric 80% interval",
  },
  "overview.OptionsPulseTable.modelImplied80IntervalWidth13MeansThe": {
    zh: "模型 / 隐含 80% 区间宽度；<0.7 市场定价的波动明显更大，>1.3 模型区间明显更宽",
    en: "Model / implied 80% interval width; <0.7 means market-priced volatility is much higher, >1.3 means the model interval is much wider",
  },
  "overview.OptionsPulseTable.saved": {
    zh: "已保存",
    en: "Saved",
  },
  "overview.OptionsPulseTable.noCompleteSavedOptionsSnapshotMetricsAndHistoryAre": {
    zh: "尚无已保存的完整期权快照；不生成指标或历史。",
    en: "No complete saved options snapshot; metrics and history are not fabricated.",
  },
  "overview.OptionsSkewRank.skewProtectionVsUpside": {
    zh: "偏斜 ； 防跌 vs 追涨",
    en: "Skew; protection vs upside",
  },
  "overview.OptionsSkewRank.approx5OTMPutIVCallIVExpiryNearest": {
    zh: "约 5% 价外 Put IV − Call IV ； 最接近 30 天到期",
    en: "Approx. 5% OTM Put IV − Call IV; expiry nearest 30 days",
  },
  "overview.OptionsSkewRank.upsideDemand": {
    zh: "← 追涨",
    en: "← Upside demand",
  },
  "overview.OptionsSkewRank.downsideProtection": {
    zh: "防跌 →",
    en: "Downside protection →",
  },
  "overview.OptionsSkewRank.putCallIVByExpiry": {
    zh: "各到期日 Put ； Call IV",
    en: "Put / Call IV by expiry",
  },
  "overview.OptionsSkewRank.noExpiry": {
    zh: "无到期日",
    en: "No expiry",
  },
  "overview.OptionsSkewRank.days": {
    zh: "{expiration} ； {dte} 天",
    en: "{expiration}; {dte} days",
  },
  "overview.OptionsSkewRank.linearInterpolationRequiresEligibleIVOnBothSidesOf": {
    zh: "在 0.95×现价 / 1.05×现价两侧有合格 IV 时线性插值；不是 25Δ 偏斜。正值表示保护型 Put 相对更贵。",
    en: "Linear interpolation requires eligible IV on both sides of 0.95× / 1.05× spot. This is not 25Δ skew. Positive values indicate relatively expensive protective Puts.",
  },
  "overview.OptionsVerdict.todaySOptionsSummary": {
    zh: "今日期权结论",
    en: "Today's options summary",
  },
  "overview.OptionsVerdict.nextMonth": {
    zh: "未来 1 个月：",
    en: "Next month:",
  },
  "overview.OptionsVerdict.noClassifiableData": {
    zh: "尚无可归类数据",
    en: "No classifiable data",
  },
  "overview.OptionsVerdict.sPYProbabilityDown5": {
    zh: "SPY 跌超 5% 的概率",
    en: "SPY probability down >5%",
  },
  "overview.OptionsVerdict.probabilityUp5": {
    zh: "，涨超 5% 的概率",
    en: ", probability up >5%",
  },
  "overview.OptionsVerdict.theFormerIsAboutTheLatter": {
    zh: "，前者约为后者的 {number} 倍。",
    en: "; the former is about {number}× the latter.",
  },
  "overview.OptionsVerdict.upsideTailProbabilityIsZeroSoNoRatioIs": {
    zh: "，上涨尾概率为零，不计算倍数。",
    en: "; upside tail probability is zero, so no ratio is calculated.",
  },
  "overview.OptionsVerdict.premium": {
    zh: "资金：",
    en: "Premium:",
  },
  "overview.OptionsVerdict.oTMCallEstimatedTurnover": {
    zh: "价外 Call 估算成交额",
    en: "OTM Call estimated turnover",
  },
  "overview.OptionsVerdict.oTMPut": {
    zh: "，价外 Put",
    en: ", OTM Put",
  },
  "overview.OptionsVerdict.oTMPremium": {
    zh: "价外资金",
    en: "OTM premium:",
  },
  "overview.OptionsVerdict.orMoreInCalls": {
    zh: "以上进 Call；",
    en: "or more in Calls;",
  },
  "overview.OptionsVerdict.largestSkew": {
    zh: "，偏斜最大：",
    en: "; largest skew:",
  },
  "overview.OptionsVerdict.premiumFlowsToProtectiveContracts": {
    zh: "，资金进入保护型合约。",
    en: "; premium flows to protective contracts.",
  },
  "overview.OptionsVerdict.volatility": {
    zh: "波动：",
    en: "Volatility:",
  },
  "overview.OptionsVerdict.highestIV": {
    zh: "IV 最高",
    en: "Highest IV",
  },
  "overview.OptionsVerdict.instrumentsWithInvertedTermStructures": {
    zh: "个标的期限倒挂；",
    en: "instruments with inverted term structures;",
  },
  "overview.OptionsVerdict.instrumentsHaveIVAboveRecent20DayRealizedVolatility": {
    zh: "{count}/{count2} 个标的 IV 高于近 20 日实际波动",
    en: "{count}/{count2} instruments have IV above recent 20-day realized volatility",
  },
  "overview.OptionsVerdict.noComparable20DayRealizedVolatility": {
    zh: "缺少可比较的 20 日实际波动",
    en: "No comparable 20-day realized volatility",
  },
  "overview.OptionsVerdict.relativelyCheaper": {
    zh: "，{symbols} 偏便宜",
    en: "; {symbols} relatively cheaper",
  },
  "overview.OptionsVerdict.marketWide": {
    zh: "；全市场",
    en: "; market-wide",
  },
  "overview.OptionsVerdict.ofEstimatedTurnoverIsInCallsTheLargestTurnover": {
    zh: "的估算成交额在 Call，成交额最大的是",
    en: "of estimated turnover is in Calls; the largest turnover is in",
  },
  "overview.OptionsVerdict.probabilitiesAreOptionsImpliedRiskNeutralProbabilitiesModelsAre":
    {
      zh: "概率为期权价格隐含的风险中性概率，模型均未发布。成交额按成交量 × 买卖中间价 × 合约乘数估算；不代表主动买入或卖出。",
      en: "Probabilities are options-implied risk-neutral probabilities; models are unpublished. Turnover is estimated as volume × bid/ask midpoint × contract multiplier; it does not indicate active buying or selling.",
    },
  "overview.OptionsVerdict.instrumentsWithMissingMetricsAreExcludedFromTheRelevant": {
    zh: "部分标的指标缺失，未参与相应排名或归类。",
    en: "Instruments with missing metrics are excluded from the relevant rankings or classifications.",
  },
  "overview.overviewInsights.awaitingCompatibleHistoryToAssessMarketParticipation": {
    zh: "等待兼容历史确认市场参与度",
    en: "Awaiting compatible history to assess market participation",
  },
  "overview.overviewInsights.marketParticipationImproving": {
    zh: "市场参与度改善",
    en: "Market participation improving",
  },
  "overview.overviewInsights.marketParticipationWeakening": {
    zh: "市场参与度走弱",
    en: "Market participation weakening",
  },
  "overview.overviewInsights.limitedChangeInMarketParticipation": {
    zh: "市场参与度变化有限",
    en: "Limited change in market participation",
  },
  "overview.overviewInsights.but": {
    zh: "，但",
    en: ", but",
  },
  "overview.overviewInsights.sectorWeakeningAgainstTheTrend": {
    zh: "{breadthClause}{conjunction}{sector}板块逆势走弱",
    en: "{breadthClause}{conjunction}{sector} sector weakening against the trend",
  },
  "overview.overviewInsights.requiresAtLeastTwoCompatibleSnapshotsWithMarketProxy": {
    zh: "需要至少两个兼容快照并保留指数代理行情",
    en: "Requires at least two compatible snapshots with market proxy prices",
  },
  "overview.overviewInsights.breadthFourProxyAverageSessionReturn": {
    zh: "宽度 {change}；四大代理平均当日 {value}%",
    en: "Breadth {change}; four-proxy average session return {value}%",
  },
  "overview.overviewInsights.breadthAndIndicesAligned": {
    zh: "宽度与指数同向",
    en: "Breadth and indices aligned",
  },
  "overview.overviewInsights.breadthAndIndicesDiverging": {
    zh: "宽度与指数分歧",
    en: "Breadth and indices diverging",
  },
  "overview.overviewInsights.aligned": {
    zh: "同步",
    en: "Aligned",
  },
  "overview.overviewInsights.diverging": {
    zh: "背离",
    en: "Diverging",
  },
  "overview.overviewInsights.atLeast5SectorSamplesRequiredInBothCurrent": {
    zh: "当前与上个快照均需至少 5 个行业样本",
    en: "At least 5 sector samples required in both current and previous snapshots",
  },
  "overview.overviewInsights.uptrendShare": {
    zh: "趋势偏多占比 {value}% → {value2}%",
    en: "Uptrend share {value}% → {value2}%",
  },
  "overview.overviewInsights.weakestSector": {
    zh: "最弱行业",
    en: "Weakest sector",
  },
  "overview.overviewInsights.noCompatibleComparisonSnapshotChangeIsNotShownAs": {
    zh: "无兼容比较快照，不把变化显示为零",
    en: "No compatible comparison snapshot; change is not shown as zero",
  },
  "overview.overviewInsights.toUptrendToDowntrend": {
    zh: "转多 {toUptrend}；转空 {toDowntrend}",
    en: "To uptrend {toUptrend}; to downtrend {toDowntrend}",
  },
  "overview.OverviewPulse.instruments": {
    zh: "{transitions} 个",
    en: "{transitions} instruments",
  },
  "overview.overviewInsights.threshold20DayVolumeZ2": {
    zh: "阈值为 |20 日成交量 Z| ≥ 2",
    en: "Threshold: |20-day volume Z| ≥ 2",
  },
  "overview.OverviewPulse.volumeAnomalies": {
    zh: "成交量异常",
    en: "Volume anomalies",
  },
  "overview.overviewInsights.completeChampionModelAvailableInTheCurrentSnapshot": {
    zh: "当前快照已发布模型预测；验证状态请查看模型门禁。",
    en: "Model forecasts are published; check the model gates for validation status.",
  },
  "overview.overviewInsights.researchEvidenceAvailableNoExecutableSignals": {
    zh: "研究证据可用，但不产生可执行信号",
    en: "Research evidence available; no executable signals",
  },
  "overview.overviewInsights.ready": {
    zh: "已就绪",
    en: "Ready",
  },
  "overview.overviewInsights.instrumentsUncovered": {
    zh: "未覆盖 {abstainCount} 个",
    en: "{abstainCount} instruments uncovered",
  },
  "overview.overviewSummary.noBroadMarketProxyPrices": {
    zh: "暂无大盘代理行情",
    en: "No broad-market proxy prices",
  },
  "overview.overviewSummary.unchanged": {
    zh: " {unchanged} 平",
    en: ", {unchanged} unchanged",
  },
  "overview.overviewSummary.marketProxiesUpDown": {
    zh: "大盘代理 {advancers} 涨 {decliners} 跌{value}",
    en: "Market proxies: {advancers} up, {decliners} down{value}",
  },
  "overview.overviewSummary.completeRegimeDistributionUnavailable": {
    zh: "市场状态暂无完整分布",
    en: "Complete regime distribution unavailable",
  },
  "overview.overviewSummary.hasTheLargestShare": {
    zh: "{value}占比最高 {percentage}",
    en: "{value} has the largest share: {percentage}",
  },
  "overview.overviewSummary.marketBreadth": {
    zh: "{proxyClause}；{regimeClause}；市场宽度 {percentage}。",
    en: "{proxyClause}; {regimeClause}; market breadth {percentage}.",
  },
  "overview.OverviewHeaderBand.marketSummary": {
    zh: "市场概况",
    en: "Market summary",
  },
  "overview.OverviewPulse.uptrendShare": {
    zh: "趋势偏多占比",
    en: "Uptrend share",
  },
  "overview.OverviewPulse.unchanged": {
    zh: "平",
    en: "unchanged",
  },
  "overview.OverviewPage.marketOverview": {
    zh: "市场总览",
    en: "Market overview",
  },
  "overview.OverviewHeaderBand.marketOverviewTodaySBriefing": {
    zh: "市场总览 ； 今日简报",
    en: "Market overview; today's briefing",
  },
  "overview.OverviewHeaderBand.selectedSnapshotMarketCutoff": {
    zh: "所选快照行情截至",
    en: "Selected snapshot market cutoff",
  },
  "overview.OverviewHeaderBand.marketContext": {
    zh: "市场背景",
    en: "Market context",
  },
  "overview.OverviewHeaderBand.sectorDrivers": {
    zh: "行业驱动",
    en: "Sector drivers",
  },
  "overview.OverviewHeaderBand.insufficientSectorEvidence": {
    zh: "行业证据不足",
    en: "Insufficient sector evidence",
  },
  "overview.OverviewHeaderBand.20DayPositiveReturnBreadth": {
    zh: "；20 日正收益宽度",
    en: "; 20-day positive-return breadth",
  },
  "overview.OverviewHeaderBand.changeFromPrevious": {
    zh: "%，较上次",
    en: "%, change from previous",
  },
  "overview.OverviewHeaderBand.unusualMoves": {
    zh: "异动",
    en: "Unusual moves",
  },
  "overview.OverviewHeaderBand.upwardBreakouts": {
    zh: "个向上突破、",
    en: "upward breakouts,",
  },
  "overview.OverviewHeaderBand.downwardBreakouts": {
    zh: "个向下突破、",
    en: "downward breakouts,",
  },
  "overview.OverviewHeaderBand.volumeAnomalies": {
    zh: "个量能异常；",
    en: "volume anomalies;",
  },
  "overview.OverviewHeaderBand.regimeChanges": {
    zh: "个状态切换",
    en: "regime changes",
  },
  "overview.OverviewHeaderBand.historicalSnapshot": {
    zh: "历史快照",
    en: "Historical snapshot",
  },
  "overview.OverviewHeaderBand.selectedSnapshot": {
    zh: "所选快照",
    en: "Selected snapshot",
  },
  "overview.OverviewHeaderBand.generatedFromCurrentSnapshotFiguresAndRules": {
    zh: "； 由当前快照数字与规则生成",
    en: "; generated from current snapshot figures and rules",
  },
  "overview.OverviewHeaderBand.formalForecastsExcludeInstrumentsResearchRankingModelTaskCoverage":
    {
      zh: "正式预测未覆盖 {abstainCount} 个标的；研究排序标的 {count}；模型任务覆盖 {coverage}，非准确率；研究结果不可执行",
      en: "Formal forecasts exclude {abstainCount} instruments; research ranking {count}; model task coverage {coverage}, not accuracy; research results are non-executable",
    },
  "overview.OverviewHeaderBand.formalExecutableOpportunities": {
    zh: "正式可执行机会",
    en: "Formal executable opportunities",
  },
  "overview.OverviewPage.researchRanking": {
    zh: "研究排序",
    en: "Research ranking",
  },
  "overview.OverviewHeaderBand.rows": {
    zh: "条",
    en: "rows",
  },
  "overview.OverviewHeaderBand.nonExecutable": {
    zh: "不可执行",
    en: "Non-executable",
  },
  "overview.OverviewHeaderBand.modelTaskCoverageNotAccuracy": {
    zh: "模型任务覆盖，非准确率",
    en: "Model task coverage, not accuracy",
  },
  "overview.OverviewHeaderBand.modelGateDetails": {
    zh: "模型门禁详情 →",
    en: "Model gate details →",
  },
  "overview.OverviewHeaderBand.formalForecastsAwaitACompleteChampionModelInstrumentsWithout":
    {
      zh: "正式预测仍等待完整主模型。正式预测未覆盖：",
      en: "Formal forecasts await a complete Champion model. Instruments without formal forecasts:",
    },
  "overview.SectorMarketMap.instruments": {
    zh: "个标的",
    en: "instruments",
  },
  "overview.OverviewHeaderBand.optionsSentiment": {
    zh: "期权情绪",
    en: "Options sentiment",
  },
  "overview.OverviewHeaderBand.optionsSentiment2": {
    zh: "； 期权情绪",
    en: "; options sentiment",
  },
  "overview.OverviewHeaderBand.relativelyExpensiveOptions": {
    zh: "期权偏贵",
    en: "Relatively expensive options",
  },
  "overview.OverviewHeaderBand.relativelyCheaperOptions": {
    zh: "期权偏便宜",
    en: "Relatively cheaper options",
  },
  "overview.OverviewHeaderBand.unchanged": {
    zh: "持平",
    en: "Unchanged",
  },
  "overview.OverviewHeaderBand.normalUpwardCurve": {
    zh: "正常向上",
    en: "Normal upward curve",
  },
  "overview.OverviewHeaderBand.termStructureUnconfirmed": {
    zh: "期限结构未确认",
    en: "Term structure unconfirmed",
  },
  "overview.OverviewHeaderBand.marketTimeUnconfirmed": {
    zh: "市场时刻未确认",
    en: "Market time unconfirmed",
  },
  "overview.OverviewHeaderBand.coverage": {
    zh: "； 覆盖",
    en: "; coverage",
  },
  "overview.OverviewHeaderBand.optionsRequestFailed": {
    zh: "期权读取失败",
    en: "Options request failed",
  },
  "overview.OverviewHeaderBand.loadingOptionsSnapshot": {
    zh: "正在读取期权快照…",
    en: "Loading options snapshot…",
  },
  "overview.OverviewHeaderBand.openOptionsPulseToRetry": {
    zh: "前往期权脉搏重新读取 →",
    en: "Open Options Pulse to retry →",
  },
  "overview.OverviewHistoryPanels.market": {
    zh: "市场",
    en: "Market",
  },
  "overview.OverviewPage.risksCoverage": {
    zh: "风险与覆盖",
    en: "Risks & coverage",
  },
  "overview.OverviewHistoryPanels.chapters": {
    zh: "章节",
    en: "Chapters",
  },
  "overview.OverviewHistoryPanels.marketSectorsInstrumentsResearch": {
    zh: "市场 → 行业 → 标的 → 研究",
    en: "Market → Sectors → Instruments → Research",
  },
  "overview.OverviewHistoryPanels.viewData": {
    zh: "查看{title}数据",
    en: "View {title} data",
  },
  "overview.OverviewHistoryPanels.data": {
    zh: "{title}数据",
    en: "{title} data",
  },
  "overview.OverviewHistoryPanels.index": {
    zh: "指数",
    en: "Index",
  },
  "overview.OverviewHistoryPanels.rawDailyBarsTradingDays": {
    zh: "{symbol}原始日线，{count}个交易日",
    en: "{symbol} raw daily bars, {count} trading days",
  },
  "overview.OverviewHistoryPanels.sP500": {
    zh: "标普 500",
    en: "S&P 500",
  },
  "overview.OverviewHistoryPanels.nasdaq100": {
    zh: "纳斯达克 100",
    en: "Nasdaq 100",
  },
  "overview.OverviewHistoryPanels.dowJones": {
    zh: "道琼斯",
    en: "Dow Jones",
  },
  "overview.OverviewHistoryPanels.russell2000": {
    zh: "罗素 2000",
    en: "Russell 2000",
  },
  "overview.OverviewHistoryPanels.fourMajorIndexQuotes": {
    zh: "四大指数行情",
    en: "Four major index quotes",
  },
  "overview.OverviewHistoryPanels.openResearch": {
    zh: "{symbol} {value}，查看研究",
    en: "{symbol} {value}, open research",
  },
  "overview.OverviewHistoryPanels.insufficientData": {
    zh: "数据不足",
    en: "Insufficient data",
  },
  "overview.OverviewHistoryPanels.fourIndexRelativePerformance": {
    zh: "四指数相对走势",
    en: "Four-index relative performance",
  },
  "overview.OverviewHistoryPanels.rebasedToZeroLast60TradingDaysUnadjusted": {
    zh: "起点归零 ； 近 60 个交易日 ； 未复权",
    en: "Rebased to zero; last 60 trading days; unadjusted",
  },
  "overview.OverviewHistoryPanels.above20DayMovingAverage": {
    zh: "站上 20 日均线",
    en: "Above 20-day moving average",
  },
  "overview.OverviewHistoryPanels.above50DayMovingAverage": {
    zh: "站上 50 日均线",
    en: "Above 50-day moving average",
  },
  "overview.OverviewHistoryPanels.eligible": {
    zh: "有效 {n50} / {count}",
    en: "Eligible {n50} / {count}",
  },
  "overview.OverviewHistoryPanels.60DayHighsLows": {
    zh: "60 日新高 / 新低",
    en: "60-day highs / lows",
  },
  "overview.OverviewHistoryPanels.eligible2": {
    zh: "有效 {n60}",
    en: "Eligible {n60}",
  },
  "overview.OverviewHistoryPanels.advancersDeclinersADRatio": {
    zh: "涨跌家数 / A/D 比",
    en: "Advancers / decliners / A/D ratio",
  },
  "overview.OverviewHistoryPanels.medianReturn": {
    zh: "中位涨跌",
    en: "Median return",
  },
  "overview.OverviewHistoryPanels.sPY20Day": {
    zh: "SPY 20 日 {returnValue}",
    en: "SPY 20-day {returnValue}",
  },
  "overview.OverviewHistoryPanels.crossSectionNotAForecastInterval": {
    zh: "横截面，不是预测区间",
    en: "Cross-section, not a forecast interval",
  },
  "overview.OverviewHistoryPanels.sPYClose": {
    zh: "SPY 收盘",
    en: "SPY close",
  },
  "overview.OverviewHistoryPanels.aboveDayMovingAverage": {
    zh: "站上 {count} 日均线",
    en: "Above {count}-day moving average",
  },
  "overview.OverviewHistoryPanels.60DayHighs": {
    zh: "60 日新高",
    en: "60-day highs",
  },
  "overview.OverviewHistoryPanels.60DayLows": {
    zh: "60 日新低",
    en: "60-day lows",
  },
  "overview.OverviewHistoryPanels.priceBreadthDivergence": {
    zh: "价格与宽度背离",
    en: "Price / breadth divergence",
  },
  "overview.OverviewHistoryPanels.20DayPriceDirectionOpposesMovingAverageBreadthChange":
    {
      zh: "20 日价格方向与均线宽度变化相反",
      en: "20-day price direction opposes moving-average breadth change",
    },
  "overview.OverviewHistoryPanels.above20DayMA": {
    zh: "站上 20 日 {percentage} → {percentage2}",
    en: "Above 20-day MA {percentage} → {percentage2}",
  },
  "overview.OverviewHistoryPanels.breadthThrust": {
    zh: "宽度推力",
    en: "Breadth thrust",
  },
  "overview.OverviewHistoryPanels.fromBelow40ToAbove60Within10Days": {
    zh: "10 日内从低于 40% 上升至超过 60%",
    en: "From below 40% to above 60% within 10 days",
  },
  "overview.OverviewHistoryPanels.eligibleDays10": {
    zh: "有效日数 {count}/10",
    en: "Eligible days {count}/10",
  },
  "overview.OverviewHistoryPanels.broadParticipation": {
    zh: "广泛参与",
    en: "Broad participation",
  },
  "overview.OverviewHistoryPanels.advancers90OfAdvancersPlusDecliners": {
    zh: "上涨家数占涨跌家数 ≥ 90%",
    en: "Advancers ≥90% of advancers plus decliners",
  },
  "overview.OverviewHistoryPanels.advancingMoving": {
    zh: "上涨 {advancers} / 涨跌 {value}",
    en: "Advancing {advancers} / moving {value}",
  },
  "overview.OverviewHistoryPanels.concentratedSellingPressure": {
    zh: "卖压集中",
    en: "Concentrated selling pressure",
  },
  "overview.OverviewHistoryPanels.decliners90Or60DayLows20": {
    zh: "下跌占比 ≥ 90%，或 60 日新低 ≥ 20%",
    en: "Decliners ≥90%, or 60-day lows ≥20%",
  },
  "overview.OverviewHistoryPanels.newLows": {
    zh: "新低 {low} 个（{percentage}）",
    en: "New lows {low} ({percentage})",
  },
  "overview.OverviewHistoryPanels.previous2": {
    zh: "上次 {value}",
    en: "Previous {value}",
  },
  "overview.OverviewHistoryPanels.current": {
    zh: "本次 {value}",
    en: "Current {value}",
  },
  "overview.OverviewHistoryPanels.marketBreadthHistory": {
    zh: "市场宽度历史",
    en: "Market breadth history",
  },
  "overview.OverviewHistoryPanels.calculatedFromObservedRawDailyBarsEligible2050": {
    zh: "真实原始日线计算 ； 有效 20/50/60 日样本 {n20}/{n50}/{n60}",
    en: "Calculated from observed raw daily bars; eligible 20/50/60-day samples {n20}/{n50}/{n60}",
  },
  "overview.OverviewHistoryPanels.breadthSignals": {
    zh: "宽度信号",
    en: "Breadth signals",
  },
  "overview.OverviewHistoryPanels.fourFixedRulesNotTradingSignals": {
    zh: "四条固定规则复述，非交易信号",
    en: "Four fixed rules; not trading signals",
  },
  "overview.OverviewHistoryPanels.triggered": {
    zh: "已触发",
    en: "Triggered",
  },
  "overview.OverviewHistoryPanels.notTriggered": {
    zh: "未触发",
    en: "Not triggered",
  },
  "overview.OverviewHistoryPanels.marketRegimeComposition": {
    zh: "市场状态构成",
    en: "Market regime composition",
  },
  "overview.OverviewHistoryPanels.dailyBarsWithEligibleRegimeSamplesLastTradingDays": {
    zh: "日线有效状态样本 {value}/{count} ； 近 {count2} 个交易日",
    en: "Daily bars with eligible regime samples {value}/{count}; last {count2} trading days",
  },
  "overview.OverviewHistoryPanels.regimeTransitions": {
    zh: "状态转移",
    en: "Regime transitions",
  },
  "overview.OverviewHistoryPanels.currentTwoCompatibleSnapshots": {
    zh: "{time} → 本次 ； 两个兼容快照",
    en: "{time} → current; two compatible snapshots",
  },
  "overview.OverviewHistoryPanels.insufficientHistoryNoCompatibleSnapshot": {
    zh: "历史不足，没有兼容快照",
    en: "Insufficient history; no compatible snapshot",
  },
  "overview.OverviewHistoryPanels.unusuallyHighVolume": {
    zh: "量能异常放大",
    en: "Unusually high volume",
  },
  "overview.OverviewHistoryPanels.unusuallyLowVolume": {
    zh: "量能异常萎缩",
    en: "Unusually low volume",
  },
  "overview.OverviewHistoryPanels.changedTo": {
    zh: "转为{value}",
    en: "Changed to {value}",
  },
  "overview.OverviewHistoryPanels.aboveUpperChannel": {
    zh: "通道上沿之外",
    en: "Above upper channel",
  },
  "overview.OverviewHistoryPanels.belowLowerChannel": {
    zh: "通道下沿之外",
    en: "Below lower channel",
  },
  "overview.OverviewHistoryPanels.20DayGainers": {
    zh: "20 日领涨",
    en: "20-day gainers",
  },
  "overview.OverviewHistoryPanels.20DayLosers": {
    zh: "20 日领跌",
    en: "20-day losers",
  },
  "overview.OverviewHistoryPanels.ruleSignalBoard": {
    zh: "规则信号板",
    en: "Rule signal board",
  },
  "overview.OverviewHistoryPanels.instrumentsTriggeringEachRuleInThisSnapshotRankedBy":
    {
      zh: "当前快照触发各类规则的标的，按强度排序",
      en: "Instruments triggering each rule in this snapshot, ranked by strength",
    },
  "overview.OverviewHistoryPanels.noneInThisSnapshot": {
    zh: "本快照无",
    en: "None in this snapshot",
  },
  "overview.OverviewHistoryPanels.20DayRelativeToSPY": {
    zh: "20日相对SPY %",
    en: "20-day relative to SPY %",
  },
  "overview.OverviewHistoryPanels.5DayRelativeToSPY": {
    zh: "5日相对SPY %",
    en: "5-day relative to SPY %",
  },
  "overview.OverviewHistoryPanels.leading": {
    zh: "领先",
    en: "Leading",
  },
  "overview.OverviewHistoryPanels.improving": {
    zh: "改善",
    en: "Improving",
  },
  "overview.OverviewHistoryPanels.lagging": {
    zh: "落后",
    en: "Lagging",
  },
  "overview.OverviewHistoryPanels.weakening": {
    zh: "转弱",
    en: "Weakening",
  },
  "overview.OverviewHistoryPanels.down": {
    zh: "下跌",
    en: "Down",
  },
  "overview.OverviewHistoryPanels.up": {
    zh: "上涨",
    en: "Up",
  },
  "overview.OverviewHistoryPanels.current2": {
    zh: "本次",
    en: "Current",
  },
  "overview.OverviewHistoryPanels.sectorPerformanceHeatmap": {
    zh: "行业表现热力表",
    en: "Sector performance heatmap",
  },
  "overview.OverviewHistoryPanels.medianConstituentReturns60DayReturnsFromObservedDaily":
    {
      zh: "成分股中位涨跌；60 日由真实日线计算",
      en: "Median constituent returns; 60-day returns from observed daily bars",
    },
  "overview.SectorMarketMap.samples": {
    zh: "样本",
    en: "Samples",
  },
  "overview.OverviewHistoryPanels.session": {
    zh: "当日",
    en: "Session",
  },
  "overview.OverviewHistoryPanels.60Days": {
    zh: "60 日",
    en: "60 days",
  },
  "overview.OverviewHistoryPanels.change": {
    zh: "变化",
    en: "Change",
  },
  "overview.OverviewHistoryPanels.sectorTrends": {
    zh: "行业走势",
    en: "Sector trends",
  },
  "overview.OverviewHistoryPanels.medianCumulativeConstituentReturnsLast60TradingDaysRaw":
    {
      zh: "成分股累计涨跌中位数 ； 近 60 个交易日 ； 原始价格",
      en: "Median cumulative constituent returns; last 60 trading days; raw prices",
    },
  "overview.OverviewHistoryPanels.sectorRotationQuadrants": {
    zh: "行业轮动象限",
    en: "Sector rotation quadrants",
  },
  "overview.OverviewHistoryPanels.20DayAnd5DayReturnsRelativeToSPY": {
    zh: "相对 SPY 的 20 日与 5 日收益 ； 近 10 日尾迹",
    en: "20-day and 5-day returns relative to SPY; last 10-day trails",
  },
  "overview.OverviewHistoryPanels.sectorParticipation": {
    zh: "行业参与度",
    en: "Sector participation",
  },
  "overview.OverviewHistoryPanels.barLengthRepresentsAdvancingDecliningSectorSharesMissingReturns":
    {
      zh: "条长为行业内上涨 / 下跌占比；缺失收益排除",
      en: "Bar length represents advancing / declining sector shares; missing returns excluded",
    },
  "overview.OverviewHistoryPanels.sectorHighsLows": {
    zh: "行业新高 / 新低",
    en: "Sector highs / lows",
  },
  "overview.OverviewHistoryPanels.60DayClosingHighsLowsAndMovingAverageParticipation": {
    zh: "收盘创 60 日新高、新低家数与均线参与",
    en: "60-day closing highs / lows and moving-average participation",
  },
  "overview.OverviewHistoryPanels.sectorHighsAndLows": {
    zh: "行业新高新低",
    en: "Sector highs and lows",
  },
  "overview.OverviewHistoryPanels.newHighs": {
    zh: "新高",
    en: "New highs",
  },
  "overview.OverviewHistoryPanels.newLows2": {
    zh: "新低",
    en: "New lows",
  },
  "overview.OverviewHistoryPanels.net": {
    zh: "净",
    en: "Net",
  },
  "overview.OverviewHistoryPanels.above20DayMA2": {
    zh: "站上20日",
    en: "Above 20-day MA",
  },
  "overview.OverviewHistoryPanels.above50DayMA": {
    zh: "站上50日",
    en: "Above 50-day MA",
  },
  "overview.OverviewHistoryPanels.eligibleHighsLowsSamples": {
    zh: "新高低有效样本",
    en: "Eligible highs / lows samples",
  },
  "overview.OverviewHistoryPanels.insufficientHistoryAndGapsExcluded": {
    zh: "；不足与缺口排除。",
    en: "; insufficient history and gaps excluded.",
  },
  "overview.OverviewHistoryPanels.changeInSectorUptrendShare": {
    zh: "行业趋势偏多占比变化",
    en: "Change in sector uptrend share",
  },
  "overview.OverviewHistoryPanels.twoCompatibleSnapshotsSectorsWithFewerThan5Samples": {
    zh: "两个兼容快照；样本不足 5 个的行业不参与比较",
    en: "Two compatible snapshots; sectors with fewer than 5 samples excluded",
  },
  "overview.SectorRegimeMatrix.sectorRegimeMatrix": {
    zh: "行业 × 状态矩阵",
    en: "Sector × regime matrix",
  },
  "overview.OverviewHistoryPanels.withinRowShareDarkerColorsMeanLargerShares": {
    zh: "行内占比，颜色越深占比越高",
    en: "Within-row share; darker colors mean larger shares",
  },
  "overview.OverviewHistoryPanels.actual": {
    zh: "实际 {xName}",
    en: "Actual {xName}",
  },
  "overview.OverviewHistoryPanels.sessionReturnDistribution": {
    zh: "当日涨跌分布",
    en: "Session return distribution",
  },
  "overview.OverviewHistoryPanels.eligible05BinsTailsCountedSeparately": {
    zh: "有效 {count}/{count2} ； 0.5% 分箱 ； 尾部独立计数",
    en: "Eligible {count}/{count2}; 0.5% bins; tails counted separately",
  },
  "overview.OverviewHistoryPanels.5Day20DayReturns": {
    zh: "5 日 × 20 日收益",
    en: "5-day × 20-day returns",
  },
  "overview.OverviewHistoryPanels.x5DayY20DayDisplayClippedAt": {
    zh: "横轴5日、纵轴20日；1/99分位裁边，空心点越界，悬停显示原值",
    en: "X: 5-day, Y: 20-day. Display clipped at 1st/99th percentiles; hollow points are outside; hover for raw values",
  },
  "overview.OverviewHistoryPanels.5Day": {
    zh: "5日 %",
    en: "5-day %",
  },
  "overview.OverviewHistoryPanels.20Day": {
    zh: "20日 %",
    en: "20-day %",
  },
  "overview.OverviewHistoryPanels.regimeDistribution": {
    zh: "状态分布",
    en: "Regime distribution",
  },
  "overview.OverviewHistoryPanels.currentSnapshotInstruments": {
    zh: "当前快照 ； {count} 个标的",
    en: "Current snapshot; {count} instruments",
  },
  "overview.OverviewHistoryPanels.20DayChannelPositionDistribution": {
    zh: "20 日通道位置分布",
    en: "20-day channel position distribution",
  },
  "overview.OverviewHistoryPanels.eligibleOutOfChannelValuesKeptInSeparateBins": {
    zh: "有效 {count}/{count2} ； 越出通道保留独立区间",
    en: "Eligible {count}/{count2}; out-of-channel values kept in separate bins",
  },
  "overview.OverviewHistoryPanels.volatility20DayReturn": {
    zh: "波动 × 20 日收益",
    en: "Volatility × 20-day return",
  },
  "overview.OverviewHistoryPanels.aTR2020DayReturnDisplayClippedAt1st99th": {
    zh: "ATR20 × 20日收益；1/99分位裁边，空心点越界，悬停显示原值",
    en: "ATR20 × 20-day return. Display clipped at 1st/99th percentiles; hollow points are outside; hover for raw values",
  },
  "overview.OverviewHistoryPanels.volumeAnomalyRanking": {
    zh: "量能异常榜",
    en: "Volume anomaly ranking",
  },
  "overview.OverviewHistoryPanels.rankedBy20DayVolumeZ2CountsAs": {
    zh: "按 |20 日成交量 Z| 排序，≥2 记为异常",
    en: "Ranked by |20-day volume Z|; ≥2 counts as unusual",
  },
  "overview.OverviewHistoryPanels.20DayPositiveReturnBreadth": {
    zh: "20日正收益宽度",
    en: "20-day positive-return breadth",
  },
  "overview.OverviewPulse.crossSectionalMedianReturn": {
    zh: "横截面中位收益",
    en: "Cross-sectional median return",
  },
  "overview.OverviewHistoryPanels.twoSnapshotComparison": {
    zh: "两次快照对比",
    en: "Two-snapshot comparison",
  },
  "overview.OverviewHistoryPanels.insufficientHistoryNoTwoCompatibleSnapshots": {
    zh: "历史不足，没有两个兼容快照",
    en: "Insufficient history; no two compatible snapshots",
  },
  "overview.OverviewHistoryPanels.metric": {
    zh: "指标",
    en: "Metric",
  },
  "overview.OverviewHistoryPanels.researchRankingEntriesExits": {
    zh: "研究排序进出",
    en: "Research ranking entries / exits",
  },
  "overview.OverviewHistoryPanels.comparedWithPreviousCompatibleSnapshotFormalModeExcludesCandidate":
    {
      zh: "较上个兼容快照；正式模式不展示候选排序",
      en: "Compared with previous compatible snapshot; formal mode excludes candidate rankings",
    },
  "overview.OverviewHistoryPanels.retained": {
    zh: "保留",
    en: "Retained",
  },
  "overview.OverviewHistoryPanels.exited": {
    zh: "退出",
    en: "Exited",
  },
  "overview.OverviewHistoryPanels.insufficientHistoryOrNoComparableRankingInThisMode": {
    zh: "历史不足或该模式没有可比较排序。",
    en: "Insufficient history or no comparable ranking in this mode.",
  },
  "overview.OverviewHistoryPanels.anomalySnapshotComparison": {
    zh: "异常快照对比",
    en: "Anomaly snapshot comparison",
  },
  "overview.OverviewHistoryPanels.currentVsPreviousCompatibleSnapshotMissingValuesNotFilled":
    {
      zh: "当前与上个兼容快照；缺失值不补零",
      en: "Current vs previous compatible snapshot; missing values not filled with zero",
    },
  "overview.OverviewHistoryPanels.dataStatus": {
    zh: "数据状态",
    en: "Data status",
  },
  "overview.OverviewHistoryPanels.statusAndExclusionReasonsReturnedByTheAPI": {
    zh: "接口返回的状态与排除原因",
    en: "Status and exclusion reasons returned by the API",
  },
  "overview.OverviewHistoryPanels.servingStatus": {
    zh: "服务状态",
    en: "Serving status",
  },
  "overview.OverviewHistoryPanels.forecastReadiness": {
    zh: "预测就绪",
    en: "Forecast readiness",
  },
  "overview.OverviewHistoryPanels.marketCutoff": {
    zh: "行情截至",
    en: "Market cutoff",
  },
  "overview.OverviewHistoryPanels.exclusionReasons": {
    zh: "排除原因",
    en: "Exclusion reasons",
  },
  "overview.OverviewHistoryPanels.none": {
    zh: "无",
    en: "None",
  },
  "overview.OverviewHistoryPanels.snapshot": {
    zh: "快照",
    en: "Snapshot",
  },
  "overview.OverviewPage.loadingOverviewEvidence": {
    zh: "正在加载总览证据",
    en: "Loading overview evidence",
  },
  "overview.OverviewPage.overviewDataUnavailable": {
    zh: "总览数据不可用",
    en: "Overview data unavailable",
  },
  "overview.OverviewPage.theSavedResearchSnapshotCannotBeReadUnverifiedProbabilities": {
    zh: "当前无法读取已保存的研究快照。页面不会展示未经验证的概率或收益值。",
    en: "The saved research snapshot cannot be read. Unverified probabilities or returns will not be displayed.",
  },
  "overview.OverviewPage.retryOverviewData": {
    zh: "重试总览数据",
    en: "Retry overview data",
  },
  "overview.OverviewPage.marketOverviewEvidence": {
    zh: "市场总览证据",
    en: "Market overview evidence",
  },
  "overview.OverviewPage.marketTicker": {
    zh: "行情滚动条",
    en: "Market ticker",
  },
  "overview.OverviewPage.fullCrossSectionalEvidenceUnavailable": {
    zh: "全量横截面证据不可用",
    en: "Full cross-sectional evidence unavailable",
  },
  "overview.OverviewPage.noObservedInstrumentsAvailableInThisSnapshotPlaceholderData": {
    zh: "当前快照没有可展示的真实标的，不生成占位数据。",
    en: "No observed instruments available in this snapshot; placeholder data is not generated.",
  },
  "overview.OverviewPage.whichSectorsAreDrivingOrDraggingTheMarket": {
    zh: "哪些行业在带动或拖累市场",
    en: "Which sectors are driving or dragging the market",
  },
  "overview.OverviewPage.nextInstruments": {
    zh: "下一章：标的 ↓",
    en: "Next: instruments ↓",
  },
  "overview.OverviewPage.whoSMovingTodayHowFarAndHowMoves": {
    zh: "今天谁在动、动得多大、整体如何分布",
    en: "Who's moving today, how far, and how moves are distributed",
  },
  "overview.OverviewPage.nextResearch": {
    zh: "下一章：研究 ↓",
    en: "Next: research ↓",
  },
  "overview.OverviewPage.rankedRuleEvidenceResearchResultsAreNonExecutable": {
    zh: "规则证据排序，研究结果不可执行",
    en: "Ranked rule evidence; research results are non-executable",
  },
  "overview.OverviewPage.nextRisksCoverage": {
    zh: "下一章：风险与覆盖 ↓",
    en: "Next: risks & coverage ↓",
  },
  "overview.OverviewPage.rankedByUpwardRuleBreakoutTrendAnd20Day": {
    zh: "按规则型向上突破优先、趋势和 20 日相对强弱排序",
    en: "Ranked by upward rule breakout, trend and 20-day relative strength",
  },
  "overview.OverviewPage.qualityGatesNotPassedExperimentalResearchOnlyChallengerOutput":
    {
      zh: "未通过门禁，仅供实验研究。候选输出不可用于可执行机会、置信等级或交易建议。",
      en: "Quality gates not passed; experimental research only. Challenger output cannot support executable opportunities, confidence levels or trading advice.",
    },
  "overview.OverviewPage.verifiedIndexResearchEvidence": {
    zh: "指数已验证研究证据",
    en: "Verified index research evidence",
  },
  "overview.OverviewPage.changesToWatchAndHowFarTheDataCan": {
    zh: "需要留意的变化，以及这些数据能信到什么程度",
    en: "Changes to watch and how far the data can be trusted",
  },
  "overview.OverviewPage.dailyBarsLoadedCurrentSnapshotInformationIsAvailable": {
    zh: "日线加载 {completed}/{total}；当前快照信息已可用。",
    en: "Daily bars loaded {completed}/{total}; current snapshot information is available.",
  },
  "overview.OverviewPage.observedDailyBarsRawPricesUnadjusted": {
    zh: "真实日线 {value}/{total} ； 原始价格（未复权）",
    en: "Observed daily bars {value}/{total}; raw prices (unadjusted)",
  },
  "overview.OverviewPage.historyMissingForInstruments": {
    zh: "；缺少历史 {excluded} 个",
    en: "; history missing for {excluded} instruments",
  },
  "overview.OverviewPage.rawPriceJumps": {
    zh: "原始价格跳变（",
    en: "Raw price jumps (",
  },
  "overview.OverviewPage.adjacentClosesDifferByMoreThan40PossiblyDue": {
    zh: "相邻收盘变动超过 40%，可能含公司行动；未复权日线不代表总回报。",
    en: "Adjacent closes differ by more than 40%, possibly due to corporate actions. Unadjusted daily prices do not represent total return.",
  },
  "overview.OverviewPage.moreSectorAndResearchDiagnostics": {
    zh: "更多行业与研究诊断",
    en: "More sector and research diagnostics",
  },
  "overview.OverviewPage.forecastVersionsAndLatency": {
    zh: "预测版本与延迟",
    en: "Forecast versions and latency",
  },
  "overview.OverviewPage.model": {
    zh: "； 模型",
    en: "; model",
  },
  "overview.OverviewPage.calibration": {
    zh: "； 校准",
    en: "; calibration",
  },
  "overview.OverviewPage.inferenceTime": {
    zh: "； 本次推理耗时",
    en: "; inference time",
  },
  "overview.OverviewPage.forResearchPurposesOnlyThisDoesNotConstituteInvestment": {
    zh: "仅供研究参考，不构成任何投资建议。",
    en: "For research purposes only. This does not constitute investment advice.",
  },
  "overview.OverviewPulse.upDown": {
    zh: "{breakoutUp} 上 / {breakoutDown} 下",
    en: "{breakoutUp} up / {breakoutDown} down",
  },
  "overview.OverviewPulse.todaySMarketPulse": {
    zh: "今日市场脉搏",
    en: "Today's market pulse",
  },
  "overview.OverviewPulse.5CompatibleSnapshots": {
    zh: "/5 个兼容快照",
    en: "/5 compatible snapshots",
  },
  "overview.OverviewPulse.20DayPositiveReturnBreadth": {
    zh: "20 日正收益宽度",
    en: "20-day positive-return breadth",
  },
  "overview.OverviewPulse.marketBreadthAcrossTheLastFiveSnapshots": {
    zh: "市场宽度近五快照趋势",
    en: "Market breadth across the last five snapshots",
  },
  "overview.OverviewPulse.uptrendShareAcrossTheLastFiveSnapshots": {
    zh: "趋势偏多占比近五快照趋势",
    en: "Uptrend share across the last five snapshots",
  },
  "overview.OverviewPulse.upDownUnchanged": {
    zh: "涨 / 跌 / 平",
    en: "Up / down / unchanged",
  },
  "overview.OverviewPulse.up": {
    zh: "涨 ；",
    en: "up;",
  },
  "overview.OverviewPulse.down": {
    zh: "跌 ；",
    en: "down;",
  },
  "overview.OverviewPulse.instrumentCrossSection": {
    zh: "标的横截面",
    en: "Instrument cross-section",
  },
  "overview.OverviewPulse.awaitingCompatibleSnapshots": {
    zh: "等待兼容快照",
    en: "Awaiting compatible snapshots",
  },
  "overview.OverviewPulse.toUptrendToDowntrend": {
    zh: "转多 {toUptrend} ； 转空 {toDowntrend}",
    en: "To uptrend {toUptrend}; to downtrend {toDowntrend}",
  },
  "overview.OverviewPulse.upDown2": {
    zh: "向上 / 向下",
    en: "Up / down",
  },
  "overview.OverviewPulse.noCompatibleComparisonSnapshotForTheCurrentUniverseCurrently":
    {
      zh: "当前池暂缺兼容比较快照（当前",
      en: "No compatible comparison snapshot for the current universe (currently",
    },
  "overview.OverviewPulse.5ChangesAndTrendsCompareTheSameUniverseOnly": {
    zh: "/5）；变化与趋势仅比较同池快照，不代表日线历史缺失。",
    en: "/5). Changes and trends compare the same universe only; daily history may still be available.",
  },
  "overview.OverviewPulse.crossSectionalEvidence": {
    zh: "横截面证据",
    en: "Cross-sectional evidence",
  },
  "overview.OverviewPulse.comparisonSnapshot": {
    zh: "比较快照",
    en: "Comparison snapshot",
  },
  "overview.OverviewPulse.noCompatibleComparisonSnapshot": {
    zh: "暂无兼容比较快照",
    en: "No compatible comparison snapshot",
  },
  "overview.OverviewTerminalChart.chart": {
    zh: "{title}图表；{summary}",
    en: "{title} chart; {summary}",
  },
  "overview.PriceIntervalTrack.priceIntervalUnavailable": {
    zh: "价格区间暂不可用",
    en: "Price interval unavailable",
  },
  "overview.PriceIntervalTrack.currentCloseQ10Q50Q90": {
    zh: "当前收盘 {value}；q10 {value2}；q50 {value3}；q90 {value4}",
    en: "Current close {value}; q10 {value2}; q50 {value3}; q90 {value4}",
  },
  "overview.RegimeDistribution.upwardBreakoutUnconfirmed": {
    zh: "向上突破待确认",
    en: "Upward breakout unconfirmed",
  },
  "overview.RegimeDistribution.downwardBreakoutUnconfirmed": {
    zh: "向下突破待确认",
    en: "Downward breakout unconfirmed",
  },
  "overview.RegimeDistribution.marketRegimeDistribution": {
    zh: "市场状态分布",
    en: "Market regime distribution",
  },
  "overview.RegimeDistribution.totalSamples": {
    zh: "总样本",
    en: "Total samples",
  },
  "overview.RegimeDistribution.dominant": {
    zh: " ； 主导 {value}",
    en: "; dominant {value}",
  },
  "overview.RegimeDistribution.dominant2": {
    zh: "主导",
    en: "Dominant",
  },
  "overview.RiskDivergencePanel.risksAndDivergence": {
    zh: "风险与分歧",
    en: "Risks and divergence",
  },
  "overview.RiskDivergencePanel.fixedRuleSummaryNotTradingAdvice": {
    zh: "固定规则摘要 ； 不构成交易建议",
    en: "Fixed-rule summary; not trading advice",
  },
  "overview.RiskDivergencePanel.collapseRisks": {
    zh: "收起风险",
    en: "Collapse risks",
  },
  "overview.RiskDivergencePanel.viewAll5Risks": {
    zh: "查看全部 5 项风险",
    en: "View all 5 risks",
  },
  "overview.RiskDivergencePanel.optionsRiskAlertsFixedBackendRules": {
    zh: "期权风险提示 ； 后端固定规则",
    en: "Options risk alerts; fixed backend rules",
  },
  "overview.RiskDivergencePanel.currentValue": {
    zh: "当前值",
    en: "Current value",
  },
  "overview.RiskDivergencePanel.undefined": {
    zh: "{number} 倍",
    en: "{number}×",
  },
  "overview.RiskDivergencePanel.calendarDay": {
    zh: "/自然日",
    en: "/calendar day",
  },
  "overview.RiskDivergencePanel.percentagePoints": {
    zh: "{value} 个百分点{value2}",
    en: "{value} percentage points{value2}",
  },
  "overview.RiskDivergencePanel.threshold": {
    zh: "； 阈值",
    en: "; threshold",
  },
  "overview.RiskDivergencePanel.marketTimeUnconfirmed": {
    zh: "市场时间未确认",
    en: "Market time unconfirmed",
  },
  "overview.RiskDivergencePanel.availableEvidenceHasNotTriggeredRulesRulesWithMissing":
    {
      zh: "当前可用证据未触发规则；历史或指标缺失的规则无法判断。",
      en: "Available evidence has not triggered rules. Rules with missing history or metrics cannot be assessed.",
    },
  "overview.RiskDivergencePanel.optionsRiskEvidenceNotLoadedYet": {
    zh: "期权风险证据尚未读取。",
    en: "Options risk evidence not loaded yet.",
  },
  "overview.SectorChangePanel.sectorStrengthChanges": {
    zh: "行业强弱变化",
    en: "Sector strength changes",
  },
  "overview.SectorChangePanel.uptrendShareVsPreviousCompatibleSnapshot5SamplesOn": {
    zh: "趋势偏多占比较上个兼容快照 ； 双边样本均 ≥ 5",
    en: "Uptrend share vs previous compatible snapshot; ≥5 samples on both sides",
  },
  "overview.SectorHealthTerminal.sectorHealthAndRelativeStrength": {
    zh: "行业体检与相对强弱",
    en: "Sector health and relative strength",
  },
  "overview.SectorHealthTerminal.sectorCrossSection": {
    zh: "行业横截面",
    en: "Sector cross-section",
  },
  "overview.SectorHealthTerminal.sectorHealthAndStrengthRelativeToSPY": {
    zh: "行业体检与相对 SPY 强弱",
    en: "Sector health and strength relative to SPY",
  },
  "overview.SectorHealthTerminal.medianReturnsAndAnomalyCountsUseTheCurrentSnapshot": {
    zh: "中位收益与异常计数来自当前快照；变化仅纳入前后样本均不少于 5 的行业。",
    en: "Median returns and anomaly counts use the current snapshot. Changes include only sectors with at least 5 samples in both snapshots.",
  },
  "overview.SectorHealthTerminal.sectorReturnHorizon": {
    zh: "行业收益周期",
    en: "Sector return horizon",
  },
  "overview.SectorHealthTerminal.sectorHealthTable": {
    zh: "行业体检表",
    en: "Sector health table",
  },
  "overview.SectorHealthTerminal.dayMedianReturn": {
    zh: "日中位收益",
    en: "-day median return",
  },
  "overview.SectorHealthTerminal.relativeToSPY": {
    zh: "相对 SPY",
    en: "Relative to SPY",
  },
  "overview.SectorHealthTerminal.relativeStrength": {
    zh: "强弱判断",
    en: "Relative strength",
  },
  "overview.SectorHealthTerminal.vsPrevious": {
    zh: "较上次",
    en: "vs previous",
  },
  "overview.SectorHealthTerminal.volumeBreakouts": {
    zh: "量能 / 突破",
    en: "Volume / breakouts",
  },
  "overview.SectorHealthTerminal.topGainerLoser": {
    zh: "领涨 / 领跌",
    en: "Top gainer / loser",
  },
  "overview.SectorHealthTerminal.insufficientSamples5SectorStrengthNotAssessed": {
    zh: "样本不足（{sampleCount}/5），不作行业强弱判断",
    en: "Insufficient samples ({sampleCount}/5); sector strength not assessed",
  },
  "overview.SectorHealthTerminal.insufficientEvidence": {
    zh: "证据不足",
    en: "Insufficient evidence",
  },
  "overview.SectorHealthTerminal.relativelyStrong": {
    zh: "相对偏强",
    en: "Relatively strong",
  },
  "overview.SectorHealthTerminal.relativelyWeak": {
    zh: "相对偏弱",
    en: "Relatively weak",
  },
  "overview.SectorHealthTerminal.relativelyUnchanged": {
    zh: "相对持平",
    en: "Relatively unchanged",
  },
  "overview.SectorHealthTerminal.samples": {
    zh: "； 样本",
    en: "; samples",
  },
  "overview.SectorHealthTerminal.insufficientSectorSamples5": {
    zh: "行业样本不足（{comparableSample}/5）",
    en: "Insufficient sector samples ({comparableSample}/5)",
  },
  "overview.SectorHealthTerminal.singleInstrument": {
    zh: "（单一标的）",
    en: "(single instrument)",
  },
  "overview.SectorMarketMap.dayReturn2": {
    zh: "{symbol}，{value}，{horizon} 日收益 {returnValue}{value2}",
    en: "{symbol}, {value}, {horizon}-day return {returnValue}{value2}",
  },
  "overview.SectorMarketMap.showAllInstrumentsIn": {
    zh: "只看{sector}全部 {total} 个标的",
    en: "Show all {total} instruments in {sector}",
  },
  "overview.SectorMarketMap.selectedInstrumentDetails": {
    zh: "选中标的详情",
    en: "Selected instrument details",
  },
  "overview.SectorMarketMap.selectAnInstrumentOnTheMapToViewPrices": {
    zh: "在地图中选择一个标的，查看行情、规则证据与所在行业。",
    en: "Select an instrument on the map to view prices, rule evidence and its sector.",
  },
  "overview.SectorMarketMap.1Day": {
    zh: "1 日",
    en: "1 day",
  },
  "overview.SectorMarketMap.selectedInstrument": {
    zh: "选中标的 ；",
    en: "Selected instrument;",
  },
  "overview.SectorMarketMap.last": {
    zh: "近",
    en: "Last",
  },
  "overview.SectorMarketMap.tradingDaysRawDailyBarsUnadjustedMedianSectorTrend": {
    zh: "个交易日 ； 原始日线（未复权） ； 行业中位走势 ； ▲▼ 状态转多 / 转空",
    en: "trading days; raw daily bars (unadjusted); median sector trend; ▲▼ transitions to uptrend / downtrend",
  },
  "overview.SectorMarketMap.20DayChannelPosition": {
    zh: "20 日通道位置",
    en: "20-day channel position",
  },
  "overview.SectorMarketMap.0IsTheLowerChannelBoundary100IsThe": {
    zh: "0% 为通道下沿，100% 为上沿",
    en: "0% is the lower channel boundary; 100% is the upper boundary",
  },
  "overview.SectorMarketMap.currentlyOutsideTheChannel": {
    zh: "；当前已越出通道",
    en: "; currently outside the channel",
  },
  "overview.SectorMarketMap.sector": {
    zh: "所在行业",
    en: "Sector",
  },
  "overview.SectorMarketMap.sector2": {
    zh: "所在行业 ；",
    en: "Sector;",
  },
  "overview.SectorMarketMap.dayMedian": {
    zh: "日中位",
    en: "-day median",
  },
  "overview.SectorMarketMap.noOtherInstrumentsInThisSector": {
    zh: "行业内没有其他标的",
    en: "No other instruments in this sector",
  },
  "overview.SectorMarketMap.sectorTopByDayReturn": {
    zh: "同行业 {horizon} 日涨幅前 {count}",
    en: "Sector top {count} by {horizon}-day return",
  },
  "overview.SectorMarketMap.researchRank": {
    zh: "研究排序位置",
    en: "Research rank",
  },
  "overview.SectorMarketMap.outsideTheCurrentResearchRankingFullPriceAndRule": {
    zh: "未进入当前研究排序；仍可查看完整行情与规则证据。",
    en: "Outside the current research ranking; full price and rule evidence remain available.",
  },
  "overview.SectorMarketMap.researchRank2": {
    zh: "研究排序第",
    en: "Research rank",
  },
  "overview.SectorMarketMap.position": {
    zh: "位",
    en: "position",
  },
  "overview.SectorMarketMap.researchResultsAreNonExecutableAndDoNotConstitute": {
    zh: "研究结果不可执行，不构成交易建议。",
    en: "Research results are non-executable and do not constitute trading advice.",
  },
  "overview.SectorMarketMap.marketMapRepresentativeInstruments": {
    zh: "{shownCount} 个代表标的市场地图",
    en: "Market map: {shownCount} representative instruments",
  },
  "overview.SectorMarketMap.marketMapAllInstruments": {
    zh: "{sector}全部 {shownCount} 个标的市场地图",
    en: "{sector} market map: all {shownCount} instruments",
  },
  "overview.SectorMarketMap.marketMapRepresentativeInstruments2": {
    zh: "市场地图 ； {shownCount} 个代表标的",
    en: "Market map; {shownCount} representative instruments",
  },
  "overview.SectorMarketMap.allInstruments": {
    zh: "{sector} ； 全部 {shownCount} 个标的",
    en: "{sector}; all {shownCount} instruments",
  },
  "overview.SectorMarketMap.equalAreaNotMarketCapWeighted": {
    zh: "等面积，非市值权重",
    en: "Equal area, not market-cap weighted",
  },
  "overview.SectorMarketMap.allSectors": {
    zh: "全部行业 ；",
    en: "All sectors;",
  },
  "overview.SectorMarketMap.representatives": {
    zh: "个代表",
    en: "representatives",
  },
  "overview.SectorMarketMap.showing": {
    zh: "{sector}，显示 {count}/{total} 个",
    en: "{sector}, showing {count}/{total}",
  },
  "overview.SectorMarketMap.viewAllInSector": {
    zh: "查看行业全部",
    en: "View all in sector",
  },
  "overview.SectorMarketMap.sectorTotal": {
    zh: "行业共",
    en: "Sector total:",
  },
  "overview.SectorMarketMap.equalAreaNotMarketCapWeighted2": {
    zh: "等面积 ； 非市值权重",
    en: "Equal area; not market-cap weighted",
  },
  "overview.SectorMarketMap.viewAllInstruments": {
    zh: "查看全部 {count} 个标的",
    en: "View all {count} instruments",
  },
  "overview.SectorMarketMap.viewAll": {
    zh: "查看全部",
    en: "View all",
  },
  "overview.SectorMarketMap.backTo": {
    zh: "返回",
    en: "Back to",
  },
  "overview.SectorMarketMap.representativeView": {
    zh: "个代表视图",
    en: "representative view",
  },
  "overview.SectorMarketMap.dayReturnColorScaleClippedAt": {
    zh: "{horizon} 日收益色阶，±{value}% 截断",
    en: "{horizon}-day return color scale, clipped at ±{value}%",
  },
  "overview.SectorMarketMap.greenGainsRedLossesBeyond": {
    zh: "上涨绿、下跌红；超出 ±",
    en: "Green gains, red losses; beyond ±",
  },
  "overview.SectorMarketMap.usesEndpointColors": {
    zh: "% 按端点着色",
    en: "% uses endpoint colors",
  },
  "overview.SectorMarketMap.volVolumeZ202BreakBreakRuleBreakoutShift": {
    zh: "量 = |量能 Z20| ≥ 2 ； 突↑/突↓ = 规则突破 ； 切 = 状态切换",
    en: "Vol = |volume Z20| ≥2; Break↑/Break↓ = rule breakout; Shift = regime change",
  },
  "overview.SectorMarketMap.selection4PerSectorInitiallyRankedByAnomalyScore": {
    zh: "选取：每行业先取 4 个，按规则异常分与",
    en: "Selection: 4 per sector initially, ranked by anomaly score and",
  },
  "overview.SectorMarketMap.dayAbsoluteReturnUnusedSlotsGoToLargerSectors": {
    zh: "日涨跌幅排序；不足 4 个的名额顺延给样本多的行业",
    en: "-day absolute return; unused slots go to larger sectors",
  },
  "overview.SectorRegimeMatrix.sectorSampleTotal": {
    zh: "行业样本共",
    en: "Sector sample total:",
  },
  "overview.SectorRegimeMatrix.instrumentsOrderedBySectorInstrumentCount": {
    zh: "个 ； 按行业内标的数量",
    en: "instruments; ordered by sector instrument count",
  },
  "overview.useOverviewHistory.dailyBarRequestFailed": {
    zh: "日线读取失败",
    en: "Daily bar request failed",
  },
} as const;
