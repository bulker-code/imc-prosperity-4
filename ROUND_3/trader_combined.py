"""
IMC Prosperity 4 – Round 3 | HYDROGEL_PACK + VELVETFRUIT_EXTRACT (combined)
============================================================================

HYDROGEL_PACK strategy: EMA-based aggressive taking + passive MM (MM commented out).
  • Bots always quote bid1 = mid - 8, ask1 = mid + 8 (spread = 16, 93% of ticks).
  • Take aggressively when best ask < EMA - TAKE_EDGE or best bid > EMA + TAKE_EDGE.
  • Passive MM layer (currently disabled) quotes 1 tick inside with inventory skew.
  • Position limits: HYDROGEL_PACK = 200

VELVETFRUIT_EXTRACT strategy: rolling zscore mean reversion.
  • Price oscillates ~60-80 ticks peak to trough — pure mean reversion.
  • Buy when zscore < -ENTRY_ZSCORE, sell when zscore > +ENTRY_ZSCORE.
  • Exit when |zscore| < EXIT_ZSCORE. Full size on extreme dislocations.
  • Conviction sizing between entry thresholds.
  • Position limits: VELVETFRUIT_EXTRACT = 200
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json
import math


# ── HYDROGEL_PACK parameters ─────────────────────────────────────────────────
HP_PRODUCT          = "HYDROGEL_PACK"
HP_POS_LIMIT        = 200
HP_QUOTE_INSIDE     = 1
HP_QUOTE_SIZE       = 14
HP_SKEW_TICKS       = 2
HP_ONE_SIDE_LEVEL   = 150
HP_TAKE_EDGE        = 8
HP_EMA_ALPHA        = 0.05
HP_FAIR_VALUE_0     = 9997.0
HP_MAX_TAKE_SHORT   = -150
HP_MAX_TAKE_LONG    = 150
HP_MIN_STD          = 2
HP_WARMUP           = 50
HP_LOOKBACK         = 500
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

def rolling_mean_std(prices: list, min_std: float):
    n = len(prices)
    if n == 0:
        return 0.0, min_std
    mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, min_std)

def rolling_mean_std_with_mean(prices: list, mean: float, min_std: float):
    """Compute std against a provided mean (e.g. EMA) rather than the sample mean."""
    n = len(prices)
    if n == 0:
        return mean, min_std
    variance = sum((p - mean) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    return mean, max(std, min_std)


# ─────────────────────────────────────────────────────────────────────────────

class Trader:

    # ── HYDROGEL_PACK logic ──────────────────────────────────────────────────

    def _run_hydrogel(
        self,
        state: TradingState,
        saved: dict,
    ):
        mid_history: list = saved.get("hp_mid_history", [])
        ema_hp: float     = saved.get("hp_ema", HP_FAIR_VALUE_0)

        od: OrderDepth = state.order_depths.get(HP_PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return orders, mid_history, ema_hp

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── update EMA fair value ────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
            ema_hp = _ema(ema_hp, mid, HP_EMA_ALPHA)
        elif best_bid is not None:
            mid = float(best_bid)
            ema_hp = _ema(ema_hp, mid, HP_EMA_ALPHA)
        elif best_ask is not None:
            mid = float(best_ask)
            ema_hp = _ema(ema_hp, mid, HP_EMA_ALPHA)
        else:
            return orders, mid_history, ema_hp

        mid_history.append(mid)
        if len(mid_history) > HP_LOOKBACK:
            mid_history.pop(0)

        pos      = state.position.get(HP_PRODUCT, 0)
        buy_cap  = HP_POS_LIMIT - pos
        sell_cap = HP_POS_LIMIT + pos

        # ── warmup guard ─────────────────────────────────────────────────────
        if len(mid_history) < HP_WARMUP:
            print(f"[HP] t={state.timestamp} warming up {len(mid_history)}/{HP_WARMUP}")
            return orders, mid_history, ema_hp

        # ── 1. AGGRESSIVE TAKING ─────────────────────────────────────────────
        ema_ready = state.timestamp > HP_EMA_WARMUP_TICKS
        if ema_ready:
            if best_ask is not None and best_ask < ema_hp - HP_TAKE_EDGE:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap)
                if qty > 0:
                    orders.append(Order(HP_PRODUCT, best_ask, qty))

            if best_bid is not None and best_bid > ema_hp + HP_TAKE_EDGE:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap)
                if qty > 0:
                    orders.append(Order(HP_PRODUCT, best_bid, -qty))

        # ── 2. PASSIVE MARKET-MAKING QUOTES (currently disabled) ─────────────
        """
        if best_bid is not None and best_ask is not None:
            skew = HP_SKEW_TICKS * round(pos / 100)

            our_bid = best_bid + HP_QUOTE_INSIDE - skew
            our_ask = best_ask - HP_QUOTE_INSIDE - skew

            if our_bid >= our_ask:
                our_bid = int(ema_hp) - 1
                our_ask = int(ema_hp) + 1

            post_bid = pos < HP_ONE_SIDE_LEVEL
            post_ask = pos > -HP_ONE_SIDE_LEVEL

            if post_bid:
                qty_bid = min(HP_QUOTE_SIZE, buy_cap)
                if qty_bid > 0:
                    orders.append(Order(HP_PRODUCT, our_bid, qty_bid))

            if post_ask:
                qty_ask = min(HP_QUOTE_SIZE, sell_cap)
                if qty_ask > 0:
                    orders.append(Order(HP_PRODUCT, our_ask, -qty_ask))
        """

        print(
            f"[HP] t={state.timestamp} pos={pos} ema={ema_hp:.1f} "
            f"bid={best_bid} ask={best_ask} orders={len(orders)}"
        )

        return orders, mid_history, ema_hp

    # ── VELVETFRUIT_EXTRACT logic ────────────────────────────────────────────

    def _run_velvetfruit(
        self,
        state: TradingState,
        saved: dict,
    ):
        mid_history: list = saved.get("vf_mid_history", [])
        ema_hp: float     = saved.get("vf_ema", VF_FAIR_VALUE_0)

        od = state.order_depths.get(VF_PRODUCT)
        orders: List[Order] = []

        if od is None or (not od.buy_orders and not od.sell_orders):
            return orders, mid_history, ema_hp

        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # ── mid price & EMA ──────────────────────────────────────────────────
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
            ema_hp = _ema(ema_hp, mid, VF_EMA_ALPHA)
        elif best_bid is not None:
            mid = float(best_bid)
            ema_hp = _ema(ema_hp, mid, VF_EMA_ALPHA)
        else:
            mid = float(best_ask)
            ema_hp = _ema(ema_hp, mid, VF_EMA_ALPHA)

        mid_history.append(mid)
        if len(mid_history) > VF_LOOKBACK:
            mid_history.pop(0)

        pos      = state.position.get(VF_PRODUCT, 0)
        buy_cap  = VF_POS_LIMIT - pos
        sell_cap = VF_POS_LIMIT + pos

        # ── warmup guard ─────────────────────────────────────────────────────
        if len(mid_history) < VF_WARMUP:
            print(f"[VF] t={state.timestamp} warming up {len(mid_history)}/{VF_WARMUP}")
            return orders, mid_history, ema_hp

        # ── zscore ───────────────────────────────────────────────────────────
        mean, std = rolling_mean_std_with_mean(mid_history, ema_hp, VF_MIN_STD)
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

        # ── exits ────────────────────────────────────────────────────────────
        if pos > 0 and exit_long:
            if best_bid is not None:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap, pos)
                if qty > 0:
                    orders.append(Order(VF_PRODUCT, best_bid, -qty))
                    print(
                        f"[VF] t={state.timestamp} EXIT LONG pos={pos} "
                        f"zscore={zscore:.3f} qty={qty}"
                    )
                    return orders, mid_history, ema_hp

        if pos < 0 and exit_short:
            if best_ask is not None:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap, abs(pos))
                if qty > 0:
                    orders.append(Order(VF_PRODUCT, best_ask, qty))
                    print(
                        f"[VF] t={state.timestamp} EXIT SHORT pos={pos} "
                        f"zscore={zscore:.3f} qty={qty}"
                    )
                    return orders, mid_history, ema_hp

        # ── entries ───────────────────────────────────────────────────────────
        if buy_signal and best_ask is not None and buy_cap > 0:
            if buy_large:
                qty = min(-od.sell_orders[best_ask], buy_cap)
            else:
                target    = int(VF_POS_LIMIT * conviction)
                shortfall = max(0, target - pos)
                qty       = min(-od.sell_orders[best_ask], buy_cap, shortfall)
            if qty > 0:
                orders.append(Order(VF_PRODUCT, best_ask, qty))

        elif sell_signal and best_bid is not None and sell_cap > 0:
            if sell_large:
                qty = min(od.buy_orders[best_bid], sell_cap)
            else:
                target    = int(VF_POS_LIMIT * conviction)
                shortfall = max(0, target + pos)
                qty       = min(od.buy_orders[best_bid], sell_cap, shortfall)
            if qty > 0:
                orders.append(Order(VF_PRODUCT, best_bid, -qty))

        print(
            f"[VF] t={state.timestamp} pos={pos} mid={mid:.1f} "
            f"mean={mean:.1f} std={std:.2f} zscore={zscore:.3f} "
            f"conviction={conviction:.2f} orders={len(orders)}"
        )

        return orders, mid_history, ema_hp

    # ── main run ─────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        # ── restore persisted state ──────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        result: Dict[str, List[Order]] = {}

        # ── HYDROGEL_PACK ────────────────────────────────────────────────────
        hp_orders, hp_history, hp_ema = self._run_hydrogel(state, saved)
        if hp_orders:
            result[HP_PRODUCT] = hp_orders

        # ── VELVETFRUIT_EXTRACT ──────────────────────────────────────────────
        vf_orders, vf_history, vf_ema = self._run_velvetfruit(state, saved)
        if vf_orders:
            result[VF_PRODUCT] = vf_orders

        # ── persist state for both products ──────────────────────────────────
        trader_data = json.dumps({
            "hp_mid_history": hp_history,
            "hp_ema":         hp_ema,
            "vf_mid_history": vf_history,
            "vf_ema":         vf_ema,
        })

        return result, 0, trader_data
