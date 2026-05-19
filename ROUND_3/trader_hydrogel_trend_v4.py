"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (take + trend v4)
=============================================================
Two independent layers:

1. EMA TAKE LAYER — unchanged 75k baseline
2. GROUP CONFIRMATION TREND LAYER
   - Splits price history into groups of GROUP_SIZE ticks
   - Computes average mid price per group
   - Requires GROUPS_NEEDED consecutive groups trending in same direction
     before flipping position — tolerates small reversals within trend
   - Only trades on confirmed flips, holds until next confirmed flip
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

# Group confirmation trend
GROUP_SIZE        = 20    # ticks per group
GROUPS_NEEDED     = 5     # consecutive confirming groups to confirm trend flip
TREND_MAX         = 150    # units to hold per trend direction
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
        ema_hp: float          = saved.get("ema_hp", FAIR_VALUE_0)
        trend_side: int        = saved.get("trend_side", 0)
        current_group: list    = saved.get("current_group", [])
        confirmed_groups: list = saved.get("confirmed_groups", [])
        candidate_side: int    = saved.get("candidate_side", 0)

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({
                "ema_hp": ema_hp, "trend_side": trend_side,
                "current_group": current_group,
                "confirmed_groups": confirmed_groups,
                "candidate_side": candidate_side
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
        # ── 2. GROUP CONFIRMATION TREND LAYER ────────────────────────────────
        current_group.append(mid)

        if len(current_group) >= GROUP_SIZE:
            group_avg = sum(current_group) / len(current_group)
            current_group = []  # reset for next group

            if len(confirmed_groups) == 0:
                # First group — just store it, no direction yet
                confirmed_groups.append(group_avg)

            else:
                prev_avg   = confirmed_groups[-1]
                if group_avg > prev_avg + 1:
                    group_side = 1
                elif group_avg < prev_avg - 1:
                    group_side = -1
                else:
                    group_side = 0

                if candidate_side == 0:
                    # No candidate yet — set one
                    candidate_side = group_side
                    confirmed_groups = [prev_avg, group_avg]

                elif group_side == candidate_side:
                    # Continues in same direction — add to confirmed groups
                    confirmed_groups.append(group_avg)

                else:
                    # Direction broke — reset with last two groups
                    confirmed_groups = [prev_avg, group_avg]
                    candidate_side   = group_side

            # Check if we have enough consecutive confirming groups to flip
            if len(confirmed_groups) >= GROUPS_NEEDED and candidate_side != trend_side:
                if candidate_side == 1 and best_ask is not None:
                    qty = min(TREND_MAX, buy_cap)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))
                elif candidate_side == -1 and best_bid is not None:
                    qty = min(TREND_MAX, sell_cap)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))

                trend_side       = candidate_side
                confirmed_groups = []  # reset after flip
                candidate_side   = 0

        # ── debug ─────────────────────────────────────────────────────────────
        side_str = "LONG" if trend_side == 1 else "SHORT" if trend_side == -1 else "NONE"
        print(
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"groups={len(confirmed_groups)}/{GROUPS_NEEDED} "
            f"candidate={'LONG' if candidate_side == 1 else 'SHORT' if candidate_side == -1 else 'NONE'} "
            f"trend_side={side_str} orders={len(orders)}"
        )

        trader_data = json.dumps({
            "ema_hp": ema_hp, "trend_side": trend_side,
            "current_group": current_group,
            "confirmed_groups": confirmed_groups,
            "candidate_side": candidate_side
        })
        return {PRODUCT: orders}, 0, trader_data
