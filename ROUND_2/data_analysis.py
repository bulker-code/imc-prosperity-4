import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Load the price data - adjust filename as needed
df = pd.read_csv("prices_round_2_day_0.csv", sep=";")

# The columns are: day, timestamp, product, bid_price_1..3, ask_price_1..3, mid_price, profit_and_loss
products = df["product"].unique()
print("Products found:", products)

fig, axes = plt.subplots(len(products), 2, figsize=(16, 5 * len(products)))

for i, product in enumerate(products):
    data = df[df["product"] == product].copy()
    data = data.sort_values("timestamp")

    mid = data["mid_price"]
    timestamps = data["timestamp"]
    # Filter out bad values
    valid = mid >= 5000
    mid = mid[valid]
    timestamps = timestamps[valid]

    # --- Left plot: raw mid price + rolling mean ---
    ax1 = axes[i, 0]
    ax1.plot(timestamps, mid, label="Mid Price", alpha=0.7, linewidth=0.8)

    for window in [20, 50, 200]:
        ax1.plot(timestamps, mid.rolling(window).mean(),
                 label=f"SMA {window}", linewidth=1.2)

    ax1.set_title(f"{product} — Price & Moving Averages")
    ax1.set_xlabel("Timestamp")
    ax1.set_ylabel("Price")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # --- Right plot: returns & volatility (z-score) ---
    ax2 = axes[i, 1]
    returns = mid.diff()
    rolling_mean = mid.rolling(20).mean()
    rolling_std = mid.rolling(20).std()
    zscore = (mid - rolling_mean) / rolling_std

    ax2.plot(timestamps, zscore, label="Z-Score (20)", color="purple", linewidth=0.8)
    ax2.axhline(2, color="red", linestyle="--", alpha=0.5, label="+2σ")
    ax2.axhline(-2, color="green", linestyle="--", alpha=0.5, label="-2σ")
    ax2.axhline(0, color="gray", linestyle="-", alpha=0.3)
    ax2.set_title(f"{product} — Z-Score (mean reversion signal)")
    ax2.set_xlabel("Timestamp")
    ax2.set_ylabel("Z-Score")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # Print some stats
    print(f"\n{product}:")
    print(f"  Price range: {mid.min():.1f} – {mid.max():.1f}")
    print(f"  Mean: {mid.mean():.2f}, Std: {mid.std():.2f}")
    print(f"  % time z-score > 2: {(zscore.abs() > 2).mean()*100:.1f}%")

plt.tight_layout()
plt.savefig("price_analysis.png", dpi=150)
plt.show()
