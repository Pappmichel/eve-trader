"""Pure costing engine: ledger -> FIFO cost layers + realized results.

docs/PRODUCTION_REALIZED_PROFIT_PLAN.md, Phase 2. No I/O: the caller (Phase 3)
loads the archive into a `LedgerSnapshot` (already filtered by the D11 share
gate) and `rebuild_costing` returns everything derived from it. Same input,
same output - layer and result ids are assigned sequentially in event order.

Event order
-----------
Events at or after the cutover `t0` are sorted by
``(timestamp, KIND_RANK, id, owner_type, owner_id, division)``:

    buy=10, job_start=20, job_cancel=30, job_end=40, sell=50

So at the same second a purchase is available to a job that starts, a job's
output is available to a sale, and a sale never consumes a job's inputs.

Passes
------
1. Net item flows per type since T0 (bought + produced - consumed - sold).
2. Opening inventory (D8) = max(0, ESI stock - net flows), seeded as the
   oldest layer of each type; negative results are clamped to 0 with a
   warning. BPC runs never get an opening layer (ESI stock is items only).
3. Broker fee pools (D12) spread pro rata by value over each owner's
   order-based fills of the same UTC day.
4. Events are processed for real against the seeded layers.

Cost components and coverage
----------------------------
Every layer carries per-unit `CostComponents`. Coverage = real / (real +
estimated) with real = materials_bought + job + invention + fees and
estimated = materials_estimated; freight is the configured rate (D9) and
never lowers coverage. Opening layers at the reference price, unknown-origin
layers (D5) and the consumed inputs of a job whose frozen inputs are
`assumed_me`/`assumed_decryptor` are materials_estimated.

A value that has no price at all (no reference price for an opening or
unknown-origin quantity) is booked at 0 and marks the layer, every layer
built from it and every sale drawing from it `price_missing`; such a sale
is always "estimated", whatever its coverage says (a 0 would otherwise
count as neither real nor estimated cost).

Inputs the loader must prepare (not checked here)
-------------------------------------------------
- `esi_stock_by_type`: items only - assembled ships and their fitting
  excluded, blueprints (BPOs and BPCs) excluded; items listed in open sell
  orders or outstanding contracts included (they left the hangar, not the
  inventory).
- `IndustryJob.output_quantity` for manufacturing/reaction: runs x the SDE
  product quantity. Without it the output defaults to `successful_runs` or
  `runs` (one unit per run).
- `IndustryJob.output_runs_per_success` for invention: the recipe's base
  runs + the decryptor's run bonus. ESI's `licensed_runs` on an invention
  job is NOT that (live: 300 on jobs whose success yields 20 runs).
- `IndustryJob.uses_bpc` for manufacturing/reaction: True when the job ran
  on a BPC (consumes BPC runs), False for a BPO, None when unknown (treated
  like a BPO: no BPC runs drawn).
- `reference_prices` are item prices; BPC runs use the separate
  `bpc_run_reference_prices` (a BPO market price is not a per-run price).
  Every lookup that finds nothing is listed in `CostingResult.price_requests`
  so the loader can fetch exactly those and run again.
- `bpo_type_ids`: blueprint types bought on the market - always BPOs, since
  BPCs cannot be traded there (D3: capital, never a layer).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

# Same numbers as production.constants / ESI; not imported to keep this pure.
ACTIVITY_MANUFACTURING = 1
ACTIVITY_RESEARCH_TE = 3
ACTIVITY_RESEARCH_ME = 4
ACTIVITY_COPYING = 5
ACTIVITY_INVENTION = 8
ACTIVITY_REACTION_ESI = 9
ACTIVITY_REACTION = 11

_RESEARCH_ACTIVITIES = frozenset({ACTIVITY_RESEARCH_TE, ACTIVITY_RESEARCH_ME})
_BUILD_ACTIVITIES = frozenset({ACTIVITY_MANUFACTURING, ACTIVITY_REACTION})
_KNOWN_ACTIVITIES = _RESEARCH_ACTIVITIES | _BUILD_ACTIVITIES | {ACTIVITY_COPYING, ACTIVITY_INVENTION}

KIND_BUY = "buy"
KIND_JOB_START = "job_start"
KIND_JOB_CANCEL = "job_cancel"
KIND_JOB_END = "job_end"
KIND_SELL = "sell"
KIND_RANK = {
    KIND_BUY: 10,
    KIND_JOB_START: 20,
    KIND_JOB_CANCEL: 30,
    KIND_JOB_END: 40,
    KIND_SELL: 50,
}

UNIT_ITEM = "item"
UNIT_BPC_RUN = "bpc_run"

ORIGIN_BOUGHT = "bought"
ORIGIN_PRODUCED = "produced"
ORIGIN_OPENING = "opening"
ORIGIN_UNKNOWN = "unknown"

ESTIMATED_COVERAGE_THRESHOLD = 0.70

ASSUMED_SOURCES = frozenset({"assumed_me", "assumed_decryptor"})
CANCELLED_STATUSES = frozenset({"cancelled", "reverted"})
COMPLETED_STATUSES = frozenset({"delivered", "ready"})

FEE_REF_TYPES = frozenset({"brokers_fee", "market_provider_tax"})
MARKET_TRANSACTION_REF_TYPE = "market_transaction"
TRANSACTION_TAX_REF_TYPE = "transaction_tax"
# Same plausibility band as trade_reconciliation: id+1 can land on another
# same-second sale's tax entry.
MAX_PLAUSIBLE_TAX_RATE = 0.15

_EPS = 1e-9


# ====================================================================== costs
@dataclass(frozen=True)
class CostComponents:
    materials_bought: float = 0.0
    materials_estimated: float = 0.0
    freight: float = 0.0
    job: float = 0.0
    invention: float = 0.0
    fees: float = 0.0

    @property
    def total(self) -> float:
        return (self.materials_bought + self.materials_estimated + self.freight
                + self.job + self.invention + self.fees)

    @property
    def real(self) -> float:
        return self.materials_bought + self.job + self.invention + self.fees

    @property
    def estimated(self) -> float:
        return self.materials_estimated

    def coverage(self) -> Optional[float]:
        """Real share of the cost, freight excluded; None when there is no
        real or estimated cost at all."""
        base = self.real + self.estimated
        if base <= _EPS:
            return None
        return self.real / base

    def scale(self, qty_ratio: float) -> "CostComponents":
        r = float(qty_ratio)
        return CostComponents(
            self.materials_bought * r, self.materials_estimated * r, self.freight * r,
            self.job * r, self.invention * r, self.fees * r,
        )

    def add(self, other: "CostComponents") -> "CostComponents":
        return CostComponents(
            self.materials_bought + other.materials_bought,
            self.materials_estimated + other.materials_estimated,
            self.freight + other.freight,
            self.job + other.job,
            self.invention + other.invention,
            self.fees + other.fees,
        )

    def as_estimated(self) -> "CostComponents":
        """Everything except freight becomes materials_estimated (inputs of
        a job with an assumed ME or decryptor)."""
        return CostComponents(materials_estimated=self.total - self.freight, freight=self.freight)

    def as_invention(self) -> "CostComponents":
        """Real parts roll into invention; estimates and freight keep their
        own component so coverage stays honest."""
        return CostComponents(
            materials_estimated=self.materials_estimated,
            freight=self.freight,
            invention=self.real,
        )


def _sum_components(items: Iterable[CostComponents]) -> CostComponents:
    out = CostComponents()
    for c in items:
        out = out.add(c)
    return out


# ====================================================================== inputs
@dataclass(frozen=True)
class WalletTransaction:
    owner_type: str
    owner_id: int
    transaction_id: int
    date: datetime
    type_id: int
    location_id: int
    unit_price: float
    quantity: float
    is_buy: bool
    division: int = 0
    region_id: Optional[int] = None
    freight_rate_per_m3: Optional[float] = None
    freight_m3: Optional[float] = None  # total for the transaction
    journal_ref_id: Optional[int] = None

    @property
    def key(self) -> tuple:
        return (self.owner_type, int(self.owner_id), int(self.division), int(self.transaction_id))

    @property
    def ref(self) -> str:
        return f"tx:{self.owner_type}:{self.owner_id}:{self.division}:{self.transaction_id}"


@dataclass(frozen=True)
class JournalEntry:
    owner_type: str
    owner_id: int
    journal_id: int
    date: datetime
    ref_type: str
    amount: float
    context_id: Optional[int] = None
    context_id_type: Optional[str] = None
    division: int = 0


@dataclass(frozen=True)
class MarketOrder:
    owner_type: str
    owner_id: int
    order_id: int
    type_id: int
    location_id: int
    is_buy_order: bool
    price: float
    volume_total: float = 0
    issued: Optional[datetime] = None
    state: Optional[str] = None


@dataclass(frozen=True)
class JobInput:
    type_id: int
    quantity: float
    source: str


@dataclass(frozen=True)
class IndustryJob:
    owner_type: str
    owner_id: int
    job_id: int
    activity_id: int
    blueprint_type_id: int
    product_type_id: Optional[int]
    runs: int
    start_date: Optional[datetime]
    status: Optional[str]
    licensed_runs: Optional[int] = None
    successful_runs: Optional[int] = None
    cost: Optional[float] = None
    installer_id: Optional[int] = None
    end_date: Optional[datetime] = None
    completed_date: Optional[datetime] = None
    probability: Optional[float] = None
    inputs: Sequence[JobInput] = ()
    output_quantity: Optional[float] = None
    output_runs_per_success: Optional[float] = None
    uses_bpc: Optional[bool] = None

    @property
    def activity(self) -> int:
        a = int(self.activity_id)
        return ACTIVITY_REACTION if a == ACTIVITY_REACTION_ESI else a

    @property
    def ref(self) -> str:
        return f"job:{self.owner_type}:{self.owner_id}:{self.job_id}"


@dataclass(frozen=True)
class LedgerSnapshot:
    t0: datetime
    reference_prices: dict = field(default_factory=dict)  # (type_id, date) -> ISK/unit
    esi_stock_by_type: dict = field(default_factory=dict)  # type_id -> qty
    production_type_ids: frozenset = frozenset()
    tenant_character_ids: frozenset = frozenset()
    wallet_transactions: Sequence[WalletTransaction] = ()
    journal_entries: Sequence[JournalEntry] = ()
    market_orders: Sequence[MarketOrder] = ()
    industry_jobs: Sequence[IndustryJob] = ()
    opening_cost_overrides: dict = field(default_factory=dict)  # type_id -> ISK/unit
    home_structure_id: Optional[int] = None
    bpc_run_reference_prices: dict = field(default_factory=dict)  # (blueprint type, date) -> ISK/run
    # Invention inputs consumed as items although they are the job's
    # blueprint_type_id (relics).
    relic_type_ids: frozenset = frozenset()
    # D3: market buys of these types go to capital_in_bpos, never a layer.
    bpo_type_ids: frozenset = frozenset()


# ====================================================================== outputs
@dataclass
class CostLayer:
    layer_id: int
    type_id: int
    unit: str
    quantity: float
    quantity_left: float
    origin: str
    source_ref: str
    unit_components: CostComponents
    layer_date: Optional[datetime]
    job_id: Optional[int] = None
    assumed_me: bool = False
    assumed_decryptor: bool = False
    # Valued without any real purchase (opening at reference price, unknown).
    estimated_origin: bool = False
    # Some of its value had no price at all and was booked at 0.
    price_missing: bool = False

    @property
    def unit_cost(self) -> float:
        return self.unit_components.total

    @property
    def components(self) -> CostComponents:
        """Components of the remaining quantity."""
        return self.unit_components.scale(self.quantity_left)


@dataclass(frozen=True)
class LayerDraw:
    layer_id: int
    type_id: int
    unit: str
    quantity: float
    origin: str
    components: CostComponents
    estimated_origin: bool
    price_missing: bool = False

    @property
    def cost(self) -> float:
        return self.components.total


@dataclass
class RealizedResult:
    result_id: int
    sale_ref: str
    type_id: int
    quantity: float
    gross: float
    sales_tax: Optional[float]
    broker_fee: float
    revenue: float
    cost: float
    profit: float
    components: CostComponents
    coverage_share: float
    sale_date: datetime
    location_id: int
    draws: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    price_missing: bool = False

    @property
    def estimated(self) -> bool:
        return self.price_missing or self.coverage_share < ESTIMATED_COVERAGE_THRESHOLD


@dataclass
class JobRecord:
    job_ref: str
    job_id: int
    owner_type: str
    owner_id: int
    activity_id: int
    blueprint_type_id: int
    product_type_id: Optional[int]
    runs: int
    start_date: datetime
    status: Optional[str]
    job_cost: float
    state: str = "wip"  # wip | produced | cancelled | failed | research | unknown_outcome
    draws: list = field(default_factory=list)
    components: CostComponents = CostComponents()
    assumed_me: bool = False
    assumed_decryptor: bool = False
    price_missing: bool = False
    output_layer_id: Optional[int] = None
    output_type_id: Optional[int] = None
    output_quantity: float = 0.0
    output_unit: Optional[str] = None


@dataclass(frozen=True)
class JobLoss:
    job_ref: str
    job_id: int
    date: datetime
    components: CostComponents

    @property
    def cost(self) -> float:
        return self.components.total


@dataclass(frozen=True)
class FeePeriod:
    owner_type: str
    owner_id: int
    day: date
    amount: float


@dataclass(frozen=True)
class OpeningLine:
    type_id: int
    raw_quantity: float
    quantity: float
    unit_cost: float
    price_source: str  # reference | override | missing | none
    layer_id: Optional[int]


@dataclass
class CostingResult:
    t0: datetime
    layers: list  # remaining (quantity_left > 0)
    all_layers: list
    realized_results: list
    jobs: list
    cancelled_job_losses: list
    failed_invention_losses: list
    fees_without_sale: list  # FeePeriod
    fees_on_other_trading: float
    capital_in_bpos: dict  # blueprint type_id -> ISK
    opening_lines: list
    jobs_before_t0: list  # job refs
    skipped_corp_jobs: list  # job refs
    warnings: list
    # (type_id, unit, day) lookups that found no reference price.
    price_requests: frozenset = frozenset()

    @property
    def work_in_progress(self) -> list:
        return [j for j in self.jobs if j.state == "wip"]

    @property
    def fees_without_sale_total(self) -> float:
        return sum(p.amount for p in self.fees_without_sale)

    def realized_components(self) -> CostComponents:
        return _sum_components(r.components for r in self.realized_results)

    def coverage(self) -> Optional[float]:
        return self.realized_components().coverage()

    def coverage_by_type(self) -> dict:
        by_type: dict[int, CostComponents] = defaultdict(CostComponents)
        for r in self.realized_results:
            by_type[r.type_id] = by_type[r.type_id].add(r.components)
        return {t: c.coverage() for t, c in sorted(by_type.items())}


# ====================================================================== engine
def rebuild_costing(ledger: LedgerSnapshot) -> CostingResult:
    return _Costing(ledger).run()


def _day(ts: datetime) -> date:
    return ts.date()


class _Costing:
    def __init__(self, ledger: LedgerSnapshot):
        self.ledger = ledger
        self.t0 = ledger.t0
        self.warnings: list[str] = []
        self._warned: set[str] = set()
        self.all_layers: list[CostLayer] = []
        self.layers_by_key: dict[tuple, list[CostLayer]] = defaultdict(list)
        self.results: list[RealizedResult] = []
        self.jobs: list[JobRecord] = []
        self.pending: dict[str, JobRecord] = {}
        self.cancelled: list[JobLoss] = []
        self.failed_invention: list[JobLoss] = []
        self.fees_without_sale: list[FeePeriod] = []
        self.fees_other = 0.0
        self.capital: dict[int, float] = defaultdict(float)
        self.opening_lines: list[OpeningLine] = []
        self.jobs_before_t0: list[str] = []
        self.skipped_corp_jobs: list[str] = []
        self.fee_by_tx: dict[tuple, float] = {}
        self.price_requests: set[tuple] = set()

    # ---------------------------------------------------------------- helpers
    def warn(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            self.warnings.append(msg)

    def _job_included(self, job: IndustryJob) -> bool:
        if job.owner_type != "corporation":
            return True
        return job.installer_id is not None and int(job.installer_id) in self.ledger.tenant_character_ids

    def _input_unit(self, job: IndustryJob, inp: JobInput) -> str:
        if (job.activity == ACTIVITY_INVENTION and int(inp.type_id) == int(job.blueprint_type_id)
                and int(inp.type_id) not in self.ledger.relic_type_ids):
            return UNIT_BPC_RUN
        return UNIT_ITEM

    def _build_output_qty(self, job: IndustryJob) -> float:
        if job.output_quantity is not None:
            return float(job.output_quantity)
        if job.successful_runs is not None:
            return float(job.successful_runs)
        return float(job.runs or 0)

    def _new_layer(self, *, type_id: int, unit: str, quantity: float, unit_components: CostComponents,
                   origin: str, source_ref: str, layer_date: Optional[datetime],
                   job_id: Optional[int] = None, assumed_me: bool = False,
                   assumed_decryptor: bool = False, estimated_origin: bool = False,
                   price_missing: bool = False) -> CostLayer:
        layer = CostLayer(
            layer_id=len(self.all_layers) + 1,
            type_id=int(type_id), unit=unit, quantity=float(quantity), quantity_left=float(quantity),
            origin=origin, source_ref=source_ref, unit_components=unit_components,
            layer_date=layer_date, job_id=job_id, assumed_me=assumed_me,
            assumed_decryptor=assumed_decryptor, estimated_origin=estimated_origin,
            price_missing=price_missing,
        )
        self.all_layers.append(layer)
        self.layers_by_key[(layer.type_id, unit)].append(layer)
        return layer

    def _remaining(self, type_id: int, unit: str) -> float:
        return sum(l.quantity_left for l in self.layers_by_key.get((int(type_id), unit), ()))

    def _reference_price(self, type_id: int, unit: str, when: datetime) -> Optional[float]:
        table = self.ledger.bpc_run_reference_prices if unit == UNIT_BPC_RUN else self.ledger.reference_prices
        price = table.get((int(type_id), _day(when)))
        if price is None:
            self.price_requests.add((int(type_id), unit, _day(when)))
            return None
        return float(price)

    def _consume(self, type_id: int, unit: str, quantity: float, when: datetime, context: str, *,
                 sale_order: bool = False, allow_unknown: bool = True) -> tuple[list[LayerDraw], CostComponents]:
        """FIFO draw; `sale_order` applies D6 (produced, opening, rest).
        A shortfall draws from a new `unknown` layer at the reference price
        on `when` (D5)."""
        need = float(quantity)
        draws: list[LayerDraw] = []
        layers = self.layers_by_key.get((int(type_id), unit), [])
        if sale_order:
            ordered = ([l for l in layers if l.origin == ORIGIN_PRODUCED]
                       + [l for l in layers if l.origin == ORIGIN_OPENING]
                       + [l for l in layers if l.origin not in (ORIGIN_PRODUCED, ORIGIN_OPENING)])
        else:
            ordered = list(layers)
        for layer in ordered:
            if need <= _EPS:
                break
            if layer.quantity_left <= _EPS:
                continue
            take = min(need, layer.quantity_left)
            draws.append(self._take(layer, take))
            need -= take
        if need > _EPS and allow_unknown:
            price = self._reference_price(type_id, unit, when)
            missing = price is None
            if missing:
                self.warn(f"missing_reference_price: type {type_id} ({unit}) on {_day(when).isoformat()}, "
                          f"unknown-origin {need:g} valued at 0")
                price = 0.0
            self.warn(f"unknown_origin: type {type_id} ({unit}) {need:g} without a layer ({context})")
            layer = self._new_layer(
                type_id=type_id, unit=unit, quantity=need,
                unit_components=CostComponents(materials_estimated=price),
                origin=ORIGIN_UNKNOWN, source_ref=f"unknown:{context}", layer_date=when,
                estimated_origin=True, price_missing=missing,
            )
            draws.append(self._take(layer, need))
        return draws, _sum_components(d.components for d in draws)

    @staticmethod
    def _take(layer: CostLayer, qty: float) -> LayerDraw:
        layer.quantity_left -= qty
        if abs(layer.quantity_left) <= _EPS:
            layer.quantity_left = 0.0
        return LayerDraw(
            layer_id=layer.layer_id, type_id=layer.type_id, unit=layer.unit, quantity=qty,
            origin=layer.origin, components=layer.unit_components.scale(qty),
            estimated_origin=layer.estimated_origin, price_missing=layer.price_missing,
        )

    # ---------------------------------------------------------------- events
    def _build_events(self) -> list[tuple]:
        events: list[tuple] = []
        for tx in self.ledger.wallet_transactions:
            if tx.date < self.t0:
                continue
            kind = KIND_BUY if tx.is_buy else KIND_SELL
            events.append((tx.date, KIND_RANK[kind], int(tx.transaction_id), tx.owner_type,
                           int(tx.owner_id), int(tx.division), kind, tx))
        for job in self.ledger.industry_jobs:
            if not self._job_included(job):
                self.skipped_corp_jobs.append(job.ref)
                continue
            if job.start_date is None or job.start_date < self.t0:
                self.jobs_before_t0.append(job.ref)
                continue
            if job.activity not in _KNOWN_ACTIVITIES:
                self.warn(f"unsupported_activity: {job.ref} activity {job.activity_id} ignored")
                continue
            base = (int(job.job_id), job.owner_type, int(job.owner_id), 0)
            events.append((job.start_date, KIND_RANK[KIND_JOB_START], *base, KIND_JOB_START, job))
            status = (job.status or "").lower()
            if status in CANCELLED_STATUSES:
                when = job.completed_date or job.end_date or job.start_date
                when = max(when, job.start_date)
                events.append((when, KIND_RANK[KIND_JOB_CANCEL], *base, KIND_JOB_CANCEL, job))
            elif status in COMPLETED_STATUSES and job.activity not in _RESEARCH_ACTIVITIES:
                when = job.end_date or job.completed_date
                if when is None:
                    self.warn(f"job_without_end_date: {job.ref} has no end date, output not booked")
                    continue
                when = max(when, job.start_date)
                events.append((when, KIND_RANK[KIND_JOB_END], *base, KIND_JOB_END, job))
        events.sort(key=lambda e: e[:6])
        return events

    def _net_item_flows(self, events: list[tuple]) -> dict[int, float]:
        flows: dict[int, float] = defaultdict(float)
        bpo = self.ledger.bpo_type_ids
        for ev in events:
            kind, obj = ev[6], ev[7]
            if kind in (KIND_BUY, KIND_SELL):
                if int(obj.type_id) in bpo:
                    continue
                sign = 1.0 if kind == KIND_BUY else -1.0
                flows[int(obj.type_id)] += sign * float(obj.quantity)
            elif kind == KIND_JOB_START:
                for inp in obj.inputs:
                    if self._input_unit(obj, inp) == UNIT_ITEM:
                        flows[int(inp.type_id)] -= float(inp.quantity)
            elif kind == KIND_JOB_END and obj.activity in _BUILD_ACTIVITIES and obj.product_type_id is not None:
                flows[int(obj.product_type_id)] += self._build_output_qty(obj)
        return flows

    def _seed_opening(self, flows: dict[int, float]) -> None:
        stock = self.ledger.esi_stock_by_type
        price_day = _day(self.t0)
        for type_id in sorted(set(int(t) for t in stock) | set(flows)):
            if type_id in self.ledger.bpo_type_ids:
                continue
            raw = float(stock.get(type_id, 0) or 0) - flows.get(type_id, 0.0)
            if raw < -_EPS:
                self.warn(f"negative_opening: type {type_id} opening {raw:g} clamped to 0")
                self.opening_lines.append(OpeningLine(type_id, raw, 0.0, 0.0, "none", None))
                continue
            if raw <= _EPS:
                continue
            override = self.ledger.opening_cost_overrides.get(type_id)
            missing = False
            if override is not None:
                unit = CostComponents(materials_bought=float(override))
                source = "override"
            else:
                price = self._reference_price(type_id, UNIT_ITEM, self.t0)
                if price is None:
                    self.warn(f"missing_reference_price: type {type_id} on {price_day.isoformat()}, "
                              f"opening {raw:g} valued at 0")
                    price, source, missing = 0.0, "missing", True
                else:
                    source = "reference"
                unit = CostComponents(materials_estimated=float(price))
            layer = self._new_layer(
                type_id=type_id, unit=UNIT_ITEM, quantity=raw, unit_components=unit,
                origin=ORIGIN_OPENING, source_ref=f"opening:{type_id}", layer_date=self.t0,
                estimated_origin=override is None, price_missing=missing,
            )
            self.opening_lines.append(OpeningLine(type_id, raw, raw, unit.total, source, layer.layer_id))

    def _attribute_broker_fees(self) -> None:
        """D12: per (owner, UTC day), real order fees spread over that day's
        order-based fills pro rata by value.

        A fill is order-based when the owner has an order for the same type,
        location and side. The price is deliberately not compared: an order's
        price changes with every update and the archive keeps only the last
        one (Phase 0 V4: 1041 of 2447 sell fills had no same-price order)."""
        pools: dict[tuple, float] = defaultdict(float)
        for j in self.ledger.journal_entries:
            if j.ref_type in FEE_REF_TYPES and j.date >= self.t0:
                pools[(j.owner_type, int(j.owner_id), _day(j.date))] += abs(float(j.amount))
        own_orders = {
            (o.owner_type, int(o.owner_id), int(o.type_id), int(o.location_id), bool(o.is_buy_order))
            for o in self.ledger.market_orders
        }
        fills: dict[tuple, list[WalletTransaction]] = defaultdict(list)
        for tx in self.ledger.wallet_transactions:
            if tx.date < self.t0:
                continue
            key = (tx.owner_type, int(tx.owner_id), int(tx.type_id), int(tx.location_id), bool(tx.is_buy))
            if key in own_orders:
                fills[(tx.owner_type, int(tx.owner_id), _day(tx.date))].append(tx)
        for period in sorted(pools):
            amount = pools[period]
            day_fills = sorted(fills.get(period, []), key=lambda t: t.key)
            total_value = sum(float(t.unit_price) * float(t.quantity) for t in day_fills)
            if total_value <= _EPS:
                self.fees_without_sale.append(FeePeriod(period[0], period[1], period[2], amount))
                continue
            for tx in day_fills:
                self.fee_by_tx[tx.key] = amount * float(tx.unit_price) * float(tx.quantity) / total_value

    def _sales_tax(self, tx: WalletTransaction, gross: float) -> Optional[float]:
        mt = self._mt_by_tx.get(tx.key)
        if mt is None:
            return None
        tax_entry = self._journal_by_id.get((mt.owner_type, int(mt.owner_id), int(mt.division), int(mt.journal_id) + 1))
        if (tax_entry is None or tax_entry.ref_type != TRANSACTION_TAX_REF_TYPE
                or tax_entry.date != mt.date):
            return None
        tax = -float(tax_entry.amount)
        if gross <= _EPS or not (0.0 <= tax / gross <= MAX_PLAUSIBLE_TAX_RATE):
            return None
        return tax

    def _index_journal(self) -> None:
        self._journal_by_id: dict[tuple, JournalEntry] = {}
        self._mt_by_tx: dict[tuple, JournalEntry] = {}
        for j in self.ledger.journal_entries:
            self._journal_by_id[(j.owner_type, int(j.owner_id), int(j.division), int(j.journal_id))] = j
            if (j.ref_type == MARKET_TRANSACTION_REF_TYPE and j.context_id_type == "market_transaction_id"
                    and j.context_id is not None):
                self._mt_by_tx[(j.owner_type, int(j.owner_id), int(j.division), int(j.context_id))] = j

    # ---------------------------------------------------------------- handlers
    def _on_buy(self, tx: WalletTransaction) -> None:
        """A buy fill always carries its fee share in its layer (D12 / 5b):
        the fee is part of what the units cost. It reaches Production only
        if a job consumes the units; a Trading import that is sold again
        never touches a Production result."""
        fee = self.fee_by_tx.get(tx.key, 0.0)
        type_id = int(tx.type_id)
        value = float(tx.unit_price) * float(tx.quantity)
        if type_id in self.ledger.bpo_type_ids:
            self.capital[type_id] += value + fee
            return
        freight = 0.0
        if tx.freight_rate_per_m3 is not None and tx.freight_m3 is not None:
            freight = float(tx.freight_rate_per_m3) * float(tx.freight_m3)
        qty = float(tx.quantity)
        if qty <= _EPS:
            return
        total = CostComponents(materials_bought=value, freight=freight, fees=fee)
        self._new_layer(
            type_id=type_id, unit=UNIT_ITEM, quantity=qty, unit_components=total.scale(1.0 / qty),
            origin=ORIGIN_BOUGHT, source_ref=tx.ref, layer_date=tx.date,
        )

    def _on_sell(self, tx: WalletTransaction) -> None:
        type_id = int(tx.type_id)
        fee = self.fee_by_tx.get(tx.key, 0.0)
        qty = float(tx.quantity)
        if type_id in self.ledger.bpo_type_ids:
            self.warn(f"bpo_sale_not_costed: type {type_id} sold ({tx.ref})")
            return
        if type_id not in self.ledger.production_type_ids:
            self.fees_other += fee
            self._consume(type_id, UNIT_ITEM, qty, tx.date, tx.ref, allow_unknown=False)
            return
        gross = float(tx.unit_price) * qty
        result_warnings: list[str] = []
        tax = self._sales_tax(tx, gross)
        if tax is None:
            result_warnings.append("tax_missing")
            self.warn(f"tax_missing: {tx.ref} sales tax not found in the journal, revenue is gross")
        if self.ledger.home_structure_id is not None and int(tx.location_id) != int(self.ledger.home_structure_id):
            result_warnings.append("outside_home_structure")
            self.warn(f"outside_home_structure: {tx.ref} type {type_id} sold at {tx.location_id}")
        draws, consumed = self._consume(type_id, UNIT_ITEM, qty, tx.date, tx.ref, sale_order=True)
        if any(d.origin == ORIGIN_UNKNOWN for d in draws):
            result_warnings.append("unknown_origin")
        price_missing = any(d.price_missing for d in draws)
        if price_missing:
            result_warnings.append("price_missing")
        components = consumed.add(CostComponents(fees=fee))
        coverage = components.coverage()
        if coverage is None:
            coverage = 0.0 if any(d.estimated_origin for d in draws) else 1.0
        revenue = gross - (tax or 0.0)
        cost = components.total
        result = RealizedResult(
            result_id=len(self.results) + 1, sale_ref=tx.ref, type_id=type_id, quantity=qty,
            gross=gross, sales_tax=tax, broker_fee=fee, revenue=revenue, cost=cost,
            profit=revenue - cost, components=components, coverage_share=coverage,
            sale_date=tx.date, location_id=int(tx.location_id), draws=draws, warnings=result_warnings,
            price_missing=price_missing,
        )
        if result.estimated:
            result.warnings.append("estimated")
            if coverage < ESTIMATED_COVERAGE_THRESHOLD:
                self.warn(f"coverage_below_threshold: {tx.ref} type {type_id} coverage {coverage:.0%}")
        self.results.append(result)

    def _on_job_start(self, job: IndustryJob) -> None:
        job_cost = float(job.cost) if job.cost is not None else 0.0
        if job.cost is None:
            self.warn(f"job_cost_missing: {job.ref} has no cost, booked as 0")
        rec = JobRecord(
            job_ref=job.ref, job_id=int(job.job_id), owner_type=job.owner_type, owner_id=int(job.owner_id),
            activity_id=job.activity, blueprint_type_id=int(job.blueprint_type_id),
            product_type_id=job.product_type_id, runs=int(job.runs or 0), start_date=job.start_date,
            status=job.status, job_cost=job_cost,
        )
        self.jobs.append(rec)
        if job.activity in _RESEARCH_ACTIVITIES:
            self.capital[int(job.blueprint_type_id)] += job_cost
            rec.state = "research"
            rec.components = CostComponents(job=job_cost)
            return
        components = CostComponents()
        for inp in sorted(job.inputs, key=lambda i: int(i.type_id)):
            unit = self._input_unit(job, inp)
            draws, consumed = self._consume(int(inp.type_id), unit, float(inp.quantity), job.start_date, job.ref)
            if inp.source in ASSUMED_SOURCES:
                consumed = consumed.as_estimated()
                if inp.source == "assumed_me":
                    rec.assumed_me = True
                else:
                    rec.assumed_decryptor = True
            elif job.activity == ACTIVITY_INVENTION:
                consumed = consumed.as_invention()
            components = components.add(consumed)
            rec.draws.extend(draws)
        if job.activity in _BUILD_ACTIVITIES and job.uses_bpc:
            # Only a job that ran on a BPC uses BPC runs - a BPO job must not
            # eat the copies made for invention. A shortfall (BPC invented or
            # bought before T0) is unknown origin and flagged, never free.
            draws, consumed = self._consume(int(job.blueprint_type_id), UNIT_BPC_RUN, float(job.runs or 0),
                                            job.start_date, job.ref)
            components = components.add(consumed)
            rec.draws.extend(draws)
        rec.price_missing = any(d.price_missing for d in rec.draws)
        if job.activity == ACTIVITY_INVENTION:
            components = components.add(CostComponents(invention=job_cost))
        else:
            components = components.add(CostComponents(job=job_cost))
        rec.components = components
        if rec.assumed_me:
            self.warn(f"assumed_me: {job.ref} inputs computed with an assumed blueprint ME")
        if rec.assumed_decryptor:
            self.warn(f"assumed_decryptor: {job.ref} invention decryptor assumed")
        self.pending[job.ref] = rec

    def _on_job_cancel(self, job: IndustryJob, when: datetime) -> None:
        rec = self.pending.pop(job.ref, None)
        if rec is None:
            return
        rec.state = "cancelled"
        self.cancelled.append(JobLoss(job.ref, int(job.job_id), when, rec.components))

    def _on_job_end(self, job: IndustryJob, when: datetime) -> None:
        rec = self.pending.get(job.ref)
        if rec is None:
            return
        if job.activity == ACTIVITY_INVENTION:
            if job.successful_runs is None:
                rec.state = "unknown_outcome"
                self.pending.pop(job.ref)
                self.warn(f"invention_outcome_unknown: {job.ref} has no successful_runs, cost not booked")
                return
            out_type, unit = job.product_type_id, UNIT_BPC_RUN
            if int(job.successful_runs) <= 0:
                rec.state = "failed"
                self.pending.pop(job.ref)
                self.failed_invention.append(JobLoss(job.ref, int(job.job_id), when, rec.components))
                return
            if not job.output_runs_per_success:
                self.warn(f"invention_runs_unknown: {job.ref} runs per success unknown, "
                          f"cost stays in progress")
                return
            qty = float(job.successful_runs) * float(job.output_runs_per_success)
        elif job.activity == ACTIVITY_COPYING:
            out_type, unit = job.blueprint_type_id, UNIT_BPC_RUN
            qty = float(job.runs or 0) * float(job.licensed_runs or 1)
        else:
            out_type, unit = job.product_type_id, UNIT_ITEM
            qty = self._build_output_qty(job)
        if out_type is None or qty <= _EPS:
            self.warn(f"job_without_output: {job.ref} output type/quantity unknown, cost stays in progress")
            return
        self.pending.pop(job.ref)
        layer = self._new_layer(
            type_id=int(out_type), unit=unit, quantity=qty,
            unit_components=rec.components.scale(1.0 / qty), origin=ORIGIN_PRODUCED,
            source_ref=job.ref, layer_date=when, job_id=int(job.job_id),
            assumed_me=rec.assumed_me, assumed_decryptor=rec.assumed_decryptor,
            price_missing=rec.price_missing,
        )
        rec.state = "produced"
        rec.output_layer_id = layer.layer_id
        rec.output_type_id = int(out_type)
        rec.output_quantity = qty
        rec.output_unit = unit

    # ---------------------------------------------------------------- run
    def run(self) -> CostingResult:
        events = self._build_events()
        self._index_journal()
        self._attribute_broker_fees()
        bought_types = sorted({int(e[7].type_id) for e in events if e[6] == KIND_BUY})
        for type_id in bought_types:
            if type_id in self.ledger.production_type_ids:
                self.warn(f"trading_overlap: Production type {type_id} is also bought on the market")
        self._seed_opening(self._net_item_flows(events))
        for ev in events:
            when, kind, obj = ev[0], ev[6], ev[7]
            if kind == KIND_BUY:
                self._on_buy(obj)
            elif kind == KIND_SELL:
                self._on_sell(obj)
            elif kind == KIND_JOB_START:
                self._on_job_start(obj)
            elif kind == KIND_JOB_CANCEL:
                self._on_job_cancel(obj, when)
            elif kind == KIND_JOB_END:
                self._on_job_end(obj, when)
        return CostingResult(
            t0=self.t0,
            layers=[l for l in self.all_layers if l.quantity_left > _EPS],
            all_layers=list(self.all_layers),
            realized_results=list(self.results),
            jobs=list(self.jobs),
            cancelled_job_losses=list(self.cancelled),
            failed_invention_losses=list(self.failed_invention),
            fees_without_sale=list(self.fees_without_sale),
            fees_on_other_trading=self.fees_other,
            capital_in_bpos=dict(sorted(self.capital.items())),
            opening_lines=list(self.opening_lines),
            jobs_before_t0=list(self.jobs_before_t0),
            skipped_corp_jobs=list(self.skipped_corp_jobs),
            warnings=list(self.warnings),
            price_requests=frozenset(self.price_requests),
        )
