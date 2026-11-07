from stock_prediction.universe.seed import (
    SEED_ETFS,
    SEED_STOCKS,
    SEED_UNIVERSE,
    SEED_UNIVERSE_REASON,
    SEED_UNIVERSE_REVIEW_CADENCE,
    SEED_UNIVERSE_SELECTION_POLICY,
    SEED_UNIVERSE_VERSION,
)


EXPECTED_ETFS = (
    "US.SPY",
    "US.QQQ",
    "US.DIA",
    "US.IWM",
    "US.VTI",
    "US.RSP",
    "US.XLK",
    "US.XLF",
    "US.XLV",
    "US.XLE",
    "US.XLY",
    "US.XLP",
    "US.XLI",
    "US.XLU",
    "US.XLB",
    "US.XLRE",
    "US.XLC",
    "US.SMH",
    "US.IYR",
    "US.TLT",
)

EXPECTED_STOCKS = (
    "US.AAPL",
    "US.MSFT",
    "US.NVDA",
    "US.AMZN",
    "US.GOOGL",
    "US.META",
    "US.AVGO",
    "US.ORCL",
    "US.CRM",
    "US.AMD",
    "US.INTC",
    "US.QCOM",
    "US.MU",
    "US.AMAT",
    "US.LRCX",
    "US.KLAC",
    "US.TXN",
    "US.CSCO",
    "US.NFLX",
    "US.NOW",
    "US.TSLA",
    "US.WMT",
    "US.COST",
    "US.HD",
    "US.MCD",
    "US.NKE",
    "US.SBUX",
    "US.LOW",
    "US.TGT",
    "US.DIS",
    "US.BKNG",
    "US.CMG",
    "US.PG",
    "US.KO",
    "US.PEP",
    "US.JPM",
    "US.BAC",
    "US.WFC",
    "US.GS",
    "US.MS",
    "US.C",
    "US.SCHW",
    "US.BLK",
    "US.AXP",
    "US.V",
    "US.MA",
    "US.COF",
    "US.LLY",
    "US.UNH",
    "US.JNJ",
    "US.ABBV",
    "US.MRK",
    "US.TMO",
    "US.AMGN",
    "US.GILD",
    "US.ISRG",
    "US.PFE",
    "US.BMY",
    "US.MDT",
    "US.ABT",
    "US.GE",
    "US.CAT",
    "US.BA",
    "US.RTX",
    "US.LMT",
    "US.DE",
    "US.UPS",
    "US.FDX",
    "US.ETN",
    "US.HON",
    "US.XOM",
    "US.CVX",
    "US.COP",
    "US.SLB",
    "US.EOG",
    "US.NEE",
    "US.LIN",
    "US.FCX",
    "US.PLD",
    "US.T",
)


def test_seed_universe_is_exactly_20_etfs_and_80_stocks() -> None:
    assert len(SEED_ETFS) == 20
    assert len(SEED_STOCKS) == 80
    assert len(SEED_UNIVERSE) == 100
    assert len(set(SEED_UNIVERSE)) == 100
    assert all(symbol.startswith("US.") for symbol in SEED_UNIVERSE)


def test_seed_universe_matches_the_blueprint_exactly() -> None:
    assert SEED_ETFS == EXPECTED_ETFS
    assert SEED_STOCKS == EXPECTED_STOCKS
    assert SEED_UNIVERSE == EXPECTED_ETFS + EXPECTED_STOCKS


def test_seed_collections_are_immutable_tuples() -> None:
    assert isinstance(SEED_ETFS, tuple)
    assert isinstance(SEED_STOCKS, tuple)
    assert isinstance(SEED_UNIVERSE, tuple)


def test_seed_universe_has_explicit_governance_metadata() -> None:
    assert SEED_UNIVERSE_VERSION == "m1-seed-2026-07-11-v1"
    assert "static" in SEED_UNIVERSE_REASON.casefold()
    assert "survivorship" in SEED_UNIVERSE_REASON.casefold()
    assert SEED_UNIVERSE_REVIEW_CADENCE == "quarterly"
    assert "20 ETFs" in SEED_UNIVERSE_SELECTION_POLICY
    assert "80 stocks" in SEED_UNIVERSE_SELECTION_POLICY
