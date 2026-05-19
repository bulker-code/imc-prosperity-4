"""
IMC Prosperity 4 – Round 3 | VELVETFRUIT_EXTRACT
=================================================
Strategy: pure zscore mean reversion.

Key observations
-----------------
- Price oscillates ~60-80 ticks peak to trough across all 3 days
- No sustained directional trend — pure mean reversion
- Tight spread (~2-4 ticks) → low friction → can enter earlier than hydrogel
- One extreme dip event on day 2 to ~5195 — large opportunity if caught
- Stable mean around 5250-5260

Strategy
---------
- Rolling zscore over VE_LOOKBACK ticks
- Buy when zscore < -ENTRY_ZSCORE (price unusually low)
- Sell when zscore > +ENTRY_ZSCORE (price unusually high)
- Exit when price returns to mean (|zscore| < EXIT_ZSCORE)
- Full size on extreme dislocations (|zscore| > ENTRY_ZSCORE_LARGE)
- Conviction sizing between thresholds
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json
import math

# ── parameters ───────────────────────────────────────────────────────────────
product            = "VELVETFRUIT_EXTRACT"
POS_LIMIT          = 200
VE_FAIR_VALUE_0       = 5260.0

VE_LOOKBACK           = 1000
VE_WARMUP             = 50
VE_MIN_STD       = 1.5
VE_EMA_ALPHA        = 0.01

ENTRY_ZSCORE       = 1.1    # lower than hydrogel — tighter spread means less friction
ENTRY_ZSCORE_LARGE = 1.9    # full size on extreme dislocations
EXIT_ZSCORE        = 0.1
MIN_CONVICTION     = 0.5

# ─────────────────────────────────────────────────────────────────────────────
def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev

def rolling_mean_std(prices: list, mean):
    n = len(prices)
    if n == 0:
        return 0.0, VE_MIN_STD
    #mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, VE_MIN_STD)


class Trader:

    def bid(self):
        return 15

    def _position(self, state: TradingState) -> int:
        return state.position.get(product, 0)

    def run(self, state: TradingState):
        # ── restore state ────────────────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}
        mid_history: list = saved.get("mid_history", [])
        ema_hp: float = saved.get("ema_hp", VE_FAIR_VALUE_0)

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(product)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {product: orders}, 0, json.dumps({"mid_history": mid_history})

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── mid price ────────────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
            ema_hp = _ema(ema_hp, mid, EMA_ALPHA)
        elif best_bid is not None:
            mid = float(best_bid)
            ema_hp = _ema(ema_hp, float(best_bid), EMA_ALPHA)
        else:
            mid = float(best_ask)
            ema_hp = _ema(ema_hp, float(best_ask), EMA_ALPHA)

        mid_history.append(mid)
        if len(mid_history) > VE_LOOKBACK:
            mid_history.pop(0)

        pos      = self._position(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── VE_WARMUP guard ─────────────────────────────────────────────────────
        if len(mid_history) < VE_WARMUP:
            print(f"t={state.timestamp} warming up {len(mid_history)}/{VE_WARMUP}")
            return {product: orders}, 0, json.dumps({"mid_history": mid_history})

        # ── zscore ───────────────────────────────────────────────────────────
        mean, std = rolling_mean_std(mid_history, ema_hp)
        zscore    = (mid - mean) / std

        buy_signal  = zscore < -ENTRY_ZSCORE
        sell_signal = zscore >  ENTRY_ZSCORE
        buy_large   = zscore < -ENTRY_ZSCORE_LARGE
        sell_large  = zscore >  ENTRY_ZSCORE_LARGE
        exit_long   = zscore > -EXIT_ZSCORE
        exit_short  = zscore <  EXIT_ZSCORE

        conviction = max(MIN_CONVICTION, min(1.0,
            (abs(zscore) - ENTRY_ZSCORE) / ENTRY_ZSCORE
        ))

        # ── exits ────────────────────────────────────────────────────────────
        if pos > 0 and exit_long:
            if best_bid is not None:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap, pos)
                if qty > 0:
                    orders.append(Order(product, best_bid, -qty))
                    return {product: orders}, 0, json.dumps({"mid_history": mid_history})

        if pos < 0 and exit_short:
            if best_ask is not None:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap, abs(pos))
                if qty > 0:
                    orders.append(Order(product, best_ask, qty))
                    return {product: orders}, 0, json.dumps({"mid_history": mid_history})

        # ── entries ───────────────────────────────────────────────────────────
        if buy_signal and best_ask is not None and buy_cap > 0:
            if buy_large:
                qty = min(-od.sell_orders[best_ask], buy_cap)
            else:
                target    = int(POS_LIMIT * conviction)
                shortfall = max(0, target - pos)
                qty       = min(-od.sell_orders[best_ask], buy_cap, shortfall)
            if qty > 0:
                orders.append(Order(product, best_ask, qty))

        elif sell_signal and best_bid is not None and sell_cap > 0:
            if sell_large:
                qty = min(od.buy_orders[best_bid], sell_cap)
            else:
                target    = int(POS_LIMIT * conviction)
                shortfall = max(0, target + pos)
                qty       = min(od.buy_orders[best_bid], sell_cap, shortfall)
            if qty > 0:
                orders.append(Order(product, best_bid, -qty))

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} mid={mid:.1f} "
            f"mean={mean:.1f} std={std:.2f} zscore={zscore:.3f} "
            f"conviction={conviction:.2f} orders={len(orders)}"
        )

        return {product: orders}, 0, json.dumps({"mid_history": mid_history})
