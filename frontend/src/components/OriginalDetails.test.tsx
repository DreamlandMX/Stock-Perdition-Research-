import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ResearchApiError } from "../api/client";
import { OriginalDetails } from "./OriginalDetails";
afterEach(cleanup);
test("retains error code and request identity as escaped original details", () => {
  const error = new ResearchApiError({
    kind: "http",
    status: 503,
    code: "worker_unavailable",
    requestId: "req-123",
    message: "<script>source</script>",
    retryable: true,
    affectedScope: [],
  });
  const { container } = render(<OriginalDetails value={error} />);
  expect(screen.getByText(/worker_unavailable/)).toHaveTextContent("req-123");
  expect(container.querySelector("script")).toBeNull();
});
