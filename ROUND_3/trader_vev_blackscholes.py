from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json
import math

# ─────────────────────────────────────────────────────────────
# PRODUCTS
# ─────────────────────────────────────────────────────────────
VF = "VELVETFRUIT_EXTRACT"
HP = "HYDROGEL_PACK"

# ─────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────
def ema(prev, new, alpha):
    return alpha * new + (1 - alpha) * prev

def rolling_mean_std(prices, min_std=1.5):
    if not prices:
        return 0.0, min_std
    mean = sum(prices) / len(prices)
    var = sum((p - mean) ** 2 for p in prices) / len(prices)
    return mean, max(math.sqrt(var), min_std)

# ─────────────────────────────────────────────────────────────
# MAIN TRADER
# ─────────────────────────────────────────────────────────────
class Trader:

    def run(self, state: TradingState):
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except:
            saved = {}

        result = {}

        # =========================
        # VELVETFRUIT STRATEGY
        # =========================
        result[VF], saved = self.trade_vf(state, saved)

        # =========================
        # HYDROGEL STRATEGY
        # =========================
        result[HP], saved = self.trade_hp(state, saved)

        return result, 0, json.dumps(saved)

    # ───────────────────────────
    # VELVETFRUIT (MEAN REVERSION)
    # ───────────────────────────
    def trade_vf(self, state, saved):
        POS_LIMIT = 200
        LOOKBACK = 1000
        WARMUP = 50
        ENTRY = 1.1
        EXIT = 0.1
        EMA_ALPHA = 0.01

        orders = []
        od = state.order_depths.get(VF)

        if not od or (not od.buy_orders and not od.sell_orders):
            return orders, saved

        best_bid = max(od.buy_orders) if od.buy_orders else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        # state
        hist = saved.get("vf_hist", [])
        ema_val = saved.get("vf_ema", 5260)

        # mid
        if best_bid and best_ask:
            mid = (best_bid + best_ask) / 2
        else:
            mid = best_bid or best_ask

        ema_val = ema(ema_val, mid, EMA_ALPHA)

        hist.append(mid)
        if len(hist) > LOOKBACK:
            hist.pop(0)

        saved["vf_hist"] = hist
        saved["vf_ema"] = ema_val

        if len(hist) < WARMUP:
            return orders, saved

        mean, std = rolling_mean_std(hist)
        z = (mid - mean) / std

        pos = state.position.get(VF, 0)
        buy_cap = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # exits
        if pos > 0 and z > -EXIT:
            qty = min(pos, od.buy_orders.get(best_bid, 0))
            if qty > 0:
                orders.append(Order(VF, best_bid, -qty))
                return orders, saved

        if pos < 0 and z < EXIT:
            qty = min(-pos, -od.sell_orders.get(best_ask, 0))
            if qty > 0:
                orders.append(Order(VF, best_ask, qty))
                return orders, saved

        # entries
        if z < -ENTRY and best_ask:
            qty = min(-od.sell_orders[best_ask], buy_cap)
            if qty > 0:
                orders.append(Order(VF, best_ask, qty))

        elif z > ENTRY and best_bid:
            qty = min(od.buy_orders[best_bid], sell_cap)
            if qty > 0:
                orders.append(Order(VF, best_bid, -qty))

        return orders, saved

    # ───────────────────────────
    # HYDROGEL (MARKET MAKING + TAKE)
    # ───────────────────────────
    def trade_hp(self, state, saved):
        POS_LIMIT = 200
        TAKE_EDGE = 8
        EMA_ALPHA = 0.05

        orders = []
        od = state.order_depths.get(HP)

        if not od or (not od.buy_orders and not od.sell_orders):
            return orders, saved

        best_bid = max(od.buy_orders) if od.buy_orders else None
        best_ask = min(od.sell_orders) if od.sell_orders else None

        ema_val = saved.get("hp_ema", 9997)

        if best_bid and best_ask:
            mid = (best_bid + best_ask) / 2
        else:
            mid = best_bid or best_ask

        ema_val = ema(ema_val, mid, EMA_ALPHA)
        saved["hp_ema"] = ema_val

        pos = state.position.get(HP, 0)
        buy_cap = POS_LIMIT - pos
        sell_cap = POS_LIMIT + pos

        # TAKE LOGIC (your strongest edge)
        if best_ask and best_ask < ema_val - TAKE_EDGE:
            qty = min(-od.sell_orders[best_ask], buy_cap)
            if qty > 0:
                orders.append(Order(HP, best_ask, qty))

        if best_bid and best_bid > ema_val + TAKE_EDGE:
            qty = min(od.buy_orders[best_bid], sell_cap)
            if qty > 0:
                orders.append(Order(HP, best_bid, -qty))

        return orders, saved
