"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (v5)
================================================
Strategy: same spread-capture core as v3 (which earned ~5k).
Philosophy: don't fix what isn't broken. v4 over-engineered inventory
management and killed fill rate. v5 keeps v3's aggressive quoting and
only patches the two real drawdown causes:

  1. Skew formula was too coarse (only ±2 ticks).
     Fix: smooth continuous skew, same magnitude at the extremes.

  2. Take layer fired regardless of inventory direction.
     Fix: only take if it moves us toward flat, cap at 30% of capacity.

Everything else is v3 — same quote size, same inside-1-tick positioning,
same hard one-sided cutoff at ±150.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

# ── parameters (mostly unchanged from v3) ───────────────────────────────────
PRODUCT        = "HYDROGEL_PACK"
POS_LIMIT      = 200
QUOTE_INSIDE   = 1       # ticks inside best bid/ask
QUOTE_SIZE     = 14      # unchanged — keep fill rate high
ONE_SIDE_LEVEL = 150     # only quote reducing side above this
TAKE_EDGE      = 10      # ticks from EMA to trigger aggressive take
TAKE_MAX_FRAC  = 0.30    # cap take at 30% of remaining capacity (was 100%)
EMA_ALPHA      = 0.05    # unchanged — slow, stable fair value
FAIR_VALUE_0   = 9991.0  # seed EMA

# Skew: smoothly interpolated between 0 (pos=0) and MAX_SKEW (pos=±POS_LIMIT)
# v3 used round(pos/100)*2 → max 4 ticks. We keep the same max but make it smooth.
MAX_SKEW_TICKS = 4       # same as v3 at extremes, linear in between
# ────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


def _skew(pos: int) -> float:
    """Linear skew: 0 at pos=0, ±MAX_SKEW_TICKS at pos=±POS_LIMIT."""
    return MAX_SKEW_TICKS * (pos / POS_LIMIT)


class Trader:

    def bid(self):
        return 15

    def _pos(self, state: TradingState) -> int:
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

        # ── update EMA ───────────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            ema = _ema(ema, (best_bid + best_ask) / 2.0, EMA_ALPHA)
        elif best_bid is not None:
            ema = _ema(ema, float(best_bid), EMA_ALPHA)
        elif best_ask is not None:
            ema = _ema(ema, float(best_ask), EMA_ALPHA)

        pos   = self._pos(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── 1. AGGRESSIVE TAKING (inventory-direction gated) ─────────────────
        # Only take if the fill moves us toward flat.
        # This avoids piling on to an already-large position.
        if best_ask is not None and best_ask < ema - TAKE_EDGE and pos < 0:
            vol = -od.sell_orders[best_ask]
            qty = min(vol, buy_cap, max(1, int(buy_cap * TAKE_MAX_FRAC)))
            if qty > 0:
                orders.append(Order(PRODUCT, best_ask, qty))
                buy_cap -= qty   # shadow update

        if best_bid is not None and best_bid > ema + TAKE_EDGE and pos > 0:
            vol = od.buy_orders[best_bid]
            qty = min(vol, sell_cap, max(1, int(sell_cap * TAKE_MAX_FRAC)))
            if qty > 0:
                orders.append(Order(PRODUCT, best_bid, -qty))
                sell_cap -= qty  # shadow update

        # ── 2. PASSIVE QUOTES ────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            sk = _skew(pos)

            our_bid = best_bid + QUOTE_INSIDE - round(sk)
            our_ask = best_ask - QUOTE_INSIDE - round(sk)

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

        print(
            f"t={state.timestamp} pos={pos} ema={ema:.1f} "
            f"skew={_skew(pos):.2f} bid={best_bid} ask={best_ask} "
            f"orders={len(orders)}"
        )

        return {PRODUCT: orders}, 0, json.dumps({"ema": ema})
