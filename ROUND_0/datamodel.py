
class Order:
    def __init__(self, product: str, price: int, quantity: int):
        self.product = product
        self.price = price
        self.quantity = quantity

    def __repr__(self):
        return f"Order({self.product}, {self.price}, {self.quantity})"


class OrderDepth:
    def __init__(self):
        self.buy_orders: Dict[int, int] = {}
        self.sell_orders: Dict[int, int] = {}


class TradingState:
    def __init__(self, order_depths, position):
        self.order_depths = order_depths
        self.position = position
