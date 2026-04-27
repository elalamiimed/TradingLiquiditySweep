"""Fix trade_detail_viewer.html JavaScript issues."""
import re

with open("trade_detail_viewer.html", "r", encoding="utf-8") as f:
    html = f.read()

print(f"Read {len(html)} chars")

# 1. Fix double braces in script section (most critical fix!)
script_match = re.search(r'(<script>)(.*?)(</script>)', html, re.DOTALL)
if script_match:
    before = html[:script_match.start(2)]
    script = script_match.group(2)
    after = html[script_match.end(2):]
    fixed_script = script.replace('{{', '{').replace('}}', '}')
    html = before + fixed_script + after
    print("Fix 1: Double braces -> single braces")

# 2. CSS fixes
html = html.replace(
    ".chart-area { flex:1; position:relative; }",
    ".chart-area { flex:1; position:relative; min-height:300px; }"
)
html = html.replace(
    "#chart-detail { width:100%; height:100%; }",
    "#chart-detail { position:absolute; top:0; left:0; right:0; bottom:0; }"
)
print("Fix 2: CSS chart sizing")

# 3. Add normTs helper + Plotly check + try-catch at function start
old_start = "function showTrade(idx) {\n  currentIdx = idx;"
new_start = """function normTs(ts) {
  if (!ts) return '';
  return ts.length >= 16 ? ts.substring(0,16) : ts;
}

function showTrade(idx) {
  currentIdx = idx;
  var t = allTrades[idx];
  document.querySelectorAll('.trade-btn').forEach(function(b){b.classList.remove('active')});
  var btn = document.getElementById('btn-'+idx);
  if(btn){btn.classList.add('active');btn.scrollIntoView({block:'nearest'})}

  if (typeof Plotly === 'undefined') {
    document.getElementById('chart-detail').innerHTML = '<div style="padding:40px;color:#f85149;text-align:center"><h3>Plotly not loaded</h3><p>CDN unreachable. Check your internet connection.</p></div>';
    return;
  }

  try {"""
html = html.replace(old_start, new_start, 1)
print("Fix 3: normTs + Plotly check + try-catch")

# 4. Add normalized timestamps
old_varc = "  var c = t.candles, n = c.length;\n  var traces = [];"
new_varc = """  var c = t.candles, n = c.length;
  var entryTsNorm = normTs(t.entry_ts);
  var exitTsNorm = normTs(t.exit_ts);
  var sweepTsNorm = t.sweep ? normTs(t.sweep.ts) : '';
  var breachXnorm = t.breach_candle_ts;
  var traces = [];"""
html = html.replace(old_varc, new_varc, 1)
print("Fix 4: normalized timestamps")

# 5. Fix entry/exit vertical lines
html = html.replace('[t.entry_ts,t.entry_ts]', '[entryTsNorm,entryTsNorm]')
html = html.replace('[t.exit_ts,t.exit_ts]', '[exitTsNorm,exitTsNorm]')
html = html.replace('[t.entry_ts,c[n-1].x]', '[entryTsNorm,c[n-1].x]')
print("Fix 5: entry/exit line refs")

# 6. Fix findIndex calls
html = html.replace('d.x===t.entry_ts', 'd.x===entryTsNorm')
html = html.replace('d.x===t.exit_ts', 'd.x===exitTsNorm')
html = html.replace('d.x===t.sweep.ts', 'd.x===sweepTsNorm')
html = html.replace('d.x===breachX', 'd.x===breachXnorm')
print("Fix 6: findIndex calls")

# 7. Fix breach candle var declaration
html = html.replace(
    'var breachX = t.breach_candle_ts;\n  if (breachX)',
    'if (breachXnorm)'
)
print("Fix 7: breachX -> breachXnorm")

# 8. Add catch block before function nextTrade
# Look for Plotly.react line and the closing } before function nextTrade
old_end = "Plotly.react('chart-detail', traces, layout, config);\n}\n\nfunction nextTrade()"
new_end = """Plotly.react('chart-detail', traces, layout, config);

  } catch(err) {
    document.getElementById('chart-detail').innerHTML = '<div style="padding:40px;color:#f85149;text-align:center"><h3>Chart render error</h3><pre style="font-size:10px;text-align:left">'+err.message+'</pre></div>';
    console.error('showTrade error:', err);
  }
}

function nextTrade()"""
html = html.replace(old_end, new_end)
print("Fix 8: catch block")

with open("trade_detail_viewer.html", "w", encoding="utf-8") as f:
    f.write(html)
print("All fixes applied successfully!")
