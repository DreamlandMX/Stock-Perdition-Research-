import { describe, expect, test } from "vitest";

import { createAppQueryClient } from "./query-client";

describe("application query client", () => {
  test("keeps the last successful response without aggressive refetching", () => {
    const client = createAppQueryClient();
    const defaults = client.getDefaultOptions().queries;

    expect(defaults).toMatchObject({
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30_000,
    });
  });
});
