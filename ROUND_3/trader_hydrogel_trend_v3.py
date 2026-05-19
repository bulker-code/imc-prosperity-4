"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (take + trend v3)
=============================================================
Two independent layers:

1. EMA TAKE LAYER — unchanged 75k baseline
2. TREND LAYER — uses exact same method as data analysis script
   - trend = EMA of price changes, span=20 → alpha = 2/(20+1) ≈ 0.0952
   - Only trades when trend EMA changes sign (direction flip)
   - Takes TREND_MAX units in new direction, holds until next flip
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
TREND_EMA_ALPHA   = 2 / (20 + 1)  # ≈ 0.0952

# Trend position
TREND_MAX         = 50     # units to hold per trend direction
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
        ema_hp: float      = saved.get("ema_hp", FAIR_VALUE_0)
        prev_mid: float    = saved.get("prev_mid", None)
        trend_ema: float   = saved.get("trend_ema", 0.0)
        trend_side: int    = saved.get("trend_side", 0)  # +1 long, -1 short
        tick_count: int    = saved.get("tick_count", 0)

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({
                "ema_hp": ema_hp, "prev_mid": prev_mid,
                "trend_ema": trend_ema, "trend_side": trend_side,
                "tick_count": tick_count
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

        # ── trend EMA (EMA of price changes, same as analysis script) ────────
        if prev_mid is not None:
            price_change = mid - prev_mid
            trend_ema    = _ema(trend_ema, price_change, TREND_EMA_ALPHA)

        prev_mid = mid

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
                    buy_cap -= qty
                    pos     += qty

            if best_bid is not None and best_bid > ema_hp + TAKE_EDGE:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))
                    sell_cap -= qty
                    pos      -= qty

        # ── 2. TREND LAYER ───────────────────────────────────────────────────
        # Only fires when trend EMA changes sign
        if tick_count >= TREND_WARMUP and prev_mid is not None:
            new_side = 1 if trend_ema > 0 else -1

            if new_side != trend_side:
                # Direction flipped — enter new position
                if new_side == 1 and best_ask is not None:
                    qty = min(TREND_MAX, buy_cap)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))
                elif new_side == -1 and best_bid is not None:
                    qty = min(TREND_MAX, sell_cap)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))

                trend_side = new_side

        # ── debug ─────────────────────────────────────────────────────────────
        side_str = "LONG" if trend_side == 1 else "SHORT"
        print(
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"trend_ema={trend_ema:.4f} side={side_str} orders={len(orders)}"
        )

        trader_data = json.dumps({
            "ema_hp": ema_hp, "prev_mid": prev_mid,
            "trend_ema": trend_ema, "trend_side": trend_side,
            "tick_count": tick_count
        })
        return {PRODUCT: orders}, 0, trader_data
