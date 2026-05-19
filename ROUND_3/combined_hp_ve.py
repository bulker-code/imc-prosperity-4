"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK only (v3)
=====================================================
Strategy: pure spread-capture market-maker with inventory skew.

Key findings from data analysis
---------------------------------
• Bots always quote bid1 = mid - 8, ask1 = mid + 8  (spread = 16, 93% of ticks).
• Every market trade hits exactly best_bid or best_ask — bots are pure takers.
• If we quote bid = bid1+1, ask = ask1-1 we jump to the FRONT of the queue.
  A bot wanting to sell at bid1 will prefer our bid1+1. Same on the ask side.
• This earns us 7 ticks of edge per side (vs 8 for the resting bots).
• 1010 market trades over 30k ticks → ~1 trade per 30 ticks on average.
  Over 10k ticks: ~337 expected fills.  337 × 7 ticks avg ≈ 2,359 raw edge.
• Adverse selection is negligible (mid barely moves directionally after fills).
• Bots are net buyers (+158 units over 3 days) → we accumulate a short bias.
  Manage via position-based quote skew and hard one-sided quoting above ±150.

Additional "take" layer
  If the best ask is genuinely below our EMA fair value by > 10, buy it.
  If the best bid is above EMA by > 10, sell it.  This captures large
  dislocations without waiting for a passive fill.

Position limits: HYDROGEL_PACK = 200
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json
import math


# ── tuneable parameters ─────────────────────────────────────────────────────
#HYDROGEL
POS_LIMIT      = 200
QUOTE_INSIDE   = 1      # how many ticks inside best bid/ask we quote
QUOTE_SIZE     = 14     # units per side (bots quote ~12, we match and exceed)
SKEW_TICKS     = 2      # extra ticks of skew per "100 units of position"
ONE_SIDE_LEVEL = 150    # if |pos| ≥ this, only quote the reducing side
TAKE_EDGE      = 8   # take aggressively if price > TAKE_EDGE away from EMA
EMA_ALPHA      = 0.05   # smoothing for fair-value EMA (slow, HP is noisy)
FAIR_VALUE_0   = 9997.0 # starting EMA (3-day historical mean)
MAX_TAKE_SHORT = -150
MAX_TAKE_LONG  = 150
MIN_STD        = 2
WARMUP         = 50
LOOKBACK       = 500
EMA_WARMUP_TICKS = 100  # after this many ticks, EMA is reliabl

#VELVET
FAIR_VALUE_0       = 5260.0

LOOKBACK           = 1000
WARMUP             = 50
MIN_STD            = 1.5
EMA_ALPHA          = 0.01

ENTRY_ZSCORE       = 1.1    # lower than hydrogel — tighter spread means less friction
ENTRY_ZSCORE_LARGE = 1.9    # full size on extreme dislocations
EXIT_ZSCORE        = 0.1
MIN_CONVICTION     = 0.5




# ────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev

def rolling_mean_std(prices: list):
    n = len(prices)
    if n == 0:
        return 0.0, MIN_STD
    mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, MIN_STD)
# ── parameters ───────────────────────────────────────────────────────────────
def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev

def rolling_mean_std(prices: list, mean):
    n = len(prices)
    if n == 0:
        return 0.0, MIN_STD
    #mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, MIN_STD)


class Trader:

    # ── helpers ─────────────────────────────────────────────────────────────

    def _position(self, state: TradingState) -> int:
        return state.position.get(product, 0)

    def _buy_capacity(self, state: TradingState) -> int:
        return POS_LIMIT - self._position(state)

    def _sell_capacity(self, state: TradingState) -> int:
        return POS_LIMIT + self._position(state)

    # ── main run ─────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        for product in state.order_depths:
            if product == "HYDROGEL_PACK":
        # ── restore persisted state ──────────────────────────────────────────
                try:
                    saved = json.loads(state.traderData) if state.traderData else {}
                except Exception:
                    saved = {}
                mid_history: list = saved.get("mid_history", [])
                ema_hp: float = saved.get("ema_hp", FAIR_VALUE_0)

                # ── read order book ──────────────────────────────────────────────────
                od: OrderDepth = state.order_depths.get(product)
                orders: List[Order] = []

                if od is None or (not od.buy_orders and not od.sell_orders):
                    return {product: orders}, 0, json.dumps({"ema_hp": ema_hp})

                best_bid = max(od.buy_orders)  if od.buy_orders  else None
                best_ask = min(od.sell_orders) if od.sell_orders else None
                

                # ── update EMA fair value ────────────────────────────────────────────
                if best_bid is not None and best_ask is not None:
                    mid = (best_bid + best_ask) / 2.0
                    ema_hp = _ema(ema_hp, mid, EMA_ALPHA)
                elif best_bid is not None:
                    ema_hp = _ema(ema_hp, float(best_bid), EMA_ALPHA)
                    mid = float(best_bid)
                elif best_ask is not None:
                    ema_hp = _ema(ema_hp, float(best_ask), EMA_ALPHA)
                    mid = float(best_ask)

                # ── update history ───────────────────────────────────────────────────
                mid_history.append(mid)
                if len(mid_history) > LOOKBACK:
                    mid_history.pop(0)
         
                pos     = self._position(state)
                buy_cap = POS_LIMIT - pos
                sell_cap = POS_LIMIT + pos
         
                # ── warmup guard ─────────────────────────────────────────────────────
                if len(mid_history) < WARMUP:
                    print(f"t={state.timestamp} warming up {len(mid_history)}/{WARMUP}")
                    return {product: orders}, 0, json.dumps({"mid_history": mid_history})
         
                # ── zscore ───────────────────────────────────────────────────────────
                mean, std = rolling_mean_std(mid_history)
                zscore    = (mid - mean) / std

                pos = self._position(state)
                
                # ── 1. AGGRESSIVE TAKING ─────────────────────────────────────────────
                # Take whenever the market offers a price far from fair value.
                # This fires rarely but captures large short-term mispricings.
                # Sell gate: don't ADD to short beyond limit
                ema_ready = state.timestamp > EMA_WARMUP_TICKS
                if ema_ready:
                        
                    if best_ask is not None and best_ask < ema_hp - TAKE_EDGE:
                        cap = self._buy_capacity(state)
                        vol = -od.sell_orders[best_ask]            # positive volume
                        qty = min(vol, cap)
                        if qty > 0:
                            orders.append(Order(product, best_ask, qty))

                    if best_bid is not None and best_bid > ema_hp + TAKE_EDGE:
                        cap = self._sell_capacity(state)
                        vol = od.buy_orders[best_bid]              # positive volume
                        qty = min(vol, cap)
                        if qty > 0:
                            orders.append(Order(product, best_bid, -qty))
                            
                elif product == "VELVETFRUIT_EXTRACT":                    
                    try:
                        saved = json.loads(state.traderData) if state.traderData else {}
                    except Exception:
                        saved = {}
                    mid_history: list = saved.get("mid_history", [])
                    ema_hp: float = saved.get("ema_hp", FAIR_VALUE_0)

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
                    if len(mid_history) > LOOKBACK:
                        mid_history.pop(0)

                    pos      = self._position(state)
                    buy_cap  = POS_LIMIT - pos
                    sell_cap = POS_LIMIT + pos

                    # ── warmup guard ─────────────────────────────────────────────────────
                    if len(mid_history) < WARMUP:
                        print(f"t={state.timestamp} warming up {len(mid_history)}/{WARMUP}")
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
