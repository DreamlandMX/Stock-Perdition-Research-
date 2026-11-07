"""Forward-only live universe layered on the immutable M1 seed."""

from datetime import date

from stock_prediction.universe.seed import SEED_ETFS, SEED_STOCKS


ACTIVE_UNIVERSE_VERSION = "live-expanded-2026-08-10-v1"
ACTIVE_UNIVERSE_EFFECTIVE_FROM = date(2026, 8, 11)
ACTIVE_UNIVERSE_SOURCE_SHA256 = (
    "11693ae4f25ec96268e789c23865aa9a5cbee4bbbe8ec484243700952ce1b616"
)
EXCLUDED_SOURCE_CODES = ("USDindex.FX", ".VIX.US")
ACTIVE_UNIVERSE_REASON = (
    "forward-only live expansion selected on 2026-08-10 from the governed CSV "
    "watchlist plus eight MSCI country ETFs; historical research remains static "
    "and carries survivorship bias."
)

COUNTRY_ETFS = (
    "US.EWJ",
    "US.EWY",
    "US.EWT",
    "US.INDA",
    "US.EWU",
    "US.EWG",
    "US.EWQ",
    "US.EWL",
)

CSV_ADDITIONAL_STOCKS = (
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

ACTIVE_ETFS = SEED_ETFS + COUNTRY_ETFS
ACTIVE_STOCKS = SEED_STOCKS + CSV_ADDITIONAL_STOCKS
ACTIVE_UNIVERSE = ACTIVE_ETFS + ACTIVE_STOCKS


__all__ = [
    "ACTIVE_ETFS",
    "ACTIVE_STOCKS",
    "ACTIVE_UNIVERSE",
    "ACTIVE_UNIVERSE_EFFECTIVE_FROM",
    "ACTIVE_UNIVERSE_REASON",
    "ACTIVE_UNIVERSE_SOURCE_SHA256",
    "ACTIVE_UNIVERSE_VERSION",
    "COUNTRY_ETFS",
    "CSV_ADDITIONAL_STOCKS",
    "EXCLUDED_SOURCE_CODES",
]
