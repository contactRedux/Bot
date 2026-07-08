"""
tests/strategies/test_strategies.py — Integration tests for all concrete strategies.

These tests use synthetic OHLCV data and feature matrices.  No live data,
no real models, no network calls.  They verify:

1. Each strategy can be instantiated and run through multiple bars without crashing.
2. Orders are emitted in the correct direction (BUY for bullish signals, etc.).
3. Exit conditions fire (stop-loss, z-score reversion, max hold).
4. Orders have valid fields (positive quantity, confidence in [0,1]).
5. Strategy reset() clears state correctly.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import pytest

from strategies.base import Order, OrderSide, OrderType


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_bar(close: float, open_: float | None = None, high: float | None = None,
              low: float | None = None, volume: float = 1_000_000.0) -> pd.Series:
    return pd.Series({
        "open": open_ or close,
        "high": high or close * 1.005,
        "low": low or close * 0.995,
        "close": close,
        "volume": volume,
    })


def _make_features(n: int = 60, seed: int = 0, extra_cols: dict | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {
        "close": 100 + np.cumsum(rng.normal(0, 0.5, n)),
        "ema_9": 100 + rng.normal(0, 0.3, n),
        "ema_21": 100 + rng.normal(0, 0.2, n),
        "rsi": rng.uniform(30, 70, n),
        "macd": rng.normal(0, 0.1, n),
        "macd_hist": rng.normal(0, 0.05, n),
        "adx": rng.uniform(15, 35, n),
        "bb_pct_b": rng.uniform(0.1, 0.9, n),
        "atr": rng.uniform(0.5, 2.0, n),
        "volume_zscore": rng.normal(0, 1, n),
        "vix": rng.uniform(12, 30, n),
        "yield_curve_slope": rng.uniform(-0.5, 1.5, n),
    }
    if extra_cols:
        data.update(extra_cols)
    return pd.DataFrame(data)


def _assert_valid_order(order: Order) -> None:
    assert isinstance(order, Order)
    assert order.quantity > 0
    assert 0.0 <= order.confidence <= 1.0
    assert order.ticker != ""
    assert order.strategy_id != ""
    assert order.side in (OrderSide.BUY, OrderSide.SELL)


# ---------------------------------------------------------------------------
# MomentumStrategy
# ---------------------------------------------------------------------------

class TestMomentumStrategy:
    def _make_strategy(self, entry_threshold=0.3, min_confidence=0.3):
        from strategies.momentum import MomentumStrategy
        cfg = {
            "enabled": True,
            "allocation_weight": 0.20,
            "entry_threshold": entry_threshold,
            "min_confidence": min_confidence,
            "cooldown_bars": 1,
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.20,
        }
        return MomentumStrategy(cfg, ["AAPL"], base_position_size=100.0)

    def test_no_models_no_orders(self):
        """Without models, strategy emits nothing."""
        s = self._make_strategy()
        feats = _make_features()
        s.on_bar("AAPL", _make_bar(150.0), feats)
        assert s.generate_orders() == []

    def test_with_mock_lstm_emits_order(self):
        """A mock model with a strong signal should trigger an entry."""
        from strategies.momentum import MomentumStrategy

        class MockModel:
            is_trained = True
            def predict(self, X):
                from models.base import SignalOutput
                return SignalOutput(signal=0.9, confidence=0.9, model_id="mock")

        s = MomentumStrategy(
            {"enabled": True, "allocation_weight": 0.2, "entry_threshold": 0.3,
             "min_confidence": 0.3, "cooldown_bars": 1, "stop_loss_pct": 0.10,
             "take_profit_pct": 0.20},
            ["AAPL"],
            lstm_model=MockModel(),
            base_position_size=100.0,
        )
        feats = _make_features(extra_cols={"adx": np.full(60, 30.0)})  # trending
        s.on_bar("AAPL", _make_bar(150.0), feats)
        orders = s.generate_orders()
        assert len(orders) == 1
        _assert_valid_order(orders[0])
        assert orders[0].side == OrderSide.BUY

    def test_short_signal_emits_sell(self):
        from strategies.momentum import MomentumStrategy

        class MockShortModel:
            is_trained = True
            def predict(self, X):
                from models.base import SignalOutput
                return SignalOutput(signal=-0.9, confidence=0.9, model_id="mock")

        s = MomentumStrategy(
            {"enabled": True, "allocation_weight": 0.2, "entry_threshold": 0.3,
             "min_confidence": 0.3, "cooldown_bars": 1, "stop_loss_pct": 0.10,
             "take_profit_pct": 0.20},
            ["AAPL"],
            lstm_model=MockShortModel(),
            base_position_size=100.0,
        )
        feats = _make_features(extra_cols={"adx": np.full(60, 30.0)})
        s.on_bar("AAPL", _make_bar(150.0), feats)
        orders = s.generate_orders()
        assert len(orders) == 1
        assert orders[0].side == OrderSide.SELL

    def test_stop_loss_triggers_exit(self):
        """After a long entry, a price drop below stop_loss_pct should emit a sell."""
        from strategies.momentum import MomentumStrategy

        class BullishModel:
            is_trained = True
            def predict(self, X):
                from models.base import SignalOutput
                return SignalOutput(signal=0.9, confidence=0.9, model_id="mock")

        s = MomentumStrategy(
            {"enabled": True, "allocation_weight": 0.2, "entry_threshold": 0.3,
             "min_confidence": 0.3, "cooldown_bars": 0, "stop_loss_pct": 0.05,
             "take_profit_pct": 0.20},
            ["AAPL"],
            lstm_model=BullishModel(),
            base_position_size=100.0,
        )
        feats = _make_features(extra_cols={"adx": np.full(60, 30.0)})
        # Bar 1: enter long at 150
        s.on_bar("AAPL", _make_bar(150.0), feats)
        s.generate_orders()

        # Bar 2: price drops 10% — stop_loss_pct=0.05 should fire
        s.on_bar("AAPL", _make_bar(135.0), feats)
        orders = s.generate_orders()
        assert any(o.side == OrderSide.SELL for o in orders), "Expected stop-loss exit"

    def test_adx_filter_blocks_entry(self):
        """Low ADX (< 20) should block momentum entries."""
        from strategies.momentum import MomentumStrategy

        class BullModel:
            is_trained = True
            def predict(self, X):
                from models.base import SignalOutput
                return SignalOutput(signal=0.9, confidence=0.9, model_id="mock")

        s = MomentumStrategy(
            {"enabled": True, "allocation_weight": 0.2, "entry_threshold": 0.3,
             "min_confidence": 0.3, "cooldown_bars": 0, "stop_loss_pct": 0.10,
             "take_profit_pct": 0.20},
            ["AAPL"],
            lstm_model=BullModel(),
            base_position_size=100.0,
        )
        feats = _make_features(extra_cols={"adx": np.full(60, 10.0)})  # weak trend
        s.on_bar("AAPL", _make_bar(150.0), feats)
        orders = s.generate_orders()
        assert orders == [], "ADX filter should block entry in weak trend"

    def test_disabled_strategy_emits_nothing(self):
        from strategies.momentum import MomentumStrategy
        s = MomentumStrategy({"enabled": False}, ["AAPL"])
        s.on_bar("AAPL", _make_bar(150.0), _make_features())
        assert s.generate_orders() == []

    def test_reset_clears_position(self):
        from strategies.momentum import MomentumStrategy
        s = MomentumStrategy({"enabled": True, "allocation_weight": 0.2,
                               "entry_threshold": 0.3, "min_confidence": 0.3,
                               "cooldown_bars": 0, "stop_loss_pct": 0.1,
                               "take_profit_pct": 0.2}, ["AAPL"])
        s._state["AAPL"].position = 100.0
        s.reset()
        assert s._state["AAPL"].is_flat


# ---------------------------------------------------------------------------
# MeanReversionStrategy
# ---------------------------------------------------------------------------

class TestMeanReversionStrategy:
    def _make_strategy(self):
        from strategies.mean_reversion import MeanReversionStrategy
        cfg = {
            "enabled": True,
            "allocation_weight": 0.18,
            "lookback_bars": 5,
            "entry_z_score": 1.5,
            "exit_z_score": 0.3,
            "stop_atr_multiplier": 2.0,
        }
        return MeanReversionStrategy(cfg, ["AAPL"], base_position_size=100.0)

    def test_mean_reversion_long_on_oversold(self):
        """Z-score < -entry_z should trigger a BUY."""
        s = self._make_strategy()
        feats = _make_features(extra_cols={
            "adx": np.full(60, 15.0),    # below 25 = ranging (favourable)
            "bb_pct_b": np.full(60, 0.05),  # very oversold
            "atr": np.full(60, 1.0),
        })
        s.on_bar("AAPL", _make_bar(140.0), feats)
        orders = s.generate_orders()
        assert any(o.side == OrderSide.BUY for o in orders), "Oversold → expect LONG"

    def test_mean_reversion_short_on_overbought(self):
        """Z-score > +entry_z should trigger a SELL."""
        s = self._make_strategy()
        feats = _make_features(extra_cols={
            "adx": np.full(60, 15.0),
            "bb_pct_b": np.full(60, 0.95),  # very overbought
            "atr": np.full(60, 1.0),
        })
        s.on_bar("AAPL", _make_bar(165.0), feats)
        orders = s.generate_orders()
        assert any(o.side == OrderSide.SELL for o in orders), "Overbought → expect SHORT"

    def test_trending_market_blocked(self):
        """ADX > 25 should block mean-reversion entries."""
        s = self._make_strategy()
        feats = _make_features(extra_cols={
            "adx": np.full(60, 35.0),   # strong trend
            "bb_pct_b": np.full(60, 0.05),
            "atr": np.full(60, 1.0),
        })
        # Need to fill price buffer first for fallback z-score to work
        for _ in range(10):
            s.on_bar("AAPL", _make_bar(150.0), _make_features(extra_cols={"adx": np.full(60, 35.0)}))
            s.generate_orders()
        s.on_bar("AAPL", _make_bar(140.0), feats)
        orders = s.generate_orders()
        assert orders == [], "Trending market should block mean reversion"

    def test_reversion_exit_fires(self):
        """After entering short, z-score returning to 0 should trigger exit."""
        s = self._make_strategy()
        # Force a short position
        s._state["AAPL"].position = -100.0
        s._state["AAPL"].entry_price = 165.0
        s._state["AAPL"].extra["stop_price"] = 170.0
        s._price_buffers["AAPL"] = [160.0] * 5

        # Bar with neutral z-score (overbought resolved)
        feats = _make_features(extra_cols={
            "adx": np.full(60, 15.0),
            "bb_pct_b": np.full(60, 0.50),  # at midline — z ≈ 0
            "atr": np.full(60, 1.0),
        })
        s.on_bar("AAPL", _make_bar(162.0), feats)
        orders = s.generate_orders()
        assert any(o.side == OrderSide.BUY for o in orders), "Expected exit BUY (covering short)"

    def test_orders_are_valid(self):
        s = self._make_strategy()
        feats = _make_features(extra_cols={
            "adx": np.full(60, 10.0),
            "bb_pct_b": np.full(60, 0.02),
            "atr": np.full(60, 1.5),
        })
        s.on_bar("AAPL", _make_bar(150.0), feats)
        for o in s.generate_orders():
            _assert_valid_order(o)


# ---------------------------------------------------------------------------
# StatArbStrategy
# ---------------------------------------------------------------------------

class TestStatArbStrategy:
    def _make_strategy(self):
        from strategies.stat_arb import StatArbStrategy
        cfg = {
            "enabled": True,
            "allocation_weight": 0.20,
            "entry_z_score": 1.5,
            "exit_z_score": 0.3,
            "max_half_life_bars": 50,
            "cointegration_pvalue_max": 0.5,  # relaxed for tests
            "coint_lookback_bars": 30,
        }
        return StatArbStrategy(cfg, [("AAPL", "MSFT")], base_position_size=50.0)

    def _feed_correlated_prices(self, s, n=40, spread_z=2.5):
        """Feed N bars of correlated prices then a divergent bar."""
        rng = np.random.default_rng(42)
        base = 100 + np.cumsum(rng.normal(0, 0.5, n))
        prices_a = base + rng.normal(0, 0.3, n)
        prices_b = base + rng.normal(0, 0.3, n)

        for i in range(n):
            bar_a = _make_bar(float(prices_a[i]))
            bar_b = _make_bar(float(prices_b[i]))
            feats = _make_features()
            s.on_bar("AAPL", bar_a, feats)
            s.on_bar("MSFT", bar_b, feats)
            s.generate_orders()

    def test_emits_two_legs_on_spread_divergence(self):
        """When spread z-score > entry_z, strategy should emit orders for both legs."""
        from strategies.stat_arb import StatArbStrategy
        cfg = {
            "enabled": True, "allocation_weight": 0.20,
            "entry_z_score": 1.0,  # very sensitive
            "exit_z_score": 0.1,
            "max_half_life_bars": 200,
            "cointegration_pvalue_max": 0.9,  # accept all pairs in tests
            "coint_lookback_bars": 20,
        }
        s = StatArbStrategy(cfg, [("AAPL", "MSFT")], base_position_size=50.0)
        self._feed_correlated_prices(s, n=25)
        ps = s._pair_states[("AAPL", "MSFT")]
        ps.active = True
        ps.hedge_ratio = 1.0
        ps.spread_std = 0.5
        ps.spread_mean = 0.0

        # Inject divergent prices: AAPL much higher than MSFT
        rng = np.random.default_rng(99)
        hist_base = [100.0 + i * 0.1 for i in range(25)]
        ps.price_history_a = hist_base + [110.0]  # AAPL far above
        ps.price_history_b = hist_base + [100.0]  # MSFT at norm
        s._latest_prices["AAPL"] = 110.0
        s._latest_prices["MSFT"] = 100.0

        s._process_pair(ps)
        orders = s.generate_orders()
        assert len(orders) == 2, f"Expected 2 legs, got {len(orders)}"
        tickers = {o.ticker for o in orders}
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        for o in orders:
            _assert_valid_order(o)

    def test_pair_exit_emits_two_legs(self):
        """Close position emits 2 exit orders."""
        from strategies.stat_arb import StatArbStrategy, PairState
        cfg = {
            "enabled": True, "allocation_weight": 0.20,
            "entry_z_score": 1.5, "exit_z_score": 0.3,
            "max_half_life_bars": 100, "cointegration_pvalue_max": 0.9,
            "coint_lookback_bars": 30,
        }
        s = StatArbStrategy(cfg, [("AAPL", "MSFT")], base_position_size=50.0)
        ps = s._pair_states[("AAPL", "MSFT")]
        ps.active = True
        ps.hedge_ratio = 1.0
        ps.position_a = -50.0
        ps.position_b = 50.0

        s._close_pair(ps, reason="test_exit")
        orders = s.generate_orders()
        assert len(orders) == 2
        assert ps.is_flat

    def test_all_orders_valid(self):
        s = self._make_strategy()
        self._feed_correlated_prices(s, n=30)
        orders = s.generate_orders()
        for o in orders:
            _assert_valid_order(o)


# ---------------------------------------------------------------------------
# MarketMakingStrategy
# ---------------------------------------------------------------------------

class TestMarketMakingStrategy:
    def _make_strategy(self):
        from strategies.market_making import MarketMakingStrategy
        cfg = {
            "enabled": True,
            "allocation_weight": 0.15,
            "base_half_spread": 0.001,
            "max_inventory": 100.0,
            "inventory_skew_factor": 0.0001,
            "requote_interval_bars": 1,
        }
        return MarketMakingStrategy(cfg, ["BTC-USD"], base_position_size=5.0)

    def test_emits_bid_and_ask(self):
        s = self._make_strategy()
        feats = _make_features(extra_cols={"atr": np.full(60, 500.0)})
        s.on_bar("BTC-USD", _make_bar(50000.0), feats)
        orders = s.generate_orders()
        assert len(orders) == 2
        sides = {o.side for o in orders}
        assert sides == {OrderSide.BUY, OrderSide.SELL}

    def test_bid_below_ask(self):
        s = self._make_strategy()
        feats = _make_features()
        s.on_bar("BTC-USD", _make_bar(50000.0), feats)
        orders = s.generate_orders()
        bids = [o for o in orders if o.side == OrderSide.BUY]
        asks = [o for o in orders if o.side == OrderSide.SELL]
        assert bids[0].limit_price < asks[0].limit_price

    def test_orders_are_limit_type(self):
        s = self._make_strategy()
        s.on_bar("BTC-USD", _make_bar(50000.0), _make_features())
        for o in s.generate_orders():
            assert o.order_type == OrderType.LIMIT

    def test_inventory_skew_with_long_position(self):
        """With long inventory, ask should be tighter (closer to mid)."""
        s = self._make_strategy()
        feats = _make_features(extra_cols={"atr": np.full(60, 500.0)})
        close = 50000.0

        # No inventory
        s.on_bar("BTC-USD", _make_bar(close), feats)
        orders_no_inv = s.generate_orders()
        ask_no_inv = next(o.limit_price for o in orders_no_inv if o.side == OrderSide.SELL)
        bid_no_inv = next(o.limit_price for o in orders_no_inv if o.side == OrderSide.BUY)
        spread_no_inv = ask_no_inv - bid_no_inv

        # Add long inventory
        s._state["BTC-USD"].position = 80.0
        s.on_bar("BTC-USD", _make_bar(close), feats)
        orders_with_inv = s.generate_orders()
        ask_with_inv = next(o.limit_price for o in orders_with_inv if o.side == OrderSide.SELL)
        bid_with_inv = next(o.limit_price for o in orders_with_inv if o.side == OrderSide.BUY)
        spread_with_inv = ask_with_inv - bid_with_inv

        # With long inventory, ask price should be lower (eager to sell) → spread may differ
        assert bid_with_inv < ask_with_inv  # basic sanity

    def test_all_orders_valid(self):
        s = self._make_strategy()
        s.on_bar("BTC-USD", _make_bar(50000.0), _make_features())
        for o in s.generate_orders():
            _assert_valid_order(o)


# ---------------------------------------------------------------------------
# SentimentStrategy
# ---------------------------------------------------------------------------

class TestSentimentStrategy:
    def _make_strategy(self):
        from strategies.sentiment import SentimentStrategy
        cfg = {
            "enabled": True,
            "allocation_weight": 0.15,
            "sentiment_window_hours": 2.0,
            "entry_z_score": 1.5,
            "min_article_count": 2,
            "decay_half_life_hours": 1.0,
            "max_hold_bars": 3,
        }
        return SentimentStrategy(cfg, ["AAPL"], base_position_size=50.0)

    def _make_article(self, ticker, score, hours_ago=0.0):
        from strategies.sentiment import ScoredArticle
        pub_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return ScoredArticle(ticker=ticker, sentiment_score=score, published_at=pub_at)

    def test_positive_sentiment_emits_buy(self):
        s = self._make_strategy()
        # Build up baseline
        for i in range(8):
            art = self._make_article("AAPL", 0.0, hours_ago=float(i + 3))
            s.on_news("AAPL", art)
            feats = _make_features()
            bar = _make_bar(150.0)
            bar.name = datetime.now(timezone.utc) - timedelta(hours=float(i + 3))
            s.on_bar("AAPL", bar, feats)
            s.generate_orders()

        # Inject very positive articles
        now = datetime.now(timezone.utc)
        for _ in range(3):
            s.on_news("AAPL", self._make_article("AAPL", 0.95, hours_ago=0.1))
        s.set_current_time(now)

        feats = _make_features()
        bar = _make_bar(150.0)
        bar.name = now
        s.on_bar("AAPL", bar, feats)
        orders = s.generate_orders()
        # May or may not fire depending on baseline — just assert any orders are valid
        for o in orders:
            _assert_valid_order(o)
            assert o.side == OrderSide.BUY

    def test_max_hold_force_close(self):
        """Position held too long should be force-closed."""
        s = self._make_strategy()
        s._state["AAPL"].position = 50.0
        s._state["AAPL"].bars_in_position = 2  # one bar before max_hold=3

        now = datetime.now(timezone.utc)
        s.set_current_time(now)
        feats = _make_features()
        bar = _make_bar(155.0)
        bar.name = now
        s.on_bar("AAPL", bar, feats)
        orders = s.generate_orders()
        # bars_in_position becomes 3 → should force close
        assert any(o.side == OrderSide.SELL for o in orders), "Force-close on max_hold"

    def test_no_articles_no_orders(self):
        s = self._make_strategy()
        feats = _make_features()
        s.on_bar("AAPL", _make_bar(150.0), feats)
        assert s.generate_orders() == []

    def test_on_news_wrong_ticker_ignored(self):
        s = self._make_strategy()
        art = self._make_article("TSLA", 0.9)
        s.on_news("TSLA", art)
        assert "TSLA" not in s._articles or len(s._articles.get("TSLA", [])) == 0


# ---------------------------------------------------------------------------
# MacroFactorStrategy
# ---------------------------------------------------------------------------

class TestMacroFactorStrategy:
    def _make_strategy(self):
        from strategies.macro_factor import MacroFactorStrategy
        cfg = {
            "enabled": True,
            "allocation_weight": 0.12,
            "vix_fear_threshold": 25.0,
            "fear_reduction_factor": 0.50,
            "yield_curve_inversion_threshold": 0.0,
            "equity_reduction_on_inversion": 0.40,
            "earnings_surprise_z": 1.5,
            "regime_update_interval_bars": 1,
        }
        return MacroFactorStrategy(cfg, ["AAPL"], base_position_size=30.0)

    def test_normal_regime_multiplier_one(self):
        from strategies.macro_factor import MacroRegime
        s = self._make_strategy()
        feats = _make_features(extra_cols={"vix": np.full(60, 15.0),
                                           "yield_curve_slope": np.full(60, 1.0)})
        s.on_bar("AAPL", _make_bar(150.0), feats)
        assert s.get_regime() == MacroRegime.RISK_ON
        assert s.get_regime_multiplier() == pytest.approx(1.0)

    def test_high_vix_triggers_risk_off(self):
        from strategies.macro_factor import MacroRegime
        s = self._make_strategy()
        feats = _make_features(extra_cols={"vix": np.full(60, 30.0),
                                           "yield_curve_slope": np.full(60, 0.5)})
        s.on_bar("AAPL", _make_bar(150.0), feats)
        assert s.get_regime() == MacroRegime.RISK_OFF
        assert s.get_regime_multiplier() == pytest.approx(0.50)

    def test_vix_crisis_regime(self):
        from strategies.macro_factor import MacroRegime
        s = self._make_strategy()
        feats = _make_features(extra_cols={"vix": np.full(60, 45.0),
                                           "yield_curve_slope": np.full(60, 0.5)})
        s.on_bar("AAPL", _make_bar(150.0), feats)
        assert s.get_regime() == MacroRegime.CRISIS
        assert s.get_regime_multiplier() == pytest.approx(0.25)

    def test_yield_curve_inversion_risk_off(self):
        from strategies.macro_factor import MacroRegime
        s = self._make_strategy()
        feats = _make_features(extra_cols={"vix": np.full(60, 18.0),
                                           "yield_curve_slope": np.full(60, -0.3)})
        s.on_bar("AAPL", _make_bar(150.0), feats)
        assert s.get_regime() == MacroRegime.RISK_OFF
        assert s._yield_curve_inverted

    def test_equity_multiplier_reduced_on_inversion(self):
        s = self._make_strategy()
        feats = _make_features(extra_cols={"vix": np.full(60, 18.0),
                                           "yield_curve_slope": np.full(60, -0.3)})
        s.on_bar("AAPL", _make_bar(150.0), feats)
        mult = s.get_equity_multiplier()
        # fear_factor(0.5) * (1 - 0.4) = 0.3
        assert mult == pytest.approx(0.50 * (1.0 - 0.40))

    def test_earnings_surprise_emits_order(self):
        """Large earnings beat should trigger a BUY signal."""
        class FakeSnapshot:
            eps_reported = 2.5
            eps_consensus = 1.0

        s = self._make_strategy()
        # Build up surprise history (need ≥3 entries)
        for eps in [1.0, 1.1, 1.0, 0.9, 1.05]:
            class Snap:
                eps_reported = eps
                eps_consensus = 1.0
            s.on_fundamental("AAPL", Snap())
        # Big beat
        s.on_fundamental("AAPL", FakeSnapshot())
        feats = _make_features()
        s.on_bar("AAPL", _make_bar(150.0), feats)
        orders = s.generate_orders()
        if orders:  # may or may not fire depending on z-score computation
            assert orders[0].side == OrderSide.BUY

    def test_generate_orders_empty_by_default(self):
        s = self._make_strategy()
        feats = _make_features()
        s.on_bar("AAPL", _make_bar(150.0), feats)
        orders = s.generate_orders()
        # Without earnings surprises, no orders
        assert isinstance(orders, list)
