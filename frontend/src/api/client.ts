import createClient from "openapi-fetch";
import type { Client, ClientOptions } from "openapi-fetch";

import type { components, paths } from "./generated/schema";
import { reportApiConnectivity } from "../app/connectivity";

type ErrorEnvelope = components["schemas"]["ErrorResponse"];

export type ResearchApiErrorKind = "http" | "network";

interface ResearchApiErrorDetails {
  kind: ResearchApiErrorKind;
  status: number | null;
  code: string;
  message: string;
  requestId: string | null;
  retryable: boolean;
  affectedScope: readonly string[];
}

export class ResearchApiError extends Error {
  readonly kind: ResearchApiErrorKind;
  readonly status: number | null;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly affectedScope: readonly string[];

  constructor(details: ResearchApiErrorDetails) {
    super(details.message);
    this.name = "ResearchApiError";
    this.kind = details.kind;
    this.status = details.status;
    this.code = details.code;
    this.requestId = details.requestId;
    this.retryable = details.retryable;
    this.affectedScope = details.affectedScope;
  }
}

export interface ResearchApiClientOptions {
  trackConnectivity?: boolean;
  baseUrl?: string;
  fetch?: ClientOptions["fetch"];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    isRecord(value) &&
    typeof value.request_id === "string" &&
    typeof value.generated_at === "string" &&
    typeof value.data_status === "string" &&
    typeof value.error_code === "string" &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean" &&
    Array.isArray(value.affected_scope) &&
    value.affected_scope.every((scope) => typeof scope === "string")
  );
}

function normalizeHttpError(error: unknown, response: Response): ResearchApiError {
  if (isErrorEnvelope(error)) {
    return new ResearchApiError({
      kind: "http",
      status: response.status,
      code: error.error_code,
      message: error.message,
      requestId: error.request_id,
      retryable: error.retryable,
      affectedScope: error.affected_scope,
    });
  }

  return new ResearchApiError({
    kind: "http",
    status: response.status,
    code: "api_error",
    message: "The research API request failed",
    requestId: null,
    retryable: response.status >= 500,
    affectedScope: [],
  });
}

function normalizeNetworkError(): ResearchApiError {
  return new ResearchApiError({
    kind: "network",
    status: null,
    code: "network_error",
    message: "Unable to reach the research API",
    requestId: null,
    retryable: true,
    affectedScope: [],
  });
}

function defaultBaseUrl(): string {
  return globalThis.location?.origin ?? "http://localhost";
}

export function createResearchApiClient(options: ResearchApiClientOptions = {}) {
  const clientOptions: ClientOptions = {
    baseUrl: options.baseUrl ?? defaultBaseUrl(),
  };
  const transport = options.fetch ?? ((request: Request) => globalThis.fetch(request));
  clientOptions.fetch = async (request) => {
    let response: Response;
    try {
      response = await transport(request);
    } catch {
      if (options.trackConnectivity !== false) reportApiConnectivity("offline");
      throw normalizeNetworkError();
    }

    if (options.trackConnectivity !== false) reportApiConnectivity("online");

    if (response.ok) {
      return response;
    }

    let error: unknown;
    try {
      error = await response.clone().json();
    } catch {
      error = undefined;
    }
    throw normalizeHttpError(error, response);
  };

  const client = createClient<paths>(clientOptions);
  const get: Client<paths>["GET"] = client.GET;
  // Operations endpoints (end-of-day update, model training, model promotion)
  // are POSTs. Without this the call sites resolve `post` to undefined and the
  // request is never sent — the failure surfaces as a generic error state
  // rather than a network error, which makes it easy to miss.
  const post: Client<paths>["POST"] = client.POST;

  const put: Client<paths>["PUT"] = client.PUT;
  const del: Client<paths>["DELETE"] = client.DELETE;
  return { get, post, put, del } as const;
}
