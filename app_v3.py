import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(page_title="Stock Dashboard v2", page_icon="📈", layout="wide")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]
FMP_KEY     = st.secrets["FMP_API_KEY"]
POLY_BASE   = "https://api.polygon.io"
FMP_BASE    = "https://financialmodelingprep.com/api/v3"

def fmt_price(n):
    return f"${n:,.2f}" if n else "N/A"
def fmt_big(n):
    if not n: return "N/A"
    if n >= 1e12: return f"${n/1e12:.2f}T"
    if n >= 1e9:  return f"${n/1e9:.1f}B"
    if n >= 1e6:  return f"${n/1e6:.1f}M"
    return f"${n:,.0f}"
def fmt_vol(n):
    if not n: return "N/A"
    if n >= 1e9: return f"{n/1e9:.2f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.0f}K"
    return str(int(n))
def fmt_pct(n, dec=1):
    return f"{n:.{dec}f}%" if n is not None else "N/A"

def classify_mktcap(mc):
    if not mc: return ("Desconocida","#888")
    if mc >= 200e9: return ("Mega-cap","#534AB7")
    if mc >= 10e9:  return ("Large-cap","#185FA5")
    if mc >= 2e9:   return ("Mid-cap","#BA7517")
    if mc >= 300e6: return ("Small-cap","#D85A30")
    if mc >= 50e6:  return ("Micro-cap","#E24B4A")
    return ("Nano-cap","#A32D2D")

def score_label(s, labels=None, colors=None):
    labels = labels or {1:"Muy bajo",2:"Bajo",3:"Medio",4:"Alto",5:"Muy alto"}
    colors = colors or {1:"#1D9E75",2:"#1D9E75",3:"#BA7517",4:"#D85A30",5:"#E24B4A"}
    s = min(5, max(1, int(round(s))))
    return s, labels[s], colors[s]

def score_volatility(beta, chg_pct):
    b, c = abs(beta or 1), abs(chg_pct or 0)
    if b>2 or c>5: s=5
    elif b>1.5 or c>3: s=4
    elif b>1.2: s=3
    elif b>0.8: s=2
    else: s=1
    return score_label(s)

def score_liquidity(avg_vol):
    v = avg_vol or 0
    if v>50e6: s=5
    elif v>10e6: s=4
    elif v>1e6: s=3
    elif v>100e3: s=2
    else: s=1
    return score_label(s,{1:"Escasa",2:"Limitada",3:"Media",4:"Alta",5:"Máxima"},
                         {1:"#E24B4A",2:"#D85A30",3:"#BA7517",4:"#1D9E75",5:"#1D9E75"})

def score_risk(beta, mc):
    s = 1.0
    b = beta or 1
    if b>2: s+=2
    elif b>1.5: s+=1.5
    elif b>1.2: s+=1
    if mc:
        if mc<50e6: s+=2
        elif mc<300e6: s+=1.5
        elif mc<2e9: s+=1
    return score_label(s)

def gen_strategy(ticker, mc_label, price, low52, high52):
    near_low  = price and low52  and (price-low52)/(low52 or 1) < 0.08
    near_high = price and high52 and (high52-price)/(high52 or 1) < 0.05
    if mc_label in ("Mega-cap","Large-cap"):
        if near_low:
            return ("Cerca del mínimo 52s — zona de soporte. Espera vela verde con volumen. Stop en mínimo reciente.",
                    "Stop en mínimo 52s · Target resistencia cercana",
                    "Calls OTM 30-45 días si IV no está inflada. Bull call spread para reducir prima.",
                    "Calls OTM 30-45d · Bull call spread · Revisar IV")
        if near_high:
            return ("Cerca de máximos. No perseguir el precio. Espera pullback al soporte.",
                    "Esperar pullback · No perseguir máximos",
                    "Momentum con stop bajo último swing low. Covered calls si ya tienes posición.",
                    "Momentum con stop · Covered calls si long")
        return ("Busca confluencia técnica (soporte + volumen) para swing de 1-2 semanas.",
                "Swing en soporte/resistencia · 1-2 semanas",
                "Earnings play si hay reporte próximo. Straddle pre-earnings o posición direccional.",
                "Earnings play · Straddle · Posición direccional")
    if mc_label in ("Mid-cap","Small-cap"):
        return ("Posición reducida (máx 3-5% portafolio). Stop obligatorio. Solo con catalizador claro.",
                "Posición reducida · Stop obligatorio · Catalizador",
                "Breakout con volumen. Catalizadores binarios. Revisar short interest para posible squeeze.",
                "Breakout · Catalizador binario · Short interest")
    return ("Máx 1-2% portafolio. Catalizador inmediato. Define salida ANTES de entrar.",
            "Máx 1-2% portafolio · Salida predefinida",
            "Especulación pura. Stop muy ajustado. Nunca promedies a la baja.",
            "Stop estricto · Sin promediar · Cuidar spread")

def gen_conclusion(ticker, mc_label, rev_growth, net_margin, fcf, debt_eq,
                   pe, roe, price, low52, high52):
    signals, concerns, score = [], [], 50
    if rev_growth is not None:
        if rev_growth>15:  signals.append(f"Crecimiento de ingresos sólido (+{rev_growth:.1f}% YoY)"); score+=10
        elif rev_growth>5: signals.append(f"Crecimiento moderado de ingresos (+{rev_growth:.1f}%)"); score+=5
        elif rev_growth<0: concerns.append(f"Ingresos en contracción ({rev_growth:.1f}%)"); score-=10
    if net_margin is not None:
        if net_margin>20:   signals.append(f"Margen neto excelente ({net_margin:.1f}%)"); score+=10
        elif net_margin>10: signals.append(f"Margen neto saludable ({net_margin:.1f}%)"); score+=5
        elif net_margin<0:  concerns.append(f"Empresa no rentable (margen {net_margin:.1f}%)"); score-=15
    if fcf is not None:
        if fcf>0:  signals.append("Genera free cash flow positivo"); score+=8
        else:      concerns.append("Free cash flow negativo — quema caja"); score-=10
    if debt_eq is not None:
        if debt_eq<0.5:   signals.append(f"Balance financiero sólido (D/E {debt_eq:.2f}x)"); score+=5
        elif debt_eq>2.0: concerns.append(f"Apalancamiento elevado (D/E {debt_eq:.2f}x)"); score-=8
    if pe is not None and pe>0:
        if pe<15:   signals.append(f"Valuación atractiva (P/E {pe:.1f}x)"); score+=8
        elif pe<30: signals.append(f"Valuación razonable (P/E {pe:.1f}x)"); score+=3
        elif pe>50: concerns.append(f"Valuación exigente (P/E {pe:.1f}x)"); score-=5
    if roe is not None:
        if roe>20:  signals.append(f"ROE alto — negocio eficiente ({roe:.1f}%)"); score+=7
        elif roe<0: concerns.append(f"ROE negativo ({roe:.1f}%)"); score-=8
    if price and low52 and high52:
        if (price-low52)/(low52 or 1)<0.10:
            signals.append("Precio cerca de soporte histórico 52s — R/R favorable")
    score = max(0, min(100, score))
    if score>=75:   rating,rc,stance = "POSITIVO","#1D9E75","COMPRA / ACUMULACIÓN"
    elif score>=55: rating,rc,stance = "NEUTRAL-POSITIVO","#BA7517","MANTENER / ENTRADA SELECTIVA"
    elif score>=40: rating,rc,stance = "NEUTRAL","#888","OBSERVAR / SIN POSICIÓN"
    else:           rating,rc,stance = "NEGATIVO","#E24B4A","CAUTELA / EVITAR"
    bp = "\n".join([f"- {s}" for s in signals]) if signals else "- Sin señales positivas claras"
    bn = "\n".join([f"- {c}" for c in concerns]) if concerns else "- Sin riesgos críticos identificados"
    return score, rating, rc, stance, bp, bn

@st.cache_data(ttl=300)
def poly_details(t):
    r = requests.get(f"{POLY_BASE}/v3/reference/tickers/{t}",
                     params={"apiKey":POLYGON_KEY}, timeout=10)
    return r.json().get("results",{}) if r.ok else {}

@st.cache_data(ttl=300)
def poly_prev(t):
    r = requests.get(f"{POLY_BASE}/v2/aggs/ticker/{t}/prev",
                     params={"adjusted":"true","apiKey":POLYGON_KEY}, timeout=10)
    res = r.json().get("results",[])
    return res[0] if res else {}

@st.cache_data(ttl=300)
def poly_aggs(t, days=90):
    to_d   = datetime.today().strftime("%Y-%m-%d")
    from_d = (datetime.today()-timedelta(days=days)).strftime("%Y-%m-%d")
    r = requests.get(f"{POLY_BASE}/v2/aggs/ticker/{t}/range/1/day/{from_d}/{to_d}",
                     params={"adjusted":"true","sort":"asc","limit":150,"apiKey":POLYGON_KEY},
                     timeout=10)
    return r.json().get("results",[]) if r.ok else []

@st.cache_data(ttl=300)
def poly_news(t):
    r = requests.get(f"{POLY_BASE}/v2/reference/news",
                     params={"ticker":t,"limit":4,"order":"desc","apiKey":POLYGON_KEY},
                     timeout=10)
    return r.json().get("results",[]) if r.ok else []

@st.cache_data(ttl=3600)
def fmp_get(endpoint, t, extra=None):
    params = {"apikey": FMP_KEY}
    if extra: params.update(extra)
    r = requests.get(f"{FMP_BASE}/{endpoint}/{t}", params=params, timeout=10)
    data = r.json() if r.ok else []
    return data if isinstance(data, list) else []

@st.cache_data(ttl=3600)
def fmp_profile(t):
    r = requests.get(f"{FMP_BASE}/profile/{t}", params={"apikey":FMP_KEY}, timeout=10)
    data = r.json()
    return data[0] if r.ok and isinstance(data,list) and data else {}

def price_chart(bars):
    if not bars: st.caption("Sin datos."); return
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"], unit="ms")
    is_up = df["c"].iloc[-1] >= df["c"].iloc[0]
    color = "#1D9E75" if is_up else "#E24B4A"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["c"], mode="lines",
        line=dict(color=color,width=1.8),
        hovertemplate="%{x|%d %b}<br>$%{y:.2f}<extra></extra>"))
    fig.update_layout(height=200, margin=dict(l=0,r=0,t=8,b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True,gridcolor="rgba(128,128,128,0.12)",
                   tickfont=dict(size=10),showline=False),
        yaxis=dict(showgrid=True,gridcolor="rgba(128,128,128,0.12)",
                   tickfont=dict(size=10),tickprefix="$",side="right"),
        showlegend=False)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

def rev_chart(income):
    if not income or len(income)<2: return
    rows = income[:4][::-1]
    years = [r.get("calendarYear", r.get("date","")[:4]) for r in rows]
    revs  = [r.get("revenue",0) for r in rows]
    nets  = [r.get("netIncome",0) for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=years,y=revs,name="Ingresos",marker_color="#378ADD",
        hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Bar(x=years,y=nets,name="Utilidad neta",marker_color="#1D9E75",
        hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>"))
    fig.update_layout(height=200,barmode="group",
        margin=dict(l=0,r=0,t=8,b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(tickfont=dict(size=10),showgrid=False),
        yaxis=dict(tickfont=dict(size=10),gridcolor="rgba(128,128,128,0.12)",side="right"),
        legend=dict(font=dict(size=10),orientation="h",y=1.12),showlegend=True)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

def range_bar(price, low52, high52):
    if not all([price,low52,high52]): return
    pct = max(0,min(100,(price-low52)/(high52-low52)*100))
    st.markdown(
        f"<div style='position:relative;height:6px;border-radius:3px;"
        f"background:#e0e0e0;margin:6px 0 4px'>"
        f"<div style='position:absolute;height:6px;border-radius:3px;"
        f"background:#378ADD;width:{pct:.0f}%'></div>"
        f"<div style='position:absolute;width:10px;height:10px;border-radius:50%;"
        f"background:#185FA5;top:-2px;left:{pct:.0f}%;transform:translateX(-50%)'></div>"
        f"</div><div style='display:flex;justify-content:space-between;"
        f"font-size:11px;color:gray'>"
        f"<span>{fmt_price(low52)}</span><span>52-week range</span>"
        f"<span>{fmt_price(high52)}</span></div>",
        unsafe_allow_html=True)

def score_card(label, score, text, color):
    st.markdown(
        f"<div style='background:var(--secondary-background-color,#f7f7f5);"
        f"border-radius:10px;padding:14px'>"
        f"<div style='font-size:10px;color:gray;text-transform:uppercase;"
        f"letter-spacing:.05em;margin-bottom:4px'>{label}</div>"
        f"<div style='font-size:16px;font-weight:600;color:{color}'>{text}</div>"
        f"<div style='height:4px;border-radius:2px;background:#e0e0e0;margin:8px 0 4px'>"
        f"<div style='height:4px;border-radius:2px;background:{color};"
        f"width:{score*20}%'></div></div>"
        f"<div style='font-size:11px;color:gray'>Score {score}/5</div></div>",
        unsafe_allow_html=True)

# ═══════════════════════  UI  ════════════════════════════════════════════════
st.markdown(
    "<h2 style='margin-bottom:2px'>📈 Stock Dashboard v2</h2>"
    "<p style='color:gray;font-size:12px;margin-bottom:16px'>"
    "Precio: Polygon.io · Fundamentals: Financial Modeling Prep · End-of-day</p>",
    unsafe_allow_html=True)

col_in, col_btn = st.columns([5,1])
with col_in:
    ticker_raw = st.text_input("",placeholder="Ticker  (ej. AAPL, NKE, TSLA, NVDA...)",
                                label_visibility="collapsed").strip().upper()
with col_btn:
    st.markdown("<div style='height:4px'></div>",unsafe_allow_html=True)
    st.button("Analizar", use_container_width=True, type="primary")

if not ticker_raw:
    st.info("Ingresa un ticker y presiona Analizar.")
    st.stop()

with st.spinner(f"Cargando datos para {ticker_raw}..."):
    poly_info = poly_details(ticker_raw)
    prev      = poly_prev(ticker_raw)
    bars90    = poly_aggs(ticker_raw, 90)
    bars30    = poly_aggs(ticker_raw, 30)
    news      = poly_news(ticker_raw)
    profile   = fmp_profile(ticker_raw)
    income    = fmp_get("income-statement", ticker_raw, {"limit":"4"})
    balance   = fmp_get("balance-sheet-statement", ticker_raw, {"limit":"2"})
    cashflow  = fmp_get("cash-flow-statement", ticker_raw, {"limit":"2"})
    metrics   = fmp_get("key-metrics", ticker_raw, {"limit":"2"})
    ratios    = fmp_get("ratios", ticker_raw, {"limit":"2"})

if not prev:
    st.error(f"No se encontraron datos para **{ticker_raw}**. Verifica el ticker.")
    st.stop()

price   = prev.get("c")
open_p  = prev.get("o")
high_d  = prev.get("h")
low_d   = prev.get("l")
volume  = prev.get("v")
vwap    = prev.get("vw")
chg_amt = (price-open_p) if price and open_p else None
chg_pct = ((price-open_p)/open_p*100) if price and open_p else None
shares  = poly_info.get("share_class_shares_outstanding") or poly_info.get("weighted_shares_outstanding")
mktcap  = (price*shares) if price and shares else profile.get("mktCap")
highs   = [b["h"] for b in bars90]
lows    = [b["l"] for b in bars90]
vols    = [b["v"] for b in bars30]
high52  = max(highs) if highs else None
low52   = min(lows)  if lows  else None
avg_vol = sum(vols)/len(vols) if vols else None

mc_label, mc_color = classify_mktcap(mktcap)
v_s,v_l,v_c = score_volatility(profile.get("beta",1.2), chg_pct or 0)
l_s,l_l,l_c = score_liquidity(avg_vol)
r_s,r_l,r_c = score_risk(profile.get("beta",1.2), mktcap)

inc0  = income[0]  if income  else {}
inc1  = income[1]  if len(income)>1 else {}
bal0  = balance[0] if balance else {}
cf0   = cashflow[0] if cashflow else {}
met0  = metrics[0] if metrics else {}
rat0  = ratios[0]  if ratios  else {}

revenue      = inc0.get("revenue")
revenue_prev = inc1.get("revenue")
net_income   = inc0.get("netIncome")
gross_profit = inc0.get("grossProfit")
operating_inc= inc0.get("operatingIncome")
ebitda       = inc0.get("ebitda")
rev_growth   = ((revenue-revenue_prev)/abs(revenue_prev)*100) if revenue and revenue_prev and revenue_prev!=0 else None
gross_margin = (gross_profit/revenue*100) if gross_profit and revenue else None
net_margin   = (net_income/revenue*100)   if net_income  and revenue else None
op_margin    = (operating_inc/revenue*100) if operating_inc and revenue else None

total_debt   = bal0.get("totalDebt")
total_equity = bal0.get("totalStockholdersEquity")
cash         = bal0.get("cashAndCashEquivalents")
cur_assets   = bal0.get("totalCurrentAssets") or 0
cur_liab     = bal0.get("totalCurrentLiabilities") or 1
current_r    = cur_assets/cur_liab
debt_eq      = (total_debt/abs(total_equity)) if total_debt and total_equity and total_equity!=0 else None

fcf          = cf0.get("freeCashFlow")
op_cf        = cf0.get("operatingCashFlow")

pe_ratio  = rat0.get("priceEarningsRatio") or met0.get("peRatio")
pb_ratio  = rat0.get("priceToBookRatio")   or met0.get("pbRatio")
ps_ratio  = rat0.get("priceToSalesRatio")  or met0.get("priceToSalesRatio")
ev_ebitda = met0.get("enterpriseValueOverEBITDA")
roe       = rat0.get("returnOnEquity",0)*100 if rat0.get("returnOnEquity") else None
roa       = rat0.get("returnOnAssets",0)*100 if rat0.get("returnOnAssets") else None
fcf_yield = met0.get("freeCashFlowYield",0)*100 if met0.get("freeCashFlowYield") else None
div_yield = profile.get("lastDiv",0)/price*100 if profile.get("lastDiv") and price else None
sector    = profile.get("sector") or ""
desc_full = profile.get("description","Sin descripción disponible.")

b_strat,b_key,a_strat,a_key = gen_strategy(ticker_raw,mc_label,price,low52,high52)
sc_fund,rating,rc,stance,bp,bn = gen_conclusion(
    ticker_raw,mc_label,rev_growth,net_margin,fcf,debt_eq,pe_ratio,roe,price,low52,high52)

# ──────────────── HEADER ─────────────────────────────────────────────────────
st.divider()
h1c, h2c = st.columns([3,1])
with h1c:
    name = profile.get("companyName") or poly_info.get("name",ticker_raw)
    exch = profile.get("exchangeShortName") or poly_info.get("primary_exchange","")
    sdesc= (profile.get("sector","")) + (" · "+profile.get("industry","") if profile.get("industry") else "")
    st.markdown(
        f"<h1 style='font-size:30px;margin:0'>{ticker_raw}</h1>"
        f"<p style='color:gray;font-size:13px;margin:2px 0 8px'>{name} · {exch} · {sdesc}</p>"
        f"<span style='background:{mc_color}22;color:{mc_color};font-size:12px;"
        f"font-weight:600;padding:4px 12px;border-radius:10px'>"
        f"{mc_label} · {fmt_big(mktcap)}</span>",unsafe_allow_html=True)
with h2c:
    s = "+" if (chg_pct or 0)>=0 else ""
    c = "green" if (chg_pct or 0)>=0 else "red"
    st.markdown(
        f"<div style='text-align:right'>"
        f"<div style='font-size:30px;font-weight:600'>{fmt_price(price)}</div>"
        f"<div style='color:{c};font-size:13px;font-weight:600'>"
        f"{s}{fmt_price(chg_amt)} ({s}{(chg_pct or 0):.2f}%)</div>"
        f"<div style='color:gray;font-size:10px'>cierre anterior</div></div>",
        unsafe_allow_html=True)

st.markdown("#### Métricas del día")
c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("Apertura",   fmt_price(open_p))
c2.metric("Máximo",     fmt_price(high_d))
c3.metric("Mínimo",     fmt_price(low_d))
c4.metric("VWAP",       fmt_price(vwap))
c5.metric("Volumen",    fmt_vol(volume), f"Avg {fmt_vol(round(avg_vol) if avg_vol else None)}")
c6.metric("Market cap", fmt_big(mktcap))
st.markdown("**Rango 52 semanas**")
range_bar(price, low52, high52)

st.markdown("#### Precio histórico")
t30, t90 = st.tabs(["30 días","90 días"])
with t30: price_chart(bars30)
with t90: price_chart(bars90)

st.markdown("#### Indicadores técnicos")
i1,i2,i3 = st.columns(3)
with i1: score_card("b) Volatilidad",v_s,v_l,v_c)
with i2: score_card("c) Liquidez",   l_s,l_l,l_c)
with i3: score_card("d) Riesgo",     r_s,r_l,r_c)

st.markdown("#### Estrategia de trading")
s1,s2 = st.columns(2)
with s1:
    st.markdown("**🟦 Trader básico**"); st.info(b_strat); st.caption(f"Clave: {b_key}")
with s2:
    st.markdown("**🟧 Trader avanzado**"); st.warning(a_strat); st.caption(f"Clave: {a_key}")

# ──────────── ANÁLISIS FUNDAMENTAL ───────────────────────────────────────────
st.divider()
st.markdown("## 🧮 Análisis Fundamental")

if not income and not profile:
    st.warning("No se encontraron datos fundamentales para este ticker en FMP.")
else:
    with st.expander("📈 1) Crecimiento", expanded=True):
        g1,g2,g3,g4 = st.columns(4)
        g1.metric("Ingresos (TTM)",    fmt_big(revenue))
        g2.metric("Crecimiento YoY",   fmt_pct(rev_growth),
                  "↑ positivo" if (rev_growth or 0)>0 else "↓ negativo")
        g3.metric("EBITDA",            fmt_big(ebitda))
        g4.metric("Ingreso operativo", fmt_big(operating_inc))
        if income and len(income)>=2:
            st.caption("Ingresos vs Utilidad Neta — últimos años")
            rev_chart(income)

    with st.expander("💰 2) Rentabilidad", expanded=True):
        r1,r2,r3,r4 = st.columns(4)
        r1.metric("Margen bruto",    fmt_pct(gross_margin))
        r2.metric("Margen operativo",fmt_pct(op_margin))
        r3.metric("Margen neto",     fmt_pct(net_margin))
        r4.metric("ROE",             fmt_pct(roe))
        rr1,rr2 = st.columns(2)
        rr1.metric("ROA",fmt_pct(roa))
        rr2.metric("Utilidad neta",fmt_big(net_income))
        if net_margin is not None:
            if net_margin>20:   st.success(f"Margen neto excelente ({net_margin:.1f}%) — empresa muy rentable.")
            elif net_margin>10: st.info(f"Margen neto saludable ({net_margin:.1f}%).")
            elif net_margin>0:  st.warning(f"Margen neto ajustado ({net_margin:.1f}%) — monitorear costos.")
            else:               st.error(f"Margen neto negativo ({net_margin:.1f}%) — empresa no rentable.")

    with st.expander("💸 3) Free Cash Flow", expanded=True):
        f1,f2,f3 = st.columns(3)
        f1.metric("Free Cash Flow",     fmt_big(fcf))
        f2.metric("Cash Flow operativo",fmt_big(op_cf))
        f3.metric("FCF Yield",          fmt_pct(fcf_yield))
        if fcf is not None:
            if fcf>0: st.success(f"FCF positivo ({fmt_big(fcf)}) — genera caja real.")
            else:     st.error(f"FCF negativo ({fmt_big(fcf)}) — quema caja.")

    with st.expander("🏦 4) Salud Financiera", expanded=True):
        h1x,h2x,h3x,h4x = st.columns(4)
        h1x.metric("Deuda total",    fmt_big(total_debt))
        h2x.metric("Cash & equiv.",  fmt_big(cash))
        h3x.metric("Deuda / Equity", f"{debt_eq:.2f}x" if debt_eq is not None else "N/A")
        h4x.metric("Current Ratio",  f"{current_r:.2f}x" if current_r else "N/A")
        if debt_eq is not None:
            if debt_eq<0.5:   st.success(f"Balance sólido — bajo apalancamiento (D/E {debt_eq:.2f}x).")
            elif debt_eq<1.5: st.info(f"Apalancamiento moderado (D/E {debt_eq:.2f}x).")
            elif debt_eq<3.0: st.warning(f"Apalancamiento elevado (D/E {debt_eq:.2f}x) — monitorear.")
            else:             st.error(f"Apalancamiento muy alto (D/E {debt_eq:.2f}x) — riesgo significativo.")

    with st.expander("📦 5) Valuación", expanded=True):
        v1x,v2x,v3x,v4x = st.columns(4)
        v1x.metric("P/E",       f"{pe_ratio:.1f}x"  if pe_ratio  else "N/A")
        v2x.metric("P/B",       f"{pb_ratio:.1f}x"  if pb_ratio  else "N/A")
        v3x.metric("P/S",       f"{ps_ratio:.1f}x"  if ps_ratio  else "N/A")
        v4x.metric("EV/EBITDA", f"{ev_ebitda:.1f}x" if ev_ebitda else "N/A")
        vv1,vv2 = st.columns(2)
        vv1.metric("Div. yield", fmt_pct(div_yield))
        vv2.metric("Beta",       f"{profile.get('beta'):.2f}" if profile.get("beta") else "N/A")
        if pe_ratio and pe_ratio>0:
            if pe_ratio<15:   st.success(f"P/E {pe_ratio:.1f}x — valuación atractiva.")
            elif pe_ratio<30: st.info(f"P/E {pe_ratio:.1f}x — valuación razonable.")
            elif pe_ratio<50: st.warning(f"P/E {pe_ratio:.1f}x — valuación exigente.")
            else:             st.error(f"P/E {pe_ratio:.1f}x — valuación muy elevada, alto riesgo.")

    with st.expander("🧪 6) Calidad del Negocio", expanded=True):
        st.markdown("**Descripción del negocio**")
        st.write(desc_full[:600]+("..." if len(desc_full)>600 else ""))
        q1,q2 = st.columns(2)
        with q1:
            st.markdown("**🔁 Ingresos recurrentes**")
            sl = sector.lower()
            if any(x in sl for x in ["software","technology","saas","cloud","subscription"]):
                st.success("Sector con alto potencial de ingresos recurrentes (SaaS / suscripciones).")
            elif any(x in sl for x in ["consumer","retail","apparel","discretionary"]):
                st.warning("Ingresos mayormente transaccionales — cíclicos.")
            elif any(x in sl for x in ["utility","healthcare","pharma","staple"]):
                st.success("Sector defensivo — ingresos estables y predecibles.")
            else:
                st.info(f"Sector: {sector or 'N/A'}. Evalúa el modelo de ingresos en los reportes 10-K.")
        with q2:
            st.markdown("**🚀 Exposición a Mega tendencias**")
            dl = desc_full.lower()
            trends = []
            if any(x in dl for x in ["artificial intelligence","machine learning","ai ","generative"]):
                trends.append("Inteligencia Artificial")
            if any(x in dl for x in ["cloud","cloud computing","aws","azure"]):
                trends.append("Cloud Computing")
            if any(x in dl for x in ["electric vehicle","ev ","battery","renewable","clean energy"]):
                trends.append("Energía limpia / EV")
            if any(x in dl for x in ["semiconductor","chip","gpu","processor"]):
                trends.append("Semiconductores")
            if any(x in dl for x in ["genomic","biotech","drug","therapeutics"]):
                trends.append("Biotech / Salud")
            if any(x in dl for x in ["cyber","security","firewall","endpoint"]):
                trends.append("Ciberseguridad")
            if any(x in dl for x in ["fintech","payment","digital wallet","blockchain"]):
                trends.append("Fintech / Pagos digitales")
            if trends:
                for tr in trends: st.success(f"✓ {tr}")
            else:
                st.info("Sin exposición directa identificada a mega tendencias tecnológicas.")

    with st.expander("⚠️ Riesgos por niveles", expanded=True):
        rk1,rk2,rk3 = st.columns(3)
        with rk1:
            st.markdown("**🌏 Geopolítica**")
            if any(x in dl for x in ["china","europe","asia","international","global"]):
                st.warning("Exposición internacional — susceptible a aranceles, tensiones y riesgo divisa.")
            else:
                st.info("Operaciones mayormente domésticas — menor riesgo geopolítico directo.")
        with rk2:
            st.markdown("**📉 Ciclos de mercado**")
            if sector and any(x in sl for x in ["consumer discretionary","technology","real estate"]):
                st.warning("Sector cíclico — sensible a recesiones y tasas de interés.")
            elif sector and any(x in sl for x in ["utilities","consumer staples","healthcare"]):
                st.success("Sector defensivo — resiliente en recesiones.")
            else:
                st.info(f"Sector {sector or 'N/A'} — evalúa correlación con el ciclo económico.")
        with rk3:
            st.markdown("**📊 Riesgo financiero**")
            if debt_eq is not None and debt_eq>2:
                st.error(f"Deuda elevada (D/E {debt_eq:.1f}x) — vulnerable a subidas de tasas.")
            elif fcf is not None and fcf<0:
                st.error("FCF negativo — depende de financiamiento externo.")
            elif net_margin is not None and net_margin<0:
                st.error("No rentable — riesgo de dilución.")
            else:
                st.success("Sin riesgos financieros críticos identificados.")

# ────────────────── CONCLUSIÓN ────────────────────────────────────────────────
st.divider()
st.markdown("## 🎯 Conclusión (estilo Hedge Fund)")
cg, cv = st.columns([1,2])
with cg:
    fig_g = go.Figure(go.Indicator(
        mode="gauge+number", value=sc_fund,
        number={"font":{"size":28},"suffix":"/100"},
        gauge={"axis":{"range":[0,100],"tickfont":{"size":10}},
               "bar":{"color":rc},
               "steps":[{"range":[0,40],"color":"#FCEBEB"},
                        {"range":[40,55],"color":"#FFF3DC"},
                        {"range":[55,75],"color":"#E6F1FB"},
                        {"range":[75,100],"color":"#EAF3DE"}],
               "threshold":{"line":{"color":rc,"width":3},"value":sc_fund}},
        title={"text":"Score fundamental","font":{"size":12}}
    ))
    fig_g.update_layout(height=200,margin=dict(l=20,r=20,t=40,b=10),
                        paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_g, use_container_width=True, config={"displayModeBar":False})
with cv:
    st.markdown(
        f"<div style='background:{rc}18;border-left:4px solid {rc};"
        f"border-radius:8px;padding:16px;margin-bottom:12px'>"
        f"<div style='font-size:11px;color:gray;text-transform:uppercase;"
        f"letter-spacing:.05em'>Veredicto</div>"
        f"<div style='font-size:22px;font-weight:700;color:{rc}'>{rating}</div>"
        f"<div style='font-size:14px;font-weight:600;color:{rc};margin-top:2px'>"
        f"{stance}</div></div>",unsafe_allow_html=True)
    cc1,cc2 = st.columns(2)
    with cc1:
        st.markdown("**Señales positivas**")
        for line in bp.split("\n"):
            if line.strip(): st.markdown(line)
    with cc2:
        st.markdown("**Señales de cautela**")
        for line in bn.split("\n"):
            if line.strip(): st.markdown(line)

# ──────────────────── NOTICIAS ────────────────────────────────────────────────
st.divider()
st.markdown("#### Noticias recientes")
if news:
    for n in news:
        pub  = n.get("published_utc","")[:10]
        sent = (n.get("insights") or [{}])[0].get("sentiment","neutral")
        icon = "🟢" if sent=="positive" else "🔴" if sent=="negative" else "🟡"
        with st.expander(f"{icon}  {n.get('title','Sin título')} — {pub}"):
            st.write(n.get("description","Sin descripción."))
            if n.get("article_url"):
                st.markdown(f"[Leer artículo completo]({n['article_url']})")
else:
    st.caption("Sin noticias recientes disponibles.")

st.divider()
st.caption(
    "Precio & técnico: Polygon.io · Fundamentals: Financial Modeling Prep (SEC filings) · "
    "End-of-day. Este dashboard es informativo y no constituye asesoría de inversión.")
