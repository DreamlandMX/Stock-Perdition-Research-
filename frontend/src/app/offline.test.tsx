import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("../features/operations/EodUpdateButton", () => ({
  EodUpdateButton: () => null,
}));

import { AppShell } from "../layout/AppShell";
import { createResearchApiClient } from "../api/client";
import type { components } from "../api/generated/schema";
import { reportApiConnectivity } from "./connectivity";
import {
  attachPersistedViewStorage,
  restorePersistedViews,
  type PersistedViewStorage,
} from "./persisted-view";

const RESPONSE = {
  data: { marker: "validated" },
  data_status: "ready",
  effective_abstain: false,
  evaluated_at: "2026-07-18T13:00:00Z",
  exclusion_reason: [],
  generated_at: "2026-07-18T13:00:01Z",
  request_id: "request-1",
  resolved_snapshot: {
    as_of: "2026-07-18T13:00:00Z",
    calibration_version: "cal-v1",
    dataset_revision: "dataset-r1",
    feature_set_version: "features-v1",
    model_version: "model-v1",
    prediction_readiness: "forecast_ready",
    research_readiness: "full_verified",
    revision_no: 1,
    snapshot_id: "snapshot-r1",
    universe_version: "universe-v1",
  },
  serving_status: "live",
} satisfies Omit<
  components["schemas"]["ResearchResponse_OverviewReadModel_"],
  "data"
> & { data: { marker: string } };

function memoryStorage(): PersistedViewStorage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

afterEach(() => {
  reportApiConnectivity("online");
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: true,
  });
  cleanup();
});

describe("bounded persisted research views", () => {
  test("restores validated data only under its exact query key and identity", () => {
    const storage = memoryStorage();
    const writer = new QueryClient();
    const detach = attachPersistedViewStorage(writer, {
      now: () => new Date("2026-07-18T13:01:00Z"),
      storage,
    });
    const exactKey = [
      "overview",
      {
        asOf: "2026-07-18T13:00:00Z",
        calibrationVersion: "cal-v1",
        datasetRevision: "dataset-r1",
        featureSetVersion: "features-v1",
        horizon: 5,
        modelVersion: "model-v1",
        snapshotId: "snapshot-r1",
        universeVersion: "universe-v1",
      },
    ];
    writer.setQueryData(exactKey, RESPONSE);
    detach();

    const reader = new QueryClient();
    const restored = restorePersistedViews(reader, {
      now: () => new Date("2026-07-18T13:02:00Z"),
      storage,
    });

    expect(restored).toBe(1);
    expect(reader.getQueryData(exactKey)).toEqual(RESPONSE);
    expect(
      reader.getQueryData(["overview", { snapshotId: "snapshot-r2" }]),
    ).toBeUndefined();
  });

  test("rejects expired, malformed and identity-mismatched records", () => {
    const storage = memoryStorage();
    storage.setItem(
      "prediction-stock:persisted-views:v1",
      JSON.stringify({
        records: [
          {
            data: RESPONSE,
            queryKey: ["overview", { snapshotId: "snapshot-r2" }],
            savedAt: "2026-07-01T00:00:00Z",
            snapshotIdentity: { ...RESPONSE.resolved_snapshot, snapshot_id: "forged" },
          },
          { data: { malformed: true }, queryKey: ["overview"], savedAt: "bad" },
        ],
        schemaVersion: 1,
      }),
    );

    const client = new QueryClient();
    expect(
      restorePersistedViews(client, {
        maxAgeMs: 72 * 60 * 60 * 1000,
        now: () => new Date("2026-07-18T13:02:00Z"),
        storage,
      }),
    ).toBe(0);
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });

  test("forces an offline banner even when retained payloads were originally live", () => {
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      value: false,
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/overview"]}>
          <Routes>
            <Route
              element={
                <AppShell
                  dataAsOf="2026-07-18T13:00:00Z"
                  dataStatus="live"
                  opendStatus="connected"
                />
              }
            >
              <Route path="*" element={<p>保留的只读证据</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("status", { name: "全局数据状态" })).toHaveTextContent(
      "当前离线",
    );
    expect(screen.getByText("保留的只读证据")).toBeVisible();
  });

  test("treats API transport loss as offline and clears it after a successful response", async () => {
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      value: true,
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/overview"]}>
          <Routes>
            <Route
              element={
                <AppShell
                  dataAsOf="2026-07-18T13:00:00Z"
                  dataStatus="live"
                  opendStatus="connected"
                />
              }
            >
              <Route path="*" element={<p>保留证据</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const failing = createResearchApiClient({
      fetch: () => Promise.reject(new TypeError("offline")),
    });

    await act(async () => {
      await failing.get("/api/v1/health").catch(() => undefined);
    });
    expect(screen.getByRole("status", { name: "全局数据状态" })).toHaveTextContent(
      "当前离线",
    );

    const recovered = createResearchApiClient({
      fetch: () => Promise.resolve(new Response("{}", { status: 200 })),
    });
    await act(async () => {
      await recovered.get("/api/v1/health");
    });
    expect(screen.queryByText("当前离线", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("data-connectivity", "online");
  });
});
