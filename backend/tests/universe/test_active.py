from datetime import date

from stock_prediction.universe.active import (
    ACTIVE_ETFS,
    ACTIVE_STOCKS,
    ACTIVE_UNIVERSE,
    ACTIVE_UNIVERSE_EFFECTIVE_FROM,
    ACTIVE_UNIVERSE_REASON,
    ACTIVE_UNIVERSE_SOURCE_SHA256,
    ACTIVE_UNIVERSE_VERSION,
    COUNTRY_ETFS,
    CSV_ADDITIONAL_STOCKS,
    EXCLUDED_SOURCE_CODES,
)
from stock_prediction.universe.seed import SEED_ETFS, SEED_STOCKS, SEED_UNIVERSE


EXPECTED_COUNTRY_ETFS = (
    "US.EWJ",
    "US.EWY",
    "US.EWT",
    "US.INDA",
    "US.EWU",
    "US.EWG",
    "US.EWQ",
    "US.EWL",
)

EXPECTED_CSV_ADDITIONAL_STOCKS = (
    "US.SKHY",
    "US.TSM",
    "US.SMCI",
    "US.SPCX",
    "US.GOOG",
    "US.MRVL",
    "US.NXPI",
    "US.ON",
    "US.NVTS",
    "US.ASML",
    "US.ADI",
    "US.ARM",
    "US.RMBS",
    "US.SIMO",
    "US.AXTI",
    "US.POET",
    "US.SWKS",
    "US.AMKR",
    "US.NBIS",
    "US.APLD",
    "US.IREN",
    "US.NOK",
    "US.SNDK",
    "US.WDC",
    "US.STX",
    "US.LITE",
    "US.CIEN",
    "US.DELL",
    "US.IBM",
    "US.BBAI",
    "US.QBTS",
    "US.HPQ",
    "US.IONQ",
    "US.RGTI",
    "US.GLW",
    "US.OUST",
    "US.AEVA",
    "US.VZ",
    "US.VRT",
    "US.PLTR",
    "US.CRWV",
    "US.PANW",
    "US.CRWD",
    "US.WRD",
    "US.MDB",
    "US.ADBE",
    "US.NET",
    "US.VRSN",
    "US.FTNT",
    "US.RKLB",
    "US.FLY",
    "US.ASTS",
    "US.GRAB",
    "US.UBER",
    "US.SHOP",
    "US.MSTR",
    "US.COIN",
    "US.CRCL",
    "US.BMNR",
    "US.MARA",
    "US.CORZ",
    "US.SOFI",
    "US.HOOD",
    "US.BRK.B",
    "US.MCO",
    "US.FUTU",
    "US.BULL",
    "US.MFG",
    "US.CB",
    "US.PYPL",
    "US.NVO",
    "US.TEM",
    "US.HIMS",
    "US.SYK",
    "US.BSX",
    "US.CRSP",
    "US.DVA",
    "US.RDDT",
    "US.BABA",
    "US.DASH",
    "US.KR",
    "US.KHC",
    "US.CASY",
    "US.SPOT",
    "US.ROKU",
    "US.ABNB",
    "US.RBLX",
    "US.FLUT",
    "US.OXY",
    "US.VST",
    "US.FLNC",
    "US.OKLO",
    "US.MP",
    "US.SVRN",
    "US.PACK",
    "US.F",
)


def test_active_universe_expands_the_immutable_seed_without_replacing_it() -> None:
    assert COUNTRY_ETFS == EXPECTED_COUNTRY_ETFS
    assert CSV_ADDITIONAL_STOCKS == EXPECTED_CSV_ADDITIONAL_STOCKS
    assert ACTIVE_ETFS == SEED_ETFS + EXPECTED_COUNTRY_ETFS
    assert ACTIVE_STOCKS == SEED_STOCKS + EXPECTED_CSV_ADDITIONAL_STOCKS
    assert ACTIVE_UNIVERSE == ACTIVE_ETFS + ACTIVE_STOCKS
    assert set(SEED_UNIVERSE).issubset(ACTIVE_UNIVERSE)


def test_active_universe_is_exactly_28_etfs_176_stocks_and_204_unique_symbols() -> None:
    assert len(ACTIVE_ETFS) == 28
    assert len(ACTIVE_STOCKS) == 176
    assert len(ACTIVE_UNIVERSE) == 204
    assert len(set(ACTIVE_UNIVERSE)) == 204
    assert all(symbol.startswith("US.") for symbol in ACTIVE_UNIVERSE)


def test_active_universe_records_its_forward_only_source_governance() -> None:
    assert ACTIVE_UNIVERSE_VERSION == "live-expanded-2026-08-10-v1"
    assert ACTIVE_UNIVERSE_EFFECTIVE_FROM == date(2026, 8, 11)
    assert (
        ACTIVE_UNIVERSE_SOURCE_SHA256
        == "11693ae4f25ec96268e789c23865aa9a5cbee4bbbe8ec484243700952ce1b616"
    )
    assert EXCLUDED_SOURCE_CODES == ("USDindex.FX", ".VIX.US")
    assert "forward-only" in ACTIVE_UNIVERSE_REASON
    assert "survivorship bias" in ACTIVE_UNIVERSE_REASON
