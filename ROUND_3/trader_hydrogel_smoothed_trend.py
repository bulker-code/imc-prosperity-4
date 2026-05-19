"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (take + smoothed trend)
===================================================================
Two independent layers:

1. EMA TAKE LAYER — unchanged 75k baseline
2. SMOOTHED TREND LAYER
   - Each tick: compute raw trend = mean(last 20) - mean(prev 20)
   - Store last TREND_SMOOTH raw trend values
   - Smoothed trend = average of those stored values
   - If smoothed trend > 0 → buy (uptrend)
   - If smoothed trend < 0 → sell (downtrend)
   - Always in a position — product rarely stagnant
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT           = "HYDROGEL_PACK"
POS_LIMIT         = 200

# EMA take layer
TAKE_EDGE         = 8
EMA_ALPHA         = 0.05
FAIR_VALUE_0      = 9997.0
EMA_WARMUP_TICKS  = 200

# Mid history
LOOKBACK          = 200    # must be >= TREND_WINDOW * 2

# Trend detection
TREND_WINDOW      = 20     # ticks per comparison window
TREND_SMOOTH      = 10     # how many raw trend values to average for smoothing

# Trend position sizing
TREND_SIZE        = 20     # units per trend entry
TREND_MAX         = 80     # max units held by trend layer
# ─────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


def raw_trend(prices: list, window: int) -> float:
    """Mean of last window ticks minus mean of window ticks before that."""
    if len(prices) < window * 2:
        return 0.0
    recent   = prices[-window:]
    previous = prices[-window * 2:-window]
    return sum(recent) / window - sum(previous) / window


class Trader:

    def bid(self):
        return 15

    def _position(self, state: TradingState) -> int:
        return state.position.get(PRODUCT, 0)

    def run(self, state: TradingState):
        # ── restore state ────────────────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}
        ema_hp: float        = saved.get("ema_hp", FAIR_VALUE_0)
        mid_history: list    = saved.get("mid_history", [])
        trend_history: list  = saved.get("trend_history", [])

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({
                "ema_hp": ema_hp,
                "mid_history": mid_history,
                "trend_history": trend_history
            })

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── mid + EMA ────────────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            mid = float(best_bid)
        else:
            mid = float(best_ask)

        ema_hp = _ema(ema_hp, mid, EMA_ALPHA)

        mid_history.append(mid)
        if len(mid_history) > LOOKBACK:
            mid_history.pop(0)

        pos      = self._position(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── compute and store raw trend ───────────────────────────────────────
        if len(mid_history) >= TREND_WINDOW * 2:
            rt = raw_trend(mid_history, TREND_WINDOW)
            trend_history.append(rt)
            if len(trend_history) > TREND_SMOOTH:
                trend_history.pop(0)

        # ── smoothed trend signal ─────────────────────────────────────────────
        if len(trend_history) >= TREND_SMOOTH:
            smoothed = sum(trend_history) / len(trend_history)
        else:
            smoothed = None  # not enough history yet

        # ── 1. EMA TAKE LAYER ────────────────────────────────────────────────
        ema_ready = state.timestamp > EMA_WARMUP_TICKS

        if ema_ready:
            if best_ask is not None and best_ask < ema_hp - TAKE_EDGE:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))
                    buy_cap -= qty
                    pos     += qty

            if best_bid is not None and best_bid > ema_hp + TAKE_EDGE:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))
                    sell_cap -= qty
                    pos      -= qty

        # ── 2. SMOOTHED TREND LAYER ───────────────────────────────────────────
        if smoothed is not None:
            if smoothed > 0:
                # Uptrend — hold long position up to TREND_MAX
                if pos < TREND_MAX and best_ask is not None:
                    qty = min(TREND_SIZE, buy_cap, TREND_MAX - pos)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))

            else:
                # Downtrend — hold short position down to -TREND_MAX
                if pos > -TREND_MAX and best_bid is not None:
                    qty = min(TREND_SIZE, sell_cap, pos + TREND_MAX)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))

        # ── debug ─────────────────────────────────────────────────────────────
        smoothed_str = f"{smoothed:.4f}" if smoothed is not None else "N/A"
        print(
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"smoothed={smoothed_str}"
            f"orders={len(orders)}"
        )

        trader_data = json.dumps({
            "ema_hp": ema_hp,
            "mid_history": mid_history,
            "trend_history": trend_history
        })
        return {PRODUCT: orders}, 0, trader_data
