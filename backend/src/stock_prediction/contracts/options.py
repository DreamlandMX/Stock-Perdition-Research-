"""Independent, traceable option data; none of these types are forecasts."""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_prediction.contracts.time import UtcDatetime
from stock_prediction.universe.active import ACTIVE_UNIVERSE

AUTOMATIC_OPTION_SYMBOLS = (
    "US.SPY",
    "US.QQQ",
    "US.AAPL",
    "US.MSFT",
    "US.AMZN",
    "US.GOOGL",
    "US.META",
    "US.NVDA",
    "US.TSLA",
    "US.ORCL",
)
OPTION_SYMBOLS = AUTOMATIC_OPTION_SYMBOLS + tuple(
    symbol for symbol in ACTIVE_UNIVERSE if symbol not in AUTOMATIC_OPTION_SYMBOLS
)

OptionMetric = Literal["oi", "volume", "iv", "gamma", "spread", "premium"]
OptionSide = Literal["call", "put"]
OptionHeatmapSide = Literal["call", "put", "net"]


class OptionModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always"
    )


class OptionPulseLease(OptionModel):
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    symbol: str
    expires_at: UtcDatetime


class OptionPulseBar(OptionModel):
    at: UtcDatetime
    open: float = Field(gt=0)
    close: float = Field(gt=0)
    low: float = Field(gt=0)
    high: float = Field(gt=0)
    forming: bool = False
    data_gap: bool = False


class OptionPulseProfile(OptionModel):
    strike: float = Field(gt=0)
    call: float | None = Field(default=None, ge=0)
    put: float | None = Field(default=None, ge=0)


class OptionPulseConePoint(OptionModel):
    at: UtcDatetime
    horizon_days: int | None = None
    lower68: float
    upper68: float
    lower80: float
    upper80: float


class OptionPulseBatch(OptionModel):
    snapshot_id: str
    captured_at: UtcDatetime
    open_at: UtcDatetime
    close_at: UtcDatetime
    bars: tuple[OptionPulseBar, ...]


class OptionPulseResponse(OptionModel):
    symbol: str
    mode: Literal["live", "daily", "latest"]
    timeframe: Literal["5m", "1d"]
    generated_at: UtcDatetime
    data_status: Literal["available", "partial", "unavailable", "unsupported"] = (
        "unavailable"
    )
    collection_state: str = "inactive"
    last_attempt_at: UtcDatetime | None = None
    worker_heartbeat_at: UtcDatetime | None = None
    snapshot_id: str | None = None
    stock_batch_id: str | None = None
    captured_at: UtcDatetime | None = None
    market_at: UtcDatetime | None = None
    contract_market_min_at: UtcDatetime | None = None
    contract_market_max_at: UtcDatetime | None = None
    close_at: UtcDatetime | None = None
    spot: float | None = None
    iv30: float | None = None
    put_call_volume: float | None = None
    total_turnover: float | None = None
    call_peak: float | None = None
    put_peak: float | None = None
    expected_contracts: int = 0
    valid_turnover_contracts: int = 0
    missing_turnover_contracts: int = 0
    excluded_turnover_contracts: int = 0
    complete: bool = False
    dte_min: int = 1
    dte_max: int = 90
    strike_min_ratio: float = 0.7
    strike_max_ratio: float = 1.3
    bars: tuple[OptionPulseBar, ...] = ()
    profile: tuple[OptionPulseProfile, ...] = ()
    cone: tuple[OptionPulseConePoint, ...] = ()
    reasons: tuple[str, ...] = ()
    formula_version: str = "iv30-remaining-regular-minutes-over390-over252-z1.2816-v1"


class OptionContract(OptionModel):
    contract_id: str = Field(min_length=1, max_length=128)
    underlying: str
    side: OptionSide
    strike: float = Field(gt=0)
    expiration: date
    multiplier: float | None = Field(default=None, gt=0)
    standard: bool | None = None
    settlement: str | None = None
    exercise: str | None = None
    bid: float | None = Field(default=None, ge=0)
    ask: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    turnover: float | None = Field(default=None, ge=0)
    session_low: float | None = Field(default=None, ge=0)
    session_high: float | None = Field(default=None, ge=0)
    open_interest: float | None = Field(default=None, ge=0)
    oi_date: date | None = None
    iv: float | None = Field(default=None, gt=0)
    delta: float | None = None
    delta_units: Literal["unknown", "signed_unit_v1"] = "unknown"
    gamma: float | None = Field(default=None, ge=0)
    theta: float | None = None
    vega: float | None = None
    observed_at: UtcDatetime | None = None
    quote_at: UtcDatetime | None = None
    greeks_at: UtcDatetime | None = None
    source_available_at: UtcDatetime
    ingested_at: UtcDatetime
    underlying_price: float | None = Field(default=None, gt=0)
    underlying_at: UtcDatetime | None = None
    greek_units: Literal["unknown", "per_usd_v1"] = "unknown"
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_times(self) -> Self:
        if self.source_available_at > self.ingested_at:
            raise ValueError("option source availability follows ingestion")
        if any(
            at is not None and at > self.source_available_at
            for at in (
                self.observed_at,
                self.quote_at,
                self.greeks_at,
                self.underlying_at,
            )
        ):
            raise ValueError("option field time follows source availability")
        if any(
            value is not None and not value.is_integer()
            for value in (self.open_interest, self.volume)
        ):
            raise ValueError("option counts require whole contracts")
        if (
            self.delta_units == "signed_unit_v1"
            and self.delta is not None
            and not (
                0 <= self.delta <= 1 if self.side == "call" else -1 <= self.delta <= 0
            )
        ):
            raise ValueError("signed Delta must match side and unit interval")
        return self


class OptionChainSnapshot(OptionModel):
    snapshot_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    raw_batch_id: str
    symbol: str
    provider: str = "futu"
    feed: str = "account_quote_rights_unconfirmed"
    sdk_version: str = "unknown"
    opend_version: str = "unknown"
    normalization_version: str = "options-v1"
    dataset_revision: str = "options-forward-v1"
    captured_at: UtcDatetime
    source_available_at: UtcDatetime
    underlying_price: float | None = Field(default=None, gt=0)
    underlying_at: UtcDatetime | None = None
    dte_min: int = 1
    dte_max: int = 90
    strike_min_ratio: float = 0.7
    strike_max_ratio: float = 1.3
    expected_contract_ids: tuple[str, ...] = ()
    contracts: tuple[OptionContract, ...] = ()
    discovery_complete: bool = False
    complete: bool = False
    errors: tuple[str, ...] = ()
    time_evidence: Literal["forward_only", "historical_verified"] = "forward_only"
    availability_evidence: tuple[str, ...] = ()
    license_reference: str | None = None
    market_as_of: UtcDatetime | None = None

    @model_validator(mode="after")
    def check_identity(self) -> Self:
        if self.symbol not in OPTION_SYMBOLS:
            raise ValueError("unsupported option symbol")
        if any(
            at is not None and at > self.source_available_at
            for at in (self.market_as_of, self.underlying_at)
        ):
            raise ValueError("snapshot market time follows source availability")
        if self.time_evidence == "historical_verified" and (
            not self.availability_evidence
            or not self.license_reference
            or self.market_as_of is None
        ):
            raise ValueError(
                "historical options require time, license and market-date evidence"
            )
        if any(
            row.source_available_at > self.source_available_at for row in self.contracts
        ):
            raise ValueError("option batch precedes field availability")
        ids = [row.contract_id for row in self.contracts]
        if len(set(ids)) != len(ids) or len(set(self.expected_contract_ids)) != len(
            self.expected_contract_ids
        ):
            raise ValueError("duplicate option identity")
        if any(row.underlying != self.symbol for row in self.contracts):
            raise ValueError("option underlying mismatch")
        if not set(ids).issubset(self.expected_contract_ids):
            raise ValueError("undiscovered option identity")
        if self.complete and (
            not self.discovery_complete or set(ids) != set(self.expected_contract_ids)
        ):
            raise ValueError(
                "complete option snapshot requires complete discovery and records"
            )
        if not (
            1 <= self.dte_min <= self.dte_max <= 90
            and 0 < self.strike_min_ratio <= self.strike_max_ratio
        ):
            raise ValueError("invalid option window")
        return self


class OptionHeaderData(OptionModel):
    symbol: str
    snapshot_id: str | None = None
    market_as_of: UtcDatetime | None = None
    captured_at: UtcDatetime | None = None
    complete: bool = False
    contract_count: int = Field(default=0, ge=0)
    reasons: tuple[str, ...] = ()


class OptionHeaderResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data_status: Literal["available", "unavailable", "unsupported"]
    resolved_snapshot_id: str | None
    data: OptionHeaderData


class OptionCoverage(OptionModel):
    expected: int
    valid: int
    missing: int
    excluded: int
    discovery_complete: bool


class OptionCell(OptionModel):
    strike: float
    expiration: date
    value: float | None
    contract_ids: tuple[str, ...]
    valid_count: int
    missing_count: int
    excluded_count: int


class OptionMapSideValues(OptionModel):
    gex: float | None = None
    vex: float | None = None
    turnover: float | None = None
    contracts: int = 0
    exposure_valid: int = 0
    turnover_valid: int = 0
    exposure_excluded: int = 0
    turnover_excluded: int = 0
    reasons: tuple[str, ...] = ()


class OptionMapGroup(OptionModel):
    call: OptionMapSideValues = OptionMapSideValues()
    put: OptionMapSideValues = OptionMapSideValues()


class OptionMapCell(OptionMapGroup):
    strike: float
    expiration: date
    contract_ids: tuple[str, ...] = ()


class OptionMapRanking(OptionModel):
    strike: float
    turnover: float
    share: float


class OptionMapWindow(OptionModel):
    dte_max: int
    total: OptionMapGroup
    rankings: tuple[OptionMapRanking, ...] = ()
    call_rankings: tuple[OptionMapRanking, ...] = ()
    put_rankings: tuple[OptionMapRanking, ...] = ()
    partial: bool = False


class OptionStructureMapReadModel(OptionModel):
    symbol: str
    snapshot: OptionChainSnapshot | None = None
    status: Literal["available", "partial", "unavailable", "unsupported"] = (
        "unavailable"
    )
    market_at: UtcDatetime | None = None
    model_at: UtcDatetime | None = None
    formula_version: str = "bs-r0-q0-act365-gex1pct-vex1pp-v1"
    cells: tuple[OptionMapCell, ...] = ()
    strikes: tuple[float, ...] = ()
    expirations: tuple[date, ...] = ()
    total: OptionMapGroup = OptionMapGroup()
    strike_summary: dict[str, OptionMapGroup] = {}
    expiry_summary: dict[str, OptionMapGroup] = {}
    windows: dict[int, OptionMapWindow] = {}
    call_walls: tuple[float, ...] = ()
    put_walls: tuple[float, ...] = ()
    turnover_peaks: tuple[float, ...] = ()
    exposure_coverage: OptionCoverage
    turnover_coverage: OptionCoverage
    reasons: tuple[str, ...] = ()


class OptionHeatmapReadModel(OptionModel):
    symbol: str
    snapshot: OptionChainSnapshot | None
    latest_attempt: OptionChainSnapshot | None = None
    status: Literal["available", "partial", "unavailable", "unsupported"]
    metric: OptionMetric
    side: OptionHeatmapSide
    unit: str
    aggregation: str
    formula_version: str = "gamma-scale-1pct-v1"
    strikes: tuple[float, ...] = ()
    expirations: tuple[date, ...] = ()
    cells: tuple[OptionCell, ...] = ()
    coverage: OptionCoverage
    total: float | None = None
    strike_summary: dict[str, float | None] = {}
    expiry_summary: dict[str, float | None] = {}
    dte_min: int = 1
    dte_max: int = 90
    strike_min_ratio: float = 0.7
    strike_max_ratio: float = 1.3
    reasons: tuple[str, ...] = ()
    prediction_status: Literal["research_unpublished"] = "research_unpublished"


class OptionStructureFeatures(OptionModel):
    symbol: str
    as_of: UtcDatetime
    snapshot_id: str
    feature_version: str = "option-numeric-v1"
    available: bool
    values: dict[str, float | None]
    reasons: tuple[str, ...]


class OptionStockReturn(OptionModel):
    symbol: str
    session: date
    log_return: float
    observed_at: UtcDatetime
    source_available_at: UtcDatetime
    annualization_sessions: int = Field(gt=0)

    @model_validator(mode="after")
    def check_return_time(self) -> Self:
        if self.observed_at > self.source_available_at:
            raise ValueError("return observation follows source availability")
        return self


class OptionIVTerm(OptionModel):
    expiration: date
    dte: int
    strike: float
    moneyness: float
    call_iv: float | None
    put_iv: float | None
    atm_iv: float | None
    contract_ids: tuple[str, ...]


class OptionSkewPoint(OptionModel):
    expiration: date
    dte: int
    put_25delta_iv: float | None
    call_25delta_iv: float | None
    skew: float | None
    contract_ids: tuple[str, ...]
    reasons: tuple[str, ...]


class OptionConcentrationPoint(OptionModel):
    metric: Literal["oi", "volume", "gamma"]
    strike: float
    value: float
    share: float
    distance_ratio: float
    contract_ids: tuple[str, ...]


class OptionHistoryPoint(OptionModel):
    snapshot_id: str
    market_as_of: UtcDatetime
    source_available_at: UtcDatetime
    underlying_price: float
    strike: float
    oi: float | None
    gamma: float | None
    contract_ids: tuple[str, ...]


class OptionAnalyticsReadModel(OptionModel):
    symbol: str
    snapshot_id: str
    as_of: UtcDatetime
    feature_version: Literal["option-numeric-v2"] = "option-numeric-v2"
    status: Literal["available", "partial", "unavailable"]
    provider: str
    feed: str
    normalization_version: str
    dataset_revision: str
    captured_at: UtcDatetime
    market_as_of: UtcDatetime | None
    source_available_at: UtcDatetime
    values: dict[str, float | None]
    missing_reasons: dict[str, tuple[str, ...]]
    source_snapshot_ids: tuple[str, ...]
    iv_terms: tuple[OptionIVTerm, ...] = ()
    skew_points: tuple[OptionSkewPoint, ...] = ()
    concentration: tuple[OptionConcentrationPoint, ...] = ()
    history: tuple[OptionHistoryPoint, ...] = ()
    reasons: tuple[str, ...] = ()
    implied_moves: tuple["OptionImpliedMove", ...] = Field(default_factory=tuple)


class OptionImpliedMove(OptionModel):
    horizon_days: int = Field(ge=1, le=20)
    iv: float | None = None
    sigma_pct: float | None = None
    lower_pct: float | None = None
    upper_pct: float | None = None
    lower_price: float | None = None
    upper_price: float | None = None
    formula_version: str = "atm-linear-calendar-h365over252-sigma-h252-z1.2816-v1"
    reason: str | None = None


class OptionModelComparison(OptionModel):
    horizon_days: int
    q10: float | None = None
    q50: float | None = None
    q90: float | None = None
    width_ratio: float | None = None
    model_source: str | None = None
    status: str = "refused"
    reasons: tuple[str, ...] = ()


class OptionDailyPair(OptionModel):
    session: date
    realized_return: float
    sigma_pct: float
    previous_snapshot_id: str
    previous_market_as_of: UtcDatetime


class OptionIVHistory(OptionModel):
    market_as_of: UtcDatetime
    atm_iv: float | None


class OptionRiskAlert(OptionModel):
    rule: str
    value: float
    threshold: float
    market_as_of: UtcDatetime


class OptionCollectionCell(OptionModel):
    symbol: str
    session: date
    status: Literal["complete", "partial", "failed", "skipped", "unavailable"]
    contracts: int | None = None
    snapshot_id: str | None = None
    reasons: tuple[str, ...] = ()


class OptionAutomaticCollection(OptionModel):
    ended_at: UtcDatetime
    status: str
    reason: str | None = None


class OptionCollectionSummary(OptionModel):
    sessions: tuple[date, ...] = ()
    cells: tuple[OptionCollectionCell, ...] = ()
    latest_automatic: OptionAutomaticCollection | None = None
    history_days: int | None = None
    complete_symbols: int | None = None
    scheduler_loaded: bool | None = None
    reasons: tuple[str, ...] = ()


class OptionCoverageHistory(OptionModel):
    market_as_of: UtcDatetime
    model_coverage: float | None
    implied_coverage: float | None
    model_samples: int
    implied_samples: int


class OptionIntervalCoverage(OptionModel):
    horizon_days: int
    model_samples: int = 0
    implied_samples: int = 0
    model_coverage: float | None = None
    implied_coverage: float | None = None
    minimum_samples: int = 20
    status: str = "insufficient_samples"
    reasons: tuple[str, ...] = ()
    history: tuple[OptionCoverageHistory, ...] = ()


class OptionOverviewItem(OptionModel):
    symbol: str
    snapshot_id: str | None = None
    market_as_of: UtcDatetime | None = None
    data_status: str
    maturity: Literal["none", "single", "history"]
    history_days: int | None = None
    analytics: OptionAnalyticsReadModel | None = None
    implied_moves: tuple[OptionImpliedMove, ...] = ()
    model_horizons: tuple[OptionModelComparison, ...] = ()
    daily_pair: OptionDailyPair | None = None
    iv_history: tuple[OptionIVHistory, ...] = ()
    previous_skew_points: tuple[OptionSkewPoint, ...] = ()
    previous_snapshot_id: str | None = None
    term_structure: Literal["upward", "flat", "inverted"] | None = None
    term_slope_threshold: float = 0.0001
    risk_alerts: tuple[OptionRiskAlert, ...] = ()
    reasons: tuple[str, ...] = ()


class OptionOverviewResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data_status: str
    resolved_snapshot_id: str | None
    items: tuple[OptionOverviewItem, ...]
    collection: OptionCollectionSummary
    coverage: tuple[OptionIntervalCoverage, ...] = ()
    reasons: tuple[str, ...] = ()


class OptionProbCurvePoint(OptionModel):
    strike: float = Field(gt=0)
    p_above: float = Field(ge=0, le=1)
    sigma: float = Field(gt=0)


class OptionImpliedProbability(OptionModel):
    expiration: date
    dte: int = Field(ge=0)
    curve: tuple[OptionProbCurvePoint, ...] = ()
    p_up5: float | None = Field(default=None, ge=0, le=1)
    p_dn5: float | None = Field(default=None, ge=0, le=1)
    p_up10: float | None = Field(default=None, ge=0, le=1)
    p_dn10: float | None = Field(default=None, ge=0, le=1)
    lo80: float | None = Field(default=None, gt=0)
    hi80: float | None = Field(default=None, gt=0)
    fit_rmse: float | None = Field(default=None, ge=0)
    fit_n: int = Field(default=0, ge=0)
    formula_version: str = "bl-bs-cubic-smile-r0-v1"
    reason: str | None = None


class OptionIntentSplit(OptionModel):
    otm_call: float = Field(default=0, ge=0)
    itm_call: float = Field(default=0, ge=0)
    otm_put: float = Field(default=0, ge=0)
    itm_put: float = Field(default=0, ge=0)


class OptionMoneyTarget(OptionModel):
    call: float | None = Field(default=None, gt=0)
    call_premium: float = Field(default=0, ge=0)
    put: float | None = Field(default=None, gt=0)
    put_premium: float = Field(default=0, ge=0)


class OptionSkew30(OptionModel):
    expiration: date
    dte: int = Field(ge=0)
    put_iv: float | None = Field(default=None, gt=0)
    call_iv: float | None = Field(default=None, gt=0)
    skew: float | None = None


class OptionTopContract(OptionModel):
    contract_id: str
    side: OptionSide
    strike: float = Field(gt=0)
    expiration: date
    dte: int = Field(ge=0)
    volume: float = Field(ge=0)
    mid: float = Field(gt=0)
    premium: float = Field(ge=0)
    iv: float | None = Field(default=None, gt=0)
    moneyness: float


class OptionBoardItem(OptionModel):
    symbol: str
    snapshot_id: str | None = None
    spot: float | None = Field(default=None, gt=0)
    market_date: date | None = None
    data_status: Literal["available", "partial", "unavailable", "unsupported"] = (
        "unavailable"
    )
    atm_iv_30d: float | None = Field(default=None, gt=0)
    realized_vol_20: float | None = Field(default=None, ge=0)
    skew_30: OptionSkew30 | None = None
    expiry_buckets_volume: tuple[float, float, float, float] = (0, 0, 0, 0)
    expiry_buckets_premium: tuple[float, float, float, float] = (0, 0, 0, 0)
    call_volume: float = Field(default=0, ge=0)
    put_volume: float = Field(default=0, ge=0)
    call_premium: float = Field(default=0, ge=0)
    put_premium: float = Field(default=0, ge=0)
    total_volume: float = Field(default=0, ge=0)
    total_premium: float = Field(default=0, ge=0)
    premium_missing_count: int = Field(default=0, ge=0)
    strike_profile: tuple[tuple[float, float, float, float, float], ...] = ()
    call_peak: float | None = Field(default=None, gt=0)
    put_peak: float | None = Field(default=None, gt=0)
    prob_week: OptionImpliedProbability | None = None
    prob_month: OptionImpliedProbability | None = None
    intent: dict[Literal["all", "short", "long"], OptionIntentSplit] = Field(
        default={
            "all": OptionIntentSplit(),
            "short": OptionIntentSplit(),
            "long": OptionIntentSplit(),
        }
    )
    targets: dict[Literal["week", "month"], OptionMoneyTarget] = Field(
        default={
            "week": OptionMoneyTarget(),
            "month": OptionMoneyTarget(),
        }
    )
    top_contracts: tuple[OptionTopContract, ...] = ()
    reasons: tuple[str, ...] = ()


class OptionBoardMarket(OptionModel):
    volume: float = Field(ge=0)
    premium: float = Field(ge=0)
    call_volume: float = Field(ge=0)
    put_volume: float = Field(ge=0)
    call_premium: float = Field(ge=0)
    put_premium: float = Field(ge=0)
    avg_atm_iv_30d: float | None = Field(default=None, gt=0)
    expiry_buckets_volume: tuple[float, float, float, float]
    intent: dict[Literal["all", "short", "long"], OptionIntentSplit]


class OptionBoardResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data_status: Literal["available", "partial", "unavailable", "unsupported"]
    resolved_snapshot_id: str | None = None
    items: tuple[OptionBoardItem, ...]
    market: OptionBoardMarket | None
    reasons: tuple[str, ...] = ()
    prediction_status: Literal["research_unpublished"] = "research_unpublished"


class OptionCollectionResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data_status: str
    resolved_snapshot_id: str | None = None
    collection: OptionCollectionSummary


class OptionPromotionCheck(OptionModel):
    name: str
    status: Literal["passed", "failed", "insufficient"]


class OptionAblationGroup(OptionModel):
    group: str
    brier: float | None = None
    delta_brier: float | None = None
    pinball: dict[str, float] = Field(default_factory=dict)
    empirical_coverage: float | None = None


class OptionExperimentHorizon(OptionModel):
    horizon_days: int
    dates: int | None = None
    samples: int | None = None
    fold_count: int | None = None
    positive_fold_fraction: float | None = None
    groups: tuple[OptionAblationGroup, ...] = ()
    bootstrap: dict[str, float | str | None] = Field(default_factory=dict)
    checks: tuple[OptionPromotionCheck, ...] = ()
    eligible: bool = False
    reasons: tuple[str, ...] = ()


class OptionExperimentReadModel(OptionModel):
    experiment_id: str
    evaluation_scope: str | None = None
    synthetic: bool
    status: str
    horizons: tuple[OptionExperimentHorizon, ...] = ()
    minimum_test_dates: int | None = None
    history_days: int | None = None
    estimated_ready_date: date | None = None
    eligible: bool = False
    reasons: tuple[str, ...] = ()


class OptionExperimentsResponse(OptionModel):
    request_id: str
    generated_at: UtcDatetime
    data_status: str
    resolved_snapshot_id: str | None = None
    experiments: tuple[OptionExperimentReadModel, ...] = ()
    coverage: tuple[OptionIntervalCoverage, ...] = ()
    reasons: tuple[str, ...] = ()


OptionAnalyticsReadModel.model_rebuild()
