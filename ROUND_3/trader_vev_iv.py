"""
IMC Prosperity 4 – Round 3 | VELVETFRUIT EXTRACT VOUCHERS (VEV)
================================================================
Strategy: Black-Scholes mispricing with live implied volatility calibration.

Implied Vol Calibration
------------------------
Each tick we solve for the implied volatility (IV) from the at-the-money
or near-the-money option (VEV_5200 or VEV_5300) using bisection search.
This gives us a live SIGMA that reflects what the market actually thinks
volatility is, rather than a hardcoded guess.

We then use this live SIGMA to price ALL 10 vouchers and trade any that
deviate from fair value by more than EDGE ticks.

This prevents the systematic bias we saw where SIGMA=0.01 underpriced
all OTM options, causing the bot to sell everything into -300.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Optional
import json
import math

# ── parameters ───────────────────────────────────────────────────────────────
UNDERLYING         = "VELVETFRUIT_EXTRACT"
POS_LIMIT_VOUCHER  = 300

# Time to expiry
TTE_START          = 5.0       # TTE at start of round 3 day 0
TICKS_PER_DAY      = 1_000_000

# IV calibration — use this voucher to solve for implied vol each tick
# VEV_5200 is near ATM so it has the most vol sensitivity
IV_CALIBRATION_VOUCHER = "VEV_5200"
IV_CALIBRATION_STRIKE  = 5200

# Fallback sigma if calibration fails
SIGMA_FALLBACK     = 0.02
SIGMA_MIN          = 0.001
SIGMA_MAX          = 0.5

# EMA smoothing on implied vol to avoid noisy spikes
IV_EMA_ALPHA       = 0.1   # react fairly quickly to vol changes

# Trading edge
EDGE               = 3.0   # ticks of mispricing before we trade
QUOTE_SIZE         = 10    # units per order

# Voucher strikes
STRIKES = {
    "VEV_4000": 4000,
    "VEV_4500": 4500,
    "VEV_5000": 5000,
    "VEV_5100": 5100,
    "VEV_5200": 5200,
    "VEV_5300": 5300,
    "VEV_5400": 5400,
    "VEV_5500": 5500,
    "VEV_5600": 5600,
    "VEV_6000": 6000,
    "VEV_6500": 6500,
}
# ─────────────────────────────────────────────────────────────────────────────


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_call(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0:
        return max(S - K, 0.0)
    if sigma <= 0 or S <= 0:
        return max(S - K, 0.0)
    try:
        d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * norm_cdf(d1) - K * norm_cdf(d2)
    except (ValueError, ZeroDivisionError):
        return max(S - K, 0.0)


def implied_vol(S: float, K: float, T: float, market_price: float,
                sigma_min: float = SIGMA_MIN, sigma_max: float = SIGMA_MAX,
                tol: float = 0.01, max_iter: int = 50) -> Optional[float]:
    """
    Bisection search for implied volatility.
    Returns None if solution not found within bounds.
    """
    if T <= 0:
        return None

    intrinsic = max(S - K, 0.0)
    if market_price <= intrinsic:
        return None

    # Check bounds
    price_low  = black_scholes_call(S, K, T, sigma_min)
    price_high = black_scholes_call(S, K, T, sigma_max)

    if market_price < price_low or market_price > price_high:
        return None

    lo, hi = sigma_min, sigma_max
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        price_mid = black_scholes_call(S, K, T, mid)
        if abs(price_mid - market_price) < tol:
            return mid
        if price_mid < market_price:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2.0


def get_tte(timestamp: int, day: int) -> float:
    day_offset = TTE_START - day
    intraday   = timestamp / TICKS_PER_DAY
    return max(0.0, day_offset - intraday)


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


class Trader:

    def bid(self):
        return 15

    def _position(self, state: TradingState, product: str) -> int:
        return state.position.get(product, 0)

    def _mid(self, od: OrderDepth) -> Optional[float]:
        if od is None:
            return None
        best_bid = max(od.buy_orders)  if od.buy_orders  else None
        best_ask = min(od.sell_orders) if od.sell_orders else None
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            return float(best_bid)
        elif best_ask is not None:
            return float(best_ask)
        return None

    def run(self, state: TradingState):
        # ── restore state ────────────────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        day: int       = saved.get("day", 0)
        last_ts: int   = saved.get("last_ts", 0)
        sigma_ema: float = saved.get("sigma_ema", SIGMA_FALLBACK)

        # Detect day rollover
        if state.timestamp < last_ts:
            day += 1

        result: Dict[str, List[Order]] = {}

        # ── get underlying price ─────────────────────────────────────────────
        underlying_od = state.order_depths.get(UNDERLYING)
        S = self._mid(underlying_od)

        if S is None:
            trader_data = json.dumps({
                "day": day, "last_ts": state.timestamp, "sigma_ema": sigma_ema
            })
            return result, 0, trader_data

        # ── time to expiry ───────────────────────────────────────────────────
        T = get_tte(state.timestamp, day)

        # ── calibrate implied vol from ATM voucher ───────────────────────────
        cal_od = state.order_depths.get(IV_CALIBRATION_VOUCHER)
        cal_mid = self._mid(cal_od)

        if cal_mid is not None and T > 0:
            iv = implied_vol(S, IV_CALIBRATION_STRIKE, T, cal_mid)
            if iv is not None:
                sigma_ema = _ema(sigma_ema, iv, IV_EMA_ALPHA)

        sigma = sigma_ema  # live calibrated vol

        # ── trade each voucher ───────────────────────────────────────────────
        for product, K in STRIKES.items():
            od = state.order_depths.get(product)
            if od is None:
                continue

            best_bid = max(od.buy_orders)  if od.buy_orders  else None
            best_ask = min(od.sell_orders) if od.sell_orders else None

            if best_bid is None and best_ask is None:
                continue

            fair = black_scholes_call(S, K, T, sigma)

            pos      = self._position(state, product)
            buy_cap  = POS_LIMIT_VOUCHER - pos
            sell_cap = POS_LIMIT_VOUCHER + pos
            orders: List[Order] = []

            # Buy if market ask is below fair value by more than EDGE
            if best_ask is not None and best_ask < fair - EDGE:
                vol = -od.sell_orders[best_ask]
                qty = min(vol, buy_cap, QUOTE_SIZE)
                if qty > 0:
                    orders.append(Order(product, best_ask, qty))

            # Sell if market bid is above fair value by more than EDGE
            if best_bid is not None and best_bid > fair + EDGE:
                vol = od.buy_orders[best_bid]
                qty = min(vol, sell_cap, QUOTE_SIZE)
                if qty > 0:
                    orders.append(Order(product, best_bid, -qty))

            if orders:
                result[product] = orders

            print(
                f"t={state.timestamp} {product} K={K} S={S:.1f} "
                f"T={T:.3f} σ={sigma:.4f} fair={fair:.2f} "
                f"bid={best_bid} ask={best_ask} pos={pos}"
            )

        trader_data = json.dumps({
            "day": day,
            "last_ts": state.timestamp,
            "sigma_ema": sigma_ema
        })
        return result, 0, trader_data
