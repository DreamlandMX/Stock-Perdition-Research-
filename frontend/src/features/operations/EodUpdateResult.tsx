import { useEffect, useState } from "react";

import {
  clearManualEodUpdate,
  EOD_UPDATE_SETTLED_EVENT,
  readManualEodUpdate,
} from "../../app/snapshot-refresh";
import type { ManualEodUpdateMarker } from "../../app/snapshot-refresh";
import { useI18n } from "../../i18n/context";
import { buildEodUpdateNotice, EodUpdateNotice } from "./EodUpdateNotice";
import type { UpdateOperation } from "./useEodUpdate";
import "./eod-update.css";

/**
 * Renders the outcome of a manually submitted end-of-day update. It only speaks
 * for updates this browser session started, so a background run never pops a
 * banner the reader did not ask for.
 */
export function EodUpdateResult() {
  const { locale } = useI18n();
  const [result, setResult] = useState<{
    operation: UpdateOperation;
    marker: ManualEodUpdateMarker;
  } | null>(null);

  useEffect(() => {
    const settle = (event: Event) => {
      const operation = (event as CustomEvent<UpdateOperation>).detail;
      const marker = readManualEodUpdate();
      if (marker === null || marker.operationId !== operation.operation_id) {
        return;
      }
      setResult({ operation, marker });
    };
    window.addEventListener(EOD_UPDATE_SETTLED_EVENT, settle);
    return () => window.removeEventListener(EOD_UPDATE_SETTLED_EVENT, settle);
  }, []);

  const notice =
    result === null
      ? null
      : buildEodUpdateNotice(result.operation, result.marker, locale);
  if (notice === null) {
    return null;
  }
  return (
    <EodUpdateNotice
      notice={notice}
      onDismiss={() => {
        clearManualEodUpdate(notice.operationId);
        setResult(null);
      }}
    />
  );
}
