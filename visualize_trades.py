"""
Generate an interactive HTML visualization of backtest trades.
Shows price chart with trade entries/exits overlaid, plus PnL summary.
"""
import subprocess
import sys
import re
import json
import pandas as pd
from pathlib import Path
from datetime import datetime

print("Running backtest...")
result = subprocess.run(
    [sys.executable, "backtest.py", "--config", "configs/config_30d.yaml",
     "--csv", "xauusd_1m_realvol.csv", "--log-level", "INFO"],
    capture_output=True, text=True
)
lines = result.stdout.split("\n")

# ── Parse trades ──
trades = []
cur = None

for line in lines:
    # Capture submitted bracket orders
    m_sub = re.search(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Submitted (LONG|SHORT) bracket.*"
        r"est=([\d.]+).*sl=([\d.]+).*tp=([\d.]+).*atr=([\d.]+).*rr=([\d.]+)",
        line
    )
    if m_sub:
        cur = {
            "open_datetime": m_sub.group(1),
            "direction": m_sub.group(2),
            "entry_est": float(m_sub.group(3)),
            "sl": float(m_sub.group(4)),
            "tp": float(m_sub.group(5)),
            "atr": float(m_sub.group(6)),
            "rr": float(m_sub.group(7)),
            "entry_px": None,
            "exit_px": None,
        }

    # Capture order fills
    m_fill = re.search(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Order COMPLETED.*px=([\d.]+).*sz=(-?\d+)",
        line
    )
    if m_fill and cur is not None:
        ts = m_fill.group(1)
        px = float(m_fill.group(2))
        sz = int(m_fill.group(3))
        rel = "parent" if abs(sz) == abs(cur.get("size", 0)) or sz > 0 else "child"
        if cur["entry_px"] is None:
            cur["entry_px"] = px
            cur["entry_ts"] = ts
        else:
            cur["exit_px"] = px
            cur["exit_ts"] = ts

    # Capture trade closed summary
    m_close = re.search(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Trade closed \| (PROFIT|LOSS).*pnl=([\-\d.]+).*bars=(\d+)",
        line
    )
    if m_close and cur is not None:
        cur["pnl"] = float(m_close.group(3))
        cur["result"] = m_close.group(2)
        cur["bars"] = int(m_close.group(4))
        trades.append(cur)
        cur = None

print(f"Parsed {len(trades)} trades")

# ── Read 1H price data ──
print("Loading price data...")
df_1m = pd.read_csv("xauusd_1m.csv")
df_1m["DateTime"] = pd.to_datetime(df_1m["DateTime"])
df_1m = df_1m.set_index("DateTime")

# Resample to 1H for the chart
df_1h = df_1m.resample("1h").agg({
    "Open": "first", "High": "max", "Low": "min", "Close": "last"
}).dropna()

# Get the trade window
trade_start = min((t["open_datetime"] for t in trades), default=df_1h.index[0].strftime("%Y-%m-%d %H:%M:%S"))
trade_end   = max((t.get("exit_ts", t["open_datetime"]) for t in trades), default=df_1h.index[-1].strftime("%Y-%m-%d %H:%M:%S"))

# Chart range: a bit before first trade and after last trade
pad = pd.Timedelta(days=5)
chart_start = pd.Timestamp(trade_start) - pad
chart_end   = pd.Timestamp(trade_end) + pad

df_chart = df_1h[(df_1h.index >= chart_start) & (df_1h.index <= chart_end)]

# ── Build Plotly data ──
candlestick = []
for idx, row in df_chart.iterrows():
    candlestick.append({
        "x": idx.strftime("%Y-%m-%d %H:%M"),
        "open": round(row["Open"], 2),
        "high": round(row["High"], 2),
        "low": round(row["Low"], 2),
        "close": round(row["Close"], 2),
    })

# Trade shapes: lines from entry to exit
win_trades = []
loss_trades = []
for t in trades:
    entry_ts = t.get("entry_ts", t["open_datetime"])
    exit_ts = t.get("exit_ts", t["open_datetime"])
    entry_px = t.get("entry_px", t["entry_est"])
    exit_px = t.get("exit_px", t["sl"])
    direction = "LONG" if t.get("direction", "SHORT") == "LONG" else "SHORT"
    result_label = "WIN" if t.get("pnl", 0) > 0 else "LOSS"

    obj = {
        "entry_ts": entry_ts,
        "exit_ts":  exit_ts,
        "entry_px": round(entry_px, 2),
        "exit_px":  round(exit_px, 2),
        "direction": direction,
        "pnl": round(t.get("pnl", 0), 2),
        "bars": t.get("bars", 0),
        "sl": round(t["sl"], 2),
        "tp": round(t["tp"], 2),
        "result": result_label,
    }
    if t.get("pnl", 0) > 0:
        win_trades.append(obj)
    else:
        loss_trades.append(obj)

# ── PnL curve ──
pnl_curve = []
running = 0
for t in trades:
    running += t.get("pnl", 0)
    pnl_curve.append({
        "x": t.get("exit_ts", t["open_datetime"]),
        "y": round(running, 2),
        "trade_num": len(pnl_curve) + 1,
    })

# ── Summary stats ──
wins = [t for t in trades if t.get("pnl", 0) > 0]
losses = [t for t in trades if t.get("pnl", 0) <= 0]
avg_win = sum(w["pnl"] for w in wins) / len(wins) if wins else 0
avg_loss = abs(sum(l["pnl"] for l in losses)) / len(losses) if losses else 0
total_pnl = sum(t.get("pnl", 0) for t in trades)
win_rate = len(wins) / len(trades) * 100 if trades else 0
pf = sum(w["pnl"] for w in wins) / abs(sum(l["pnl"] for l in losses)) if losses else 0

summary = {
    "total_trades": len(trades),
    "wins": len(wins),
    "losses": len(losses),
    "win_rate": round(win_rate, 1),
    "profit_factor": round(pf, 2),
    "total_pnl": round(total_pnl, 2),
    "avg_win": round(avg_win, 2),
    "avg_loss": round(avg_loss, 2),
    "starting_capital": 5000,
    "final_balance": round(5000 + total_pnl, 2),
    "return_pct": round(total_pnl / 5000 * 100, 2),
    "trade_window": f"{trade_start} — {trade_end}",
}

# ── Session info ──
sessions = {}
for line in lines:
    m = re.search(r"SESSIONS:\s+(.+)", line)
    if m:
        sessions["desc"] = m.group(1).strip()
    m = re.search(r"PRICE RANGE:\s+(.+)", line)
    if m:
        sessions["price_range"] = m.group(1).strip()
    m = re.search(r"RISK:\s+(.+)", line)
    if m:
        sessions["risk"] = m.group(1).strip()

# ── Sweep / rejection counts ──
sweeps_bull = sum(1 for l in lines if "Bullish sweep confirmed" in l)
sweeps_bear = sum(1 for l in lines if "Bearish sweep confirmed" in l)
kz_rej = sum(1 for l in lines if "outside kill-zone" in l)
no_sweep = sum(1 for l in lines if "no active sweep" in l)
vol_rej = sum(1 for l in lines if "no heavy-volume" in l)

# ── Generate HTML ──
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XAUUSD Session-Sweep Backtest — Trade Visualization</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', system-ui, sans-serif; }}
  .header {{ background: #161b22; padding: 16px 24px; border-bottom: 1px solid #30363d; }}
  .header h1 {{ font-size: 20px; color: #58a6ff; }}
  .header .sub {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
  .grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 12px; padding: 12px 16px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; }}
  .card h3 {{ font-size: 14px; color: #8b949e; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 1px; }}
  .stats-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .stat {{ padding: 8px 10px; background: #0d1117; border-radius: 6px; }}
  .stat .label {{ font-size: 11px; color: #8b949e; }}
  .stat .value {{ font-size: 18px; font-weight: 700; }}
  .stat .value.green {{ color: #3fb950; }}
  .stat .value.red {{ color: #f85149; }}
  .stat .value.blue {{ color: #58a6ff; }}
  .stat .subvalue {{ font-size: 11px; color: #8b949e; }}
  .full-width {{ grid-column: 1 / -1; }}
  #chart-price, #chart-pnl {{ width: 100%; }}
  .legend {{ display: flex; gap: 16px; margin-top: 8px; font-size: 12px; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .trade-list {{ max-height: 500px; overflow-y: auto; }}
  .trade-row {{ display: flex; justify-content: space-between; padding: 6px 10px; border-bottom: 1px solid #21262d; font-size: 12px; }}
  .trade-row.win {{ border-left: 3px solid #3fb950; }}
  .trade-row.loss {{ border-left: 3px solid #f85149; }}
  .trade-row .dir {{ font-weight: 600; width: 40px; }}
  .trade-row .dir.long {{ color: #3fb950; }}
  .trade-row .dir.short {{ color: #f85149; }}
  .trade-row .pnl {{ font-weight: 600; width: 70px; text-align: right; }}
  .filter-bar {{ display: flex; gap: 8px; margin-bottom: 10px; }}
  .filter-bar button {{ padding: 4px 12px; border: 1px solid #30363d; border-radius: 4px; background: #21262d; color: #c9d1d9; cursor: pointer; font-size: 12px; }}
  .filter-bar button.active {{ background: #1f6feb; border-color: #1f6feb; }}
  .rejection-stats {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .rej-chip {{ padding: 4px 10px; background: #21262d; border-radius: 12px; font-size: 11px; }}
  .rej-chip .count {{ font-weight: 700; color: #f85149; }}
  .section-title {{ font-size: 13px; font-weight: 600; color: #58a6ff; margin: 12px 0 8px 0; }}
</style>
</head>
<body>

<div class="header">
  <h1>XAUUSD Session-Sweep Backtest — Trade Visualization</h1>
  <div class="sub">
    Window: {summary['trade_window']} | 
    Trades: {summary['total_trades']} | 
    Win Rate: {summary['win_rate']}% | 
    P&L: ${summary['total_pnl']:+.2f}
  </div>
</div>

<div class="grid">
  <!-- Stats Cards -->
  <div class="card">
    <h3>Performance Summary</h3>
    <div class="stats-grid">
      <div class="stat">
        <div class="label">Starting Balance</div>
        <div class="value blue">${summary['starting_capital']:,}</div>
      </div>
      <div class="stat">
        <div class="label">Final Balance</div>
        <div class="value {'green' if summary['total_pnl'] >= 0 else 'red'}">${summary['final_balance']:,.2f}</div>
        <div class="subvalue">{summary['return_pct']:+.2f}%</div>
      </div>
      <div class="stat">
        <div class="label">Win Rate</div>
        <div class="value blue">{summary['win_rate']}%</div>
        <div class="subvalue">{summary['wins']}W / {summary['losses']}L</div>
      </div>
      <div class="stat">
        <div class="label">Profit Factor</div>
        <div class="value {'green' if summary['profit_factor'] >= 1 else 'red'}">{summary['profit_factor']}</div>
      </div>
      <div class="stat">
        <div class="label">Avg Win</div>
        <div class="value green">+${summary['avg_win']:.2f}</div>
      </div>
      <div class="stat">
        <div class="label">Avg Loss</div>
        <div class="value red">-${summary['avg_loss']:.2f}</div>
      </div>
    </div>
    <div class="section-title">Signal Activity</div>
    <div class="rejection-stats">
      <div class="rej-chip">🐂 Bullish sweeps: <span class="count" style="color:#3fb950">{sweeps_bull}</span></div>
      <div class="rej-chip">🐻 Bearish sweeps: <span class="count" style="color:#f85149">{sweeps_bear}</span></div>
      <div class="rej-chip">⏰ Kill-zone rejects: <span class="count">{kz_rej}</span></div>
      <div class="rej-chip">🔍 No sweep: <span class="count">{no_sweep}</span></div>
      <div class="rej-chip">📊 Vol filter rejects: <span class="count">{vol_rej}</span></div>
    </div>
    <div class="section-title">Settings</div>
    <div style="font-size:11px;color:#8b949e;line-height:1.6">
      Kill-zone: 12:00–16:00 UTC (NY AM only)<br>
      Confirmation: Heavy-volume candle (EMA20 vol × 1.25)<br>
      Risk: 2% per trade | ATR-based stops | 1:2 RR<br>
      Session: Asia → London ref | NY → London ref (Asia fallback)<br>
    </div>
  </div>

  <!-- PnL Chart -->
  <div class="card">
    <h3>Equity Curve</h3>
    <div id="chart-pnl" style="height: 260px;"></div>
  </div>

  <!-- Price Chart -->
  <div class="card full-width">
    <h3>Price Chart with Trade Overlays</h3>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#3fb950"></div> Win</div>
        <div class="legend-item"><div class="legend-dot" style="background:#f85149"></div> Loss</div>
        <div class="legend-item"><div class="legend-dot" style="background:#58a6ff"></div> Entry</div>
        <div class="legend-item"><div class="legend-dot" style="background:#d2a800"></div> Exit</div>
      </div>
      <div style="flex:1"></div>
      <div class="filter-bar">
        <button onclick="filterTrades('all')" class="active" id="btn-all">All</button>
        <button onclick="filterTrades('win')" id="btn-win">Wins</button>
        <button onclick="filterTrades('loss')" id="btn-loss">Losses</button>
        <button onclick="filterTrades('long')" id="btn-long">LONG</button>
        <button onclick="filterTrades('short')" id="btn-short">SHORT</button>
      </div>
    </div>
    <div id="chart-price" style="height: 520px;"></div>
  </div>

  <!-- Trade List -->
  <div class="card full-width">
    <h3>Trade Log</h3>
    <div class="trade-list" id="trade-list">
"""

for i, t in enumerate(trades, 1):
    entry_ts = t.get("entry_ts", t["open_datetime"])
    exit_ts = t.get("exit_ts", t["open_datetime"])
    pnl_str = f"${t['pnl']:+.2f}" if "pnl" in t else ""
    dir_class = "long" if t.get("direction") == "LONG" else "short"
    row_class = "win" if t.get("pnl", 0) > 0 else "loss"
    html += f"""
      <div class="trade-row {row_class}" data-result="{row_class}" data-dir="{dir_class}">
        <span style="color:#8b949e;width:24px">#{i}</span>
        <span class="dir {dir_class}">{t.get('direction', '?')}</span>
        <span style="flex:1">{entry_ts[:16]}</span>
        <span style="color:#8b949e;margin:0 8px">→</span>
        <span style="flex:1">{exit_ts[:16]}</span>
        <span style="color:#8b949e;width:50px">{t.get('bars','?')}b</span>
        <span class="pnl" style="color:{'#3fb950' if t.get('pnl',0)>0 else '#f85149'}">{pnl_str}</span>
      </div>"""

html += """
    </div>
  </div>
</div>

<script>
// ── Data ──
const candlestickData = """ + json.dumps(candlestick) + """;
const winTrades = """ + json.dumps(win_trades) + """;
const lossTrades = """ + json.dumps(loss_trades) + """;
const pnlCurve = """ + json.dumps(pnl_curve) + """;

// ── Price Chart ──
function buildPriceChart(filter) {
  const traces = [];

  // Candlestick
  traces.push({
    type: 'candlestick',
    x: candlestickData.map(d => d.x),
    open: candlestickData.map(d => d.open),
    high: candlestickData.map(d => d.high),
    low: candlestickData.map(d => d.low),
    close: candlestickData.map(d => d.close),
    increasing: {line: {color: '#26a69a', width: 1}, fillcolor: '#26a69a40'},
    decreasing: {line: {color: '#ef5350', width: 1}, fillcolor: '#ef535040'},
    name: 'XAUUSD 1H',
    showlegend: false,
  });

  // Function to add trade lines
  function addTradeArrows(tradeList, color, show) {
    const entries = {x: [], y: [], text: []};
    const lines = [];
    tradeList.forEach(t => {
      const isWin = t.result === 'WIN';
      const entryColor = color;
      const exitColor = isWin ? '#3fb950' : '#f85149';

      // Entry marker
      entries.x.push(t.entry_ts);
      entries.y.push(t.entry_px);
      entries.text.push(`${t.direction} | Entry: ${t.entry_px} | ${t.result}`);

      // Line from entry to exit
      lines.push({
        type: 'scatter',
        x: [t.entry_ts, t.exit_ts],
        y: [t.entry_px, t.exit_px],
        mode: 'lines',
        line: {color: exitColor, width: 2, dash: 'solid'},
        showlegend: false,
        hoverinfo: 'text',
        hovertext: `${t.direction} | ${t.entry_px} → ${t.exit_px} | ${t.result} $${t.pnl}`,
      });

      // Exit marker
      lines.push({
        type: 'scatter',
        x: [t.exit_ts],
        y: [t.exit_px],
        mode: 'markers',
        marker: {color: exitColor, size: 8, symbol: 'x'},
        showlegend: false,
        hoverinfo: 'skip',
      });
    });

    traces.push({
      type: 'scatter',
      x: entries.x,
      y: entries.y,
      mode: 'markers',
      marker: {color: color, size: 7, symbol: 'triangle-up'},
      text: entries.text,
      hoverinfo: 'text',
      name: color === '#3fb950' ? 'Win Entry' : 'Loss Entry',
      showlegend: true,
    });

    lines.forEach(l => traces.push(l));
  }

  if (filter === 'all' || filter === 'win') addTradeArrows(winTrades, '#3fb950');
  if (filter === 'all' || filter === 'loss') addTradeArrows(lossTrades, '#f85149');

  // SL/TP zones for a sample trade
  if (winTrades.length > 0 && filter !== 'loss') {
    const last = winTrades[winTrades.length - 1];
    traces.push({
      type: 'scatter',
      x: [last.entry_ts, last.entry_ts],
      y: [last.sl, last.tp],
      mode: 'lines',
      line: {color: '#58a6ff40', width: 1, dash: 'dot'},
      showlegend: false,
      hoverinfo: 'skip',
    });
  }

  const layout = {
    template: 'plotly_dark',
    paper_bgcolor: '#161b22',
    plot_bgcolor: '#0d1117',
    margin: {l: 60, r: 20, t: 10, b: 40},
    xaxis: {
      title: '',
      gridcolor: '#21262d',
      showgrid: true,
    },
    yaxis: {
      title: 'Price (USD)',
      gridcolor: '#21262d',
      showgrid: true,
    },
    hovermode: 'closest',
    dragmode: 'pan',
    showlegend: true,
    legend: {orientation: 'h', y: 1.12, x: 0},
  };

  Plotly.newPlot('chart-price', traces, layout, {responsive: true, displayModeBar: true, modeBarButtonsToRemove: ['lasso2d', 'select2d']});
}

// ── PnL Chart ──
function buildPnlChart() {
  const areaColor = pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].y >= 0 ? '#3fb95040' : '#f8514940';
  const lineColor = pnlCurve.length > 0 && pnlCurve[pnlCurve.length - 1].y >= 0 ? '#3fb950' : '#f85149';

  const traces = [{
    type: 'scatter',
    x: pnlCurve.map(d => d.x),
    y: pnlCurve.map(d => d.y),
    mode: 'lines+markers',
    fill: 'tozeroy',
    fillcolor: areaColor,
    line: {color: lineColor, width: 2},
    marker: {
      size: 6,
      color: pnlCurve.map(d => d.y >= 0 ? '#3fb950' : '#f85149'),
    },
    text: pnlCurve.map(d => `Trade #${d.trade_num}: $${d.y.toFixed(2)}`),
    hoverinfo: 'text',
    showlegend: false,
  }];

  const layout = {
    template: 'plotly_dark',
    paper_bgcolor: '#161b22',
    plot_bgcolor: '#0d1117',
    margin: {l: 60, r: 20, t: 5, b: 30},
    xaxis: {gridcolor: '#21262d', showgrid: true},
    yaxis: {title: 'Cumulative P&L ($)', gridcolor: '#21262d', showgrid: true},
    hovermode: 'closest',
    shapes: [{
      type: 'line',
      x0: pnlCurve.length > 0 ? pnlCurve[0].x : 0,
      x1: pnlCurve.length > 0 ? pnlCurve[pnlCurve.length - 1].x : 0,
      y0: 0,
      y1: 0,
      line: {color: '#8b949e', width: 1, dash: 'dash'},
    }],
  };

  Plotly.newPlot('chart-pnl', traces, layout, {responsive: true, displayModeBar: false});
}

// ── Filter ──
function filterTrades(type) {
  document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
  document.getElementById('btn-' + type).classList.add('active');

  document.querySelectorAll('.trade-row').forEach(row => {
    const result = row.dataset.result;
    const dir = row.dataset.dir;
    if (type === 'all') row.style.display = 'flex';
    else if (type === 'win') row.style.display = result === 'win' ? 'flex' : 'none';
    else if (type === 'loss') row.style.display = result === 'loss' ? 'flex' : 'none';
    else if (type === 'long') row.style.display = dir === 'long' ? 'flex' : 'none';
    else if (type === 'short') row.style.display = dir === 'short' ? 'flex' : 'none';
  });
}

// ── Init ──
buildPriceChart('all');
buildPnlChart();
</script>
</body>
</html>"""

out_path = Path("trade_visualization.html")
out_path.write_text(html, encoding="utf-8")
print(f"\nVisualization saved to: {out_path.resolve()}")
print(f"Open it in your browser to explore the trades.")
