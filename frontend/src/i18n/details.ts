import { messages } from "./catalog";
import { isMessageKey, translate, type Locale } from "./core";

const errors = [
  ["快照上下文不可用", "Snapshot context is unavailable"],
  ["股票元数据快照不一致，请重新读取", "Instrument metadata snapshots do not match. Reload the data."],
  ["日线响应不可用", "Daily price data is unavailable"],
  ["日线快照身份不一致", "Daily price snapshot identities do not match"],
  ["批量日线标的身份不一致", "Batch daily price instrument identities do not match"],
  ["日线标的或价格口径不一致", "Daily price instrument or adjustment basis does not match"],
  ["日线读取失败", "Daily price data could not be read"],
  ["研究接口请求失败", "The research API request failed"],
  ["研究接口连接失败", "The research API could not be reached"],
  ["无法连接研究接口", "Unable to reach the research API"],
  ["请求参数无效", "The request parameters are invalid"],
  ["请求的标的未知", "The requested symbol is unknown"],
  ["请求的标的不在当前快照的股票池中", "The requested symbol is not in the resolved universe"],
  ["没有匹配的回测证据", "No matching backtest evidence is available"],
  ["没有匹配的研究快照", "No matching research snapshot is available"],
  ["请求的版本与研究快照冲突", "The requested versions conflict with the research snapshot"],
  ["研究快照完整性校验失败", "The research snapshot failed integrity verification"],
  ["资源不存在", "Resource not found"],
  ["不允许此请求", "Request not allowed"],
  ["请求未能完成", "The request could not be completed"],
  ["标的搜索不可用", "instrument search unavailable"],
  ["标的身份快照不一致", "instrument identity snapshot mismatch"],
  ["筛选结果不可用", "screen response unavailable"],
  ["深度训练仅由手动训练或每月首个周六调度触发", "Deep training runs only on manual request or the first Saturday of each month"],
  ["复用已发布的正式预测 Champion", "Reusing the published production Champion model"],
  ["尚无 17/17 同批次正式预测 Champion", "No production Champion model has passed all 17 tasks in the same batch"],
  ["无正式 Champion；保留已验证研究组件，不生成正式数值", "No production Champion model; verified research components are retained without production forecast values"],
  ["数据哈希未变化，复用已验证结果", "The data hash is unchanged; reusing verified results"],
  ["复权数据已加载；正在检查未解释的异常收益", "Adjusted data loaded; checking unexplained return anomalies"],
  ["正在验证开发、校准与测试窗口的标签类别占比", "Checking label class proportions in development, calibration and test windows"],
  ["开发门禁未达到 17/17；独立晋升窗口未读取", "Development gates did not reach 17/17; the independent promotion window was not read"],
  ["开发交易评估已记录；正式交易门禁等待独立盲测", "Development trading evaluation recorded; production trading gates await the independent blind test"],
  ["开发门禁未全部通过；研究候选已保存", "Not all development gates passed; the research candidate was saved"],
] as const;

const phasePatterns: readonly [RegExp, string][] = [
  [/^目标交易日 (.+)$/, "Target trading session $1"],
  [/^特征截止 (.+)$/, "Feature data through $1"],
  [/^日线 (\d+)\/(\d+)；60 分钟 (\d+)\/(\d+)$/, "Daily bars $1/$2; 60-minute bars $3/$4"],
  [/^写入 (\d+) 项；(\d+) 项增量请求受阻$/, "Committed $1 items; $2 incremental requests blocked"],
  [/^使用当前 Champion 完成 (\d+) 个标的 × 3 周期推理$/, "Current Champion model inference completed for $1 instruments across 3 horizons"],
  [/^候选搜索 (\d+)\/17 [·;] (.+) (\d+)日$/, "Challenger search $1/17; $2, $3 trading days"],
  [/^候选搜索 0\/17 [·;] 正在训练第一个方向任务$/, "Challenger search 0/17; training the first direction task"],
  [/^开发窗口完成：(\d+)\/17 项通过$/, "Development window complete: $1/17 tasks passed"],
  [/^(.+) 采集 (\d+)\/(\d+) [·;] (.+)$/, "$1 collection $2/$3; $4"],
];

const entries: { zh: string; en: string }[] = [...Object.values(messages as Record<string, { zh: string; en: string }>), ...errors.map(([zh, en]) => ({ zh, en }))];
const translations = new Map(entries.flatMap(({ zh, en }) => [[zh, { zh, en }], [en, { zh, en }]] as const));

export function detailText(value: string, locale: Locale): string {
  const publication = /^snapshot_publication:([a-z]+):(\d+)\/(\d+)$/.exec(value);
  if (publication) {
    const key = `operations.publication.${publication[1]}`;
    if (isMessageKey(key)) return translate(locale, "operations.publication.progress", {
      step: translate(locale, key), completed: publication[2]!, total: publication[3]!,
    });
  }
  if (value.includes("\n")) return value.split("\n").map((line) => detailText(line, locale)).join("\n");
  const entry = translations.get(value);
  if (entry) return entry[locale === "en-US" ? "en" : "zh"];
  if (locale === "en-US") {
    for (const [pattern, replacement] of phasePatterns) {
      if (pattern.test(value)) return value.replace(pattern, replacement);
    }
  }
  return value;
}
