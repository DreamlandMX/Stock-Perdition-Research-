import { describe, expect, test } from "vitest";
import { instrumentName, instrumentNames, instrumentSearchTerm } from "./instruments";
import { detailText } from "./details";
import { translate, type MessageKey } from "./core";

describe("complete display localization", () => {
  test("every registered instrument has Chinese and English display names", () => {
    expect(Object.keys(instrumentNames)).toHaveLength(204);
    for (const [symbol, names] of Object.entries(instrumentNames)) {
      expect(instrumentName(symbol, "供应商名称", "en-US"), symbol).toBe(names[1]);
      expect(names[1], symbol).not.toMatch(/\p{Script=Han}/u);
      expect(instrumentName(symbol, "Provider name", "zh-CN"), symbol).toBe(names[0]);
      expect(instrumentName(symbol, "供应商名称", "zh-CN"), symbol).toBe("供应商名称");
      expect(names[0], symbol).toMatch(/\p{Script=Han}/u);
    }
  });
  test("localizes internal errors in both directions without changing diagnostic codes", () => {
    expect(detailText("日线读取失败", "en-US")).toBe("Daily price data could not be read");
    expect(detailText("Daily price data could not be read", "zh-CN")).toBe("日线读取失败");
    expect(detailText("Unable to reach the research API", "zh-CN")).toBe("无法连接研究接口");
    expect(detailText("The request parameters are invalid", "zh-CN")).toBe("请求参数无效");
    expect(detailText("snapshot_publication:candidate:80/227", "en-US")).toBe("Candidate model inference: 80/227 steps");
    expect(detailText("snapshot_publication:candidate:80/227", "zh-CN")).toBe("候选模型推理：80/227 步");
    expect(detailText("snapshot_integrity_error", "en-US")).toBe("snapshot_integrity_error");
    expect(detailText("日线 204/204；60 分钟 203/204", "en-US")).toBe("Daily bars 204/204; 60-minute bars 203/204");
    expect(detailText("开发窗口完成：13/17 项通过", "en-US")).toBe("Development window complete: 13/17 tasks passed");
    expect(translate("en-US", "unregistered" as MessageKey)).toBe(translate("en-US", "common.unavailable"));
  });
  test("resolves names in either language for the existing instrument search", () => {
    expect(instrumentSearchTerm("Apple")).toBe("AAPL");
    expect(instrumentSearchTerm("苹果")).toBe("AAPL");
    expect(instrumentSearchTerm("not registered")).toBe("not registered");
  });
});
