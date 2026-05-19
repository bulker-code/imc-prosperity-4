"""
IMC Prosperity 4 – Round 3 | VELVETFRUIT_EXTRACT
=================================================
Strategy: Bollinger Band mean reversion with aggressive take on extremes.

Key observations from data
----------------------------
- Price oscillates ~30-40 ticks peak to trough, completing in 200-400 ticks.
- No directional drift — stable mean around 5262.
- Bot trades cluster at local highs and lows — same signal we use.
- Two large isolated dips suggest occasional extreme dislocations.
- Wide spread (~8 ticks) means passive quoting is less useful — take aggressively.

Strategy
---------
- Enter mean reversion at zscore > ENTRY_ZSCORE (1.2) with conviction sizing.
- Enter aggressively at full size on extreme dislocations (zscore > ENTRY_ZSCORE_LARGE).
- Exit when price returns near the mean (zscore < EXIT_ZSCORE).
- Flip position immediately if zscore crosses to opposite extreme.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json
import math

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT              = "VELVETFRUIT_EXTRACT"
POS_LIMIT            = 200  # update if different for this product

LOOKBACK             = 60    # shorter than HYDROGEL — faster swings
WARMUP               = 30    # ticks before trading starts
MIN_STD              = 1.5   # floor on std

ENTRY_ZSCORE         = 1.2   # normal entry threshold
ENTRY_ZSCORE_LARGE   = 2.5   # aggressive full-size entry on extreme dislocation
EXIT_ZSCORE          = 0.2   # exit when zscore returns near zero
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
            return {PRODUCT: orders}, 0, json.dumps({"mid_history": mid_history})

        # ── zscore signal ────────────────────────────────────────────────────
        mean, std = rolling_mean_std(mid_history)
        zscore    = (mid - mean) / std

        signal_long        = zscore < -ENTRY_ZSCORE
        signal_short       = zscore >  ENTRY_ZSCORE
        signal_long_large  = zscore < -ENTRY_ZSCORE_LARGE   # extreme dislocation
        signal_short_large = zscore >  ENTRY_ZSCORE_LARGE
        near_mean          = abs(zscore) < EXIT_ZSCORE

        # Conviction sizing — scales from 30% at threshold to 100% at 2x threshold
        raw_conviction = (abs(zscore) - ENTRY_ZSCORE) / ENTRY_ZSCORE
        conviction     = max(0.3, min(1.0, raw_conviction))

        # ── exits ─────────────────────────────────────────────────────────────

        if near_mean and pos != 0:
            # Price back at mean — exit entirely
            if pos > 0 and best_bid is not None:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap, abs(pos))
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))

            elif pos < 0 and best_ask is not None:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap, abs(pos))
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))

        elif signal_short and pos > 0:
            # Was long, price now high — flip: exit long first
            if best_bid is not None:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap, abs(pos))
                if qty > 0:
                    orders.append(Order(PRODUCT, best_bid, -qty))

        elif signal_long and pos < 0:
            # Was short, price now low — flip: exit short first
            if best_ask is not None:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap, abs(pos))
                if qty > 0:
                    orders.append(Order(PRODUCT, best_ask, qty))

        # ── entries ───────────────────────────────────────────────────────────

        if signal_long and best_ask is not None:
            if signal_long_large:
                # Extreme dislocation — take full size aggressively
                qty = min(-od.sell_orders[best_ask], buy_cap)
            else:
                # Normal entry — size by conviction
                target = int(POS_LIMIT * conviction)
                shortfall = target - pos
                qty = min(-od.sell_orders[best_ask], buy_cap, shortfall)
            if qty > 0:
                orders.append(Order(PRODUCT, best_ask, qty))

        elif signal_short and best_bid is not None:
            if signal_short_large:
                # Extreme dislocation — take full size aggressively
                qty = min(od.buy_orders[best_bid], sell_cap)
            else:
                # Normal entry — size by conviction
                target = int(POS_LIMIT * conviction)
                shortfall = target - pos
                qty = min(od.buy_orders[best_bid], sell_cap, shortfall)
            if qty > 0:
                orders.append(Order(PRODUCT, best_bid, -qty))

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} mid={mid:.1f} "
            f"mean={mean:.1f} std={std:.2f} zscore={zscore:.3f} "
            f"conviction={conviction:.2f} orders={len(orders)}"
        )

        # ── persist ───────────────────────────────────────────────────────────
        trader_data = json.dumps({"mid_history": mid_history})
        return {PRODUCT: orders}, 0, trader_data
