"""
backtesting/broker.py — SimulatedBroker: realistic order filling with slippage.

What is slippage?
-----------------
In live markets, you never buy at exactly the mid-price:

* **Market impact**: your own order moves the price against you.
* **Bid-ask spread**: you cross the spread every time you trade.
* **Latency**: by the time your order hits the exchange, the price has moved.

The SimulatedBroker models these effects with configurable slippage models:

1. **Fixed-percentage slippage** (default): fill_price = close × (1 ± pct).
   Simple and commonly used.  Default: 0.05% (5 bps) per side.

2. **Half-spread slippage**: fill_price = close ± half_spread.
   More realistic when you have bid-ask spread data (e.g. from Alpaca).

3. **Square-root market impact**: slippage ∝ sqrt(qty / avg_daily_volume).
   Used when average daily volume is tracked.

Partial fill model
------------------
When ``volume_participation_rate`` is set (default 0.05 = 5%), market orders
are capped at ``bar.volume × rate`` per bar.  If the requested quantity exceeds
this cap and the unfilled remainder is above ``min_fill_pct`` of the original
order, the remaining quantity is re-queued as a pending partial fill.

Limit order logic
-----------------
Limit orders are only filled when the price crosses the limit:

    BUY  limit: fill if bar.low  <= limit_price  (price came to us)
    SELL limit: fill if bar.high >= limit_price

If the limit is not reached, the order is queued and re-checked on every
subsequent bar until it expires (``limit_order_ttl_bars`` bars).

Commission model
----------------
Default: $0.005 per share (Interactive Brokers-style) with a $1.00 minimum.
Configurable via ``commission_per_share`` and ``min_commission`` parameters.

Usage
-----
::

    from backtesting.broker import SimulatedBroker
    from backtesting.events import OrderEvent, BarEvent

    broker = SimulatedBroker(slippage_pct=0.0005, commission_per_share=0.005)
    bar = BarEvent(timestamp=ts, ticker="AAPL", open=185, high=187, low=184, close=186, volume=5e7)
    order = OrderEvent(timestamp=ts, ticker="AAPL", side="buy", quantity=10, order_type="market")
    fills = broker.process_order(order, bar)
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from enum import Enum

from backtesting.events import FillEvent, OrderEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Slippage model type
# ---------------------------------------------------------------------------

class SlippageModelType(str, Enum):  # noqa: UP042
    """Selects which slippage algorithm SimulatedBroker uses."""
    FIXED_BPS = "fixed_bps"
    SQRT_IMPACT = "sqrt_impact"


# ---------------------------------------------------------------------------
# Slippage models
# ---------------------------------------------------------------------------

class SlippageModel:
    """Computes the fill price for a market order."""

    def apply(self, side: str, close: float, high: float, low: float) -> float:
        raise NotImplementedError


class FixedPercentageSlippage(SlippageModel):
    """
    Add/subtract a fixed percentage of the close price.

    Parameters
    ----------
    pct : float
        Slippage fraction per side (e.g. 0.0005 = 5 bps).
    """

    def __init__(self, pct: float = 0.0005) -> None:
        self.pct = pct

    def apply(self, side: str, close: float, high: float, low: float) -> float:
        if side == "buy":
            return close * (1.0 + self.pct)
        return close * (1.0 - self.pct)


class HalfSpreadSlippage(SlippageModel):
    """
    Add/subtract a fixed half-spread in price units.

    More realistic when bid-ask spread is known.

    Parameters
    ----------
    half_spread : float
        Half the bid-ask spread in price units (e.g. 0.01 for a $0.02 spread).
    """

    def __init__(self, half_spread: float = 0.01) -> None:
        self.half_spread = half_spread

    def apply(self, side: str, close: float, high: float, low: float) -> float:
        if side == "buy":
            return close + self.half_spread
        return close - self.half_spread


class SqrtImpactSlippage(SlippageModel):
    """
    Square-root market impact model.

    Slippage ∝ impact_coeff × sqrt(quantity / avg_daily_volume).
    Falls back to fixed_slippage_pct when avg_daily_volume is unavailable.

    Parameters
    ----------
    impact_coeff : float
        Scaling coefficient.  A value of 0.1 means a 1% of ADV order
        incurs ~0.032% slippage (= 0.1 × sqrt(0.01)).
    fixed_slippage_pct : float
        Fallback slippage used when avg_daily_volume is 0.
    """

    def __init__(
        self,
        impact_coeff: float = 0.1,
        fixed_slippage_pct: float = 0.0005,
    ) -> None:
        self.impact_coeff = impact_coeff
        self.fixed_slippage_pct = fixed_slippage_pct
        # avg_daily_volume must be set per-bar before calling apply()
        self.avg_daily_volume: float = 0.0
        self.order_quantity: float = 0.0

    def apply(self, side: str, close: float, high: float, low: float) -> float:
        slip_frac = self._calc_slippage_frac()
        if side == "buy":
            return close * (1.0 + slip_frac)
        return close * (1.0 - slip_frac)

    def _calc_slippage_frac(self) -> float:
        """Square-root market impact: slippage ∝ sqrt(qty / ADV)."""
        if self.avg_daily_volume <= 0 or self.order_quantity <= 0:
            return self.fixed_slippage_pct
        return self.impact_coeff * math.sqrt(self.order_quantity / self.avg_daily_volume)


# ---------------------------------------------------------------------------
# Pending limit order container
# ---------------------------------------------------------------------------

@dataclass
class PendingLimitOrder:
    """A limit order waiting to be filled on a future bar."""
    order: OrderEvent
    bars_remaining: int


# ---------------------------------------------------------------------------
# SimulatedBroker
# ---------------------------------------------------------------------------

class SimulatedBroker:
    """
    Simulates order execution with slippage, commission, and limit-order logic.

    Parameters
    ----------
    slippage_model : SlippageModel
        Slippage strategy.  Defaults to FixedPercentageSlippage(0.0005).
    commission_per_share : float
        Commission charged per share/unit traded.  Default: $0.005.
    min_commission : float
        Minimum commission per order.  Default: $1.00.
    limit_order_ttl_bars : int
        Number of bars a pending limit order survives before being cancelled.
        Set to 0 for day orders (cancelled at bar close if unfilled).
        Default: 0 (day orders).
    volume_participation_rate : float
        Maximum fraction of the bar's volume that can be filled per bar.
        Default 0.05 (5%).  Set to 0.0 to disable volume-based partial fills.
    min_fill_pct : float
        Minimum fill fraction of the original order.  Remainder below this
        threshold is silently discarded to avoid dust orders.  Default: 0.10.
    fee_rate : float
        Crypto-style fee added on top of commission (default 0.001 = 10 bps).

    Notes
    -----
    Market orders are filled immediately at the current bar's close price plus
    slippage.  When ``volume_participation_rate > 0``, large market orders are
    capped per bar and re-queued as pending partial fills.
    """

    def __init__(
        self,
        slippage_model: SlippageModel | None = None,
        commission_per_share: float = 0.005,
        min_commission: float = 1.0,
        limit_order_ttl_bars: int = 0,
        volume_participation_rate: float = 0.0,
        min_fill_pct: float = 0.10,
        fee_rate: float = 0.0,
    ) -> None:
        self.slippage_model = slippage_model or FixedPercentageSlippage(pct=0.0005)
        self.commission_per_share = commission_per_share
        self.min_commission = min_commission
        self.limit_order_ttl_bars = limit_order_ttl_bars
        self.volume_participation_rate = volume_participation_rate
        self.min_fill_pct = min_fill_pct
        self.fee_rate = fee_rate

        # Pending limit/stop/partial orders: ticker → list[PendingLimitOrder]
        self._pending: dict[str, list[PendingLimitOrder]] = {}
        # Running average daily volume per ticker (updated each bar)
        self._adv: dict[str, float] = {}

    # ── Main interface ─────────────────────────────────────────────────────

    # ── ADV tracking ────────────────────────────────────────────────────────

    def update_adv(self, ticker: str, bar_volume: float) -> None:
        """
        Update the exponential moving average of daily volume for a ticker.

        Called internally by process_order / process_bar.  The EMA uses a
        20-bar decay constant so it adapts within roughly one trading month.
        """
        alpha = 2 / (20 + 1)  # 20-bar EMA
        prev = self._adv.get(ticker, bar_volume)
        self._adv[ticker] = alpha * bar_volume + (1 - alpha) * prev

    # ── Main interface ─────────────────────────────────────────────────────

    def process_order(
        self, order: OrderEvent, bar: BarEvent  # noqa: F821
    ) -> list[FillEvent]:
        """
        Attempt to fill an order given the current bar.

        Market orders are always filled immediately (subject to volume cap).
        Limit orders are queued if the price has not yet been reached.

        Parameters
        ----------
        order : OrderEvent
        bar : BarEvent
            The current simulation bar for this ticker.

        Returns
        -------
        list[FillEvent]
            Immediate fills (market) or empty list (limit not yet reached).
        """
        self.update_adv(order.ticker, bar.volume)
        if order.order_type == "market":
            return self._fill_market_with_partial(order, bar)
        elif order.order_type in ("limit",):
            return self._process_limit(order, bar)
        elif order.order_type == "stop":
            return self._process_stop(order, bar)
        else:
            logger.warning("Unknown order_type %s — treating as market", order.order_type)
            return [self._fill_market(order, bar, order.quantity)]

    def process_bar(self, ticker: str, bar: BarEvent) -> list[FillEvent]:  # noqa: F821
        """
        Check pending limit/stop orders against the new bar.

        Called by the engine on every bar to attempt fills of queued orders
        and expire day orders that were not filled.

        Parameters
        ----------
        ticker : str
        bar : BarEvent

        Returns
        -------
        list[FillEvent]
            Any limit/stop orders that were triggered by this bar.
        """
        fills: list[FillEvent] = []
        still_pending: list[PendingLimitOrder] = []
        self.update_adv(ticker, bar.volume)

        for pending in self._pending.get(ticker, []):
            order = pending.order
            # Pending market orders (partial fills re-queued by _fill_market_with_partial)
            if order.order_type == "market":
                new_fills = self._fill_market_with_partial(order, bar)
                fills.extend(new_fills)
                # If it produced a partial again (re-queued), the new pending entry
                # was appended to self._pending[ticker]; skip adding to still_pending.
                continue

            filled = self._try_fill_limit(order, bar)
            if filled is not None:
                fills.append(filled)
            elif pending.bars_remaining > 0:
                # Decrement TTL and keep alive
                still_pending.append(
                    PendingLimitOrder(order=order, bars_remaining=pending.bars_remaining - 1)
                )
            else:
                logger.debug(
                    "Limit order expired: %s %s %s @ %.4f",
                    order.side, order.quantity, ticker, order.limit_price,
                )

        self._pending[ticker] = still_pending
        return fills

    def cancel_pending(self, ticker: str) -> int:
        """Cancel all pending orders for a ticker.  Returns number cancelled."""
        n = len(self._pending.get(ticker, []))
        self._pending[ticker] = []
        return n

    def reset(self) -> None:
        """Clear all pending orders (call at start of each backtest run)."""
        self._pending.clear()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _fill_market_with_partial(
        self, order: OrderEvent, bar: BarEvent  # noqa: F821
    ) -> list[FillEvent]:
        """
        Fill a market order, capping at bar volume participation rate.

        If the order is too large to fill in one bar, the fillable portion
        is executed immediately and the remainder is re-queued as a pending
        partial fill (stored in ``self._pending``).
        """
        qty_requested = order.quantity

        # No volume-based cap → fill everything now
        if self.volume_participation_rate <= 0.0 or bar.volume <= 0.0:
            return [self._fill_market(order, bar, qty_requested)]

        max_fillable = bar.volume * self.volume_participation_rate
        fill_qty = min(qty_requested, max_fillable)
        remainder = qty_requested - fill_qty

        fills: list[FillEvent] = []
        if fill_qty > 0:
            fills.append(self._fill_market(order, bar, fill_qty))

        # Only re-queue if the remainder is significant
        if remainder > 0 and remainder >= qty_requested * self.min_fill_pct:
            partial = OrderEvent(
                timestamp=bar.timestamp,
                ticker=order.ticker,
                side=order.side,
                quantity=remainder,
                order_type="market",
                strategy_id=order.strategy_id,
                order_id=order.order_id,
                metadata=order.metadata,
            )
            ticker = order.ticker
            if ticker not in self._pending:
                self._pending[ticker] = []
            self._pending[ticker].append(
                PendingLimitOrder(order=partial, bars_remaining=self.limit_order_ttl_bars or 10)
            )
            logger.debug(
                "Partial fill: queued remainder %.4f of %s for next bar",
                remainder, ticker,
            )

        return fills

    def _fill_market(
        self, order: OrderEvent, bar: BarEvent, quantity: float  # noqa: F821
    ) -> FillEvent:
        """Fill ``quantity`` shares of a market order at close ± slippage."""
        # If using SqrtImpactSlippage, inject ADV and quantity before calling apply()
        if isinstance(self.slippage_model, SqrtImpactSlippage):
            self.slippage_model.avg_daily_volume = self._adv.get(order.ticker, 0.0)
            self.slippage_model.order_quantity = quantity

        fill_price = self.slippage_model.apply(
            side=order.side,
            close=bar.close,
            high=bar.high,
            low=bar.low,
        )
        commission = self._calc_commission(quantity)
        # Optional fee (e.g. crypto funding) on top of commission
        fee = fill_price * quantity * self.fee_rate
        slippage = fill_price - bar.close

        logger.debug(
            "Market fill: %s %s %.2f @ %.4f (slip=%.4f comm=%.2f fee=%.4f)",
            order.side, order.ticker, quantity, fill_price, slippage, commission, fee,
        )
        return FillEvent(
            timestamp=bar.timestamp,
            ticker=order.ticker,
            side=order.side,
            quantity=quantity,
            fill_price=fill_price,
            commission=commission + fee,
            strategy_id=order.strategy_id,
            order_id=order.order_id or str(uuid.uuid4()),
            slippage=slippage,
            metadata=order.metadata,
        )

    def _process_limit(self, order: OrderEvent, bar: BarEvent) -> list[FillEvent]:  # noqa: F821
        """Queue limit order or fill immediately if price already crossed."""
        fill = self._try_fill_limit(order, bar)
        if fill is not None:
            return [fill]
        # Queue for future bars
        ticker = order.ticker
        if ticker not in self._pending:
            self._pending[ticker] = []
        self._pending[ticker].append(
            PendingLimitOrder(order=order, bars_remaining=self.limit_order_ttl_bars)
        )
        return []

    def _process_stop(self, order: OrderEvent, bar: BarEvent) -> list[FillEvent]:  # noqa: F821
        """Stop orders: fill when bar touches the stop price."""
        if order.stop_price is None:
            return [self._fill_market(order, bar, order.quantity)]
        stop = order.stop_price
        # BUY stop: triggered when price rises above stop (breakout)
        # SELL stop: triggered when price falls below stop (stop-loss)
        triggered = (order.side == "buy" and bar.high >= stop) or \
                    (order.side == "sell" and bar.low <= stop)
        if triggered:
            # Fill at stop price (assume worst case)
            fill_price = self.slippage_model.apply(order.side, stop, bar.high, bar.low)
            commission = self._calc_commission(order.quantity)
            return [FillEvent(
                timestamp=bar.timestamp,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                fill_price=fill_price,
                commission=commission,
                strategy_id=order.strategy_id,
                order_id=order.order_id or str(uuid.uuid4()),
                slippage=fill_price - stop,
                metadata=order.metadata,
            )]
        # Queue for future bars
        ticker = order.ticker
        if ticker not in self._pending:
            self._pending[ticker] = []
        self._pending[ticker].append(
            PendingLimitOrder(order=order, bars_remaining=self.limit_order_ttl_bars)
        )
        return []

    def _try_fill_limit(
        self, order: OrderEvent, bar: BarEvent  # noqa: F821
    ) -> FillEvent | None:
        """
        Check if a limit order can be filled on this bar.

        BUY  limit: fill if bar.low  <= limit_price  (price traded at or below our bid)
        SELL limit: fill if bar.high >= limit_price  (price traded at or above our ask)
        """
        if order.limit_price is None:
            return None

        lp = order.limit_price
        if order.side == "buy" and bar.low <= lp:
            fill_price = min(lp, bar.close)  # pessimistic: fill at limit or worse
        elif order.side == "sell" and bar.high >= lp:
            fill_price = max(lp, bar.close)
        else:
            return None

        commission = self._calc_commission(order.quantity)
        return FillEvent(
            timestamp=bar.timestamp,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            fill_price=fill_price,
            commission=commission,
            strategy_id=order.strategy_id,
            order_id=order.order_id or str(uuid.uuid4()),
            slippage=fill_price - bar.close,
            metadata=order.metadata,
        )

    def _calc_commission(self, quantity: float) -> float:
        """Commission = max(min_commission, quantity × per_share_rate)."""
        return max(self.min_commission, quantity * self.commission_per_share)

    def calc_sqrt_slippage(self, qty: float, avg_daily_vol: float) -> float:
        """
        Public helper: square-root market impact fraction.

        Returns the slippage as a fraction of price (not the price itself).
        Uses the broker's current slippage model parameters if it is
        ``SqrtImpactSlippage``, otherwise uses a default coeff of 0.1.

        Parameters
        ----------
        qty : float
            Order quantity.
        avg_daily_vol : float
            Average daily trading volume for the instrument.

        Returns
        -------
        float
            Slippage as a fraction of price (e.g. 0.001 = 10 bps).
        """
        if isinstance(self.slippage_model, SqrtImpactSlippage):
            coeff = self.slippage_model.impact_coeff
            fallback = self.slippage_model.fixed_slippage_pct
        else:
            coeff = 0.1
            fallback = 0.0005
        if avg_daily_vol <= 0:
            return fallback
        return coeff * math.sqrt(qty / avg_daily_vol)
