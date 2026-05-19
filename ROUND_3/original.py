from datamodel import OrderDepth, UserId, TradingState, Order
import string
import numpy as np
from typing import List, Dict
import json
import math

class Trader:

    POSITION_LIMITS = {
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
    }

    # Add voucher limits
    for strike in [4000,4500,5000,5100,5200,5300,5400,5500,6000,6500]:
        POSITION_LIMITS[f"VEV_{strike}"] = 300

    def get_mid(self, depth: OrderDepth):
        if not depth.buy_orders or not depth.sell_orders:
            return None
        best_bid = max(depth.buy_orders.keys())
        best_ask = min(depth.sell_orders.keys())
        return (best_bid + best_ask) / 2

    def estimate_time_value(self, strike, spot):
        diff = abs(strike - spot)

        if diff < 200:
            return 120
        elif diff < 500:
            return 60
        else:
            return 20

    def run(self, state: TradingState):

        result = {}

        # === GET UNDERLYING PRICE ===
        if "VELVETFRUIT_EXTRACT" in state.order_depths:
            spot = self.get_mid(state.order_depths["VELVETFRUIT_EXTRACT"])
        else:
            spot = None

        for product, depth in state.order_depths.items():

            orders: List[Order] = []
            position = state.position.get(product, 0)
            limit = self.POSITION_LIMITS[product]

            # =========================
            # 1. DELTA-1 MARKET MAKING
            # =========================
            if product == "HYDROGEL_PACK":

                if depth.buy_orders and depth.sell_orders:
                    best_bid = max(depth.buy_orders.keys())
                    best_ask = min(depth.sell_orders.keys())
                    mid = (best_bid + best_ask) / 2
                else:
                    return {}, 0, json.dumps(data)

                # =========================
                # FAIR VALUE (EWMA)
                # =========================
                alpha = 0.1

                if data["fair"] is None:
                    fair = mid
                else:
                    fair = alpha * mid + (1 - alpha) * data["fair"]

                data["fair"] = fair

                # =========================
                # VOLATILITY ESTIMATE
                # =========================
                vol = abs(mid - fair)

                spread = max(1, int(vol * 10))

                # =========================
                # INVENTORY SKEW
                # =========================
                skew = position / self.LIMIT

                bid_price = int(fair - spread - skew * 3)
                ask_price = int(fair + spread - skew * 3)

                size = 15

                # =========================
                # PLACE ORDERS
                # =========================
                if position < self.LIMIT:
                    orders.append(Order("HYDROGEL_PACK", bid_price, size))

                if position > -self.LIMIT:
                    orders.append(Order("HYDROGEL_PACK", ask_price, -size))

                # =========================
                # POSITION CONTROL (FORCE EXIT)
                # =========================
                if abs(position) > 150:

                    if position > 0 and depth.buy_orders:
                        best_bid = max(depth.buy_orders.keys())
                        orders.append(Order("HYDROGEL_PACK", best_bid, -20))

                    if position < 0 and depth.sell_orders:
                        best_ask = min(depth.sell_orders.keys())
                        orders.append(Order("HYDROGEL_PACK", best_ask, 20))




            """
            # =========================
            # 2. OPTIONS TRADING
            # =========================
            elif "VEV_" in product and spot is not None:

                strike = int(product.split("_")[1])
                mid = self.get_mid(depth)

                if mid is None:
                    continue

                intrinsic = max(spot - strike, 0)
                time_val = self.estimate_time_value(strike, spot)

                fair_price = intrinsic + time_val

                # Aggression threshold
                edge = 15

                # BUY underpriced
                if depth.sell_orders:
                    best_ask = min(depth.sell_orders.keys())
                    vol = -depth.sell_orders[best_ask]

                    if best_ask < fair_price - edge:
                        buy_qty = min(vol, limit - position)
                        if buy_qty > 0:
                            orders.append(Order(product, best_ask, buy_qty))

                # SELL overpriced
                if depth.buy_orders:
                    best_bid = max(depth.buy_orders.keys())
                    vol = depth.buy_orders[best_bid]

                    if best_bid > fair_price + edge:
                        sell_qty = min(vol, position + limit)
                        if sell_qty > 0:
                            orders.append(Order(product, best_bid, -sell_qty))
            """
            result[product] = orders
            
        return result, 0, ""
