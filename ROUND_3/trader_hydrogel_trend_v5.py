"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (take + trend v5)
=============================================================
Two independent layers:

1. EMA TAKE LAYER — unchanged 75k baseline
2. TREND LAYER
   - trend_ema = EMA of price changes, alpha = 2/(20+1)
   - Every tick:
       trend_ema >  0.2 → buy  TREND_ADD_SIZE units (up to TREND_MAX)
       trend_ema < -0.2 → sell TREND_ADD_SIZE units (down to -TREND_MAX)
       between -0.2 and 0.2 → unwind trend position toward flat
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

# Trend EMA — matches analysis script span=20
TREND_EMA_ALPHA   = 2 / (2000 + 1)   # ≈ 0.0952

# Trend layer sizing
TREND_THRESHOLD   = 0.15    # abs(trend_ema) must exceed this to act
TREND_ADD_SIZE    = 10      # units to add per tick while trend is active
TREND_MAX         = 175     # max units held by trend layer
TREND_WARMUP      = 50     # ticks before trend layer activates
# ─────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


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
        prev_mid          = saved.get("prev_mid", None)
        trend_ema: float  = saved.get("trend_ema", 0.0)
        tick_count: int   = saved.get("tick_count", 0)
        trend_pos: int    = saved.get("trend_pos", 0)  # units held by trend layer

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({
                "ema_hp": ema_hp, "prev_mid": prev_mid,
                "trend_ema": trend_ema, "tick_count": tick_count,
                "trend_pos": trend_pos
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
        tick_count += 1

        # ── trend EMA update ─────────────────────────────────────────────────
        if prev_mid is not None:
            price_change = mid - prev_mid
            trend_ema    = _ema(trend_ema, price_change, TREND_EMA_ALPHA)
        prev_mid = mid

        pos      = self._position(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── 1. EMA TAKE LAYER ────────────────────────────────────────────────
        ema_ready = state.timestamp > EMA_WARMUP_TICKS
        """
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
        """
        # ── 2. TREND LAYER ───────────────────────────────────────────────────
        if tick_count >= TREND_WARMUP:

            if trend_ema > TREND_THRESHOLD:
                # Uptrend — add to long up to TREND_MAX
                if trend_pos < TREND_MAX and best_ask is not None:
                    qty = min(TREND_ADD_SIZE, buy_cap, TREND_MAX - trend_pos)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))
                        trend_pos += qty

            elif trend_ema < -TREND_THRESHOLD:
                # Downtrend — add to short down to -TREND_MAX
                if trend_pos > -TREND_MAX and best_bid is not None:
                    qty = min(TREND_ADD_SIZE, sell_cap, trend_pos + TREND_MAX)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))
                        trend_pos -= qty

            else:
                # Neutral — unwind trend position toward flat
                if trend_pos > 0 and best_bid is not None:
                    vol = od.buy_orders[best_bid]
                    qty = min(vol, sell_cap, trend_pos)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))
                        trend_pos -= qty

                elif trend_pos < 0 and best_ask is not None:
                    vol = -od.sell_orders[best_ask]
                    qty = min(vol, buy_cap, abs(trend_pos))
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))
                        trend_pos += qty

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"trend_ema={trend_ema:.4f} trend_pos={trend_pos} orders={len(orders)}"
        )

        trader_data = json.dumps({
            "ema_hp": ema_hp, "prev_mid": prev_mid,
            "trend_ema": trend_ema, "tick_count": tick_count,
            "trend_pos": trend_pos
        })
        return {PRODUCT: orders}, 0, trader_data
