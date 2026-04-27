import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parent

INITIAL_CAPITAL = 50_000.0
N_SIMULATIONS = 10_000
N_TRADES_PER_SIM = 67
RANDOM_SEED = 42

np.random.seed(RANDOM_SEED)

df = pd.read_csv(ROOT / "trade_details.csv")
pnl_series = df["PnL (Gross)"].values.astype(float)

wins = pnl_series[pnl_series > 0]
losses = pnl_series[pnl_series < 0]

historical_win_rate = len(wins) / len(pnl_series)
historical_avg_win = wins.mean()
historical_avg_loss = losses.mean()
historical_avg_win_loss_ratio = abs(historical_avg_win / historical_avg_loss)

print("=" * 60)
print("MONTE CARLO SIMULATION — XAUUSD Session Liquidity Sweep")
print("=" * 60)
print(f"Historical trades: {len(pnl_series)}")
print(f"  Wins:  {len(wins):>3d}  ({historical_win_rate*100:.1f}%)")
print(f"  Losses: {len(losses):>3d}  ({(1-historical_win_rate)*100:.1f}%)")
print(f"  Avg Win:      ${historical_avg_win:+,.2f}")
print(f"  Avg Loss:     ${historical_avg_loss:+,.2f}")
print(f"  Win/Loss Ratio: {historical_avg_win_loss_ratio:.2f}:1")
print(f"  Starting Capital: ${INITIAL_CAPITAL:,.0f}")
print("-" * 60)
print(f"Simulations: {N_SIMULATIONS:,}")
print(f"Trades per simulation: {N_TRADES_PER_SIM}")
print("=" * 60)

final_equities = np.zeros(N_SIMULATIONS)
max_drawdowns = np.zeros(N_SIMULATIONS)
profit_factors = np.zeros(N_SIMULATIONS)
equity_curves_sample = np.zeros((50, N_TRADES_PER_SIM + 1))

for i in range(N_SIMULATIONS):
    sampled = np.random.choice(pnl_series, size=N_TRADES_PER_SIM, replace=True)
    equity = np.concatenate(([INITIAL_CAPITAL], INITIAL_CAPITAL + np.cumsum(sampled)))
    
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_drawdowns[i] = np.min(dd)
    
    final_equities[i] = equity[-1]
    
    gross_profit = sampled[sampled > 0].sum()
    gross_loss = abs(sampled[sampled < 0].sum())
    profit_factors[i] = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    
    if i < 50:
        equity_curves_sample[i] = equity

final_returns = (final_equities - INITIAL_CAPITAL) / INITIAL_CAPITAL

# ---------- statistics ----------
median_final = np.median(final_equities)
median_return = np.median(final_returns)
median_dd = np.median(max_drawdowns)
p5_equity = np.percentile(final_equities, 5)
p95_equity = np.percentile(final_equities, 95)
p5_return = np.percentile(final_returns, 5)
p95_return = np.percentile(final_returns, 95)
p5_dd = np.percentile(max_drawdowns, 5)
p95_dd = np.percentile(max_drawdowns, 95)
mean_final = np.mean(final_equities)
mean_dd = np.mean(max_drawdowns)
prob_profit = np.mean(final_equities > INITIAL_CAPITAL)
prob_loss = np.mean(final_equities < INITIAL_CAPITAL)

print("\nSIMULATION RESULTS")
print("-" * 60)
print(f"  Median Final Equity:    ${median_final:,.0f}  ({median_return*100:+.1f}%)")
print(f"  5th Percentile Equity:  ${p5_equity:,.0f}    ({p5_return*100:+.1f}%)")
print(f"  95th Percentile Equity: ${p95_equity:,.0f}   ({p95_return*100:+.1f}%)")
print(f"  Mean Final Equity:      ${mean_final:,.0f}")
print(f"  Probability of Profit:  {prob_profit*100:.1f}%")
print(f"  Probability of Loss:    {prob_loss*100:.1f}%")
print("-" * 60)
print(f"  Median Max Drawdown:    {median_dd*100:.2f}%")
print(f"  Mean Max Drawdown:      {mean_dd*100:.2f}%")
print(f"  5th Pctl Drawdown:      {p5_dd*100:.2f}%")
print(f"  95th Pctl Drawdown:     {p95_dd*100:.2f}%")
print("=" * 60)

# ---------- plot ----------
fig, axes = plt.subplots(2, 2, figsize=(16, 11))
fig.suptitle(
    "XAUUSD Session Liquidity Sweep — Monte Carlo Simulation\n"
    f"({N_SIMULATIONS:,} runs × {N_TRADES_PER_SIM} trades, bootstrap from 67 historical trades)",
    fontsize=14, fontweight="bold",
)

# --- Panel 1: Distribution of Final Returns ---
ax1 = axes[0, 0]
ax1.hist(final_returns * 100, bins=80, color="steelblue", edgecolor="white", alpha=0.85)
ax1.axvline(0, color="red", linestyle="--", linewidth=1.2, label="Breakeven")
ax1.axvline(median_return * 100, color="darkorange", linestyle="--", linewidth=1.5,
            label=f"Median: {median_return*100:+.1f}%")
ax1.axvline(p5_return * 100, color="gray", linestyle=":", linewidth=1.2,
            label=f"5th Pctl: {p5_return*100:+.1f}%")
ax1.axvline(p95_return * 100, color="gray", linestyle=":", linewidth=1.2,
            label=f"95th Pctl: {p95_return*100:+.1f}%")
ax1.set_xlabel("Final Return (%)")
ax1.set_ylabel("Frequency")
ax1.set_title(f"Final Return Distribution (Prob. Profit = {prob_profit*100:.1f}%)")
ax1.legend(fontsize=8)

# --- Panel 2: Max Drawdown Distribution ---
ax2 = axes[0, 1]
ax2.hist(max_drawdowns * 100, bins=80, color="indianred", edgecolor="white", alpha=0.85)
ax2.axvline(median_dd * 100, color="darkorange", linestyle="--", linewidth=1.5,
            label=f"Median DD: {median_dd*100:.2f}%")
ax2.axvline(p95_dd * 100, color="gray", linestyle=":", linewidth=1.2,
            label=f"95th Pctl DD: {p95_dd*100:.2f}%")
ax2.set_xlabel("Max Drawdown (%)")
ax2.set_ylabel("Frequency")
ax2.set_title(f"Max Drawdown Distribution (Mean = {mean_dd*100:.2f}%)")
ax2.legend(fontsize=8)

# --- Panel 3: Sample Equity Curves ---
ax3 = axes[1, 0]
x_range = np.arange(N_TRADES_PER_SIM + 1)
for j in range(min(50, N_SIMULATIONS)):
    alpha_val = 0.12
    color = "steelblue" if equity_curves_sample[j][-1] > INITIAL_CAPITAL else "indianred"
    ax3.plot(x_range, equity_curves_sample[j], color=color, alpha=alpha_val, linewidth=0.8)
ax3.axhline(INITIAL_CAPITAL, color="black", linestyle="--", linewidth=0.8, label=f"${INITIAL_CAPITAL:,.0f} start")
ax3.set_xlabel("Trade Number")
ax3.set_ylabel("Equity ($)")
ax3.set_title("50 Random Equity Curves (blue = profitable, red = losing)")
ax3.legend(fontsize=8)

# --- Panel 4: Equity fan chart (percentiles over trade sequence) ---
ax4 = axes[1, 1]
all_curves = np.zeros((1000, N_TRADES_PER_SIM + 1))
for i in range(1000):
    sampled = np.random.choice(pnl_series, size=N_TRADES_PER_SIM, replace=True)
    all_curves[i] = np.concatenate(([INITIAL_CAPITAL], INITIAL_CAPITAL + np.cumsum(sampled)))

pcts = [5, 10, 25, 50, 75, 90, 95]
pct_curves = {p: np.percentile(all_curves, p, axis=0) for p in pcts}

ax4.fill_between(x_range, pct_curves[5], pct_curves[95], alpha=0.12, color="steelblue", label="5th–95th")
ax4.fill_between(x_range, pct_curves[10], pct_curves[90], alpha=0.15, color="steelblue", label="10th–90th")
ax4.fill_between(x_range, pct_curves[25], pct_curves[75], alpha=0.20, color="steelblue", label="25th–75th")
ax4.plot(x_range, pct_curves[50], color="darkorange", linewidth=1.8, label="Median")
ax4.axhline(INITIAL_CAPITAL, color="black", linestyle="--", linewidth=0.8)
ax4.set_xlabel("Trade Number")
ax4.set_ylabel("Equity ($)")
ax4.set_title("Equity Curve Fan Chart (Percentile Bands)")
ax4.legend(fontsize=8)

plt.tight_layout()
out_path = ROOT / "monte_carlo_results.png"
fig.savefig(out_path, dpi=150, bbox_inches="tight")
plt.close(fig)

print(f"\nChart saved to: {out_path}")
print("Done.")
