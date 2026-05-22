import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =========================
# LOAD DATA
# =========================
def load_data(file_path):
    df = pd.read_csv(file_path, sep=";")
    return df

# =========================
# PREP DATA
# =========================
def prepare_data(df, product_name):

    df = df[df["product"] == product_name].copy()

    # Best bid / ask
    df["best_bid"] = df["bid_price_1"]
    df["best_ask"] = df["ask_price_1"]

    # Mid price
    df["mid_price"] = (df["best_bid"] + df["best_ask"]) / 2

    return df


# =========================
# TREND SIGNAL
# =========================
def compute_trend(df):

    # Price change
    df["price_change"] = df["mid_price"].diff()

    # Smoothed trend (EMA)
    df["trend"] = df["price_change"].ewm(span=20).mean()

    return df


# =========================
# PLOTTING
# =========================
def plot_prices(df, start=None, end=None):

    if start is not None and end is not None:
        df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]

    plt.figure(figsize=(12,6))

    plt.plot(df["timestamp"], df["best_bid"], label="Best Bid")
    plt.plot(df["timestamp"], df["best_ask"], label="Best Ask")
    plt.plot(df["timestamp"], df["mid_price"], label="Mid Price", linewidth=2)

    plt.legend()
    plt.title("Price Levels")
    plt.xlabel("Timestamp")
    plt.ylabel("Price")
    plt.grid()

    plt.show()


def plot_trend(df, start=None, end=None):

    if start is not None and end is not None:
        df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]

    plt.figure(figsize=(12,4))

    plt.plot(df["timestamp"], df["trend"], label="Trend Signal")
    plt.axhline(0, linestyle="--")

    plt.title("Trend Signal (EMA of price changes)")
    plt.xlabel("Timestamp")
    plt.ylabel("Trend")

    plt.legend()
    plt.grid()

    plt.show()


# =========================
# FIND TREND REGIMES
# =========================
def find_trend_periods(df, threshold=1.5):

    trending = df[np.abs(df["trend"]) > threshold]

    print("\nStrong trend periods:")
    print(trending[["timestamp", "mid_price", "trend"]].head(20))

    return trending


# =========================
# MAIN
# =========================
if __name__ == "__main__":

    FILE = "prices_round_3_day_2.csv"
    PRODUCT = "VEV_5500"  # change to HYDROGEL_PACK if needed

    df = load_data(FILE)
    print(df.columns)
    df = prepare_data(df, PRODUCT)
    df = compute_trend(df)

    print(df.head())

    # FULL PLOT
    plot_prices(df)
    plot_trend(df)

    # ZOOM into problem region (adjust this!!)
    plot_prices(df, start=50000, end=60000)
    plot_trend(df, start=50000, end=60000)

    # Find strong trends
    find_trend_periods(df)
