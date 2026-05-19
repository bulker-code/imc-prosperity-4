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
        self.price_history = {}
        self.window = 15
        
    def bid(self):
        return 1001
    
    def get_mid_price(self, order_depth: OrderDepth):
        if len(order_depth.buy_orders) == 0 or len(order_depth.sell_orders) == 0:
            return None
        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        return (best_bid + best_ask) // 2

    def run(self, state: TradingState):
        result = {}
        
        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []
            
            position = state.position.get(product, 0)
            limit = self.LIMIT[product]
            if len(order_depth.sell_orders) != 0:
                best_ask = min(order_depth.sell_orders.keys())
                best_ask_amount = order_depth.sell_orders[best_ask]
            if len(order_depth.buy_orders) != 0:
                best_bid = max(order_depth.buy_orders.keys())
                best_bid_amount = order_depth.buy_orders[best_bid]   
            
            if product not in self.price_history:
                self.price_history[product] = []

            mid_price = self.get_mid_price(order_depth)
            if mid_price is None:
                result[product] = orders
                continue
            last_mid = 0
            if len(self.price_history[product]) > 0:
                last_mid = np.mean(self.price_history[product][-5:])
            self.price_history[product].append(mid_price)
            mid_prices = self.price_history[product][-self.window:]
            spread = best_ask - best_bid
            #edge = spread // 9
            #fair_value = np.mean(mid_prices)
            edge_small = 1
            edge_large = 4
                        
            # Or: weighted by multiple levels if available
            if len(order_depth.sell_orders) != 0 and len(order_depth.buy_orders) != 0:
                total_bid = sum(order_depth.buy_orders.values())
                total_ask = sum(-v for v in order_depth.sell_orders.values())
                book_pressure = (total_bid - total_ask) / (total_bid + total_ask)  # -1 to +1
                fair_value = mid_price + book_pressure * 2  # shift fair value toward pressure
                
            
            if len(mid_prices) >= 2:
                returns = np.diff(mid_prices)
                volatility = int(np.std(returns))
            else:
                volatility = 0
            
            
            if product == "INTARIAN_PEPPER_ROOT":
                if position < limit and len(order_depth.sell_orders) > 0:
                    for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                        buy_quantity = min(-ask_volume, limit - position)
                    if buy_quantity > 0:
                        orders.append(Order(product, best_ask, buy_quantity))
            
                
              
            if product == "ASH_COATED_OSMIUM":

                if not order_depth.buy_orders or not order_depth.sell_orders:
                    return result, 1, ""

                # --- Parameters ---
                ORDER_SIZE = 20 #max(3, int(15 - volatility))
                MIN_SPREAD = 2   # only trade if spread wide enough

                # --- Inventory skew (very important) ---
                skew = int(-position * 0.03)
                print(state.observations)
                
                for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                    if ask_price < fair_value - 7:#9992 and fair_value > 10005:#last_mid + 1 and ask_price < fair_value - 3:
                        qty = min(-ask_volume, limit - position)
                        if qty > 0:
                            orders.append(Order(product, ask_price, qty))
                            position += qty
                            
                for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
                    if bid_price > fair_value + 7 and bid_price > 10005:# and last_mid < 9995: #last_mid - 1 and bid_price > fair_value + 3:
                        qty =  min(bid_volume, limit + position)
                        if qty > 0:
                            orders.append(Order(product, bid_price, -qty))
                            position -= qty
                            
                # --- Only market make if spread is good ---       
                if spread >= MIN_SPREAD:
                    buy_qty = min(ORDER_SIZE, limit - position)
                    sell_qty = min(ORDER_SIZE, limit + position)
                    bid_price = best_bid + edge_small + skew
                    ask_price = best_ask - edge_small + skew
                    # place buy (bid)
                    if -limit < position < limit:
                        if bid_price >= ask_price:  # quotes crossed, skip MM
                            pass
                        else:
                            if buy_qty > 0:
                                orders.append(Order(product, bid_price, buy_qty))
                            if sell_qty > 0:
                                orders.append(Order(product, ask_price, -sell_qty))
                
            result[product] = orders
        return result, 1, ""