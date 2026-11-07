import { afterEach, describe, expect, test } from "vitest";

import {
  clearManualEodUpdate,
  readManualEodUpdate,
  rememberManualEodUpdate,
  withoutSnapshotIdentity,
} from "./snapshot-refresh";

afterEach(() => window.sessionStorage.clear());

describe("EOD snapshot refresh state", () => {
  test("removes only snapshot identity and version parameters", () => {
    const next = withoutSnapshotIdentity(
      "?as_of=2026-07-30T20%3A00%3A00Z&snapshot_id=old&revision_no=2&dataset_revision=d1&feature_set_version=f1&universe_version=u1&model_version=m1&calibration_version=c1&split_version=s1&strategy_version=t1&horizon=20&proxy_horizon=1&mode=research&sector=technology&page=3",
    );

    expect(next).toBe(
      "?horizon=20&proxy_horizon=1&mode=research&sector=technology&page=3",
    );
  });

  test("persists and clears the browser's manually submitted operation", () => {
    rememberManualEodUpdate(
      "eod-manual-1",
      "?as_of=2026-07-30T20%3A00%3A00Z&snapshot_id=old",
    );

    expect(readManualEodUpdate()).toEqual({
      operationId: "eod-manual-1",
      previousCutoff: "2026-07-30",
      previousSnapshotId: "old",
    });
    clearManualEodUpdate("another-operation");
    expect(readManualEodUpdate()).not.toBeNull();
    clearManualEodUpdate("eod-manual-1");
    expect(readManualEodUpdate()).toBeNull();
  });
});
