"""
Generate per-trade 1M detail viewer — v5
- Shows sweep formation: breach candle + confirmation candle
- Full session range always visible on y-axis
- 3-4 hours before entry to see the full sweep develop
- Range slider, scroll zoom, pan
"""
import subprocess, sys, re, json, pandas as pd, numpy as np
from pathlib import Path

print("=== Running backtest... ===")
result = subprocess.run(
    [sys.executable, "backtest.py", "--config", "configs/config_30d.yaml",
     "--csv", "xauusd_1m_realvol.csv", "--log-level", "DEBUG"],
    capture_output=True, text=True
)
lines = result.stdout.split("\n")

print("=== Loading 1M data + computing session ranges... ===")
df1m = pd.read_csv("xauusd_1m.csv")
df1m["DateTime"] = pd.to_datetime(df1m["DateTime"])
df1m = df1m.set_index("DateTime").sort_index()

# Build session range lookup
session_ranges = {}
for d in sorted(set(d.date() for d in df1m.index)):
    ds = pd.Timestamp(d)
    asia = df1m[(df1m.index >= ds + pd.Timedelta(hours=0)) & (df1m.index < ds + pd.Timedelta(hours=7))]
    london = df1m[(df1m.index >= ds + pd.Timedelta(hours=7)) & (df1m.index < ds + pd.Timedelta(hours=12))]
    sr = {}
    if len(asia) > 0: sr["asia_high"] = round(float(asia["High"].max()), 2); sr["asia_low"] = round(float(asia["Low"].min()), 2)
    if len(london) > 0: sr["london_high"] = round(float(london["High"].max()), 2); sr["london_low"] = round(float(london["Low"].min()), 2)
    session_ranges[d.strftime("%Y-%m-%d")] = sr

# Parse sweep confirmations (with breach_bar for approx breach time)
sweep_events = []
for line in lines:
    m = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*(Bullish|Bearish) sweep confirmed.*(Asia|London) (high|low) ([\d.]+)", line)
    if m:
        se = {"ts": m.group(1), "dir": m.group(2), "ref": m.group(3), "level_type": m.group(4), "level": float(m.group(5))}
        bm = re.search(r"breach_bar=(\d+)\s+confirm_bar=(\d+)", line)
        if bm: se["breach_5m_bar"] = int(bm.group(1)); se["confirm_5m_bar"] = int(bm.group(2))
        sweep_events.append(se)

# Parse trades
trades = []
for line in lines:
    m = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*Submitted (LONG|SHORT).*est=([\d.]+).*sl=([\d.]+).*tp=([\d.]+).*atr=([\d.]+).*rr=([\d.]+)", line)
    if m: trades.append({"open_ts": m.group(1), "dir": m.group(2), "entry_est": float(m.group(3)),
                          "sl": float(m.group(4)), "tp": float(m.group(5)), "atr": float(m.group(6))})

for line in lines:
    m = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*Order COMPLETED.*(BUY|SELL).*px=([\d.]+)", line)
    if m and trades:
        ts, px = m.group(1), float(m.group(3))
        for t in reversed(trades):
            if "entry_px" not in t: t.update(entry_px=px, entry_fill_ts=ts); break
            elif "exit_px" not in t: t.update(exit_px=px, exit_ts=ts); break

for line in lines:
    m = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*Trade closed.*(PROFIT|LOSS).*pnl=([\-\d.]+).*bars=(\d+)", line)
    if m:
        for t in reversed(trades):
            if "pnl" not in t: t.update(result=m.group(2), pnl=float(m.group(3)), bars=int(m.group(4))); break

# Attach sweep + session range + sweep formation candles
for trade in trades:
    trade_ts = pd.Timestamp(trade["open_ts"])
    trade_date = trade_ts.strftime("%Y-%m-%d")

    # Find nearest sweep
    best = None
    for s in sweep_events:
        sts = pd.Timestamp(s["ts"])
        if sts <= trade_ts and (trade_ts - sts) < pd.Timedelta(hours=4):
            if (trade["dir"] == "LONG" and s["dir"] == "Bullish") or (trade["dir"] == "SHORT" and s["dir"] == "Bearish"):
                if best is None or sts > pd.Timestamp(best["ts"]): best = s
    trade["sweep"] = best

    # Session range
    sr = session_ranges.get(trade_date, {})
    ref_session = best["ref"] if best else None
    if ref_session == "Asia" and "asia_high" in sr:
        trade["session_range"] = {"name": "Asia", "high": sr["asia_high"], "low": sr["asia_low"]}
    elif ref_session == "London" and "london_high" in sr:
        trade["session_range"] = {"name": "London", "high": sr["london_high"], "low": sr["london_low"]}
    elif "asia_high" in sr:
        trade["session_range"] = {"name": "Asia", "high": sr["asia_high"], "low": sr["asia_low"]}
    elif "london_high" in sr:
        trade["session_range"] = {"name": "London", "high": sr["london_high"], "low": sr["london_low"]}

    # Approximate breach candle: if sweep exists, scan bars before confirmation_ts
    # for the first one whose wick crossed the swept level
    if best and best.get("level"):
        trade["breach_approx"] = None
        conf_ts = pd.Timestamp(best["ts"])
        level = best["level"]
        is_bearish = best["dir"] == "Bearish"
        # Look at 1M bars before confirmation, up to 30 bars back
        search_start = conf_ts - pd.Timedelta(minutes=45)
        search_df = df1m[(df1m.index >= search_start) & (df1m.index < conf_ts)]
        for idx, row in search_df.iterrows():
            if is_bearish and float(row["High"]) > level:
                trade["breach_approx"] = idx.strftime("%Y-%m-%d %H:%M")
                break
            if (not is_bearish) and float(row["Low"]) < level:
                trade["breach_approx"] = idx.strftime("%Y-%m-%d %H:%M")
                break

trades = [t for t in trades if "pnl" in t]
print(f"Parsed {len(trades)} trades")

# Build slices with 3-4 hours context before entry
context_before = 200   # ~3h20m of 1M bars before entry
context_after  = 120   # ~2h after exit
trade_slices = []
for i, t in enumerate(trades):
    entry_ts = pd.Timestamp(t.get("entry_fill_ts", t["open_ts"]))
    exit_ts  = pd.Timestamp(t.get("exit_ts", t["open_ts"]))
    ws = entry_ts - pd.Timedelta(minutes=context_before)
    we = max(exit_ts, entry_ts) + pd.Timedelta(minutes=max(context_after, t.get("bars", 30)))
    sdf = df1m[(df1m.index >= ws) & (df1m.index <= we)]
    if len(sdf) < 5: continue
    candles = [{"x": idx.strftime("%Y-%m-%d %H:%M"), "o": round(float(r["Open"]),2),
                 "h": round(float(r["High"]),2), "l": round(float(r["Low"]),2),
                 "c": round(float(r["Close"]),2)} for idx, r in sdf.iterrows()]
    trade_slices.append({
        "trade_num": i+1, "direction": t["dir"], "result": t.get("result","?"),
        "pnl": t.get("pnl",0), "bars_held": t.get("bars",0),
        "open_ts": t["open_ts"], "entry_ts": t.get("entry_fill_ts", t["open_ts"]),
        "exit_ts": t.get("exit_ts", t["open_ts"]),
        "entry_px": t.get("entry_px", t["entry_est"]),
        "exit_px": t.get("exit_px", 0), "sl": t["sl"], "tp": t["tp"], "atr": t["atr"],
        "candles": candles,
        "sweep": {"ts": t["sweep"]["ts"], "ref": t["sweep"]["ref"],
                   "level": t["sweep"]["level"], "level_type": t["sweep"].get("level_type",""),
                   "dir": t["sweep"]["dir"]} if t.get("sweep") else None,
        "breach_candle_ts": t.get("breach_approx"),
        "session_range": t.get("session_range"),
    })

wins = len([t for t in trades if t.get("pnl",0) > 0])
losses = len([t for t in trades if t.get("pnl",0) <= 0])
total_pnl = sum(t.get("pnl",0) for t in trades)

print(f"Slices: {len(trade_slices)} (wins={wins}, losses={losses})")

# ── HTML ──
js_data = json.dumps(trade_slices, indent=2)

html_parts = []

html_parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XAUUSD Session-Sweep — Trade Detail Viewer</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0d1117; color:#c9d1d9; font-family:'Segoe UI',system-ui,sans-serif; overflow:hidden; }}
.header {{ background:#161b22; padding:6px 12px; border-bottom:1px solid #30363d; display:flex; align-items:center; gap:10px; height:38px; }}
.header h1 {{ font-size:14px; color:#58a6ff; white-space:nowrap; }}
.badge {{ padding:1px 8px; border-radius:9px; font-size:10px; font-weight:600; }}
.badge.win {{ background:#3fb95020; color:#3fb950; }}
.badge.loss {{ background:#f8514920; color:#f85149; }}
.hs {{ font-size:10px; color:#8b949e; }}
.layout {{ display:grid; grid-template-columns:290px 1fr; height:calc(100vh - 38px); }}
.sidebar {{ background:#161b22; border-right:1px solid #30363d; overflow-y:auto; padding:3px; }}
.sidebar h3 {{ font-size:10px; color:#8b949e; padding:3px 5px; text-transform:uppercase; letter-spacing:1px; }}
.trade-btn {{ display:flex; align-items:center; gap:4px; padding:5px 7px; border:none; border-radius:4px; background:transparent; color:#c9d1d9; cursor:pointer; font-size:10px; text-align:left; width:100%; }}
.trade-btn:hover {{ background:#21262d; }}
.trade-btn.active {{ background:#1f6feb20; border:1px solid #1f6feb; }}
.trade-btn .num {{ width:16px; font-weight:600; color:#8b949e; font-size:9px; }}
.db {{ padding:1px 4px; border-radius:3px; font-size:8px; font-weight:600; width:26px; text-align:center; }}
.db.LONG {{ background:#3fb95020; color:#3fb950; }}
.db.SHORT {{ background:#f8514920; color:#f85149; }}
.trade-btn .pnl {{ margin-left:auto; font-weight:600; font-size:10px; }}
.main {{ display:flex; flex-direction:column; background:#0d1117; }}
.main-header {{ padding:4px 10px; background:#0d1117; border-bottom:1px solid #30363d; font-size:11px; min-height:26px; }}
.chart-area {{ flex:1; position:relative; min-height:300px; }}
#chart-detail {{ position:absolute; top:0; left:0; right:0; bottom:0; }}
.nav-controls {{ display:flex; gap:5px; padding:4px 10px; background:#0d1117; border-top:1px solid #30363d; align-items:center; }}
.nav-controls button {{ padding:3px 10px; border:1px solid #30363d; border-radius:4px; background:#21262d; color:#c9d1d9; cursor:pointer; font-size:10px; }}
.nav-controls button:hover {{ background:#30363d; }}
.lc {{ display:flex; gap:8px; font-size:9px; align-items:center; margin-left:auto; }}
.lc span {{ display:flex; align-items:center; gap:3px; }}
.ld {{ width:7px; height:3px; border-radius:1px; }}
.stats {{ padding:4px; font-size:9px; color:#8b949e; line-height:1.5; }}
.stats b {{ color:#c9d1d9; }}
</style>
</head>
<body>
<div class="header">
  <h1>XAUUSD Session-Sweep</h1>
  <span class="badge win">{wins} Wins</span>
  <span class="badge loss">{losses} Losses</span>
  <span class="hs">{wins/len(trades)*100:.0f}% WR | $5K → ${5000+total_pnl:,.2f}</span>
</div>
<div class="layout">
<div class="sidebar">
<div class="stats">
<b>$5,000</b> | <b>2RR</b> | <b>12-16 UTC</b><br>
Vol EMA20×1.25 | NY→LON→Asia
</div>
<h3>{len(trades)} Trades</h3>
<div id="trade-nav">
""")

for i, ts in enumerate(trade_slices):
    rc = "win" if ts["result"] == "PROFIT" else "loss"
    pv = f"${ts['pnl']:+.2f}"
    pc = "#3fb950" if ts["result"] == "PROFIT" else "#f85149"
    html_parts.append(f"""  <button class="trade-btn" onclick="showTrade({i})" id="btn-{i}">
    <span class="num">#{ts['trade_num']}</span>
    <span class="db {ts['direction']}">{ts['direction']}</span>
    <span style="flex:1;font-size:8px;color:#8b949e">{ts['open_ts'][5:16]}</span>
    <span class="pnl" style="color:{pc}">{pv}</span>
  </button>
""")

html_parts.append("""</div></div>
<div class="main">
<div class="main-header" id="trade-info-bar"><span style="color:#8b949e">Select a trade →</span></div>
<div class="chart-area"><div id="chart-detail"></div></div>
<div class="nav-controls">
  <button onclick="prevTrade()">◀ Prev</button>
  <button onclick="nextTrade()">Next ▶</button>
  <span style="font-size:9px;color:#8b949e">← → keys | scroll zoom | drag pan | slider below</span>
  <span style="flex:1"></span>
  <div class="lc">
    <span><span class="ld" style="background:#58a6ff"></span>Entry</span>
    <span><span class="ld" style="background:#d2a800"></span>Exit</span>
    <span><span class="ld" style="background:#3fb950"></span>TP</span>
    <span><span class="ld" style="background:#f85149"></span>SL</span>
    <span><span class="ld" style="background:#ff6e00"></span>Sess H</span>
    <span><span class="ld" style="background:#00bcd4"></span>Sess L</span>
    <span><span class="ld" style="background:#ab47bc"></span>Sweep Lvl</span>
    <span><span class="ld" style="background:#ff9800"></span>Breach</span>
  </div>
</div>
</div></div>

<script>
var allTrades = """ + js_data + """;
var currentIdx = 0;

function normTs(ts) {{
  if (!ts) return '';
  return ts.length >= 16 ? ts.substring(0,16) : ts;
}}

function showTrade(idx) {{
  currentIdx = idx;
  var t = allTrades[idx];
  document.querySelectorAll('.trade-btn').forEach(function(b){{b.classList.remove('active')}});
  var btn = document.getElementById('btn-'+idx);
  if(btn){{btn.classList.add('active');btn.scrollIntoView({{block:'nearest'}})}}

  if (typeof Plotly === 'undefined') {{
    document.getElementById('chart-detail').innerHTML =
      '<div style="padding:40px;color:#f85149;text-align:center">'+
      '<h3>Plotly not loaded</h3>'+
      '<p>CDN unreachable. Try refreshing or check your internet connection.</p>'+
      '<p style="font-size:10px;color:#8b949e">CDN: cdn.plot.ly</p></div>';
    return;
  }}

  try {{

  var rc = t.result === 'PROFIT' ? '#3fb950' : '#f85149';
  var sessionH = t.session_range ? t.session_range.high : 0;
  var sessionL = t.session_range ? t.session_range.low : 0;
  var sessName = t.session_range ? t.session_range.name : '?';
  document.getElementById('trade-info-bar').innerHTML =
    '<span style="font-size:15px;font-weight:700;margin-right:6px;color:'+rc+'">#'+t.trade_num+' '+t.direction+'</span>'+
    '<span class="badge '+(t.result==='PROFIT'?'win':'loss')+'">'+t.result+'</span>'+
    '<span style="font-size:15px;font-weight:700;margin-left:6px;color:'+rc+'">$'+t.pnl.toFixed(2)+'</span>'+
    '<span style="margin-left:10px;font-size:10px;color:#8b949e">Entry $'+t.entry_px.toFixed(2)+' | Exit $'+t.exit_px.toFixed(2)+' | SL $'+t.sl.toFixed(2)+' | TP $'+t.tp.toFixed(2)+' | ATR '+t.atr.toFixed(2)+' | '+t.bars_held+' bars | '+sessName+' H/L: $'+sessionH.toFixed(2)+' / $'+sessionL.toFixed(2)+'</span>';

  var c = t.candles, n = c.length;
  var entryTsNorm = normTs(t.entry_ts);
  var exitTsNorm = normTs(t.exit_ts);
  var sweepTsNorm = t.sweep ? normTs(t.sweep.ts) : '';
  var breachXnorm = t.breach_candle_ts;
  var traces = [];

  traces.push({type:'candlestick', x:c.map(function(d){{return d.x}}), open:c.map(function(d){{return d.o}}), high:c.map(function(d){{return d.h}}), low:c.map(function(d){{return d.l}}), close:c.map(function(d){{return d.c}}), increasing:{{line:{{color:'#26a69a',width:1.2}},fillcolor:'#26a69a'}}, decreasing:{{line:{{color:'#ef5350',width:1.2}},fillcolor:'#ef5350'}}, whiskerwidth:0.5, name:'XAUUSD 1M', showlegend:true, hoverinfo:'x+open+high+low+close', hoverlabel:{{bgcolor:'#1a1a2e',font:{{color:'#eee',size:11}}}}, xhoverformat:'%Y-%m-%d %H:%M'}});

  var yMin = sessionL, yMax = sessionH;
  if (!sessionL || !sessionH || sessionL >= sessionH) {{
    var lo=999999, hi=0;
    c.forEach(function(d){{if(d.l<lo)lo=d.l;if(d.h>hi)hi=d.h}});
    yMin=lo; yMax=hi;
  }}
  var pad = (yMax - yMin) * 0.06;
  yMin = yMin - pad;
  yMax = yMax + pad;

  if (t.session_range) {{
    traces.push({{type:'scatter', x:[c[0].x,c[n-1].x,c[n-1].x,c[0].x,c[0].x], y:[sessionH,sessionH,sessionL,sessionL,sessionH], mode:'lines',fill:'toself',fillcolor:'rgba(255,110,0,0.05)',line:{{width:0}},name:sessName+' Range',showlegend:false,hoverinfo:'skip'}});
    traces.push({{type:'scatter', x:[c[0].x,c[n-1].x], y:[sessionH,sessionH], mode:'lines', line:{{color:'#ff6e00',width:2}}, name:sessName+' High $'+sessionH.toFixed(2), hoverinfo:'name',showlegend:true}});
    traces.push({{type:'scatter', x:[c[0].x,c[n-1].x], y:[sessionL,sessionL], mode:'lines', line:{{color:'#00bcd4',width:2}}, name:sessName+' Low $'+sessionL.toFixed(2), hoverinfo:'name',showlegend:true}});
  }}

  if (t.sweep && t.sweep.level > 0) {{
    var slbl = t.sweep.dir+' sweep '+t.sweep.ref+' '+t.sweep.level_type;
    traces.push({{type:'scatter', x:[c[0].x,c[n-1].x], y:[t.sweep.level,t.sweep.level], mode:'lines', line:{{color:'#ab47bc',width:1.5,dash:'dot'}}, name:slbl+' $'+t.sweep.level.toFixed(2), hoverinfo:'name',showlegend:true}});
  }}

  if (breachXnorm) {{
    var bIdx = c.findIndex(function(d){{return d.x===breachXnorm}});
    if (bIdx >= 0) {{
      traces.push({{type:'scatter', x:[c[bIdx].x], y:[c[bIdx].h], mode:'markers', marker:{{color:'#ff9800',size:12,symbol:'triangle-down',line:{{color:'#fff',width:1}}}}, name:'Breach candle (wick crossed)', hoverinfo:'name+x+y',showlegend:true}});
      traces.push({{type:'scatter', x:[c[bIdx].x,c[bIdx].x], y:[t.sweep.level,c[bIdx].h], mode:'lines', line:{{color:'#ff9800',width:1,dash:'dot'}}, name:'',showlegend:false,hoverinfo:'skip'}});
    }}
  }}

  if (t.sweep && t.sweep.ts) {{
    var confIdx = c.findIndex(function(d){{return d.x===sweepTsNorm}});
    if (confIdx >= 0) {{
      traces.push({{type:'scatter', x:[c[confIdx].x], y:[c[confIdx].c], mode:'markers+text', marker:{{color:'#ab47bc',size:10,symbol:'diamond',line:{{color:'#fff',width:1}}}}, text:['SWEEP'],textposition:'bottom center',textfont:{{color:'#ab47bc',size:9}}, name:'Sweep confirmed here', hoverinfo:'name+x+y',showlegend:true}});
    }}
  }}

  traces.push({{type:'scatter', x:[entryTsNorm,entryTsNorm], y:[yMin,yMax], mode:'lines', line:{{color:'#58a6ff',width:2.5}}, name:'Entry $'+t.entry_px.toFixed(2), hoverinfo:'name',showlegend:true}});
  traces.push({{type:'scatter', x:[exitTsNorm,exitTsNorm], y:[yMin,yMax], mode:'lines', line:{{color:'#d2a800',width:2,dash:'longdash'}}, name:'Exit $'+t.exit_px.toFixed(2)+' ('+t.result+')', hoverinfo:'name',showlegend:true}});
  traces.push({{type:'scatter', x:[entryTsNorm,c[n-1].x], y:[t.tp,t.tp], mode:'lines', line:{{color:'#3fb950',width:1.5,dash:'dash'}}, name:'TP $'+t.tp.toFixed(2), hoverinfo:'name',showlegend:true}});
  traces.push({{type:'scatter', x:[entryTsNorm,c[n-1].x], y:[t.sl,t.sl], mode:'lines', line:{{color:'#f85149',width:1.5,dash:'dash'}}, name:'SL $'+t.sl.toFixed(2), hoverinfo:'name',showlegend:true}});

  var eIdx = c.findIndex(function(d){{return d.x===entryTsNorm}});
  if (eIdx >= 0) {{
    traces.push({{type:'scatter', x:[c[eIdx].x], y:[c[eIdx].c], mode:'markers', marker:{{color:'#58a6ff',size:11,symbol:'star',line:{{color:'#fff',width:1}}}}, name:'Entry candle ('+(t.direction==='LONG'?'GREEN':'RED')+')', hoverinfo:'name+x+y',showlegend:true}});
  }}
  var xIdx = c.findIndex(function(d){{return d.x===exitTsNorm}});
  if (xIdx >= 0) {{
    traces.push({{type:'scatter', x:[c[xIdx].x], y:[c[xIdx].c], mode:'markers', marker:{{color:'#d2a800',size:10,symbol:'x-thin',line:{{color:'#fff',width:1}}}}, name:'Exit candle', hoverinfo:'name+x+y',showlegend:true}});
  }}

  var layout = {{
    template:'plotly_dark', paper_bgcolor:'#0d1117', plot_bgcolor:'#0d1117',
    margin:{{l:75,r:30,t:8,b:60}},
    xaxis:{{title:'',gridcolor:'#1a1f2b',showgrid:true,zeroline:false,type:'category',
      rangeslider:{{visible:true,thickness:0.05,bgcolor:'#161b22',borderwidth:0}},
      tickformat:'%H:%M',nticks:20}},
    yaxis:{{title:'Price (USD)',gridcolor:'#1a1f2b',showgrid:true,zeroline:false,
      range:[yMin,yMax],fixedrange:false,tickformat:'.2f'}},
    hovermode:'x unified',hoverdistance:10,dragmode:'pan',
    showlegend:true,legend:{{orientation:'h',y:1.08,x:0,xanchor:'left',font:{{size:8,color:'#8b949e'}},bgcolor:'#0d1117e0',bordercolor:'#30363d',borderwidth:1}},
  }};

  var config = {{
    responsive:true,displayModeBar:true,displaylogo:false,scrollZoom:true,
    modeBarButtonsToRemove:['lasso2d','select2d','autoScale2d'],
    modeBarButtonsToAdd:[{{name:'Reset',icon:Plotly.Icons.home,click:function(gd){{Plotly.relayout(gd,{{'xaxis.autorange':true,'yaxis.autorange':true}})}}}}],
    toImageButtonOptions:{{format:'png',filename:'trade_'+t.trade_num,height:600,width:1200}},
  }};

  Plotly.react('chart-detail', traces, layout, config);

  }} catch(err) {{
    document.getElementById('chart-detail').innerHTML =
      '<div style="padding:40px;color:#f85149;text-align:center">'+
      '<h3>Chart render error</h3><pre style="font-size:10px;text-align:left">'+err.message+'</pre></div>';
    console.error('showTrade error:', err);
  }}
}}

function nextTrade(){{if(currentIdx<allTrades.length-1)showTrade(currentIdx+1)}}
function prevTrade(){{if(currentIdx>0)showTrade(currentIdx-1)}}
document.addEventListener('keydown',function(e){{if(e.key==='ArrowRight'){{nextTrade();e.preventDefault()}}if(e.key==='ArrowLeft'){{prevTrade();e.preventDefault()}}}});
if(allTrades.length>0)showTrade(0);
</script>
</body>
</html>""")

out_path = Path("trade_detail_viewer.html")
final = "".join(html_parts)
# Fix double braces in JS section: {{ -> { and }} -> }
final = final.replace("{{", "{").replace("}}", "}")
out_path.write_text(final, encoding="utf-8")
print(f"\nSaved to: {out_path.resolve()}")
print("v5: 3-4h sweep context + full session range always visible + breach candle + confirmation candle")
