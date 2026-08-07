"""Shared synthetic price-series builders for the offline test suites."""
import numpy as np
import pandas as pd


def pullback_hist(seed: int, n: int = 260) -> pd.DataFrame:
    """Uptrending series with a recent quiet pullback toward a pivot low —
    the exact shape the screener is built to find."""
    rng = np.random.default_rng(seed)
    base = np.linspace(50, 100, n) + rng.normal(0, 0.4, n)
    base[-12:] -= np.linspace(0, 7, 12)          # pullback
    base[-3] = base[-4] - 1.2                    # local pivot low
    base[-2] = base[-3] + 0.8
    base[-1] = base[-2] + 0.3
    close = pd.Series(base)
    high = close + 1.0
    low = close - 1.0
    vol = pd.Series(np.full(n, 60_000_000.0))
    vol.iloc[-10:] = 40_000_000.0                # quiet pullback volume
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=n)
    return pd.DataFrame({"Open": close.values, "High": high.values,
                         "Low": low.values, "Close": close.values,
                         "Volume": vol.values}, index=idx)


def weak_hist(seed: int, n: int = 260) -> pd.DataFrame:
    """Clear downtrend: finishes far below its own 50-day average, so it
    counts as 'weak' in the market-breadth measure by construction."""
    rng = np.random.default_rng(1000 + seed)
    base = np.linspace(120, 60, n) + rng.normal(0, 0.3, n)
    close = pd.Series(base)
    high = close + 0.8
    low = close - 0.8
    vol = pd.Series(np.full(n, 50_000_000.0))
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=n)
    return pd.DataFrame({"Open": close.values, "High": high.values,
                         "Low": low.values, "Close": close.values,
                         "Volume": vol.values}, index=idx)
