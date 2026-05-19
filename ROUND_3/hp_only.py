from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

class Trader:

    LIMIT = 200

    def run(self, state: TradingState):

        result = {}

        # =========================
        # LOAD STATE
        # =========================
        if state.traderData:
            data = json.loads(state.traderData)
        else:
            data = {
                "fair": None,
                "prev_mid": None,
                "trend": 0
            }

        if "HYDROGEL_PACK" not in state.order_depths:
            return {}, 0, json.dumps(data)

        depth = state.order_depths["HYDROGEL_PACK"]
        orders: List[Order] = []
        position = state.position.get("HYDROGEL_PACK", 0)

        if not depth.buy_orders or not depth.sell_orders:
            return {}, 0, json.dumps(data)

        best_bid = max(depth.buy_orders.keys())
        best_ask = min(depth.sell_orders.keys())
        mid = (best_bid + best_ask) / 2

        # =========================
        # FAIR VALUE (FASTER EWMA)
        # =========================
        alpha = 0.3  # faster reaction than before

        if data["fair"] is None:
            fair = mid
        else:
            fair = alpha * mid + (1 - alpha) * data["fair"]

        data["fair"] = fair

        # =========================
        # TREND DETECTION
        # =========================
        if data["prev_mid"] is None:
            price_change = 0
        else:
            price_change = mid - data["prev_mid"]

        data["prev_mid"] = mid

        # smooth trend
        trend = 1 # 0.2 * price_change + 0.8 * data["trend"]
        data["trend"] = trend

        TREND_THRESHOLD = 1.25

        # =========================
        # VOLATILITY / SPREAD
        # =========================
        vol = abs(mid - fair)
        spread = 2

        # =========================
        # INVENTORY SKEW
        # =========================
        skew = position / self.LIMIT
        fair_adj = fair - skew * 3

        # =========================
        # REGIME SWITCH
        # =========================
        if abs(trend) < TREND_THRESHOLD:
            mode = "MM"
        else:
            mode = "TREND"

        size = 15

        # =========================
        # MARKET MAKING MODE
        # =========================
        if mode == "MM":

            bid_price = int(fair_adj - spread)
            ask_price = int(fair_adj + spread)
            
            if position < self.LIMIT and mid < fair:
                orders.append(Order("HYDROGEL_PACK", best_bid, size))

            if position > -self.LIMIT and mid > fair:
                orders.append(Order("HYDROGEL_PACK", best_ask, -size))

        # =========================
        # TREND MODE (ANTI-BLOWUP)
        # =========================
        else:

            # UP TREND → only buy / don’t sell
            if trend > 0:
                if position < self.LIMIT:
                    orders.append(Order("HYDROGEL_PACK", best_ask, size))

            # DOWN TREND → only sell / don’t buy
            else:
                if position > -self.LIMIT:
                    orders.append(Order("HYDROGEL_PACK", best_bid, -size))

        # =========================
        # KILL SWITCH (CRITICAL)
        # =========================
        if abs(position) > 120 and abs(trend) > TREND_THRESHOLD:

            if position > 0:
                orders.append(Order("HYDROGEL_PACK", best_bid, -20))

            elif position < 0:
                orders.append(Order("HYDROGEL_PACK", best_ask, 20))

        # =========================
        # STORE RESULTS
        # =========================
        result["HYDROGEL_PACK"] = orders

        return result, 0, json.dumps(data)
