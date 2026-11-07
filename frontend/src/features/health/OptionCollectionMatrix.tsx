import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useOptionsCollection, useOptionCutoff } from "../options/useOptionsOverview";
import { optionReasonLabel } from "../options/option-labels";
import { formatUsMarketTime } from "../../app/presentation";
import "./health.css";
export function OptionCollectionMatrix({
  asOf,
  snapshotId,
}: {
  asOf?: string | undefined;
  snapshotId?: string | undefined;
}) {
  const { locale } = useI18n();
  const cutoff = useOptionCutoff(asOf, snapshotId);
  const result = useOptionsCollection({ as_of: cutoff.asOf }, cutoff.ready);
  const response = result.data;
  const collection = response?.collection;
  const automatic = collection?.latest_automatic;
  const symbols = [...new Set(collection?.cells.map((cell) => cell.symbol) ?? [])];
  return (
    <section
      id="options"
      aria-label={translate(locale, "health.optionsCollectionMatrix")}
      className="health-panel option-collection-matrix"
    >
      <header className="health-panel__header">
        <div>
          <h2>
            {translate(locale, "health.automaticCollection10SymbolsLast10TradingDays")}
          </h2>
          <p>
            {" " +
              (translate(locale, "health.theOriginal10SymbolsAreCollectedAt15") + " ")}
          </p>
        </div>
        <small>
          {" " + (translate(locale, "health.checkedAt") + " ")}
          {response
            ? formatUsMarketTime(response.generated_at, locale)
            : translate(locale, "health.noneYet")}
        </small>
      </header>
      {result.isPending ? (
        <p role="status">
          {translate(locale, "health.loadingOptionsCollectionEvidence")}
        </p>
      ) : result.isError ? (
        <div role="alert">
          <p>
            {translate(
              locale,
              "health.optionsCollectionEvidenceFailedToLoadOtherHealth",
            )}
          </p>
          <button type="button" onClick={() => void result.refetch()}>
            {" " + (translate(locale, "health.retryCollectionEvidence") + " ")}
          </button>
        </div>
      ) : (
        collection && (
          <>
            <div className="option-collection-summary">
              <span>
                {" " + (translate(locale, "health.latestAutomaticRun") + " ")}
                {automatic
                  ? `${formatUsMarketTime(automatic.ended_at, locale)}; ${optionReasonLabel(automatic.status, locale)}${automatic.reason ? ` ； ${optionReasonLabel(automatic.reason, locale)}` : ""}`
                  : translate(locale, "health.noRecordsYet")}
              </span>
              <span>
                {translate(locale, "health.collected") + " "}
                {collection.history_days ?? translate(locale, "health.unconfirmed")}
                {" " + translate(locale, "health.tradingDays")}
              </span>
              <span>
                {" " + (translate(locale, "health.completeSymbols") + " ")}
                {collection.complete_symbols ??
                  translate(locale, "health.unconfirmed")}{" "}
                / {symbols.length || translate(locale, "health.unconfirmed")}
              </span>
              <span>
                {" " + (translate(locale, "health.newSchedulerSchedule") + " ")}
                {collection.scheduler_loaded === true
                  ? translate(locale, "health.loadConfirmed")
                  : collection.scheduler_loaded === false
                    ? translate(locale, "health.loadUnconfirmed")
                    : translate(locale, "health.statusUnconfirmed")}
              </span>
            </div>
            {!automatic && (
              <p className="option-collection-warning" role="status">
                {" " +
                  (translate(
                    locale,
                    "health.noAutomaticCollectionRecordsDetectedTheSchedulerMay",
                  ) +
                    " ")}
              </p>
            )}
            {collection.reasons.length > 0 && (
              <p>
                {collection.reasons
                  .map((value) => optionReasonLabel(value, locale))
                  .join(locale === "en-US" ? "; " : "；")}
              </p>
            )}
            {collection.sessions.length === 0 || symbols.length === 0 ? (
              <p>
                {translate(
                  locale,
                  "health.noVerifiableTradingDayCollectionMatrixYetMissing",
                )}
              </p>
            ) : (
              <div className="option-collection-scroll">
                <table className="option-collection-table">
                  <caption>
                    {translate(
                      locale,
                      "health.colorAndTextIndicateCollectionStatusHoverFor",
                    )}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">{translate(locale, "health.symbol")}</th>
                      {collection.sessions.map((session) => (
                        <th key={session} scope="col">
                          <time dateTime={session}>{session.slice(5)}</time>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {symbols.map((symbol) => (
                      <tr key={symbol}>
                        <th scope="row">{symbol.replace(/^US\./, "")}</th>
                        {collection.sessions.map((session) => {
                          const cell = collection.cells.find(
                            (entry) =>
                              entry.symbol === symbol && entry.session === session,
                          );
                          const nonTrading = cell?.reasons.some(
                            (reason) =>
                              reason === "not_trading_day" ||
                              reason === "non_trading_day",
                          );
                          const status = nonTrading
                            ? "non_trading_day"
                            : (cell?.status ?? "unavailable");
                          const details = cell
                            ? translate(locale, "health.contracts", {
                                contracts: String(
                                  cell.contracts === null
                                    ? translate(locale, "health.unconfirmed")
                                    : cell.contracts,
                                ),
                                join: String(
                                  cell.reasons
                                    .map((value) => optionReasonLabel(value, locale))
                                    .join(locale === "en-US" ? "; " : "；") ||
                                    translate(locale, "health.noAdditionalReason"),
                                ),
                                snapshot_id: String(
                                  cell.snapshot_id
                                    ? translate(locale, "health.snapshot", {
                                        snapshot_id: String(cell.snapshot_id),
                                      })
                                    : "",
                                ),
                              })
                            : translate(
                                locale,
                                "health.collectionRecordMissingContractCountUnconfirmed",
                              );
                          return (
                            <td key={session}>
                              <span
                                className={`option-collection-cell option-collection-cell--${status}`}
                                title={details}
                                tabIndex={0}
                                aria-label={`${symbol} ${session} ${optionReasonLabel(status, locale)}; ${details}`}
                              >
                                {optionReasonLabel(status, locale)}
                              </span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <ul
              className="option-collection-legend"
              aria-label={translate(locale, "health.collectionStatusLegend")}
            >
              {[
                "complete",
                "partial",
                "failed",
                "skipped",
                "non_trading_day",
                "unavailable",
              ].map((status) => (
                <li key={status}>
                  <span
                    className={`option-collection-cell option-collection-cell--${status}`}
                  >
                    {optionReasonLabel(status, locale)}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )
      )}
    </section>
  );
}
