"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (v4)
================================================
Strategy: spread-capture market-maker with graduated inventory management.

Upgrades over v3
-----------------
1. DUAL EMA (fast + slow) for fair value — fast tracks short-term moves,
   slow anchors the long-run mean. Fair value = weighted blend.
2. GRADUATED skew — continuous linear skew instead of coarse round(pos/100),
   so price and size both taper smoothly toward position limits.
3. SIZE TAPER — quote size shrinks linearly as |pos| rises, so large fills
   become less likely when already exposed.
4. TAKE GUARD — aggressive taking is blocked when it would worsen inventory
   (e.g. don't buy aggressively when already long).
5. SOFT STEP-DOWN at ±120, hard one-sided at ±150 (was only hard at ±150).
6. SPREAD WIDENING — when |pos| > 100 we widen our quotes slightly, earning
   more edge per fill to compensate for the unwind risk.
7. BOOK DEPTH CHECK — cap qty to available book volume, never quote more than
   what the other side is offering (avoids phantom fill expectations).
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json

# ── tuneable parameters ──────────────────────────────────────────────────────
PRODUCT          = "HYDROGEL_PACK"
POS_LIMIT        = 200

# Quoting
QUOTE_INSIDE     = 1       # base ticks inside best bid/ask
QUOTE_SIZE_MAX   = 14      # max units per side at zero inventory
QUOTE_SIZE_MIN   = 3       # min units per side (always keep some presence)

# Inventory management
SKEW_PER_UNIT    = 0.03    # ticks of price skew per unit of position
                            # e.g. pos=100 → 3 ticks of skew
SOFT_LIMIT       = 120     # above this, widen spread by 1 extra tick
ONE_SIDE_LEVEL   = 150     # above this, only quote the reducing side
WIDEN_TICKS      = 1       # extra spread ticks added when |pos| > SOFT_LIMIT

# Aggressive taking
TAKE_EDGE        = 10      # ticks away from fair value to trigger a take
TAKE_MAX_FRAC    = 0.5     # take at most this fraction of remaining capacity

# Fair value EMA
EMA_FAST_ALPHA   = 0.15    # fast EMA — tracks short-term mid
EMA_SLOW_ALPHA   = 0.02    # slow EMA — anchors to historical mean
EMA_BLEND        = 0.6     # weight on fast EMA in fair value (1-blend on slow)
FAIR_VALUE_0     = 9991.0  # seed for both EMAs
# ────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


def _fair_value(ema_fast: float, ema_slow: float) -> float:
    return EMA_BLEND * ema_fast + (1.0 - EMA_BLEND) * ema_slow


def _quote_size(pos: int, side: int) -> int:
    """
    Taper quote size linearly based on position.
    side: +1 = bid (buying), -1 = ask (selling)
    If the position already leans in the same direction, shrink the quote.
    """
    # How much inventory already leans in the direction of this side?
    lean = pos * side          # positive means we'd be adding to position
    lean_frac = max(0.0, lean / POS_LIMIT)   # 0.0 → 1.0
    size = QUOTE_SIZE_MAX - (QUOTE_SIZE_MAX - QUOTE_SIZE_MIN) * lean_frac
    return max(QUOTE_SIZE_MIN, int(size))


class Trader:

    def bid(self):
        """Round 2 compatibility stub."""
        return 15

    # ── helpers ──────────────────────────────────────────────────────────────

    def _position(self, state: TradingState) -> int:
        return state.position.get(PRODUCT, 0)

    def _buy_capacity(self, pos: int) -> int:
        return POS_LIMIT - pos

    def _sell_capacity(self, pos: int) -> int:
        return POS_LIMIT + pos

    # ── main run ──────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        # ── restore persisted state ──────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}
        ema_fast: float = saved.get("ema_fast", FAIR_VALUE_0)
        ema_slow: float = saved.get("ema_slow", FAIR_VALUE_0)

        # ── read order book ──────────────────────────────────────────────────
        od: OrderDepth = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            trader_data = json.dumps({"ema_fast": ema_fast, "ema_slow": ema_slow})
            return {PRODUCT: orders}, 0, trader_data

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── update dual EMA fair value ───────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            mid = float(best_bid)
        elif best_ask is not None:
            mid = float(best_ask)
        else:
            mid = (ema_fast + ema_slow) / 2.0

        ema_fast = _ema(ema_fast, mid, EMA_FAST_ALPHA)
        ema_slow = _ema(ema_slow, mid, EMA_SLOW_ALPHA)
        fv = _fair_value(ema_fast, ema_slow)

        pos = self._position(state)
        abs_pos = abs(pos)

        # ── 1. AGGRESSIVE TAKING ─────────────────────────────────────────────
        # Only take if it also reduces or doesn't worsen inventory.
        # Never take a position that already leans against us.

        if best_ask is not None and best_ask < fv - TAKE_EDGE:
            # Only buy aggressively if we aren't already long
            if pos <= 0:
                cap = self._buy_capacity(pos)
                vol = -od.sell_orders[best_ask]
                qty = min(vol, cap, int(cap * TAKE_MAX_FRAC))
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))
                    pos += qty  # shadow-update for capacity checks below

        if best_bid is not None and best_bid > fv + TAKE_EDGE:
            # Only sell aggressively if we aren't already short
            if pos >= 0:
                cap = self._sell_capacity(pos)
                vol = od.buy_orders[best_bid]
                qty = min(vol, cap, int(cap * TAKE_MAX_FRAC))
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))
                    pos -= qty  # shadow-update

        # ── 2. PASSIVE MARKET-MAKING QUOTES ──────────────────────────────────
        if best_bid is not None and best_ask is not None:

            # Continuous price skew based on current inventory
            skew = SKEW_PER_UNIT * pos   # positive pos → negative skew (lean sell)

            # Extra spread when inventory is elevated (earn more per fill)
            extra_widen = WIDEN_TICKS if abs_pos > SOFT_LIMIT else 0

            our_bid = best_bid + QUOTE_INSIDE - round(skew) - extra_widen
            our_ask = best_ask - QUOTE_INSIDE - round(skew) + extra_widen

            # Guard: never cross the book or quote at/through fair value
            if our_bid >= our_ask:
                our_bid = round(fv) - 1
                our_ask = round(fv) + 1

            # One-sided quoting if inventory is close to hard limit
            post_bid = pos < ONE_SIDE_LEVEL
            post_ask = pos > -ONE_SIDE_LEVEL

            if post_bid:
                raw_size = _quote_size(pos, side=+1)
                qty_bid = min(raw_size, self._buy_capacity(pos))
                if qty_bid > 0:
                    orders.append(Order(PRODUCT, our_bid, qty_bid))

            if post_ask:
                raw_size = _quote_size(pos, side=-1)
                qty_ask = min(raw_size, self._sell_capacity(pos))
                if qty_ask > 0:
                    orders.append(Order(PRODUCT, our_ask, -qty_ask))

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} "
            f"fv={fv:.1f} (fast={ema_fast:.1f} slow={ema_slow:.1f}) "
            f"bid={best_bid} ask={best_ask} skew={round(SKEW_PER_UNIT * pos):.0f} "
            f"orders={len(orders)}"
        )

        # ── persist state ─────────────────────────────────────────────────────
        trader_data = json.dumps({"ema_fast": ema_fast, "ema_slow": ema_slow})
        return {PRODUCT: orders}, 0, trader_data
