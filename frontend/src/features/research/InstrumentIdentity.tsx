import { instrumentName } from "../../i18n/instruments";
import { translate } from "../../i18n/core";
import { useI18n } from "../../i18n/context";
import { useQuery } from "@tanstack/react-query";
import { createResearchApiClient } from "../../api/client";
import type { components } from "../../api/generated/schema";
import { assetLabel, sectorLabel, formatUsMarketTime } from "../../app/presentation";
const client = createResearchApiClient();
export function InstrumentIdentity({
  symbol,
  snapshot,
}: {
  symbol: string;
  snapshot: components["schemas"]["ResolvedSnapshot"];
}) {
  const { locale } = useI18n();
  const query = useQuery({
    queryKey: ["instrument-identity", symbol, snapshot.snapshot_id],
    retry: false,
    queryFn: async () => {
      const { data } = await client.get("/api/v1/instruments/{symbol}", {
        params: {
          path: { symbol },
          query: { snapshot_id: snapshot.snapshot_id, as_of: snapshot.as_of },
        },
      });
      if (
        !data ||
        data.data.symbol !== symbol ||
        data.resolved_snapshot.snapshot_id !== snapshot.snapshot_id
      ) {
        throw new Error("instrument identity snapshot mismatch");
      }
      return data.data;
    },
  });
  const metadata = query.data;
  return (
    <section
      aria-label={translate(
        locale,
        "research.instrumentidentity.instrumentInformation",
      )}
      className="instrument-identity"
    >
      {metadata ? (
        <>
          <p>
            {translate(locale, "research.instrumentidentity.assetType.withValues", {
              name: instrumentName(symbol, metadata.name, locale),
              assetType: assetLabel(metadata.asset_type, locale),
              currency: metadata.currency,
            })}
          </p>
          <details>
            <summary>
              {translate(
                locale,
                "research.instrumentidentity.identityInformationSource",
              )}
            </summary>
            <p>
              {translate(
                locale,
                "research.instrumentidentity.marketExchangeSourceCode.withValues",
                {
                  translate:
                    metadata.asset_type === "etf"
                      ? translate(
                          locale,
                          "research.instrumentidentity.etfHoldingsFeesAndTrackedIndexDetailsAreNot",
                        )
                      : translate(locale, "research.instrumentidentity.sector", {
                          sector: sectorLabel(metadata.sector, locale),
                        }),
                  exchange: metadata.exchange,
                },
              )}
            </p>
            <p>
              {translate(
                locale,
                "research.instrumentidentity.metadataVersion.withValues",
                {
                  metadataVersion:
                    metadata.metadata_version ??
                    translate(locale, "research.instrumentidentity.notProvided"),
                },
              )}
            </p>
            <p>
              {translate(locale, "research.instrumentidentity.observedAt")}{" "}
              {metadata.observed_at ? (
                <time dateTime={metadata.observed_at} title={metadata.observed_at}>
                  {formatUsMarketTime(metadata.observed_at, locale)}
                </time>
              ) : (
                translate(locale, "research.instrumentidentity.notProvided")
              )}
              {translate(
                locale,
                "research.instrumentidentity.thisIsNotTheMarketDataUpdateTime",
              )}
            </p>
            <p>
              {translate(locale, "research.instrumentidentity.snapshot.withValues", {
                snapshotId: snapshot.snapshot_id,
              })}
            </p>
          </details>
        </>
      ) : (
        <p role="status">
          {query.isPending
            ? translate(
                locale,
                "research.instrumentidentity.loadingInstrumentInformation",
              )
            : translate(
                locale,
                "research.instrumentidentity.instrumentInformationIsUnavailableForThisSnapshot",
              )}
        </p>
      )}
    </section>
  );
}
