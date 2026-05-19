"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK + VELVETFRUIT_EXTRACT (combined)
============================================================================

HYDROGEL_PACK strategy: pure spread-capture market-maker with inventory skew.
VELVETFRUIT_EXTRACT strategy: pure zscore mean reversion.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json
import math


# ── HYDROGEL_PACK parameters ─────────────────────────────────────────────────
HP_PRODUCT        = "HYDROGEL_PACK"
HP_POS_LIMIT      = 200
HP_QUOTE_INSIDE   = 1
HP_QUOTE_SIZE     = 14
HP_SKEW_TICKS     = 2
HP_ONE_SIDE_LEVEL = 150
HP_TAKE_EDGE      = 8
HP_EMA_ALPHA      = 0.05
HP_FAIR_VALUE_0   = 9997.0
HP_MAX_TAKE_SHORT = -150
HP_MAX_TAKE_LONG  = 150
HP_MIN_STD        = 2
HP_WARMUP         = 50
HP_LOOKBACK       = 500
HP_EMA_WARMUP_TICKS = 100

# ── VELVETFRUIT_EXTRACT parameters ───────────────────────────────────────────
VF_PRODUCT            = "VELVETFRUIT_EXTRACT"
VF_POS_LIMIT          = 200
VF_FAIR_VALUE_0       = 5260.0
VF_LOOKBACK           = 1000
VF_WARMUP             = 50
VF_MIN_STD            = 1.5
VF_EMA_ALPHA          = 0.01
VF_ENTRY_ZSCORE       = 1.1
VF_ENTRY_ZSCORE_LARGE = 1.9
VF_EXIT_ZSCORE        = 0.1
VF_MIN_CONVICTION     = 0.5


# ── shared utilities ─────────────────────────────────────────────────────────

def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev

def hp_rolling_mean_std(prices: list):
    n = len(prices)
    if n == 0:
        return 0.0, HP_MIN_STD
    mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, HP_MIN_STD)

def vf_rolling_mean_std(prices: list, mean):
    n = len(prices)
    if n == 0:
        return 0.0, VF_MIN_STD
    #mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, VF_MIN_STD)


# ─────────────────────────────────────────────────────────────────────────────

class Trader:

    def bid(self):
        return 15

    # ── HYDROGEL_PACK helpers ────────────────────────────────────────────────

    def _hp_position(self, state: TradingState) -> int:
        return state.position.get(HP_PRODUCT, 0)

    def _hp_buy_capacity(self, state: TradingState) -> int:
        return HP_POS_LIMIT - self._hp_position(state)

    def _hp_sell_capacity(self, state: TradingState) -> int:
        return HP_POS_LIMIT + self._hp_position(state)

    # ── VELVETFRUIT_EXTRACT helpers ──────────────────────────────────────────

    def _vf_position(self, state: TradingState) -> int:
        return state.position.get(VF_PRODUCT, 0)

    # ── main run ─────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        # ── restore persisted state ──────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        result: Dict[str, List[Order]] = {}

        # ════════════════════════════════════════════════════════════════════
        # HYDROGEL_PACK
        # ════════════════════════════════════════════════════════════════════

        hp_mid_history: list = saved.get("hp_mid_history", [])
        ema_hp: float        = saved.get("hp_ema_hp", HP_FAIR_VALUE_0)

        od: OrderDepth = state.order_depths.get(HP_PRODUCT)
        hp_orders: List[Order] = []

        if od is not None and (od.buy_orders or od.sell_orders):

            best_bid = max(od.buy_orders)  if od.buy_orders  else None
            best_ask = min(od.sell_orders) if od.sell_orders else None

            # ── update EMA fair value ────────────────────────────────────────
            if best_bid is not None and best_ask is not None:
                mid = (best_bid + best_ask) / 2.0
                ema_hp = _ema(ema_hp, mid, HP_EMA_ALPHA)
            elif best_bid is not None:
                ema_hp = _ema(ema_hp, float(best_bid), HP_EMA_ALPHA)
                mid = float(best_bid)
            elif best_ask is not None:
                ema_hp = _ema(ema_hp, float(best_ask), HP_EMA_ALPHA)
                mid = float(best_ask)

            # ── update history ───────────────────────────────────────────────
            hp_mid_history.append(mid)
            if len(hp_mid_history) > HP_LOOKBACK:
                hp_mid_history.pop(0)

            pos      = self._hp_position(state)
            buy_cap  = HP_POS_LIMIT - pos
            sell_cap = HP_POS_LIMIT + pos

            # ── warmup guard ─────────────────────────────────────────────────
            if len(hp_mid_history) < HP_WARMUP:
                print(f"t={state.timestamp} warming up {len(hp_mid_history)}/{HP_WARMUP}")
            else:
                # ── zscore ───────────────────────────────────────────────────
                mean, std = hp_rolling_mean_std(hp_mid_history)
                zscore    = (mid - mean) / std

                pos = self._hp_position(state)

                # ── 1. AGGRESSIVE TAKING ─────────────────────────────────────
                ema_ready = state.timestamp > HP_EMA_WARMUP_TICKS
                if ema_ready:

                    if best_ask is not None and best_ask < ema_hp - HP_TAKE_EDGE:
                        cap = self._hp_buy_capacity(state)
                        vol = -od.sell_orders[best_ask]
                        qty = min(vol, cap)
                        if qty > 0:
                            hp_orders.append(Order(HP_PRODUCT, best_ask, qty))

                    if best_bid is not None and best_bid > ema_hp + HP_TAKE_EDGE:
                        cap = self._hp_sell_capacity(state)
                        vol = od.buy_orders[best_bid]
                        qty = min(vol, cap)
                        if qty > 0:
                            hp_orders.append(Order(HP_PRODUCT, best_bid, -qty))

                # ── 2. PASSIVE MARKET-MAKING QUOTES (disabled) ───────────────
                """
                if best_bid is not None and best_ask is not None:
                    skew = HP_SKEW_TICKS * round(pos / 100)

                    our_bid = best_bid + HP_QUOTE_INSIDE - skew
                    our_ask = best_ask - HP_QUOTE_INSIDE - skew

                    # Guard: never cross the book
                    if our_bid >= our_ask:
                        our_bid = int(ema_hp) - 1
                        our_ask = int(ema_hp) + 1

                    # One-sided quoting if inventory is near the limit
                    post_bid = pos < HP_ONE_SIDE_LEVEL
                    post_ask = pos > -HP_ONE_SIDE_LEVEL

                    if post_bid:
                        qty_bid = min(HP_QUOTE_SIZE, self._hp_buy_capacity(state))
                        if qty_bid > 0:
                            hp_orders.append(Order(HP_PRODUCT, our_bid, qty_bid))

                    if post_ask:
                        qty_ask = min(HP_QUOTE_SIZE, self._hp_sell_capacity(state))
                        if qty_ask > 0:
                            hp_orders.append(Order(HP_PRODUCT, our_ask, -qty_ask))
                """

                # ── debug ────────────────────────────────────────────────────
                print(
                    f"t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
                    f"bid={best_bid} ask={best_ask} orders={len(hp_orders)}"
                )

        result[HP_PRODUCT] = hp_orders

        # ════════════════════════════════════════════════════════════════════
        # VELVETFRUIT_EXTRACT
        # ════════════════════════════════════════════════════════════════════

        vf_mid_history: list = saved.get("vf_mid_history", [])
        ema_vf: float        = saved.get("vf_ema_hp", VF_FAIR_VALUE_0)

        od = state.order_depths.get(VF_PRODUCT)
        vf_orders: List[Order] = []

        if od is not None and (od.buy_orders or od.sell_orders):

            best_bid = max(od.buy_orders)  if od.buy_orders  else None
            best_ask = min(od.sell_orders) if od.sell_orders else None

            # ── mid price ────────────────────────────────────────────────────
            if best_bid is not None and best_ask is not None:
                mid = (best_bid + best_ask) / 2.0
                ema_vf = _ema(ema_vf, mid, VF_EMA_ALPHA)
            elif best_bid is not None:
                mid = float(best_bid)
                ema_vf = _ema(ema_vf, float(best_bid), VF_EMA_ALPHA)
            else:
                mid = float(best_ask)
                ema_vf = _ema(ema_vf, float(best_ask), VF_EMA_ALPHA)

            vf_mid_history.append(mid)
            if len(vf_mid_history) > VF_LOOKBACK:
                vf_mid_history.pop(0)

            pos      = self._vf_position(state)
            buy_cap  = VF_POS_LIMIT - pos
            sell_cap = VF_POS_LIMIT + pos

            # ── warmup guard ─────────────────────────────────────────────────
            if len(vf_mid_history) < VF_WARMUP:
                print(f"t={state.timestamp} warming up {len(vf_mid_history)}/{VF_WARMUP}")
            else:
                # ── zscore ───────────────────────────────────────────────────
                mean, std = vf_rolling_mean_std(vf_mid_history, ema_vf)
                zscore    = (mid - mean) / std

                buy_signal  = zscore < -VF_ENTRY_ZSCORE
                sell_signal = zscore >  VF_ENTRY_ZSCORE
                buy_large   = zscore < -VF_ENTRY_ZSCORE_LARGE
                sell_large  = zscore >  VF_ENTRY_ZSCORE_LARGE
                exit_long   = zscore > -VF_EXIT_ZSCORE
                exit_short  = zscore <  VF_EXIT_ZSCORE

                conviction = max(VF_MIN_CONVICTION, min(1.0,
                    (abs(zscore) - VF_ENTRY_ZSCORE) / VF_ENTRY_ZSCORE
                ))

                # ── exits ────────────────────────────────────────────────────
                if pos > 0 and exit_long:
                    if best_bid is not None:
                        vol = od.buy_orders[best_bid]
                        qty = min(vol, sell_cap, pos)
                        if qty > 0:
                            vf_orders.append(Order(VF_PRODUCT, best_bid, -qty))
                            result[VF_PRODUCT] = vf_orders
                            return result, 0, json.dumps({
                                "hp_mid_history": hp_mid_history, "hp_ema_hp": ema_hp,
                                "vf_mid_history": vf_mid_history, "vf_ema_hp": ema_vf,
                            })

                if pos < 0 and exit_short:
                    if best_ask is not None:
                        vol = -od.sell_orders[best_ask]
                        qty = min(vol, buy_cap, abs(pos))
                        if qty > 0:
                            vf_orders.append(Order(VF_PRODUCT, best_ask, qty))
                            result[VF_PRODUCT] = vf_orders
                            return result, 0, json.dumps({
                                "hp_mid_history": hp_mid_history, "hp_ema_hp": ema_hp,
                                "vf_mid_history": vf_mid_history, "vf_ema_hp": ema_vf,
                            })

                # ── entries ──────────────────────────────────────────────────
                if buy_signal and best_ask is not None and buy_cap > 0:
                    if buy_large:
                        qty = min(-od.sell_orders[best_ask], buy_cap)
                    else:
                        target    = int(VF_POS_LIMIT * conviction)
                        shortfall = max(0, target - pos)
                        qty       = min(-od.sell_orders[best_ask], buy_cap, shortfall)
                    if qty > 0:
                        vf_orders.append(Order(VF_PRODUCT, best_ask, qty))

                elif sell_signal and best_bid is not None and sell_cap > 0:
                    if sell_large:
                        qty = min(od.buy_orders[best_bid], sell_cap)
                    else:
                        target    = int(VF_POS_LIMIT * conviction)
                        shortfall = max(0, target + pos)
                        qty       = min(od.buy_orders[best_bid], sell_cap, shortfall)
                    if qty > 0:
                        vf_orders.append(Order(VF_PRODUCT, best_bid, -qty))

                # ── debug ────────────────────────────────────────────────────
                print(
                    f"t={state.timestamp} pos={pos} mid={mid:.1f} "
                    f"mean={mean:.1f} std={std:.2f} zscore={zscore:.3f} "
                    f"conviction={conviction:.2f} orders={len(vf_orders)}"
                )

        result[VF_PRODUCT] = vf_orders

        # ── persist state for both products ──────────────────────────────────
        trader_data = json.dumps({
            "hp_mid_history": hp_mid_history,
            "hp_ema_hp":      ema_hp,
            "vf_mid_history": vf_mid_history,
            "vf_ema_hp":      ema_vf,
        })

        return result, 0, trader_data
