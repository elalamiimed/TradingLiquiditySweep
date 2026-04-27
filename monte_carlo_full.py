import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)

# ── load historical trade data ──
df = pd.read_csv(ROOT / "trade_details.csv")
raw_pnl = df["PnL (Gross)"].values.astype(float)

wins_raw = raw_pnl[raw_pnl > 0]
losses_raw = raw_pnl[raw_pnl < 0]

hist_wr = len(wins_raw) / len(raw_pnl)
hist_avg_win = wins_raw.mean()
hist_avg_loss = losses_raw.mean()

INITIAL_CAPITAL = 50_000.0
N_TRADES_YEAR = 67
N_SIM = 5_000

# ── risk levels (baseline = 2%, scale PnL proportionally) ──
RISK_LEVELS = [
    ("0.5% Risk\nper trade", 0.25),
    ("1% Risk\nper trade", 0.50),
    ("2% Risk (baseline)\nper trade", 1.00),
    ("3% Risk\nper trade", 1.50),
]
HORIZONS = [
    ("6 months\n(~34 trades)", 34),
    ("1 year\n(67 trades)", 67),
    ("2 years\n(134 trades)", 134),
    ("5 years\n(335 trades)", 335),
]
WR_SCENARIOS = [
    ("WR=30%", 0.30),
    ("WR=35%", 0.35),
    ("WR=40%", 0.40),
    ("WR=45%", 0.45),
    ("WR=50.7%\n(observed)", hist_wr),
]

print("=" * 70)
print("  FULL MONTE CARLO SIMULATION — XAUUSD Session Liquidity Sweep")
print("=" * 70)
print(f"  Historical: {len(raw_pnl)} trades, WR={hist_wr*100:.1f}%, "
      f"AvgW=${hist_avg_win:+,.0f}, AvgL=${hist_avg_loss:+,.0f}")
print(f"  Starting Capital: ${INITIAL_CAPITAL:,.0f}")
print(f"  Simulations per scenario: {N_SIM:,}")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════
#  SIM 1: Multi-risk × multi-horizon
# ═══════════════════════════════════════════════════════════════
print("\n[1/5] Running multi-risk × multi-horizon simulation ...")
sim1 = {}  # sim1[(r_label, h_label)] = {"ret": ..., "dd": ...}
for r_label, scale in RISK_LEVELS:
    pnl_s = raw_pnl * scale
    for h_label, n_trades in HORIZONS:
        rets = np.zeros(N_SIM)
        dds = np.zeros(N_SIM)
        for i in range(N_SIM):
            s = np.random.choice(pnl_s, size=n_trades, replace=True)
            eq = np.concatenate(([INITIAL_CAPITAL], INITIAL_CAPITAL + np.cumsum(s)))
            peak = np.maximum.accumulate(eq)
            dds[i] = np.min((eq - peak) / peak)
            rets[i] = (eq[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
        sim1[(r_label, h_label)] = {"ret": rets, "dd": dds}

# ═══════════════════════════════════════════════════════════════
#  SIM 2: Forward return cone (2% risk, up to 5yr)
# ═══════════════════════════════════════════════════════════════
print("[2/5] Building 5-year return cone ...")
CONE_SIM = 2_000
CONE_N = HORIZONS[-1][1]  # 335
cone_eq = np.zeros((CONE_SIM, CONE_N + 1))
for i in range(CONE_SIM):
    s = np.random.choice(raw_pnl, size=CONE_N, replace=True)
    cone_eq[i] = np.concatenate(([INITIAL_CAPITAL], INITIAL_CAPITAL + np.cumsum(s)))
cone_bands = {}
for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
    cone_bands[p] = np.percentile(cone_eq, p, axis=0)

# ═══════════════════════════════════════════════════════════════
#  SIM 3: Win-rate sensitivity (2% risk, 1yr)
# ═══════════════════════════════════════════════════════════════
print("[3/5] Running win-rate sensitivity ...")
sim3 = {}
for wr_label, wr in WR_SCENARIOS:
    rets = np.zeros(N_SIM)
    dds = np.zeros(N_SIM)
    for i in range(N_SIM):
        pnl_sim = []
        for _ in range(N_TRADES_YEAR):
            pnl_sim.append(np.random.choice(wins_raw) if np.random.random() < wr
                           else np.random.choice(losses_raw))
        pnl_sim = np.array(pnl_sim)
        eq = np.concatenate(([INITIAL_CAPITAL], INITIAL_CAPITAL + np.cumsum(pnl_sim)))
        peak = np.maximum.accumulate(eq)
        dds[i] = np.min((eq - peak) / peak)
        rets[i] = (eq[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    sim3[wr_label] = {"ret": rets, "dd": dds,
                       "prob_profit": np.mean(rets > 0),
                       "median_ret": np.median(rets),
                       "median_dd": np.median(dds)}

# ═══════════════════════════════════════════════════════════════
#  SIM 4: Drawdown depth × duration (2% risk, 1yr)
# ═══════════════════════════════════════════════════════════════
print("[4/5] Running drawdown depth vs duration ...")
DD_SIM = 1_000
all_dd_depths = []
all_dd_durations = []
for i in range(DD_SIM):
    s = np.random.choice(raw_pnl, size=N_TRADES_YEAR, replace=True)
    eq = np.concatenate(([INITIAL_CAPITAL], INITIAL_CAPITAL + np.cumsum(s)))
    peak = np.maximum.accumulate(eq)
    in_dd = False
    dd_start = 0
    worst = 0.0
    for t in range(1, len(eq)):
        cur = (eq[t] - peak[t]) / peak[t]
        if cur < -0.005:
            if not in_dd:
                in_dd = True
                dd_start = t
                worst = cur
            else:
                worst = min(worst, cur)
        else:
            if in_dd:
                dur = t - dd_start
                if dur >= 1:
                    all_dd_durations.append(dur)
                    all_dd_depths.append(worst * 100)
                in_dd = False
    if in_dd:
        dur = len(eq) - 1 - dd_start
        if dur >= 1:
            all_dd_durations.append(dur)
            all_dd_depths.append(worst * 100)

all_dd_durations = np.array(all_dd_durations)
all_dd_depths = np.array(all_dd_depths)

# ═══════════════════════════════════════════════════════════════
#  SIM 5: Consecutive loss streak distribution
# ═══════════════════════════════════════════════════════════════
print("[5/5] Computing loss streak distribution ...")
STREAK_SIM = 10_000
loss_streaks_all = []
for i in range(STREAK_SIM):
    s = np.random.choice(raw_pnl, size=N_TRADES_YEAR, replace=True)
    cur_streak = 0
    max_streak = 0
    for pnl_val in s:
        if pnl_val < 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0
    loss_streaks_all.append(max_streak)
loss_streaks_all = np.array(loss_streaks_all)

# ═══════════════════════════════════════════════════════════════
#  PRINT SUMMARY TABLE
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("  SUMMARY: Median Return  |  Prob(Profit)  |  Median Max DD  |  5th-%ile DD")
print("=" * 90)
for r_label, scale in RISK_LEVELS:
    for h_label, n_trades in HORIZONS:
        d = sim1[(r_label, h_label)]
        med_ret = np.median(d["ret"]) * 100
        prob = np.mean(d["ret"] > 0) * 100
        med_dd = np.median(d["dd"]) * 100
        p5_dd = np.percentile(d["dd"], 5) * 100
        print(f"  {r_label.replace(chr(10),' | '):35s} × {h_label.replace(chr(10),' '):22s}  "
              f"Ret={med_ret:+6.1f}%  P(win)={prob:5.1f}%  DD_med={med_dd:5.1f}%  DD_p5={p5_dd:5.1f}%")

print("\n  WIN-RATE SENSITIVITY (2% risk, 1 year):")
for wr_label, wr in WR_SCENARIOS:
    d = sim3[wr_label]
    print(f"    {wr_label.replace(chr(10),' '):20s}  MedRet={d['median_ret']*100:+6.1f}%  "
          f"P(win)={d['prob_profit']*100:5.1f}%  MedDD={d['median_dd']*100:5.1f}%")

streak_pcts = [50, 75, 90, 95, 99]
print(f"\n  LOSS STREAK (observed WR={hist_wr*100:.1f}%, 1yr):")
for p in streak_pcts:
    print(f"    {p}th percentile: {int(np.percentile(loss_streaks_all, p))} consecutive losses")

print("=" * 90)

# ═══════════════════════════════════════════════════════════════
#  PLOTTING — 4×3 grid = 12 panels
# ═══════════════════════════════════════════════════════════════
print("\nGenerating comprehensive chart ...")
COLORS = ["#2196F3", "#4CAF50", "#FF9800", "#F44336"]

fig, axes = plt.subplots(4, 4, figsize=(24, 20))
fig.suptitle(
    "XAUUSD Session Liquidity Sweep — Full Monte Carlo Simulation\n"
    f"({N_SIM:,} simulations per scenario, bootstrapped from {len(raw_pnl)} historical trades | "
    f"WR={hist_wr*100:.1f}%, W/L={abs(hist_avg_win/hist_avg_loss):.1f}:1)",
    fontsize=16, fontweight="bold", y=0.98)

# ── ROW 0: Return distributions at 4 risk levels (1yr horizon) ──
for idx, (r_label, scale) in enumerate(RISK_LEVELS):
    ax = axes[0, idx]
    h_label = "1 year\n(67 trades)"
    d = sim1[(r_label, h_label)]
    rets_pct = d["ret"] * 100
    ax.hist(rets_pct, bins=70, color=COLORS[idx], edgecolor="white", alpha=0.82, density=True)
    med = np.median(rets_pct)
    p5 = np.percentile(rets_pct, 5)
    p95 = np.percentile(rets_pct, 95)
    pp = np.mean(d["ret"] > 0) * 100
    ax.axvline(0, color="black", linestyle="--", linewidth=1.0)
    ax.axvline(med, color="darkorange", linestyle="-", linewidth=1.8, label=f"Med={med:+.0f}%")
    ax.axvline(p5, color="gray", linestyle=":", linewidth=1.0)
    ax.axvline(p95, color="gray", linestyle=":", linewidth=1.0)
    ax.set_title(f"{r_label}\nP(profit)={pp:.0f}% | 5%→95%: [{p5:+.0f}%, {p95:+.0f}%]",
                 fontsize=10, fontweight="bold")
    ax.set_xlabel("Return (%)"); ax.set_ylabel("Density")
    ax.legend(fontsize=7, loc="upper left")

# ── ROW 1: Drawdown distributions at 4 risk levels (1yr horizon) ──
for idx, (r_label, scale) in enumerate(RISK_LEVELS):
    ax = axes[1, idx]
    h_label = "1 year\n(67 trades)"
    d = sim1[(r_label, h_label)]
    dds_pct = d["dd"] * 100
    ax.hist(dds_pct, bins=70, color=COLORS[idx], edgecolor="white", alpha=0.82, density=True)
    med = np.median(dds_pct)
    p5 = np.percentile(dds_pct, 5)
    p95 = np.percentile(dds_pct, 95)
    ax.axvline(med, color="darkorange", linestyle="-", linewidth=1.8, label=f"Med={med:.1f}%")
    ax.axvline(p5, color="gray", linestyle=":", linewidth=1.0)
    ax.axvline(p95, color="gray", linestyle=":", linewidth=1.0)
    ax.set_title(f"{r_label}\nMed DD={med:.1f}% | 5% worst: {p5:.1f}% | 95% best: {p95:.1f}%",
                 fontsize=10, fontweight="bold")
    ax.set_xlabel("Max Drawdown (%)"); ax.set_ylabel("Density")
    ax.legend(fontsize=7, loc="upper right")

# ── ROW 2: Return cone (col 0,1) | Probability of profit vs horizon (col 2) | Risk level comparison (col 3) ──
# Panel: 5-year return cone (2% risk)
ax_cone = axes[2, 0]
x_cone = np.arange(CONE_N + 1)
ax_cone.fill_between(x_cone, cone_bands[1], cone_bands[99], alpha=0.08, color="steelblue")
ax_cone.fill_between(x_cone, cone_bands[5], cone_bands[95], alpha=0.12, color="steelblue",
                     label="5–95%")
ax_cone.fill_between(x_cone, cone_bands[10], cone_bands[90], alpha=0.15, color="steelblue",
                     label="10–90%")
ax_cone.fill_between(x_cone, cone_bands[25], cone_bands[75], alpha=0.20, color="steelblue",
                     label="25–75%")
ax_cone.plot(x_cone, cone_bands[50], color="darkorange", linewidth=2.0, label="Median")
ax_cone.axhline(INITIAL_CAPITAL, color="black", linestyle="--", linewidth=0.8)
# annotate key values
for yr, t in [("Yr1", 67), ("Yr2", 134), ("Yr3", 201), ("Yr4", 268), ("Yr5", 335)]:
    v = cone_bands[50][t]
    ax_cone.annotate(f"{yr}\n${v/1000:.0f}k", (t, v), textcoords="offset points",
                     xytext=(0, 12), fontsize=7, ha="center", color="darkorange", fontweight="bold")
ax_cone.set_title("5-Year Return Cone (2% risk)\nMedian + 25/75/90/95% bands", fontsize=10, fontweight="bold")
ax_cone.set_xlabel("Trade Number"); ax_cone.set_ylabel("Equity ($)")
ax_cone.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
ax_cone.legend(fontsize=7, loc="upper left")

# Panel: Probability of profit vs time for all risk levels
ax_prob = axes[2, 1]
for idx, (r_label, scale) in enumerate(RISK_LEVELS):
    probs = []
    trades_axis = [h[1] for h in HORIZONS]
    for h_label, n_trades in HORIZONS:
        d = sim1[(r_label, h_label)]
        probs.append(np.mean(d["ret"] > 0) * 100)
    ax_prob.plot(trades_axis, probs, "o-", color=COLORS[idx], linewidth=2, markersize=8,
                 label=r_label.split("\n")[0])
ax_prob.axhline(100, color="black", linestyle="--", linewidth=0.5)
ax_prob.set_xlabel("Number of Trades"); ax_prob.set_ylabel("Probability of Profit (%)")
ax_prob.set_title("P(Profit) vs Time Horizon", fontsize=10, fontweight="bold")
ax_prob.legend(fontsize=7)
ax_prob.set_ylim(0, 105)
ax_prob.grid(True, alpha=0.3)

# Panel: Risk level return comparison (boxplot)
ax_box = axes[2, 2]
box_data = []
box_labels = []
for r_label, scale in RISK_LEVELS:
    d = sim1[(r_label, "1 year\n(67 trades)")]
    box_data.append(d["ret"] * 100)
    box_labels.append(r_label.split("\n")[0])
bp = ax_box.boxplot(box_data, tick_labels=box_labels, patch_artist=True, showfliers=False,
                     widths=0.5)
for patch, col in zip(bp["boxes"], COLORS):
    patch.set_facecolor(col)
    patch.set_alpha(0.5)
ax_box.axhline(0, color="black", linestyle="--", linewidth=0.8)
ax_box.set_ylabel("1-Year Return (%)")
ax_box.set_title("Return Distribution by Risk Level\n(1yr, box = IQR, line = median)", fontsize=10, fontweight="bold")
ax_box.grid(True, alpha=0.3, axis="y")

# Panel: Drawdown risk-level comparison
ax_ddbox = axes[2, 3]
dd_box_data = []
dd_box_labels = []
for r_label, scale in RISK_LEVELS:
    d = sim1[(r_label, "1 year\n(67 trades)")]
    dd_box_data.append(d["dd"] * 100)
    dd_box_labels.append(r_label.split("\n")[0])
bp2 = ax_ddbox.boxplot(dd_box_data, tick_labels=dd_box_labels, patch_artist=True, showfliers=False,
                        widths=0.5)
for patch, col in zip(bp2["boxes"], COLORS):
    patch.set_facecolor(col)
    patch.set_alpha(0.5)
ax_ddbox.set_ylabel("1-Year Max Drawdown (%)")
ax_ddbox.set_title("Drawdown Distribution by Risk Level\n(1yr)", fontsize=10, fontweight="bold")
ax_ddbox.grid(True, alpha=0.3, axis="y")

# ── ROW 3: Win-rate sensitivity | DD depth×duration | Loss streaks | PnL distribution ──
# Panel: Win-rate sensitivity
ax_wr = axes[3, 0]
wr_labels_short = ["30%", "35%", "40%", "45%", "50.7%"]
wr_prob = [sim3[wl[0]]["prob_profit"] * 100 for wl in WR_SCENARIOS]
wr_med = [sim3[wl[0]]["median_ret"] * 100 for wl in WR_SCENARIOS]
wr_dd = [sim3[wl[0]]["median_dd"] * 100 for wl in WR_SCENARIOS]
colors_wr = ["#d32f2f", "#f44336", "#ff9800", "#4caf50", "#2196f3"]
x_wr = np.arange(len(WR_SCENARIOS))
bars1 = ax_wr.bar(x_wr - 0.2, wr_prob, 0.35, color=colors_wr, alpha=0.85, label="P(Profit) %")
ax_wr.set_ylabel("Probability of Profit (%)", color="black")
ax_wr.set_ylim(0, 105)
ax_wr2 = ax_wr.twinx()
ax_wr2.plot(x_wr + 0.2, wr_med, "ko-", linewidth=2, markersize=8, markerfacecolor="black",
            label="Median Return %")
ax_wr2.set_ylabel("Median Return (%)")
ax_wr2.axhline(0, color="gray", linestyle="--", linewidth=0.8)
ax_wr.set_xticks(x_wr)
ax_wr.set_xticklabels(wr_labels_short, fontsize=8)
ax_wr.set_title("Win-Rate Sensitivity\n(2% risk, 1yr | bar=P(profit), dots=median return)",
                fontsize=10, fontweight="bold")
lines1, labels1 = ax_wr.get_legend_handles_labels()
lines2, labels2 = ax_wr2.get_legend_handles_labels()
ax_wr.legend(lines1 + lines2, labels1 + labels2, fontsize=7, loc="upper left")
ax_wr.grid(True, alpha=0.3, axis="y")

# Panel: Drawdown depth vs duration scatter
ax_scat = axes[3, 1]
# downsample for clarity
mask = np.random.choice(len(all_dd_depths), size=min(5_000, len(all_dd_depths)), replace=False)
sc = ax_scat.scatter(all_dd_durations[mask], all_dd_depths[mask],
                     c=all_dd_depths[mask], cmap="YlOrRd", alpha=0.35, s=8, edgecolors="none")
ax_scat.axhline(np.median(all_dd_depths), color="darkorange", linestyle="--", linewidth=1.2,
                label=f"Median depth: {np.median(all_dd_depths):.1f}%")
ax_scat.axvline(np.median(all_dd_durations), color="darkorange", linestyle="--", linewidth=1.2,
                label=f"Median dur: {np.median(all_dd_durations):.0f} trades")
ax_scat.set_xlabel("Drawdown Duration (trades)")
ax_scat.set_ylabel("Drawdown Depth (%)")
ax_scat.set_title("Drawdown Depth vs Duration\n(2% risk, 1yr, each dot = 1 DD episode)",
                  fontsize=10, fontweight="bold")
ax_scat.legend(fontsize=7)
plt.colorbar(sc, ax=ax_scat, label="Depth (%)")

# Panel: Consecutive loss streak distribution
ax_streak = axes[3, 2]
bins_streak = np.arange(0, max(loss_streaks_all) + 2) - 0.5
ax_streak.hist(loss_streaks_all, bins=bins_streak, color="indianred", edgecolor="white",
               alpha=0.85, density=True)
for p in [50, 90, 95, 99]:
    v = np.percentile(loss_streaks_all, p)
    ax_streak.axvline(v, color="darkorange" if p == 50 else "gray",
                      linestyle="-" if p == 50 else ":", linewidth=1.5,
                      label=f"P{p}: {int(v)} losses")
ax_streak.set_xlabel("Max Consecutive Losses in 1 Year")
ax_streak.set_ylabel("Probability")
ax_streak.set_title("Worst-Case Loss Streak Distribution\n(observed WR, 1yr horizon)",
                    fontsize=10, fontweight="bold")
ax_streak.legend(fontsize=7)
ax_streak.set_xticks(range(0, int(max(loss_streaks_all)) + 2, 2))

# Panel: PnL per trade distribution (observed vs fitted normal)
ax_pnl = axes[3, 3]
ax_pnl.hist(raw_pnl, bins=40, color="steelblue", edgecolor="white", alpha=0.7, density=True,
            label="Observed (n=67)")
# fitted normal
x_fit = np.linspace(raw_pnl.min() * 1.2, raw_pnl.max() * 1.2, 300)
mu, sigma = raw_pnl.mean(), raw_pnl.std()
ax_pnl.plot(x_fit, 1/(sigma*np.sqrt(2*np.pi)) * np.exp(-0.5*((x_fit-mu)/sigma)**2),
            "darkorange", linewidth=2, label=f"Normal fit (μ={mu:+.0f}, σ={sigma:.0f})")
ax_pnl.axvline(0, color="black", linestyle="--", linewidth=0.8)
ax_pnl.axvline(mu, color="darkorange", linestyle="--", linewidth=1.0)
ax_pnl.set_xlabel("PnL per Trade ($)")
ax_pnl.set_ylabel("Density")
ax_pnl.set_title("Trade PnL Distribution\n(observed vs normal fit)", fontsize=10, fontweight="bold")
ax_pnl.legend(fontsize=7)

plt.tight_layout(rect=[0, 0, 1, 0.96])
out_path = ROOT / "monte_carlo_full_results.png"
fig.savefig(out_path, dpi=120, bbox_inches="tight")
plt.close(fig)

print(f"\nChart saved to: {out_path}")
print("Done.")
