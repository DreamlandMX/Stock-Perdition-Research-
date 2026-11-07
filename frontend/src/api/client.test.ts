import { describe, expect, it } from "vitest";

import type { components } from "./generated/schema";
import { createResearchApiClient, ResearchApiError } from "./client";

const SANITIZED_ERROR_PROPERTIES = [
  "affectedScope",
  "code",
  "kind",
  "message",
  "name",
  "requestId",
  "retryable",
  "stack",
  "status",
];

async function captureRejection(request: Promise<unknown>): Promise<Error> {
  try {
    await request;
  } catch (error) {
    if (error instanceof Error) {
      return error;
    }
    throw error;
  }
  throw new Error("expected_request_rejection");
}

describe("research API client", () => {
  it("exposes targeted training, cancellation, promotion, and shadow types", () => {
    const training: components["schemas"]["CandidateTrainingRequest"] = {
      bundles: ["direction", "range_exit_20"],
      trigger: "manual",
    };
    const promotion: components["schemas"]["ModelPromotionRequest"] = {
      approved_by: "local_user",
      training_run_id: "run-targeted-vnext-1",
      bundle_id: "direction",
      expected_package_sha256: "a".repeat(64),
      expected_incumbent_id: "direction-incumbent-v1",
    };
    const scope: components["schemas"]["ResearchEvidenceScope"] = "shadow";
    const statuses: components["schemas"]["UpdateOperationStatus"][] = [
      "cancel_requested",
      "cancelled",
    ];

    expect(training.bundles).toEqual(["direction", "range_exit_20"]);
    expect(promotion.bundle_id).toBe("direction");
    expect(scope).toBe("shadow");
    expect(statuses).toEqual(["cancel_requested", "cancelled"]);
  });

  it("exposes typed request helpers and honors injected transport options", async () => {
    const requests: Request[] = [];
    const payload = { readiness: "ready" };
    const fetchImplementation = (request: Request) => {
      requests.push(request);
      return Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    };
    const client = createResearchApiClient({
      baseUrl: "https://research.test",
      fetch: fetchImplementation,
    });

    const result = await client.get("/api/v1/health");

    expect(Object.keys(client)).toEqual(["get", "post", "put", "del"]);
    expect(requests).toHaveLength(1);
    expect(requests[0]?.method).toBe("GET");
    expect(requests[0]?.url).toBe("https://research.test/api/v1/health");
    expect(result.data).toEqual(payload);
  });

  it("normalizes the stable API error envelope", async () => {
    const rawBody = {
      request_id: "req-503",
      generated_at: "2026-07-14T12:00:00Z",
      data_status: "blocked",
      error_code: "capabilities_unavailable",
      message: "No capability snapshot is available",
      retryable: true,
      affected_scope: ["data_capabilities"],
      raw_debug: "upstream-http-body-secret",
    };
    const fetchImplementation = () =>
      Promise.resolve(
        new Response(JSON.stringify(rawBody), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createResearchApiClient({ fetch: fetchImplementation });

    const error = await captureRejection(client.get("/api/v1/data-capabilities"));

    expect(error).toMatchObject({
      name: "ResearchApiError",
      kind: "http",
      status: 503,
      code: "capabilities_unavailable",
      message: "No capability snapshot is available",
      requestId: "req-503",
      retryable: true,
      affectedScope: ["data_capabilities"],
    });
    expect(Object.getOwnPropertyNames(error).sort()).toEqual(
      SANITIZED_ERROR_PROPERTIES,
    );
    expect(Reflect.get(error, "cause")).toBeUndefined();
    expect(Reflect.get(error, "raw_debug")).toBeUndefined();
  });

  it("normalizes transport failures without leaking implementation details", async () => {
    const transportFailure = new TypeError("connection refused");
    const client = createResearchApiClient({
      fetch: () => Promise.reject(transportFailure),
    });

    const error = await captureRejection(client.get("/api/v1/health"));

    expect(error).toMatchObject({
      name: "ResearchApiError",
      kind: "network",
      status: null,
      code: "network_error",
      message: "Unable to reach the research API",
      requestId: null,
      retryable: true,
      affectedScope: [],
    });
    expect(Object.getOwnPropertyNames(error).sort()).toEqual(
      SANITIZED_ERROR_PROPERTIES,
    );
    expect(Reflect.get(error, "cause")).toBeUndefined();
    expect(Reflect.get(error, "cause")).not.toBe(transportFailure);
  });

  it("re-sanitizes ResearchApiError instances rejected by the transport", async () => {
    const rawCause = new Error("transport-cause-secret");
    const maliciousError = new ResearchApiError({
      kind: "http",
      status: 418,
      code: "malicious_transport_error",
      message: "transport-message-secret",
      requestId: "malicious-request-id",
      retryable: false,
      affectedScope: ["raw-transport-scope"],
    });
    Object.defineProperties(maliciousError, {
      cause: { value: rawCause },
      rawTransportPayload: { value: "raw-transport-payload-secret" },
    });
    const client = createResearchApiClient({
      fetch: () => Promise.reject(maliciousError),
    });

    const error = await captureRejection(client.get("/api/v1/health"));

    expect(error).not.toBe(maliciousError);
    expect(error).toMatchObject({
      name: "ResearchApiError",
      kind: "network",
      status: null,
      code: "network_error",
      message: "Unable to reach the research API",
      requestId: null,
      retryable: true,
      affectedScope: [],
    });
    expect(Object.getOwnPropertyNames(error).sort()).toEqual(
      SANITIZED_ERROR_PROPERTIES,
    );
    expect(Reflect.get(error, "cause")).toBeUndefined();
    expect(Reflect.get(error, "rawTransportPayload")).toBeUndefined();
    expect(Reflect.get(error, "cause")).not.toBe(rawCause);
  });
});
