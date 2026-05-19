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


# ── tuneable parameters ─────────────────────────────────────────────────────
PRODUCT        = "HYDROGEL_PACK"
POS_LIMIT      = 200
QUOTE_INSIDE   = 1      # how many ticks inside best bid/ask we quote
QUOTE_SIZE     = 14     # units per side (bots quote ~12, we match and exceed)
SKEW_TICKS     = 2      # extra ticks of skew per "100 units of position"
ONE_SIDE_LEVEL = 150    # if |pos| ≥ this, only quote the reducing side
TAKE_EDGE      = 8    # take aggressively if price > TAKE_EDGE away from EMA
EMA_ALPHA      = 0.03   # smoothing for fair-value EMA (slow, HP is noisy)
FAIR_VALUE_0   = 9991.0 # starting EMA (3-day historical mean)
MAX_TAKE_SHORT = -100
MAX_TAKE_LONG  = 100
# ────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev

def trend_slope(prices: list) -> float:
    """
    Returns the slope of the best-fit line through prices.
    Positive = trending up, Negative = trending down.
    Near zero = no clear trend.
    """
    n = len(prices)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(prices) / n
    numerator   = sum((i - x_mean) * (prices[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    return numerator / denominator if denominator != 0 else 0.0


class Trader:
    
    # ── helpers ─────────────────────────────────────────────────────────────

    def _position(self, state: TradingState) -> int:
        return state.position.get(PRODUCT, 0)

    def _buy_capacity(self, state: TradingState) -> int:
        return POS_LIMIT - self._position(state)

    def _sell_capacity(self, state: TradingState) -> int:
        return POS_LIMIT + self._position(state)

    # ── main run ─────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        # ── restore persisted state ──────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}
        ema_hp: float = saved.get("ema_hp", FAIR_VALUE_0)

        # ── read order book ──────────────────────────────────────────────────
        od: OrderDepth = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({"ema_hp": ema_hp})

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── update EMA fair value ────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
            ema_hp = _ema(ema_hp, mid, EMA_ALPHA)
        elif best_bid is not None:
            ema_hp = _ema(ema_hp, float(best_bid), EMA_ALPHA)
        elif best_ask is not None:
            ema_hp = _ema(ema_hp, float(best_ask), EMA_ALPHA)
        # In persisted state
        mid_history = saved.get("mid_history", [])

        # After calculating mid each tick
        mid_history.append(mid)
        TREND_WINDOW    = 10     # how many ticks to measure slope over
        TREND_THRESHOLD = 0.3    # minimum slope to consider it a real trend (ticks/tick)

        slope = trend_slope(mid_history) if len(mid_history) >= TREND_WINDOW else 0.0

        trending_up   = slope > 0   #TREND_THRESHOLD
        trending_down = slope < 0   #-TREND_THRESHOLD
        neutral       = not trending_up and not trending_down        
        
        pos = self._position(state)
        
        # ── 1. AGGRESSIVE TAKING ─────────────────────────────────────────────
        # Take whenever the market offers a price far from fair value.
        # This fires rarely but captures large short-term mispricings.
        # Sell gate: don't ADD to short beyond limit
        
        if best_ask is not None and best_ask < ema_hp - TAKE_EDGE and pos > MAX_TAKE_SHORT and trending_up:
            cap = self._buy_capacity(state)
            vol = -od.sell_orders[best_ask]            # positive volume
            qty = min(vol, cap)
            if qty > 0:
                orders.append(Order(PRODUCT, best_ask, qty))

        if best_bid is not None and best_bid > ema_hp + TAKE_EDGE and pos < MAX_TAKE_LONG and trending_down:
            cap = self._sell_capacity(state)
            vol = od.buy_orders[best_bid]              # positive volume
            qty = min(vol, cap)
            if qty > 0:
                orders.append(Order(PRODUCT, best_bid, -qty))
        

        # ── 2. PASSIVE MARKET-MAKING QUOTES ──────────────────────────────────
        # We quote 1 tick inside the current best bid/ask.
        # Position skew: if long, slide our ask down (sell cheaper to unwind)
        # and slide our bid down (buy more expensive to deter buying more).
        # This naturally rebalances the book toward zero without sacrificing edge.
        #
        # Skew formula:  skew_ticks = SKEW_TICKS * round(pos / 100)
        #   pos=+100 → skew=2 → bid_price −2, ask_price −2  (bias toward selling)
        #   pos=−100 → skew=−2 → bid_price +2, ask_price +2 (bias toward buying)
        """
        if best_bid is not None and best_ask is not None:
            skew = SKEW_TICKS * round(pos / 100)

            our_bid = best_bid + QUOTE_INSIDE - skew
            our_ask = best_ask - QUOTE_INSIDE - skew

            # Guard: never cross the book
            if our_bid >= our_ask:
                our_bid = int(ema_hp) - 1
                our_ask = int(ema_hp) + 1

            # One-sided quoting if inventory is near the limit
            post_bid = pos < ONE_SIDE_LEVEL    # only quote bid if not too long
            post_ask = pos > -ONE_SIDE_LEVEL   # only quote ask if not too short

            if post_bid:
                qty_bid = min(QUOTE_SIZE, self._buy_capacity(state))
                if qty_bid > 0:
                    orders.append(Order(PRODUCT, our_bid, qty_bid))

            if post_ask:
                qty_ask = min(QUOTE_SIZE, self._sell_capacity(state))
                if qty_ask > 0:
                    orders.append(Order(PRODUCT, our_ask, -qty_ask))
        """

        # ── debug ─────────────────────────────────────────────────────────────
        print(
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"bid={best_bid} ask={best_ask} orders={len(orders)}"
        )

        # ── persist state ─────────────────────────────────────────────────────
        trader_data = json.dumps({"ema_hp": ema_hp})
        return {PRODUCT: orders}, 0, trader_data
