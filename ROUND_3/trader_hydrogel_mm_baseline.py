"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (market making baseline)
====================================================================
Strategy: pure spread capture market making, no directional bets.

This is the original v3 market making layer only — no take layer,
no mean reversion, no zscore. Just queue-jumping passive quotes
with inventory skew to stay balanced.

The only change from original v3:
- FAIR_VALUE_0 corrected to 9992 (actual opening price)
- EMA only used for book-cross guard, not for directional signals
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT        = "HYDROGEL_PACK"
POS_LIMIT      = 200
QUOTE_INSIDE   = 1       # ticks inside best bid/ask
QUOTE_SIZE     = 14      # units per side
SKEW_TICKS     = 2       # ticks of skew per 100 units of position
ONE_SIDE_LEVEL = 150     # only quote reducing side beyond this
EMA_ALPHA      = 0.05
FAIR_VALUE_0   = 9992.0  # corrected from 9997 to actual opening price
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
        ema_hp: float = saved.get("ema_hp", FAIR_VALUE_0)

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({"ema_hp": ema_hp})

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── update EMA ───────────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            mid = float(best_bid)
        else:
            mid = float(best_ask)

        ema_hp = _ema(ema_hp, mid, EMA_ALPHA)

        pos      = self._position(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── passive market making ────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            skew = SKEW_TICKS * round(pos / 100)

            our_bid = best_bid + QUOTE_INSIDE - skew
            our_ask = best_ask - QUOTE_INSIDE - skew

            # Never cross the book
            if our_bid >= our_ask:
                our_bid = round(ema_hp) - 1
                our_ask = round(ema_hp) + 1

            post_bid = pos < ONE_SIDE_LEVEL
            post_ask = pos > -ONE_SIDE_LEVEL

            if post_bid:
                qty = min(QUOTE_SIZE, buy_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, our_bid, qty))

            if post_ask:
                qty = min(QUOTE_SIZE, sell_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, our_ask, -qty))

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"bid={best_bid} ask={best_ask} orders={len(orders)}"
        )

        trader_data = json.dumps({"ema_hp": ema_hp})
        return {PRODUCT: orders}, 0, trader_data
