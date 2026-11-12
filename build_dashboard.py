#!/usr/bin/env python3
"""Build the Easee energy dashboard (single self-contained HTML) from easee_log.csv.

Usage:  python build_dashboard.py easee_log.csv docs/index.html [--cdn] [--max-days 31]
  --cdn   load Plotly from a CDN instead of embedding it (smaller file; for web hosting)
"""
import sys, json, argparse, datetime as dt
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument('csv'); ap.add_argument('out')
ap.add_argument('--cdn', action='store_true'); ap.add_argument('--max-days', type=int, default=31)
args = ap.parse_args()

df = pd.read_csv(args.csv, parse_dates=['timestamp'])
df = df.dropna(subset=['timestamp']).drop_duplicates('timestamp').sort_values('timestamp').set_index('timestamp')
for c in ['charger_power_kw','import_power_kw','export_power_kw','import_energy_kwh','export_energy_kwh']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df[['import_power_kw','export_power_kw','charger_power_kw']] = df[['import_power_kw','export_power_kw','charger_power_kw']].fillna(0)
d = df.copy(); d['net'] = d.import_power_kw - d.export_power_kw
d['dt'] = d.index.to_series().diff().dt.total_seconds().fillna(10)
d['dtc'] = d.dt.clip(upper=180)
d['imp_kwh'] = d.import_power_kw*d.dtc/3600; d['exp_kwh'] = d.export_power_kw*d.dtc/3600
d['ev_kwh'] = d.charger_power_kw*d.dtc/3600
counter_h = d.import_energy_kwh.resample('1h').last().diff()   # computed on the full log

dates = sorted(set(d.index.date)); today = dates[-1]
F = lambda t: t.strftime('%Y-%m-%d %H:%M:%S')
def num(x, n=3):
    return None if (x is None or (isinstance(x, float) and np.isnan(x))) else round(float(x), n)

def build(v, label, multi, long_span):
    p = v.import_power_kw; out = {'label': label}
    imp = v.imp_kwh.sum(); exp = v.exp_kwh.sum()
    q = p.resample('15min').mean(); hrs = v.imp_kwh.resample('1h').sum()
    dur_h = v.dtc.sum()/3600
    tgt1 = (v.dtc[p > 1].sum()/v.dtc.sum()*100)
    nz = p[p > 0.1]; ex = v[v.export_power_kw > 0]
    solar = 'none'; sol_win = None
    if len(ex):                                   # solar window = the day with the most export
        best = ex.exp_kwh.groupby(ex.index.date).sum().idxmax()
        e1 = ex[ex.index.date == best]; sol_win = (e1.index.min(), e1.index.max())
        solar = f'{sol_win[0]:%H:%M}–{sol_win[1]:%H:%M}' if not multi else f'{sol_win[0]:%d %b} {sol_win[0]:%H:%M}–{sol_win[1]:%H:%M}'
    pk_t = p.idxmax()
    out['coverage'] = f'{v.index.min():%d %b %H:%M} → {v.index.max():%d %b %H:%M}  ·  {len(v):,} readings  ·  {dur_h:.1f} h covered'
    st_top = v.status.value_counts(normalize=True)
    out['idle'] = f'{st_top.iloc[0]*100:.0f}% {st_top.index[0]}'
    active = (v.charger_power_kw > 0.05); sessions = int(((active) & (~active.shift(fill_value=False))).sum())
    ev = v.ev_kwh.sum()
    out['kpis'] = [
     ['Energy imported', f'{imp:.2f} kWh', '#38bdf8'], ['Energy exported', f'{exp:.2f} kWh', '#34d399'],
     ['Net grid energy', f'{imp-exp:.2f} kWh', '#e2e8f0'], ['Mean load', f'{p.mean():.2f} kW', '#38bdf8'],
     ['Peak load', f'{p.max():.2f} kW', '#f43f5e'], ['Peak 15-min avg', f'{q.max():.2f} kW', '#f97316'],
     ['Load factor', f'{p.mean()/p.max()*100:.0f} %' if p.max() > 0 else '–', '#a78bfa'],
     ['Top-3 hour avg', f'{hrs.nlargest(3).mean():.2f} kWh', '#f97316'],
     ['Time > 1 kW', f'{tgt1:.1f} %', '#f43f5e'], ['Solar window', solar, '#34d399'],
     ['EV charging', f'{sessions} sess · {ev:.1f} kWh' if sessions else 'No sessions', '#e879f9' if sessions else '#94a3b8'],
     ['Base load', f'{nz.quantile(.1):.2f} kW' if len(nz) else '–', '#38bdf8']]
    # timeline
    res = '5min' if long_span else '30s'; step = 300 if long_span else 30
    m = v.net.resample(res).mean().ffill(limit=max(1, int(180/step)))
    st = np.where(m < 0, 4, np.where(m <= 0.1, 0, np.where(m <= 0.5, 1, np.where(m <= 1.0, 2, np.where(m <= 1.4, 3, 5)))))
    out['tl'] = {'t': [F(t) for t in m.index], 'y': [num(x) for x in m.values], 's': st.tolist(), 'w': step*1000}
    if sessions:
        evs = v.charger_power_kw.resample(res).mean().ffill(limit=max(1, int(180/step)))
        out['ev'] = {'t': [F(t) for t in evs.index], 'y': [num(x) for x in evs.values]}
    else: out['ev'] = None
    out['mean'] = num(p.mean())
    mm = v.net.resample('1min').median().dropna(); dm = mm.diff(); big = dm[abs(dm) > 0.4].abs().sort_values(ascending=False)
    ann = []; used = []
    for t in big.index:
        if all(abs((t-u).total_seconds()) > 600 for u in used):
            used.append(t); ann.append({'t': F(t), 'y': num(mm[t]), 'text': f'{dm[t]:+.2f} kW step'})
        if len(ann) >= 5: break
    if len(ex):
        tpk = v.export_power_kw.idxmax(); ann.append({'t': F(tpk), 'y': num(-v.export_power_kw.max()), 'text': f'Solar export peak {v.export_power_kw.max():.2f} kW'})
    out['ann'] = ann
    gaps = v[v.dt >= 100]; out['gaps'] = [[F(t-pd.Timedelta(seconds=s)), F(t)] for t, s in zip(gaps.index, gaps.dt)][:40]
    slow = v[(v.dt >= 50) & (v.dt <= 75)]
    out['slow'] = [F(slow.index.min()), F(slow.index.max())] if len(slow) >= 5 and not long_span else None
    h = pd.DataFrame({'imp': v.imp_kwh.resample('1h').sum(), 'exp': v.exp_kwh.resample('1h').sum()})
    h['cnt'] = counter_h.reindex(h.index)
    lab = [(f'{t:%d %b %Hh}' if multi else f'{t:%H}h') for t in h.index]
    out['hour'] = {'x': lab, 'imp': [num(x) for x in h.imp], 'exp': [num(x) for x in h.exp], 'cnt': [num(x) for x in h.cnt]}
    s = np.sort(p.values)[::-1]; idx = np.linspace(0, len(s)-1, 250).astype(int)
    out['ldc'] = {'x': (idx/max(len(s)-1, 1)*100).round(2).tolist(), 'y': s[idx].round(3).tolist()}
    bins = [-.01, .1, .5, 1.0, 1.4, 99]; lv = pd.cut(p, bins, labels=range(5))
    tt = v.dtc.groupby(lv, observed=False).sum(); ee = v.imp_kwh.groupby(lv, observed=False).sum()
    out['lv'] = {'t': (tt/tt.sum()*100).round(1).tolist(), 'e': (ee/ee.sum()*100).round(1).tolist() if ee.sum() > 0 else [0]*5}
    cres = '10min' if long_span else '1min'
    ci = v.imp_kwh.cumsum().resample(cres).last().ffill(); ce = v.exp_kwh.cumsum().resample(cres).last().ffill()
    out['cum'] = {'t': [F(t) for t in ci.index], 'imp': ci.round(3).tolist(), 'exp': ce.round(3).tolist(), 'net': (ci-ce).round(3).tolist()}
    if sol_win:
        w = v[sol_win[0]-pd.Timedelta(minutes=45):sol_win[1]+pd.Timedelta(minutes=45)].resample('1min').mean(numeric_only=True)
        wi = v[sol_win[0]:sol_win[1]].imp_kwh.sum(); we = v[sol_win[0]:sol_win[1]].exp_kwh.sum()
        out['sol'] = {'t': [F(t) for t in w.index], 'imp': [num(x) for x in w.import_power_kw], 'exp': [num(x) for x in w.export_power_kw],
                      'txt': f'Window export {we:.2f} kWh · import {wi:.2f} kWh'}
    else: out['sol'] = None
    seg = (p.diff().abs() > 0.15).cumsum()
    S = p.groupby(seg).agg(start=lambda s: s.index[0], end=lambda s: s.index[-1], mean='mean')
    S['dur'] = (S.end-S.start).dt.total_seconds()/60; S = S[S.dur >= 10]
    out['plat'] = [[f'{r.start:%d %b %H:%M}' if multi else f'{r.start:%H:%M}', f'{r.end:%H:%M}', f'{r.dur:.0f} min', f'{r["mean"]:.2f}',
                    f'{v.imp_kwh[r.start:r.end].sum():.2f}'] for _, r in S.iterrows()][-60:]
    hi = p > 1; ins = []
    if sessions: ins.append(['#e879f9', f'{sessions} EV charging session(s), {ev:.1f} kWh delivered; peak charger power {v.charger_power_kw.max():.1f} kW.'])
    else: ins.append(['#94a3b8', f'Charger was {out["idle"]} with 0 kW – no EV charging in this period; analysis reflects site grid load.'])
    if hi.any(): ins.append(['#f43f5e', f'{v.dtc[hi].sum()/v.dtc.sum()*100:.0f}% of time above 1 kW delivers {v.imp_kwh[hi].sum()/imp*100:.0f}% of energy; load factor {p.mean()/p.max()*100:.0f}%.'])
    else: ins.append(['#38bdf8', f'Load never exceeded 1 kW (peak {p.max():.2f} kW at {pk_t:%H:%M}).'])
    tp = hrs.nlargest(3); ins.append(['#f97316', 'Busiest hours: '+', '.join(f'{t:%H}:00 ({x:.2f} kWh)' for t, x in tp.items())+f' – avg {tp.mean():.2f} kWh.'])
    ins.append(['#34d399', (f'{exp:.2f} kWh solar export, main window {solar}.' if len(ex) else 'No solar export in this period (no net feed-in).')])
    ng = len(gaps)
    ins.append(['#a78bfa', f'Data quality: {ng} logging gap(s) ≥100 s'+('; sampling slows to ~61 s from '+out["slow"][0][11:16] if out['slow'] else '')+'.' if ng or out['slow'] else 'Data quality: continuous sampling, no gaps.'])
    ins.append(['#38bdf8', 'Meter energy counter lags power by ~1 h – time-of-day analysis uses integrated power.'])
    out['ins'] = ins
    return out

views = {}; order = []
for dt_ in reversed(dates[-args.max_days:]):
    v = d[d.index.date == dt_]
    if len(v) < 30: continue
    key = str(dt_); views[key] = build(v, f'{pd.Timestamp(dt_):%d %b}'+(' · Today' if dt_ == today else ''), False, False); order.append(key)
span_days = (d.index.max()-d.index.min()).total_seconds()/86400
vall = d[d.index >= pd.Timestamp(dates[-args.max_days:][0])]
views['all'] = build(vall, 'All data', True, span_days > 1.5); order.append('all')
meta = {'generated': dt.datetime.utcnow().strftime('%d %b %Y %H:%M UTC'), 'last': f'{d.index.max():%d %b %Y %H:%M}'}
data = {'order': order, 'default': order[0], 'views': views, 'meta': meta}

TEMPLATE = r'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Easee Energy Analytics Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f172a;--card:#1e293b;--tx:#e2e8f0;--mut:#94a3b8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:Segoe UI,Helvetica,Arial,sans-serif;padding:18px 24px}
h1{margin:0;font-size:26px;letter-spacing:.5px}.sub{color:var(--mut);font-size:13px;margin-top:4px}
.top{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;margin-bottom:14px}
.nav{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.btn,select{background:var(--card);color:var(--tx);border:1px solid #334155;border-radius:8px;padding:9px 15px;font-size:13px;cursor:pointer;transition:.15s}
.btn:hover,select:hover{border-color:#38bdf8}.btn.on{background:#38bdf8;color:#0f172a;font-weight:700;border-color:#38bdf8}
.arrow{padding:9px 12px}.sep{width:1px;height:26px;background:#334155;margin:0 6px}
.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:12px}
.kpi{background:var(--card);border-radius:8px;padding:10px 14px;border-left:4px solid}
.kpi .l{font-size:11px;color:var(--mut)}.kpi .v{font-size:19px;font-weight:700;margin-top:3px}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px}
.card{background:var(--card);border-radius:10px;padding:12px 14px 6px}
.card h3{margin:0 0 4px;font-size:13px;letter-spacing:.4px}
.c12{grid-column:span 12}.c5{grid-column:span 5}.c4{grid-column:span 4}.c3{grid-column:span 3}.c7{grid-column:span 7}
table{width:100%;border-collapse:collapse;font-size:13px}th{color:var(--mut);text-align:left;padding:4px 6px}td{padding:5px 6px;border-top:1px solid #2d3b50}
.ins{font-size:13px;line-height:1.45}.ins div{display:flex;gap:9px;margin:9px 0}.dot{font-size:16px;line-height:1.2}
.pill{display:inline-block;background:#334155;border-radius:999px;padding:2px 10px;font-size:11px;margin-left:8px;color:#e2e8f0}
.foot{color:var(--mut);font-size:11px;margin-top:12px;text-align:right}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.c5,.c4,.c3,.c7{grid-column:span 12}}
</style>__PLOTLY__</head><body>
<div class="top"><div><h1>EASEE CHARGER SITE · ENERGY ANALYTICS DASHBOARD</h1><div class="sub" id="cov"></div></div>
<div class="nav" id="nav"></div></div>
<div class="kpis" id="kpis"></div>
<div class="grid">
<div class="card c12"><h3>GRID POWER TIMELINE — load states, steps &amp; solar export</h3><div id="tl" style="height:340px"></div></div>
<div class="card c5"><h3>HOURLY ENERGY (kWh) — import vs export</h3><div id="hr" style="height:260px"></div></div>
<div class="card c4"><h3>LOAD DURATION CURVE</h3><div id="ldc" style="height:260px"></div></div>
<div class="card c3"><h3>WHERE THE ENERGY GOES</h3><div id="lv" style="height:260px"></div></div>
<div class="card c4"><h3>CUMULATIVE ENERGY (integrated)</h3><div id="cum" style="height:260px"></div></div>
<div class="card c4"><h3>SOLAR EXPORT WINDOW</h3><div id="sol" style="height:260px"></div></div>
<div class="card c4"><h3>METER COUNTER vs INTEGRATED POWER</h3><div id="cnt" style="height:260px"></div></div>
<div class="card c7"><h3>DETECTED LOAD PLATEAUS (≥ 10 min)</h3><div id="plat" style="max-height:300px;overflow:auto"></div></div>
<div class="card c5"><h3>KEY INSIGHTS</h3><div class="ins" id="ins"></div></div>
</div>
<div class="foot" id="foot"></div>
<script>
const DATA=__DATA__;
const CARD='#1e293b',TX='#e2e8f0',MUT='#94a3b8',GR='#334155';
const SC=[['≈0','#64748b'],['Base 0.1–0.5','#0ea5e9'],['Medium 0.5–1.0','#f59e0b'],['High 1.0–1.4','#f97316'],['Export','#34d399'],['Peak >1.4','#ef4444']];
const cfg={displayModeBar:false,responsive:true};
function lay(o){o=o||{};const b={paper_bgcolor:CARD,plot_bgcolor:CARD,font:{color:MUT,size:11},margin:{l:48,r:12,t:6,b:38},
 legend:{orientation:'h',y:1.12,font:{color:TX,size:10}},hovermode:'x unified'};
 const r=Object.assign({},b,o);r.xaxis=Object.assign({gridcolor:GR,zeroline:false},o.xaxis||{});r.yaxis=Object.assign({gridcolor:GR,zeroline:false},o.yaxis||{});return r}
const h0=decodeURIComponent(location.hash.slice(1));
let cur=DATA.views[h0]?h0:DATA.default;
const days=DATA.order.filter(k=>k!=='all');
function nav(){const n=document.getElementById('nav');n.innerHTML='';
 const mk=(t,f,cls)=>{const b=document.createElement('button');b.className='btn '+(cls||'');b.textContent=t;b.onclick=f;n.appendChild(b);return b};
 const di=days.indexOf(cur);
 mk('◀',()=>{if(di>=0&&di<days.length-1)set(days[di+1])},'arrow');
 const SHOW=7;
 days.slice(0,SHOW).forEach(k=>mk(DATA.views[k].label,()=>set(k),k===cur?'on':''));
 if(days.length>SHOW){const s=document.createElement('select');s.innerHTML='<option value="">Older…</option>'+days.slice(SHOW).map(k=>`<option value="${k}"${k===cur?' selected':''}>${DATA.views[k].label}</option>`).join('');
  s.onchange=()=>{if(s.value)set(s.value)};n.appendChild(s)}
 mk('▶',()=>{if(di>0)set(days[di-1])},'arrow');
 n.appendChild(Object.assign(document.createElement('span'),{className:'sep'}));
 mk(DATA.views.all.label,()=>set('all'),cur==='all'?'on':'')}
function set(k){cur=k;location.hash=k;nav();render()}
function render(){const v=DATA.views[cur];
 document.getElementById('cov').innerHTML=v.coverage+'<span class="pill">Charger: '+v.idle+'</span>';
 document.getElementById('foot').textContent='Latest reading '+DATA.meta.last+' · dashboard built '+DATA.meta.generated+' · page refreshes automatically every 10 min';
 document.getElementById('kpis').innerHTML=v.kpis.map(k=>`<div class="kpi" style="border-color:${k[2]}"><div class="l">${k[0]}</div><div class="v" style="color:${k[2]}">${k[1]}</div></div>`).join('');
 const tr=SC.map((s,i)=>({type:'bar',name:s[0],x:v.tl.t,y:v.tl.y.map((y,j)=>v.tl.s[j]===i?y:null),marker:{color:s[1]},width:v.tl.w,hovertemplate:'%{y:.2f} kW'}));
 tr.push({type:'scatter',mode:'lines',name:'Mean '+v.mean+' kW',x:[v.tl.t[0],v.tl.t[v.tl.t.length-1]],y:[v.mean,v.mean],line:{color:'#a78bfa',dash:'dash',width:1.2},hoverinfo:'skip'});
 if(v.ev)tr.push({type:'scatter',mode:'lines',name:'Charger power',x:v.ev.t,y:v.ev.y,line:{color:'#e879f9',width:2}});
 const shapes=[],anns=[];
 v.gaps.forEach(g=>shapes.push({type:'rect',xref:'x',yref:'paper',x0:g[0],x1:g[1],y0:0,y1:1,fillcolor:'#f43f5e',opacity:.35,line:{width:0}}));
 if(v.slow)shapes.push({type:'rect',xref:'x',yref:'paper',x0:v.slow[0],x1:v.slow[1],y0:0,y1:1,fillcolor:'#ffffff',opacity:.06,line:{width:0}});
 v.ann.forEach(a=>anns.push({x:a.t,y:a.y,text:a.text,showarrow:true,arrowcolor:MUT,arrowwidth:1,ax:0,ay:a.y>=0?-34:34,font:{color:TX,size:10},bgcolor:'rgba(15,23,42,.8)'}));
 const ys=v.tl.y.filter(x=>x!==null),evm=v.ev?Math.max(...v.ev.y.filter(x=>x!==null)):0;
 Plotly.react('tl',tr,lay({barmode:'overlay',bargap:0,shapes:shapes,annotations:anns,yaxis:{title:'kW (import − export)',range:[Math.min(-.7,Math.min(...ys)-.25),Math.max(Math.max(...ys),evm)+.6]},xaxis:{tickformat:cur==='all'?'%d %b %H:%M':'%H:%M'},legend:{orientation:'h',y:1.1,font:{color:TX,size:10}}}),cfg);
 Plotly.react('hr',[{type:'bar',name:'Import',x:v.hour.x,y:v.hour.imp,marker:{color:'#38bdf8'},text:v.hour.imp.map(x=>x==null?'':x.toFixed(2)),textposition:'outside',textfont:{color:TX,size:10}},
  {type:'bar',name:'Export',x:v.hour.x,y:v.hour.exp.map(x=>x?-x:0),marker:{color:'#34d399'}}],lay({barmode:'relative',yaxis:{title:'kWh',range:[-0.35,Math.max(...v.hour.imp)*1.25+.05]},xaxis:{type:'category'}}),cfg);
 Plotly.react('ldc',[{type:'scatter',x:v.ldc.x,y:v.ldc.y,fill:'tozeroy',fillcolor:'rgba(56,189,248,.25)',line:{color:'#38bdf8',width:2},name:'Load',hovertemplate:'%{y:.2f} kW exceeded %{x:.0f}% of time'}],
  lay({showlegend:false,xaxis:{title:'% of time exceeded'},yaxis:{title:'kW'},shapes:[{type:'line',xref:'paper',x0:0,x1:1,y0:1,y1:1,line:{color:'#f97316',dash:'dash',width:1}},{type:'line',xref:'paper',x0:0,x1:1,y0:v.mean,y1:v.mean,line:{color:'#a78bfa',dash:'dash',width:1}}]}),cfg);
 const L=['≈0','Base','Medium','High','Peak'];
 Plotly.react('lv',[{type:'bar',name:'% time',x:L,y:v.lv.t,marker:{color:MUT},text:v.lv.t.map(x=>x.toFixed(0)),textposition:'outside',textfont:{color:TX,size:10}},
  {type:'bar',name:'% energy',x:L,y:v.lv.e,marker:{color:'#f97316'},text:v.lv.e.map(x=>x.toFixed(0)),textposition:'outside',textfont:{color:TX,size:10}}],lay({barmode:'group',yaxis:{range:[0,Math.max(...v.lv.t,...v.lv.e)*1.2+5]}}),cfg);
 Plotly.react('cum',[{type:'scatter',name:'Import',x:v.cum.t,y:v.cum.imp,line:{color:'#38bdf8',width:2}},{type:'scatter',name:'Export',x:v.cum.t,y:v.cum.exp,line:{color:'#34d399',width:2}},
  {type:'scatter',name:'Net',x:v.cum.t,y:v.cum.net,line:{color:TX,width:1.2,dash:'dash'}}],lay({yaxis:{title:'kWh'},xaxis:{tickformat:cur==='all'?'%d %b %H:%M':'%H:%M'}}),cfg);
 if(v.sol){Plotly.react('sol',[{type:'scatter',name:'Import',x:v.sol.t,y:v.sol.imp,fill:'tozeroy',line:{color:'#f43f5e',width:1},fillcolor:'rgba(244,63,94,.5)'},
  {type:'scatter',name:'Export',x:v.sol.t,y:v.sol.exp,fill:'tozeroy',line:{color:'#34d399',width:1},fillcolor:'rgba(52,211,153,.6)'}],
  lay({yaxis:{title:'kW'},xaxis:{tickformat:'%H:%M'},annotations:[{xref:'paper',yref:'paper',x:.98,y:.98,xanchor:'right',yanchor:'top',showarrow:false,text:v.sol.txt,font:{color:TX,size:11}}]}),cfg)}
 else Plotly.react('sol',[],lay({xaxis:{visible:false},yaxis:{visible:false},annotations:[{xref:'paper',yref:'paper',x:.5,y:.5,showarrow:false,text:'No solar export recorded<br>in this period',font:{color:MUT,size:14}}]}),cfg);
 Plotly.react('cnt',[{type:'bar',name:'Integrated power',x:v.hour.x,y:v.hour.imp,marker:{color:'#38bdf8'}},{type:'bar',name:'Counter increment',x:v.hour.x,y:v.hour.cnt,marker:{color:'#f97316'}}],
  lay({barmode:'group',yaxis:{title:'kWh'},xaxis:{type:'category'}}),cfg);
 document.getElementById('plat').innerHTML=v.plat.length?'<table><tr><th>Start</th><th>End</th><th>Duration</th><th>Avg kW</th><th>kWh</th></tr>'+v.plat.map(r=>{const a=parseFloat(r[3]);const c=a>1.4?'#f43f5e':a>1?'#f97316':a>.5?'#38bdf8':MUT;
  return `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td style="color:${c};font-weight:700">${r[3]}</td><td>${r[4]}</td></tr>`}).join('')+'</table>':'<div style="color:#94a3b8;padding:12px">No stable load plateau ≥ 10 min in this period.</div>';
 document.getElementById('ins').innerHTML=v.ins.map(i=>`<div><span class="dot" style="color:${i[0]}">●</span><span>${i[1]}</span></div>`).join('')}
document.addEventListener('keydown',e=>{const i=days.indexOf(cur);
 if(e.key==='ArrowLeft'&&i>=0&&i<days.length-1)set(days[i+1]);if(e.key==='ArrowRight'&&i>0)set(days[i-1])});
setInterval(()=>location.reload(),600000);
nav();render();
</script></body></html>'''
if args.cdn: plotly = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
else:
    from plotly.offline import get_plotlyjs; plotly = '<script>'+get_plotlyjs()+'</script>'
html = TEMPLATE.replace('__PLOTLY__', plotly).replace('__DATA__', json.dumps(data))
open(args.out, 'w', encoding='utf-8').write(html)
print(f'Wrote {args.out}: {len(html)/1e6:.1f} MB, views={order}, last reading {meta["last"]}')
