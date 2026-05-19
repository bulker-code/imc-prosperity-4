"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK (final)
===================================================
Strategy: EMA-based aggressive taking with zscore-gated entries.

What we learned through iteration
-----------------------------------
- Market making alone: ~5k but builds large short bias
- Aggressive taking alone with slow EMA: ~10k but bleeds on price rises
- The slow EMA lag IS the edge — it makes best_bid look perpetually above
  fair value during bot buying pressure, so we collect that spread
- Position pinned at -200 is fine as long as price mean-reverts
- The drawdowns happen when price rises sustained against the short
- Fix: use zscore to gate entries — only add to short when price is
  genuinely high relative to recent history, not just EMA-lagged high

Combined strategy
------------------
1. EMA take layer — same as original v3, captures mispricing vs slow EMA
2. Zscore gate — only sell when zscore > threshold (price genuinely elevated)
   only buy when zscore < -threshold (price genuinely depressed)
3. Position-aware: buying always allowed to reduce short, selling gated
4. Market making re-enabled with inventory skew to earn spread passively
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List
import json
import math

# ── parameters ───────────────────────────────────────────────────────────────
PRODUCT        = "HYDROGEL_PACK"
POS_LIMIT      = 200

# EMA
EMA_ALPHA      = 0.05
FAIR_VALUE_0   = 9997.0

# Aggressive taking
TAKE_EDGE      = 8       # ticks from EMA to trigger take

# Zscore gate on selling — only sell when price is genuinely elevated
LOOKBACK       = 100     # rolling window for zscore
WARMUP         = 50      # ticks before zscore is trusted
MIN_STD        = 2.0
SELL_ZSCORE    = 0.5     # only sell aggressively when zscore > this
BUY_ZSCORE     = -0.5    # only buy aggressively when zscore < this

# Position limits on aggressive taking
MAX_SHORT      = -150    # don't build short beyond this via taking
MAX_LONG       = 150     # don't build long beyond this via taking

# Market making
QUOTE_INSIDE   = 1
QUOTE_SIZE     = 14
SKEW_TICKS     = 2       # ticks of skew per 100 units of position
ONE_SIDE_LEVEL = 150     # only quote reducing side beyond this
# ─────────────────────────────────────────────────────────────────────────────


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
        ema_hp: float     = saved.get("ema_hp", FAIR_VALUE_0)
        mid_history: list = saved.get("mid_history", [])

        # ── order book ───────────────────────────────────────────────────────
        od = state.order_depths.get(PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return {PRODUCT: orders}, 0, json.dumps({
                "ema_hp": ema_hp, "mid_history": mid_history
            })

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── mid + EMA update ─────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            mid = float(best_bid)
        else:
            mid = float(best_ask)

        ema_hp = _ema(ema_hp, mid, EMA_ALPHA)

        mid_history.append(mid)
        if len(mid_history) > LOOKBACK:
            mid_history.pop(0)

        pos      = self._position(state)
        buy_cap  = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # ── zscore ───────────────────────────────────────────────────────────
        warmed_up = len(mid_history) >= WARMUP
        if warmed_up:
            mean, std = rolling_mean_std(mid_history)
            zscore    = (mid - mean) / std
        else:
            zscore = 0.0

        # ── 1. AGGRESSIVE TAKING ─────────────────────────────────────────────
        # Buy: always allowed when price is below EMA — reduces short or builds long
        # Sell: only when zscore confirms price is genuinely elevated AND
        #       we haven't hit the short cap
        if best_ask is not None and best_ask < ema_hp - TAKE_EDGE:
            qty = min(-od.sell_orders[best_ask], buy_cap)
            if qty > 0:
                orders.append(Order(PRODUCT, best_ask, qty))

        if (best_bid is not None
                and best_bid > ema_hp + TAKE_EDGE
                and pos < MAX_LONG
                and (not warmed_up or zscore > SELL_ZSCORE)):
            qty = min(od.buy_orders[best_bid], sell_cap)
            if qty > 0:
                orders.append(Order(PRODUCT, best_bid, -qty))

        # ── 2. MARKET MAKING ─────────────────────────────────────────────────
        # Passive quotes 1 tick inside best bid/ask with inventory skew.
        # Skew shifts both quotes down when long (bias to sell),
        # and up when short (bias to buy) — naturally rebalances position.
        if best_bid is not None and best_ask is not None:
            skew = SKEW_TICKS * round(pos / 100)

            our_bid = best_bid + QUOTE_INSIDE - skew
            our_ask = best_ask - QUOTE_INSIDE - skew

            if our_bid >= our_ask:
                our_bid = round(ema_hp) - 1
                our_ask = round(ema_hp) + 1

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
            f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"zscore={zscore:.2f} bid={best_bid} ask={best_ask} "
            f"orders={len(orders)}"
        )

        # ── persist ───────────────────────────────────────────────────────────
        trader_data = json.dumps({"ema_hp": ema_hp, "mid_history": mid_history})
        return {PRODUCT: orders}, 0, trader_data
