"""
IMC Prosperity 4 - Round 3 Trading Algorithm
Products: HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000..VEV_6500

Strategy summary
----------------
HYDROGEL_PACK
  Mean-reverts around ~9991 with std ~32. Market-make aggressively around a
  running EMA fair value. Quote bid/ask symmetrically inside the observed spread
  and take any clear mispricings from the order book.

VELVETFRUIT_EXTRACT
  Low spread (~5), mild upward drift. Market-make with a very tight edge around
  the EMA. Also take aggressively when the book shows a clear edge vs fair value.

VEV options (VEV_4000 … VEV_6500)
  Each option is a European-style call on VE with a known strike and TTE that
  decreases by 1 day each round (TTE=5 at the start of Round 3).
  Fair value is computed via Black-Scholes using a calibrated IV of ~31%.
  Strategy per option tier:

  Deep ITM (K ≤ 5000, delta ≈ 1.0):
    Price ≈ VE − K. Market-make around this. Very tight spread.

  Near-the-money (K = 5100–5400, delta 0.2–0.8):
    BS fair value > observed market price (market consistently underprices by ~2-5).
    Buy aggressively when market is below FV and delta-hedge via VE.
    Also post passive sell quotes above FV.

  Far OTM (K ≥ 5500):
    Near zero value. Sell into any quote above BS fair value. Cap position.

Position limits: HP 200, VE 200, each VEV voucher 300.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import math
import json


# ---------------------------------------------------------------------------
# Black-Scholes helpers (no external libs needed — only math)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Accurate normal CDF using Horner's method (Abramowitz & Stegun 26.2.17)."""
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + 0.2316419 * x)
    poly = t * (0.319381530
                + t * (-0.356563782
                       + t * (1.781477937
                              + t * (-1.821255978
                                     + t * 1.330274429))))
    cdf = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
    return 0.5 + sign * (cdf - 0.5)


def bs_call_price(S: float, K: float, T: float, sigma: float) -> float:
    """Black-Scholes call price (r=0, no dividends)."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K)
    try:
        d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * _norm_cdf(d1) - K * _norm_cdf(d2)
    except (ValueError, ZeroDivisionError):
        return max(0.0, S - K)


def bs_call_delta(S: float, K: float, T: float, sigma: float) -> float:
    """BS call delta N(d1)."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    try:
        d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
        return _norm_cdf(d1)
    except (ValueError, ZeroDivisionError):
        return 1.0 if S > K else 0.0


# ---------------------------------------------------------------------------
# EMA helper
# ---------------------------------------------------------------------------

def ema_update(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1 - alpha) * prev


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------

PRODUCTS = [
    "HYDROGEL_PACK",
    "VELVETFRUIT_EXTRACT",
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
    "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500",
    "VEV_6000", "VEV_6500",
]

VOUCHERS = [p for p in PRODUCTS if p.startswith("VEV_")]
STRIKES: Dict[str, int] = {v: int(v.split("_")[1]) for v in VOUCHERS}

# Position limits
POS_LIMIT = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    **{v: 300 for v in VOUCHERS},
}

# Calibrated implied vol (back-solved from historical data: ~31%)
SIGMA = 0.31

# Round 3: TTE starts at 5 days.  Each Solvenarian day = 1 calendar day.
# timestamp runs 0..999_900 in steps of 100 per "day" of historical data.
# Within a single submission the TTE does not change during the 10_000 ticks.
TTE_ROUND3_DAYS = 5
TICKS_PER_DAY = 1_000_000     # how many timestamp units per day
T_YEARS = TTE_ROUND3_DAYS / 365.0   # convert days to years for BS

# Market-making edge parameters
HP_MM_SPREAD = 6        # half-spread to quote around HP fair value
VE_MM_SPREAD = 2        # half-spread to quote around VE fair value
VEV_EDGE = 2            # minimum edge required to take a VEV trade
MM_SIZE_HP = 15         # max quote size per side for HP
MM_SIZE_VE = 15         # max quote size per side for VE
MM_SIZE_VEV = 10        # max quote size per side for VEV

# EMA smoothing factors
ALPHA_HP = 0.05   # slow — HP is noisy
ALPHA_VE = 0.08   # slightly faster for VE


class Trader:

    def bid(self):
        return 15

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get_pos(self, state: TradingState, product: str) -> int:
        return state.position.get(product, 0)

    def _mid(self, od: OrderDepth) -> float | None:
        """Best-bid / best-ask midpoint from order depth."""
        if od.buy_orders and od.sell_orders:
            best_bid = max(od.buy_orders.keys())
            best_ask = min(od.sell_orders.keys())
            return (best_bid + best_ask) / 2.0
        if od.buy_orders:
            return float(max(od.buy_orders.keys()))
        if od.sell_orders:
            return float(min(od.sell_orders.keys()))
        return None

    def _best_bid(self, od: OrderDepth):
        if od.buy_orders:
            return max(od.buy_orders.keys())
        return None

    def _best_ask(self, od: OrderDepth):
        if od.sell_orders:
            return min(od.sell_orders.keys())
        return None

    def _capacity(self, state: TradingState, product: str, side: int) -> int:
        """How many units we can still buy (side=+1) or sell (side=-1)."""
        pos = self._get_pos(state, product)
        limit = POS_LIMIT[product]
        if side > 0:
            return limit - pos
        else:
            return limit + pos

    def _take_orders(
        self,
        state: TradingState,
        product: str,
        fair: float,
        edge: float,
    ) -> List[Order]:
        """
        Take aggressively from the order book when price vs fair value exceeds
        the required edge.  Respects position limits.
        """
        orders: List[Order] = []
        od = state.order_depths.get(product)
        if od is None:
            return orders

        # Hit cheap asks (buy)
        if od.sell_orders:
            for ask_price in sorted(od.sell_orders.keys()):
                if ask_price < fair - edge:
                    vol = -od.sell_orders[ask_price]   # positive quantity
                    cap = self._capacity(state, product, +1)
                    qty = min(vol, cap)
                    if qty > 0:
                        orders.append(Order(product, ask_price, qty))
                else:
                    break

        # Lift expensive bids (sell)
        if od.buy_orders:
            for bid_price in sorted(od.buy_orders.keys(), reverse=True):
                if bid_price > fair + edge:
                    vol = od.buy_orders[bid_price]     # positive quantity
                    cap = self._capacity(state, product, -1)
                    qty = min(vol, cap)
                    if qty > 0:
                        orders.append(Order(product, bid_price, -qty))
                else:
                    break

        return orders

    def _post_mm_orders(
        self,
        state: TradingState,
        product: str,
        fair: float,
        half_spread: int,
        max_size: int,
        skew_factor: float = 0.5,
    ) -> List[Order]:
        """
        Post passive market-making quotes.
        Skews quotes toward zero-position when we are directionally exposed.
        """
        orders: List[Order] = []
        pos = self._get_pos(state, product)
        limit = POS_LIMIT[product]

        # Compute position skew: positive pos → tighten bid (discourage buying)
        skew = int(round(skew_factor * pos / limit * half_spread))
        bid_price = int(math.floor(fair - half_spread - skew))
        ask_price = int(math.ceil(fair + half_spread - skew))

        cap_buy = self._capacity(state, product, +1)
        cap_sell = self._capacity(state, product, -1)

        qty_bid = min(max_size, cap_buy)
        qty_ask = min(max_size, cap_sell)

        if qty_bid > 0:
            orders.append(Order(product, bid_price, qty_bid))
        if qty_ask > 0:
            orders.append(Order(product, ask_price, -qty_ask))

        return orders

    # -----------------------------------------------------------------------
    # Per-product strategy methods
    # -----------------------------------------------------------------------

    def _strategy_hp(self, state: TradingState, ema_hp: float) -> List[Order]:
        """
        HYDROGEL_PACK: mean-reverting market-maker.
        Fair value = slow EMA of mid price (≈ 9991).
        """
        od = state.order_depths.get("HYDROGEL_PACK")
        if od is None:
            return []

        orders = []
        # Take aggressively first
        orders += self._take_orders(state, "HYDROGEL_PACK", ema_hp, HP_MM_SPREAD)
        # Post passive quotes
        orders += self._post_mm_orders(
            state, "HYDROGEL_PACK", ema_hp, HP_MM_SPREAD, MM_SIZE_HP, skew_factor=0.4
        )
        return orders

    def _strategy_ve(self, state: TradingState, ema_ve: float) -> List[Order]:
        """
        VELVETFRUIT_EXTRACT: tight market-maker around EMA.
        """
        od = state.order_depths.get("VELVETFRUIT_EXTRACT")
        if od is None:
            return []

        orders = []
        orders += self._take_orders(state, "VELVETFRUIT_EXTRACT", ema_ve, VE_MM_SPREAD)
        orders += self._post_mm_orders(
            state, "VELVETFRUIT_EXTRACT", ema_ve, VE_MM_SPREAD, MM_SIZE_VE, skew_factor=0.3
        )
        return orders

    def _strategy_vev(
        self,
        state: TradingState,
        voucher: str,
        ve_fair: float,
        tte_years: float,
    ) -> List[Order]:
        """
        VEV options strategy:

        1. Compute BS fair value using current VE estimate and calibrated sigma.
        2. Take from the book whenever the quoted price is sufficiently away from FV.
        3. Post passive quotes around FV.

        Deep ITM (delta ≈ 1): treat like a linear product, tight spread.
        Near ATM: buy if cheap vs FV (market historically underprices), sell if rich.
        Far OTM: mostly sell, cap position since value approaches zero.
        """
        od = state.order_depths.get(voucher)
        if od is None:
            return []

        K = STRIKES[voucher]
        fv = bs_call_price(ve_fair, K, tte_years, SIGMA)
        delta = bs_call_delta(ve_fair, K, tte_years, SIGMA)

        orders: List[Order] = []

        # Choose edge based on moneyness tier
        if delta >= 0.95:
            # Deep ITM — very tight market making
            edge = 1
            half_spread = 3
            size = 20
        elif delta >= 0.5:
            # Near / at the money
            edge = VEV_EDGE
            half_spread = 3
            size = MM_SIZE_VEV
        elif delta >= 0.1:
            # OTM
            edge = VEV_EDGE
            half_spread = 2
            size = 8
        else:
            # Deep OTM / worthless
            # Only sell if above BS fair value + edge
            edge = 1
            half_spread = 1
            size = 5

        # Take orders where market is clearly mispriced vs our FV
        od_take = state.order_depths.get(voucher)

        # For near-ATM options the market tends to underprice → be more
        # aggressive buying.  For far OTM be more aggressive selling.
        buy_edge = edge * (0.5 if delta >= 0.5 else 1.5)
        sell_edge = edge * (1.5 if delta >= 0.5 else 0.5)

        if od_take and od_take.sell_orders:
            for ask_price in sorted(od_take.sell_orders.keys()):
                if ask_price < fv - buy_edge:
                    vol = -od_take.sell_orders[ask_price]
                    cap = self._capacity(state, voucher, +1)
                    qty = min(vol, cap, size * 2)
                    if qty > 0:
                        orders.append(Order(voucher, ask_price, qty))
                else:
                    break

        if od_take and od_take.buy_orders:
            for bid_price in sorted(od_take.buy_orders.keys(), reverse=True):
                if bid_price > fv + sell_edge:
                    vol = od_take.buy_orders[bid_price]
                    cap = self._capacity(state, voucher, -1)
                    qty = min(vol, cap, size * 2)
                    if qty > 0:
                        orders.append(Order(voucher, bid_price, -qty))
                else:
                    break

        # Post passive quotes
        pos = self._get_pos(state, voucher)
        limit = POS_LIMIT[voucher]
        skew = int(round(0.4 * pos / limit * half_spread))

        bid_p = int(math.floor(fv - half_spread - skew))
        ask_p = int(math.ceil(fv + half_spread - skew))

        # Ensure prices are non-negative for vouchers
        bid_p = max(0, bid_p)
        ask_p = max(bid_p + 1, ask_p)

        cap_buy = min(self._capacity(state, voucher, +1), size)
        cap_sell = min(self._capacity(state, voucher, -1), size)

        if cap_buy > 0:
            orders.append(Order(voucher, bid_p, cap_buy))
        if cap_sell > 0:
            orders.append(Order(voucher, ask_p, -cap_sell))

        return orders

    # -----------------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------------

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        # -------------------------------------------------------------------
        # Restore persisted state
        # -------------------------------------------------------------------
        try:
            trader_state = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            trader_state = {}

        # EMAs for fair value tracking
        ema_hp: float = trader_state.get("ema_hp", 9991.0)
        ema_ve: float = trader_state.get("ema_ve", 5250.0)

        # Track what day/TTE we are on using timestamp
        # In the live simulation timestamps start at 0 each round, so TTE = 5d fixed.
        # We adjust TTE estimate if we can infer elapsed time.
        # timestamp 0..999_900 = "day 0"; 1_000_000..1_999_900 = "day 1" etc.
        ts = state.timestamp
        elapsed_days = ts // TICKS_PER_DAY   # integer days elapsed within round
        tte_days = max(1, TTE_ROUND3_DAYS - elapsed_days)
        tte_years = tte_days / 365.0

        # -------------------------------------------------------------------
        # Update fair values from current market data
        # -------------------------------------------------------------------
        od_hp = state.order_depths.get("HYDROGEL_PACK")
        od_ve = state.order_depths.get("VELVETFRUIT_EXTRACT")

        mid_hp = self._mid(od_hp) if od_hp else None
        mid_ve = self._mid(od_ve) if od_ve else None

        if mid_hp is not None:
            ema_hp = ema_update(ema_hp, mid_hp, ALPHA_HP)
        if mid_ve is not None:
            ema_ve = ema_update(ema_ve, mid_ve, ALPHA_VE)

        # -------------------------------------------------------------------
        # Generate orders
        # -------------------------------------------------------------------

        # HYDROGEL_PACK
        if "HYDROGEL_PACK" in state.order_depths:
            result["HYDROGEL_PACK"] = self._strategy_hp(state, ema_hp)

        # VELVETFRUIT_EXTRACT
        if "VELVETFRUIT_EXTRACT" in state.order_depths:
            result["VELVETFRUIT_EXTRACT"] = self._strategy_ve(state, ema_ve)

        # VEV Vouchers
        for voucher in VOUCHERS:
            if voucher in state.order_depths:
                result[voucher] = self._strategy_vev(
                    state, voucher, ema_ve, tte_years
                )

        # -------------------------------------------------------------------
        # Debug prints (visible in the Prosperity log)
        # -------------------------------------------------------------------
        print(
            f"ts={ts} tte={tte_days}d "
            f"ema_hp={ema_hp:.1f} ema_ve={ema_ve:.1f} "
            f"pos_hp={self._get_pos(state,'HYDROGEL_PACK')} "
            f"pos_ve={self._get_pos(state,'VELVETFRUIT_EXTRACT')}"
        )

        # -------------------------------------------------------------------
        # Persist state
        # -------------------------------------------------------------------
        new_state = {
            "ema_hp": ema_hp,
            "ema_ve": ema_ve,
        }
        trader_data = json.dumps(new_state)

        return result, conversions, trader_data
