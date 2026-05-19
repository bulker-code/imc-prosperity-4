"""
IMC Prosperity 4 – Round 3 | VELVETFRUIT_EXTRACT
=================================================
Strategy: pure spread-capture market making with inventory skew.

Key observations
-----------------
- Tight spread (~2-4 ticks) means bots quote very close to mid
- We quote 1 tick inside best bid/ask to jump to front of queue
- Inventory skew keeps position balanced around zero
- One-sided quoting at ±150 to avoid hitting position limits
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT        = "VELVETFRUIT_EXTRACT"
POS_LIMIT      = 200
QUOTE_INSIDE   = 1       # ticks inside best bid/ask
QUOTE_SIZE     = 14      # units per side
SKEW_TICKS     = 2       # ticks of skew per 100 units of position
ONE_SIDE_LEVEL = 150     # only quote reducing side beyond this
EMA_ALPHA      = 0.05
FAIR_VALUE_0   = 5260.0  # opening price day 0
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
        ema: float = saved.get("ema", FAIR_VALUE_0)

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({"ema": ema})

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── mid + EMA ────────────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            mid = float(best_bid)
        else:
            mid = float(best_ask)

        ema = _ema(ema, mid, EMA_ALPHA)

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
                our_bid = round(ema) - 1
                our_ask = round(ema) + 1

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
            f"t={state.timestamp} pos={pos} ema={ema:.1f} "
            f"bid={best_bid} ask={best_ask} orders={len(orders)}"
        )

        return {PRODUCT: orders}, 0, json.dumps({"ema": ema})
