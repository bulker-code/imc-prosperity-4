"""
IMC Prosperity 4 – Round 3 | VELVETFRUIT_EXTRACT (dual zscore)
===============================================================
Strategy: two independent zscore mean reversion layers.

FAST LAYER — EMA-based mean, short std window
- Reacts quickly to short-term deviations (10-20 tick wiggles)
- Smaller position per trade, enters/exits frequently
- Uses EMA as the mean reference

SLOW LAYER — rolling mean, long lookback
- Only fires on large sustained deviations (full swing extremes)
- Larger position, holds longer until price returns to long-run mean
- Captures big events like the day 2 dip to 5195

Position budget
----------------
- FAST_MAX_POS  = 80  units (fast layer budget)
- SLOW_MAX_POS  = 120 units (slow layer budget)
- Combined hard cap = POS_LIMIT = 200
- Both layers tracked separately via fast_pos and slow_pos in state
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json
import math

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT           = "VELVETFRUIT_EXTRACT"
POS_LIMIT         = 200

# Fast layer — EMA mean + short rolling std
EMA_ALPHA_FAST    = 0.0001    # mean EMA alpha (~20 tick window)
FAST_STD_WINDOW   = 1000      # rolling std window for fast layer
FAST_ENTRY_Z      = 2     # enter on smaller deviations
FAST_EXIT_Z       = 0.1     # exit quickly near mean
FAST_MAX_POS      = 80      # max units for fast layer
FAST_MIN_STD      = 1.0     # floor on fast std

# Slow layer — rolling mean + long rolling std
SLOW_LOOKBACK     = 10000     # long window captures full oscillation
SLOW_ENTRY_Z      = 1.5     # only enter on large deviations
SLOW_EXIT_Z       = 0.3     # hold until closer to mean
SLOW_MAX_POS      = 120     # max units for slow layer
SLOW_MIN_STD      = 1.6     # floor on slow std

WARMUP            = 500     # ticks before slow layer activates
FAIR_VALUE_0      = 5260.0  # EMA seed
# ─────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


def rolling_mean_std(prices: list, min_std: float):
    n = len(prices)
    if n == 0:
        return 0.0, min_std
    mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, min_std)


def rolling_std_around_ema(prices: list, ema: float, min_std: float):
    """Std of prices relative to a given EMA mean."""
    n = len(prices)
    if n == 0:
        return min_std
    variance = sum((p - ema) ** 2 for p in prices) / n
    return max(math.sqrt(variance), min_std)


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
        ema_fast: float      = saved.get("ema_fast", FAIR_VALUE_0)
        mid_history: list    = saved.get("mid_history", [])
        fast_pos: int        = saved.get("fast_pos", 0)
        slow_pos: int        = saved.get("slow_pos", 0)

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({
                "ema_fast": ema_fast, "mid_history": mid_history,
                "fast_pos": fast_pos, "slow_pos": slow_pos
            })

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── mid price ────────────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            mid = float(best_bid)
        else:
            mid = float(best_ask)

        # ── update EMA and history ───────────────────────────────────────────
        ema_fast = _ema(ema_fast, mid, EMA_ALPHA_FAST)

        mid_history.append(mid)
        if len(mid_history) > SLOW_LOOKBACK:
            mid_history.pop(0)

        pos      = self._position(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── fast zscore ──────────────────────────────────────────────────────
        fast_window = mid_history[-FAST_STD_WINDOW:] if len(mid_history) >= FAST_STD_WINDOW else mid_history
        fast_std    = rolling_std_around_ema(fast_window, ema_fast, FAST_MIN_STD)
        fast_z      = (mid - ema_fast) / fast_std

        # ── slow zscore ──────────────────────────────────────────────────────
        warmup_done = len(mid_history) >= WARMUP
        if warmup_done:
            slow_mean, slow_std = rolling_mean_std(mid_history, SLOW_MIN_STD)
            slow_z = (mid - slow_mean) / slow_std
        else:
            slow_mean = ema_fast
            slow_std  = SLOW_MIN_STD
            slow_z    = 0.0

        # ── conviction sizing helper ─────────────────────────────────────────
        def conviction_qty(zscore, entry_z, max_pos, current_layer_pos, cap):
            raw = max(0.3, min(1.0, (abs(zscore) - entry_z) / entry_z))
            target    = int(max_pos * raw)
            shortfall = max(0, target - abs(current_layer_pos))
            return min(shortfall, cap)

        # ════════════════════════════════════════════════════════════════════
        # FAST LAYER
        # ════════════════════════════════════════════════════════════════════
        if warmup_done:
        # Fast exits
            if fast_pos > 0 and fast_z > -FAST_EXIT_Z:
                if best_bid is not None:
                    qty = min(od.buy_orders[best_bid], sell_cap, fast_pos)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))
                        fast_pos -= qty
                        sell_cap -= qty
                        pos      -= qty

            elif fast_pos < 0 and fast_z < FAST_EXIT_Z:
                if best_ask is not None:
                    qty = min(-od.sell_orders[best_ask], buy_cap, abs(fast_pos))
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))
                        fast_pos += qty
                        buy_cap  -= qty
                        pos      += qty

            # Fast entries
            if fast_z < -FAST_ENTRY_Z and fast_pos < FAST_MAX_POS and best_ask is not None:
                qty = conviction_qty(fast_z, FAST_ENTRY_Z, FAST_MAX_POS, fast_pos, buy_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))
                    fast_pos += qty
                    buy_cap  -= qty
                    pos      += qty

            elif fast_z > FAST_ENTRY_Z and fast_pos > -FAST_MAX_POS and best_bid is not None:
                qty = conviction_qty(fast_z, FAST_ENTRY_Z, FAST_MAX_POS, fast_pos, sell_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))
                    fast_pos -= qty
                    sell_cap -= qty
                    pos      -= qty

        # ════════════════════════════════════════════════════════════════════
        # SLOW LAYER
        # ════════════════════════════════════════════════════════════════════
        if warmup_done:

            # Slow exits
            if slow_pos > 0 and slow_z > -SLOW_EXIT_Z:
                if best_bid is not None:
                    qty = min(od.buy_orders[best_bid], sell_cap, slow_pos)
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_bid, -qty))
                        slow_pos -= qty
                        sell_cap -= qty

            elif slow_pos < 0 and slow_z < SLOW_EXIT_Z:
                if best_ask is not None:
                    qty = min(-od.sell_orders[best_ask], buy_cap, abs(slow_pos))
                    if qty > 0:
                        orders.append(Order(PRODUCT, best_ask, qty))
                        slow_pos += qty
                        buy_cap  -= qty

            # Slow entries
            if slow_z < -SLOW_ENTRY_Z and slow_pos < SLOW_MAX_POS and best_ask is not None:
                qty = conviction_qty(slow_z, SLOW_ENTRY_Z, SLOW_MAX_POS, slow_pos, buy_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))
                    slow_pos += qty

            elif slow_z > SLOW_ENTRY_Z and slow_pos > -SLOW_MAX_POS and best_bid is not None:
                qty = conviction_qty(slow_z, SLOW_ENTRY_Z, SLOW_MAX_POS, slow_pos, sell_cap)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))
                    slow_pos -= qty

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} "
            f"fast_z={fast_z:.3f} fast_pos={fast_pos} "
            f"slow_z={slow_z:.3f} slow_pos={slow_pos} "
            f"orders={len(orders)}"
        )

        trader_data = json.dumps({
            "ema_fast": ema_fast, "mid_history": mid_history,
            "fast_pos": fast_pos, "slow_pos": slow_pos
        })
        return {PRODUCT: orders}, 0, trader_data
