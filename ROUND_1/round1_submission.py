from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string
import numpy as np

class Trader:
    
    
    def __init__(self):
        self.LIMIT = {
            "INTARIAN_PEPPER_ROOT": 80,
            "ASH_COATED_OSMIUM": 80
        }
    def bid(self):
        return 15

    def run(self, state: TradingState):
        result = {}
        
        product = "INTARIAN_PEPPER_ROOT"

        if product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            position = state.position.get(product, 0)
            limit = self.LIMIT[product]

            # Only buy if we have room
            if position < limit and len(order_depth.sell_orders) > 0:
                best_ask = min(order_depth.sell_orders.keys())
                best_ask_amount = order_depth.sell_orders[best_ask]

                buy_quantity = min(-best_ask_amount, limit - position)

                if buy_quantity > 0:
                    orders.append(Order(product, best_ask, buy_quantity))

            result[product] = orders
        
        product = "ASH_COATED_OSMIUM"

        if product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if not order_depth.buy_orders or not order_depth.sell_orders:
                return result, 1, ""

            # --- Get best prices ---
            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())

            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            edge = spread / 2

            position = state.position.get(product, 0)
            limit = self.LIMIT[product]

            # --- Parameters ---
            ORDER_SIZE = 13
            MIN_SPREAD = 2   # only trade if spread wide enough

            # --- Inventory skew (very important) ---
            skew = 0
            if position > 40:
                skew = -1   # push prices down → encourage selling
            elif position < -40:
                skew = +1   # push prices up → encourage buying

            print(state.observations)
            
            # --- Only market make if spread is good ---
            if spread >= MIN_SPREAD:
                buy_qty = min(ORDER_SIZE, limit - position)
                sell_qty = min(ORDER_SIZE, limit + position)

                # place buy (bid)
                if position < limit:
                    bid_price = best_bid + 1 + skew
                    if buy_qty > 0:
                        orders.append(Order(product, bid_price, buy_qty))

                # place sell (ask)
                if position > -limit:
                    ask_price = best_ask - 1 + skew
                    if sell_qty > 0:
                        orders.append(Order(product, ask_price, -sell_qty))

            result[product] = orders

        return result, 1, ""
