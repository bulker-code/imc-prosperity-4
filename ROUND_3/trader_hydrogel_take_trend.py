"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (take + trend)
==========================================================
Two fully independent strategies running simultaneously:

1. EMA TAKE LAYER (unchanged from 75k baseline)
   - Buy when best_ask < ema - TAKE_EDGE
   - Sell when best_bid > ema + TAKE_EDGE
   - Captures short-term mispricings vs lagging EMA

2. TREND LAYER (new, additive)
   - Compare mean of last 20 ticks vs mean of 20 ticks before that
   - Uptrend → open/add to long position
   - Downtrend → open/add to short position
   - Neutral → exit trend position toward flat
   - Completely independent of the take layer
   - Uses a separate position budget so both can run without interfering

Position budget split
----------------------
- Total limit: 200
- Take layer budget: TAKE_LIMIT (uses first N units)
- Trend layer budget: remaining units
- Both share the same hard cap of POS_LIMIT via capacity checks
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT           = "HYDROGEL_PACK"
POS_LIMIT         = 200

# EMA take layer — unchanged from 75k baseline
TAKE_EDGE         = 8
EMA_ALPHA         = 0.05
FAIR_VALUE_0      = 9997.0
EMA_WARMUP_TICKS  = 200

# History
LOOKBACK          = 200    # must be >= TREND_WINDOW * 2

# Trend layer
TREND_WINDOW      = 20     # ticks per comparison window
TREND_THRESHOLD   = 1.0    # minimum mean diff to confirm trend (ticks)
TREND_SIZE        = 20     # units to buy/sell per trend signal
TREND_MAX         = 80     # max units held by trend layer at once
# ─────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


def detect_trend(prices: list, window: int) -> float:
    """
    Positive = recent mean higher than previous = uptrend.
    Negative = recent mean lower than previous = downtrend.
    """
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
        ema_hp: float     = saved.get("ema_hp", FAIR_VALUE_0)
        mid_history: list = saved.get("mid_history", [])

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({
                "ema_hp": ema_hp, "mid_history": mid_history
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

        # ── 1. EMA TAKE LAYER ────────────────────────────────────────────────
        ema_ready = state.timestamp > EMA_WARMUP_TICKS

        if ema_ready:
            if best_ask is not None and best_ask < ema_hp - TAKE_EDGE:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))
                    buy_cap  -= qty
                    pos      += qty

            if best_bid is not None and best_bid > ema_hp + TAKE_EDGE:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))
                    sell_cap -= qty
                    pos      -= qty

        # ── 2. TREND LAYER ───────────────────────────────────────────────────
        # Completely independent — uses remaining capacity after take layer
        if len(mid_history) >= TREND_WINDOW * 2:
            trend = detect_trend(mid_history, TREND_WINDOW)

            trending_up   = trend >  TREND_THRESHOLD
            trending_down = trend < -TREND_THRESHOLD
            neutral       = not trending_up and not trending_down

            if trending_up and best_ask is not None:
                # Price drifting up — buy into the trend
                # Only add if we haven't hit the trend size cap
                if pos < TREND_MAX:
                    qty = min(TREND_SIZE, buy_cap, TREND_MAX - pos)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))

            elif trending_down and best_bid is not None:
                # Price drifting down — sell into the trend
                if pos > -TREND_MAX:
                    qty = min(TREND_SIZE, sell_cap, pos + TREND_MAX)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))

            elif neutral and pos != 0:
                # Trend gone — unwind trend position toward flat
                if pos > 0 and best_bid is not None:
                    qty = min(od.buy_orders[best_bid], sell_cap, pos)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))
                elif pos < 0 and best_ask is not None:
                    qty = min(-od.sell_orders[best_ask], buy_cap, abs(pos))
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))

        # ── debug ─────────────────────────────────────────────────────────────
        trend_val = detect_trend(mid_history, TREND_WINDOW) if len(mid_history) >= TREND_WINDOW * 2 else 0.0
        trend_label = "UP" if trend_val > TREND_THRESHOLD else "DOWN" if trend_val < -TREND_THRESHOLD else "NEUTRAL"
        print(
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"trend={trend_val:.3f} ({trend_label}) orders={len(orders)}"
        )

        trader_data = json.dumps({"ema_hp": ema_hp, "mid_history": mid_history})
        return {PRODUCT: orders}, 0, trader_data
