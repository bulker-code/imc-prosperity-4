"""
IMC Prosperity 4 – Round 3 | VELVETFRUIT EXTRACT VOUCHERS (VEV)
================================================================
Strategy: Black-Scholes mispricing with multi-voucher IV calibration.

IV Calibration
---------------
Each tick we solve for implied vol from EVERY voucher that has a valid
market price. We take the median implied vol across all vouchers as our
best estimate of the true market sigma. This is more robust than using
a single voucher because:

- Deep ITM options (VEV_4000, VEV_4500) have low vega — their prices
  are dominated by intrinsic value, not vol. Their IV estimates are noisy.
- Deep OTM options (VEV_6000, VEV_6500) have very low prices (0-1 tick)
  making IV extraction unreliable.
- Near ATM options (VEV_5000-5400) have the highest vega and give the
  most accurate IV estimates.

We weight the IV estimates by vega (sensitivity to vol) so near-ATM
options dominate the calibration.

We then use the calibrated sigma to price all vouchers and trade any
that deviate from fair value by more than EDGE ticks.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Optional
import json
import math

# ── parameters ───────────────────────────────────────────────────────────────
UNDERLYING         = "VELVETFRUIT_EXTRACT"
POS_LIMIT_VOUCHER  = 300

# Time to expiry
TTE_START          = 5.0
TICKS_PER_DAY      = 1_000_000

# IV calibration
SIGMA_FALLBACK     = 0.02
SIGMA_MIN          = 0.001
SIGMA_MAX          = 0.5
IV_EMA_ALPHA       = 0.15   # how fast sigma EMA reacts to new estimates
SIGMA_WARMUP_TICKS = 200  # wait 200 ticks before placing any orders



# Only use vouchers within this moneyness range for IV calibration
# (avoids noisy deep ITM/OTM estimates)
MIN_MONEYNESS      = 0.95   # S/K >= this (not too deep OTM)
MAX_MONEYNESS      = 1.10   # S/K <= this (not too deep ITM)

# Trading
EDGE               = 2.0    # ticks of mispricing to trigger a trade
QUOTE_SIZE         = 20     # units per order

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
# Smile: add extra vol for OTM options
# Positive skew (OTM calls trade richer)
SMILE_ADJUSTMENT = {
    "VEV_4000": -0.299,  # exclude from trading, deep ITM
    "VEV_4500": -0.299,  # exclude from trading, deep ITM
    "VEV_5000":  0.000,
    "VEV_5100":  0.000,
    "VEV_5200":  0.000,
    "VEV_5300":  0.000,
    "VEV_5400": -0.001,
    "VEV_5500":  0.000,
    "VEV_6000": +0.0093,
    "VEV_6500": +0.0214,
}

"""
SMILE_ADJUSTMENT = {
   "VEV_4000": +0.0363,
    "VEV_4500": +0.0142,
    "VEV_5000": -0.0001,
    "VEV_5100": -0.0003,
    "VEV_5200": +0.0002,
    "VEV_5300": +0.0004,
    "VEV_5400": -0.0009,
    "VEV_5500": +0.0003,
    "VEV_6000": +0.0093,
    "VEV_6500": +0.0214,
}
"""
# Then when pricing:

# ─────────────────────────────────────────────────────────────────────────────


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


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


def vega(S: float, K: float, T: float, sigma: float) -> float:
    """Sensitivity of option price to sigma — highest near ATM."""
    if T <= 0 or sigma <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
        return S * norm_pdf(d1) * math.sqrt(T)
    except (ValueError, ZeroDivisionError):
        return 0.0


def implied_vol_bisection(S: float, K: float, T: float, market_price: float,
                          tol: float = 0.01, max_iter: int = 50) -> Optional[float]:
    """Bisection search for implied volatility."""
    if T <= 0:
        return None
    intrinsic = max(S - K, 0.0)
    if market_price <= intrinsic:
        return None

    price_low  = black_scholes_call(S, K, T, SIGMA_MIN)
    price_high = black_scholes_call(S, K, T, SIGMA_MAX)

    if market_price < price_low or market_price > price_high:
        return None

    lo, hi = SIGMA_MIN, SIGMA_MAX
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        p   = black_scholes_call(S, K, T, mid)
        if abs(p - market_price) < tol:
            return mid
        if p < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def calibrate_sigma(S: float, T: float,
                    voucher_mids: Dict[str, float]) -> Optional[float]:
    """
    Solve for implied vol from all available voucher prices.
    Returns vega-weighted average IV across near-ATM vouchers.
    Returns None if no valid estimates found.
    """
    iv_estimates = []  # (iv, weight) pairs

    for product, K in STRIKES.items():
        if product not in voucher_mids:
            continue

        # Moneyness filter — only use near-ATM vouchers
        moneyness = S / K
        if moneyness < MIN_MONEYNESS or moneyness > MAX_MONEYNESS:
            continue

        market_price = voucher_mids[product]
        if market_price <= 0:
            continue

        iv = implied_vol_bisection(S, K, T, market_price)
        if iv is None:
            continue

        # Weight by vega at this IV estimate
        w = vega(S, K, T, iv)
        if w > 0:
            iv_estimates.append((iv, w))

    if not iv_estimates:
        return None

    # Vega-weighted average IV
    total_weight = sum(w for _, w in iv_estimates)
    if total_weight == 0:
        return None

    return sum(iv * w for iv, w in iv_estimates) / total_weight


def get_tte(timestamp: int, day: int) -> float:
    return max(0.0, (TTE_START - day) - timestamp / TICKS_PER_DAY)


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return alpha * new_val + (1.0 - alpha) * prev


def _mid(od: OrderDepth) -> Optional[float]:
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


class Trader:

    def bid(self):
        return 15

    def _position(self, state: TradingState, product: str) -> int:
        return state.position.get(product, 0)

    def run(self, state: TradingState):
        # ── restore state ────────────────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        day: int         = saved.get("day", 0)
        last_ts: int     = saved.get("last_ts", 0)
        sigma_ema: float = saved.get("sigma_ema", SIGMA_FALLBACK)

        if state.timestamp < last_ts:
            day += 1

        result: Dict[str, List[Order]] = {}

        # ── underlying price ─────────────────────────────────────────────────
        S = _mid(state.order_depths.get(UNDERLYING))
        if S is None:
            return result, 0, json.dumps({
                "day": day, "last_ts": state.timestamp, "sigma_ema": sigma_ema
            })

        T = get_tte(state.timestamp, day)

        # ── collect all voucher mid prices ───────────────────────────────────
        voucher_mids: Dict[str, float] = {}
        for product in STRIKES:
            m = _mid(state.order_depths.get(product))
            if m is not None:
                voucher_mids[product] = m

        # ── calibrate sigma from all near-ATM vouchers ───────────────────────
        if T > 0:
            new_sigma = calibrate_sigma(S, T, voucher_mids)
            if new_sigma is not None:
                sigma_ema = _ema(sigma_ema, new_sigma, IV_EMA_ALPHA)

        sigma = sigma_ema

        tick_count = saved.get("tick_count", 0) + 1

        # ── trade each voucher ───────────────────────────────────────────────

        for product, K in STRIKES.items():
            od = state.order_depths.get(product)
            if od is None:
                continue

            adjusted_sigma = sigma + SMILE_ADJUSTMENT[product]  # ← move here
            fair = black_scholes_call(S, K, T, adjusted_sigma)

            best_bid = max(od.buy_orders)  if od.buy_orders  else None
            best_ask = min(od.sell_orders) if od.sell_orders else None
            if best_bid is None and best_ask is None:
                continue

            pos      = self._position(state, product)
            buy_cap  = POS_LIMIT_VOUCHER - pos
            sell_cap = POS_LIMIT_VOUCHER + pos
            orders: List[Order] = []

            if tick_count >= SIGMA_WARMUP_TICKS:  # ← >= not 
                if best_ask is not None and best_ask < fair - EDGE:
                    vol = -od.sell_orders[best_ask]
                    qty = min(vol, buy_cap, QUOTE_SIZE)
                    if qty > 0:
                        orders.append(Order(product, best_ask, qty))

                if best_bid is not None and best_bid > fair + EDGE:
                    vol = od.buy_orders[best_bid]
                    qty = min(vol, sell_cap, QUOTE_SIZE)
                    if qty > 0:
                        orders.append(Order(product, best_bid, -qty))

            if orders:
                result[product] = orders

            trader_data = json.dumps({
            "day": day,
            "last_ts": state.timestamp,
            "sigma_ema": sigma_ema,
            "tick_count": tick_count,   # ← persist this
            })
            print(
                f"t={state.timestamp} {product} K={K} S={S:.1f} "
                f"T={T:.3f} σ={sigma:.4f} fair={fair:.2f} "
                f"bid={best_bid} ask={best_ask} pos={pos}"
            )
        return result, 0, trader_data
