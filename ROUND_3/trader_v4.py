"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK + VELVETFRUIT_EXTRACT (v4)
======================================================================
Strategy:
  HYDROGEL_PACK     — aggressive taker on EMA dislocations.
  VELVETFRUIT_EXTRACT — mean-reversion via z-score on rolling EMA.

Fixes vs v3:
  1. traderData loaded once before the loop; ema_hp, ema_ve, mid_history
     all persisted in the single final return.
  2. All mid-loop `return` calls replaced with `continue`; orders
     accumulated into all_orders dict; single return at end.
  3. rolling_std n==0 branch returned a tuple — fixed to return float.
  4. _buy_capacity / _sell_capacity take explicit product + limit args.
  5. Redundant second `od` assignment inside VELVETFRUIT removed.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json
import math


# ── tuneable parameters ──────────────────────────────────────────────────────

POS_LIMIT             = 200

HP_TAKE_EDGE          = 8
HP_EMA_ALPHA          = 0.05
HP_FAIR_VALUE_0       = 9997.0
HP_WARMUP             = 50
HP_LOOKBACK           = 500
HP_EMA_WARMUP_TICKS   = 100

VE_FAIR_VALUE_0       = 5260.0
VE_LOOKBACK           = 1000
VE_WARMUP             = 50
VE_MIN_STD            = 1.5
VE_EMA_ALPHA          = 0.01

ENTRY_ZSCORE          = 1.1
ENTRY_ZSCORE_LARGE    = 1.9
EXIT_ZSCORE           = 0.1
MIN_CONVICTION        = 0.5

# ─────────────────────────────────────────────────────────────────────────────


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


def rolling_std(prices: list, ema: float) -> float:
    n = len(prices)
    if n == 0:
        return VE_MIN_STD   # was incorrectly returning a tuple (0.0, VE_MIN_STD)
    variance = sum((p - ema) ** 2 for p in prices) / n
    return max(math.sqrt(variance), VE_MIN_STD)


class Trader:
    def __init__(self):
        self.price_history: Dict[str, list] = {}

    # ── helpers ──────────────────────────────────────────────────────────────

    def _position(self, state: TradingState, product: str) -> int:
        return state.position.get(product, 0)

    def _buy_capacity(self, state: TradingState, product: str,
                      limit: int = POS_LIMIT) -> int:
        return limit - self._position(state, product)

    def _sell_capacity(self, state: TradingState, product: str,
                       limit: int = POS_LIMIT) -> int:
        return limit + self._position(state, product)

    # ── main run ─────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        # Load persisted state once, before the product loop
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        ema_hp: float     = saved.get("ema_hp", HP_FAIR_VALUE_0)
        ema_ve: float     = saved.get("ema_ve", VE_FAIR_VALUE_0)
        mid_history: list = saved.get("mid_history", [])

        all_orders: Dict[str, List[Order]] = {}

        for product in state.order_depths:
            if product not in self.price_history:
                self.price_history[product] = []

            od: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if not od.buy_orders and not od.sell_orders:
                all_orders[product] = orders
                continue

            best_bid = max(od.buy_orders)  if od.buy_orders  else None
            best_ask = min(od.sell_orders) if od.sell_orders else None

            # ── HYDROGEL_PACK ─────────────────────────────────────────────────
            if product == "HYDROGEL_PACK":

                if best_bid is not None and best_ask is not None:
                    mid = (best_bid + best_ask) / 2.0
                elif best_bid is not None:
                    mid = float(best_bid)
                else:
                    mid = float(best_ask)

                ema_hp = _ema(ema_hp, mid, HP_EMA_ALPHA)

                self.price_history[product].append(mid)
                if len(self.price_history[product]) > HP_LOOKBACK:
                    self.price_history[product].pop(0)

                if len(self.price_history[product]) < HP_WARMUP:
                    all_orders[product] = orders
                    continue

                buy_cap  = self._buy_capacity(state, product)
                sell_cap = self._sell_capacity(state, product)

                if state.timestamp >= HP_EMA_WARMUP_TICKS:
                    if best_ask is not None and best_ask < ema_hp - HP_TAKE_EDGE:
                        vol = -od.sell_orders[best_ask]
                        qty = min(vol, buy_cap)
                        if qty > 0:
                            orders.append(Order(product, best_ask, qty))

                    if best_bid is not None and best_bid > ema_hp + HP_TAKE_EDGE:
                        vol = od.buy_orders[best_bid]
                        qty = min(vol, sell_cap)
                        if qty > 0:
                            orders.append(Order(product, best_bid, -qty))

            # ── VELVETFRUIT_EXTRACT ───────────────────────────────────────────
            elif product == "VELVETFRUIT_EXTRACT":

                if best_bid is not None and best_ask is not None:
                    mid = (best_bid + best_ask) / 2.0
                elif best_bid is not None:
                    mid = float(best_bid)
                else:
                    mid = float(best_ask)

                ema_ve = _ema(ema_ve, mid, VE_EMA_ALPHA)

                mid_history.append(mid)
                if len(mid_history) > VE_LOOKBACK:
                    mid_history.pop(0)

                if len(mid_history) < VE_WARMUP:
                    all_orders[product] = orders
                    continue

                pos      = self._position(state, product)
                buy_cap  = self._buy_capacity(state, product)
                sell_cap = self._sell_capacity(state, product)

                std    = rolling_std(mid_history, ema_ve)
                zscore = (mid - ema_ve) / std

                buy_signal  = zscore < -ENTRY_ZSCORE
                sell_signal = zscore >  ENTRY_ZSCORE
                buy_large   = zscore < -ENTRY_ZSCORE_LARGE
                sell_large  = zscore >  ENTRY_ZSCORE_LARGE
                exit_long   = zscore > -EXIT_ZSCORE
                exit_short  = zscore <  EXIT_ZSCORE

                conviction = max(MIN_CONVICTION, min(1.0,
                    (abs(zscore) - ENTRY_ZSCORE) / ENTRY_ZSCORE
                ))

                # Exits
                if pos > 0 and exit_long and best_bid is not None:
                    vol = od.buy_orders[best_bid]
                    qty = min(vol, sell_cap, pos)
                    if qty > 0:
                        orders.append(Order(product, best_bid, -qty))

                elif pos < 0 and exit_short and best_ask is not None:
                    vol = -od.sell_orders[best_ask]
                    qty = min(vol, buy_cap, abs(pos))
                    if qty > 0:
                        orders.append(Order(product, best_ask, qty))

                # Entries (only if no exit order was placed)
                elif buy_signal and best_ask is not None and buy_cap > 0:
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

            all_orders[product] = orders

        # Persist all state in one place
        trader_data = json.dumps({
            "ema_hp":      ema_hp,
            "ema_ve":      ema_ve,
            "mid_history": mid_history,
        })

        return all_orders, 0, trader_data
