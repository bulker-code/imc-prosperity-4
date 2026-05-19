"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (mean reversion final)
==================================================================
Strategy: pure zscore mean reversion — take mispriced orders, exit at mean.

Edge
-----
- Price oscillates around a stable mean with measurable std.
- When price is > ENTRY_ZSCORE std away from mean, it is statistically likely
  to revert. We take the position and exit when it returns to mean.
- No market making. No EMA bias. No directional assumptions.
- Symmetric both sides — long and short treated identically.

EMA warmup
-----------
- EMA is seeded at the true opening price (9992) and given 200 ticks to
  converge before any orders are placed, preventing seed-bias trades.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json
import math

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT            = "HYDROGEL_PACK"
POS_LIMIT          = 200

# Zscore mean reversion
LOOKBACK           = 150     # rolling window — tune based on swing duration
WARMUP             = 150     # ticks before any trading begins
MIN_STD            = 2.0     # floor to avoid noise in flat markets

ENTRY_ZSCORE       = 1.5     # enter when price is this many std from mean
ENTRY_ZSCORE_LARGE = 2.5     # full size entry on extreme dislocation
EXIT_ZSCORE        = 0.3     # exit when price returns this close to mean

# Conviction sizing
MIN_CONVICTION     = 0.3     # minimum position size at entry threshold (30%)
# ─────────────────────────────────────────────────────────────────────────────


def rolling_mean_std(prices: list):
    n = len(prices)
    if n == 0:
        return 0.0, MIN_STD
    mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, MIN_STD)


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
        mid_history: list = saved.get("mid_history", [])

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({"mid_history": mid_history})

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── mid price ────────────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            mid = float(best_bid)
        else:
            mid = float(best_ask)

        # ── update history ───────────────────────────────────────────────────
        mid_history.append(mid)
        if len(mid_history) > LOOKBACK:
            mid_history.pop(0)

        pos      = self._position(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── warmup guard ─────────────────────────────────────────────────────
        if len(mid_history) < WARMUP:
            print(f"t={state.timestamp} warming up {len(mid_history)}/{WARMUP}")
            trader_data = json.dumps({"mid_history": mid_history})
            return {PRODUCT: orders}, 0, trader_data

        # ── zscore ───────────────────────────────────────────────────────────
        mean, std = rolling_mean_std(mid_history)
        zscore    = (mid - mean) / std

        # Signals
        buy_signal  = zscore < -ENTRY_ZSCORE        # price unusually low  → buy
        sell_signal = zscore >  ENTRY_ZSCORE        # price unusually high → sell
        buy_large   = zscore < -ENTRY_ZSCORE_LARGE  # extreme → full size
        sell_large  = zscore >  ENTRY_ZSCORE_LARGE  # extreme → full size
        exit_long   = zscore > -EXIT_ZSCORE         # long close enough to mean
        exit_short  = zscore <  EXIT_ZSCORE         # short close enough to mean

        # Conviction scales position size — deeper = bigger
        conviction = max(MIN_CONVICTION, min(1.0,
            (abs(zscore) - ENTRY_ZSCORE) / ENTRY_ZSCORE
        ))

        # ── exits — always before entries ────────────────────────────────────
        if pos > 0 and exit_long:
            if best_bid is not None:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap, pos)
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))
                    trader_data = json.dumps({"mid_history": mid_history})
                    return {PRODUCT: orders}, 0, trader_data

        if pos < 0 and exit_short:
            if best_ask is not None:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap, abs(pos))
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))
                    trader_data = json.dumps({"mid_history": mid_history})
                    return {PRODUCT: orders}, 0, trader_data

        # ── entries ───────────────────────────────────────────────────────────
        if buy_signal and best_ask is not None and buy_cap > 0:
            if buy_large:
                qty = min(-od.sell_orders[best_ask], buy_cap)
            else:
                target    = int(POS_LIMIT * conviction)
                shortfall = max(0, target - pos)
                qty       = min(-od.sell_orders[best_ask], buy_cap, shortfall)
            if qty > 0:
                orders.append(Order(PRODUCT, best_ask, qty))

        elif sell_signal and best_bid is not None and sell_cap > 0:
            if sell_large:
                qty = min(od.buy_orders[best_bid], sell_cap)
            else:
                target    = int(POS_LIMIT * conviction)
                shortfall = max(0, target + pos)
                qty       = min(od.buy_orders[best_bid], sell_cap, shortfall)
            if qty > 0:
                orders.append(Order(PRODUCT, best_bid, -qty))

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} mid={mid:.1f} "
            f"mean={mean:.1f} std={std:.2f} zscore={zscore:.3f} "
            f"conviction={conviction:.2f} orders={len(orders)}"
        )

        trader_data = json.dumps({"mid_history": mid_history})
        return {PRODUCT: orders}, 0, trader_data
