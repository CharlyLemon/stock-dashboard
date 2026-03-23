import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

# ── Configuración de página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Dashboard v2",
    page_icon="📈",
    layout="wide"
)

# ── API Key desde Streamlit Secrets ─────────────────────────────────────────
API_KEY = st.secrets["POLYGON_API_KEY"]
BASE    = "https://api.polygon.io"

# ── Helpers de formato ───────────────────────────────────────────────────────
def fmt_price(n):
    if n is None: return "N/A"
    return f"${n:,.2f}"

def fmt_big(n):
    if n is None: return "N/A"
    if n >= 1e12: return f"${n/1e12:.2f}T"
    if n >= 1e9:  return f"${n/1e9:.1f}B"
    if n >= 1e6:  return f"${n/1e6:.1f}M"
    return f"${n:,.0f}"

def fmt_vol(n):
    if n is None: return "N/A"
    if n >= 1e9: return f"{n/1e9:.2f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.0f}K"
    return str(int(n))

def pct_color(v):
    if v is None: return "gray"
    return "green" if v >= 0 else "red"

# ── Clasificación por Market Cap ─────────────────────────────────────────────
def classify_mktcap(mc):
    if mc is None: return ("Desconocida", "gray")
    if mc >= 200e9: return ("Mega-cap",  "#534AB7")
    if mc >= 10e9:  return ("Large-cap", "#185FA5")
    if mc >= 2e9:   return ("Mid-cap",   "#BA7517")
    if mc >= 300e6: return ("Small-cap", "#D85A30")
    if mc >= 50e6:  return ("Micro-cap", "#E24B4A")
    return ("Nano-cap", "#A32D2D")

# ── Scores ───────────────────────────────────────────────────────────────────
def score_volatility(beta, chg_pct):
    b = abs(beta or 1)
    c = abs(chg_pct or 0)
    if b > 2   or c > 5: return (5, "Extrema",    "#E24B4A")
    if b > 1.5 or c > 3: return (4, "Alta",       "#D85A30")
    if b > 1.2:          return (3, "Media-alta",  "#BA7517")
    if b > 0.8:          return (2, "Media",       "#BA7517")
    return                      (1, "Baja",        "#1D9E75")

def score_liquidity(avg_vol):
    v = avg_vol or 0
    if v > 50e6: return (5, "Máxima",   "#1D9E75")
    if v > 10e6: return (4, "Alta",     "#1D9E75")
    if v > 1e6:  return (3, "Media",    "#BA7517")
    if v > 100e3:return (2, "Limitada", "#D85A30")
    return              (1, "Escasa",   "#E24B4A")

def score_risk(beta, mc):
    s = 1
    b = beta or 1
    if b > 2:   s += 2
    elif b > 1.5: s += 1.5
    elif b > 1.2: s += 1
    if mc:
        if mc < 50e6:  s += 2
        elif mc < 300e6: s += 1.5
        elif mc < 2e9:   s += 1
    s = min(5, max(1, round(s)))
    labels = {1:"Muy bajo", 2:"Bajo", 3:"Medio", 4:"Alto", 5:"Muy alto"}
    colors = {1:"#1D9E75", 2:"#1D9E75", 3:"#BA7517", 4:"#D85A30", 5:"#E24B4A"}
    return (s, labels[s], colors[s])

# ── Estrategias ──────────────────────────────────────────────────────────────
def gen_strategy(ticker, mc_label, price, low52, high52):
    near_low  = price and low52  and (price - low52)  / low52  < 0.08
    near_high = price and high52 and (high52 - price) / high52 < 0.05

    if mc_label in ("Mega-cap", "Large-cap"):
        if near_low:
            return (
                f"**{ticker}** está cerca de su mínimo de 52 semanas — zona histórica de soporte. "
                "Espera una vela verde con volumen antes de entrar. Pon stop en el mínimo reciente.",
                "Stop en mínimo 52s · Target primer soporte",
                "Oportunidad para **calls OTM** a 30-45 días si la IV no está inflada. "
                "También viable un bull call spread para reducir costo.",
                "Calls OTM 30-45d · Bull call spread · Revisar IV"
            )
        if near_high:
            return (
                f"**{ticker}** está cerca de máximos anuales. No perseguir el precio. "
                "Espera un pullback al soporte más cercano para entrar con mejor R/R.",
                "Esperar pullback · No perseguir máximos",
                "Momentum trade válido con stop ajustado bajo el último swing low. "
                "Covered calls si ya tienes posición larga.",
                "Momentum con stop · Covered calls si long"
            )
        return (
            f"**{ticker}** es una {mc_label} con buena liquidez. Busca confluencia técnica "
            "(soporte + volumen) para swing de 1-2 semanas.",
            "Swing en soporte/resistencia · 1-2 semanas",
            "Explorar earnings play si hay reporte próximo. "
            "Con liquidez de opciones alta: straddle pre-earnings o posición direccional.",
            "Earnings play · Opciones con liquidez · Straddle"
        )
    if mc_label in ("Mid-cap", "Small-cap"):
        return (
            f"**{ticker}** es una {mc_label} con mayor volatilidad. Posición más pequeña "
            "(máx. 3-5% del portafolio). Stop loss obligatorio. Busca catalizadores claros.",
            "Posición reducida · Stop obligatorio · Catalizador claro",
            "Momentum en breakout con volumen. Catalizadores binarios (earnings, contratos). "
            "Revisar short interest para posible short squeeze.",
            "Breakout con volumen · Catalizador binario · Short interest"
        )
    return (
        f"**{ticker}** es una {mc_label} de muy alto riesgo. Máximo 1-2% del portafolio. "
        "Solo con catalizador muy claro. Define la salida ANTES de entrar.",
        "Máx. 1-2% portafolio · Catalizador inmediato · Salida predefinida",
        "Especulación pura. Stop muy ajustado, sin promediar a la baja. "
        "Calcula el costo real del spread bid/ask antes de entrar.",
        "Stop estricto · Sin promediar · Cuidar el spread"
    )

# ── Llamadas a Polygon ───────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_ticker_details(ticker):
    r = requests.get(f"{BASE}/v3/reference/tickers/{ticker}",
                     params={"apiKey": API_KEY}, timeout=10)
    return r.json().get("results", {}) if r.ok else {}

@st.cache_data(ttl=300)
def get_prev_close(ticker):
    r = requests.get(f"{BASE}/v2/aggs/ticker/{ticker}/prev",
                     params={"adjusted": "true", "apiKey": API_KEY}, timeout=10)
    res = r.json().get("results", [])
    return res[0] if res else {}

@st.cache_data(ttl=300)
def get_aggs(ticker, days=90):
    to_date   = datetime.today().strftime("%Y-%m-%d")
    from_date = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    r = requests.get(
        f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
        params={"adjusted": "true", "sort": "asc",
                "limit": 150, "apiKey": API_KEY}, timeout=10)
    return r.json().get("results", []) if r.ok else []

@st.cache_data(ttl=300)
def get_news(ticker):
    r = requests.get(f"{BASE}/v2/reference/news",
                     params={"ticker": ticker, "limit": 5,
                             "order": "desc", "apiKey": API_KEY}, timeout=10)
    return r.json().get("results", []) if r.ok else []

# ── Barra de rango 52s ───────────────────────────────────────────────────────
def range_bar(price, low52, high52):
    if not all([price, low52, high52]): return
    pct = (price - low52) / (high52 - low52) * 100
    pct = max(0, min(100, pct))
    st.markdown(
        f"""
        <div style="position:relative;height:6px;border-radius:3px;
                    background:#e0e0e0;margin:4px 0 8px">
          <div style="position:absolute;height:6px;border-radius:3px;
                      background:#378ADD;width:{pct:.0f}%"></div>
          <div style="position:absolute;width:10px;height:10px;border-radius:50%;
                      background:#185FA5;top:-2px;left:{pct:.0f}%;
                      transform:translateX(-50%)"></div>
        </div>
        <div style="display:flex;justify-content:space-between;
                    font-size:11px;color:gray">
          <span>{fmt_price(low52)}</span><span>52-week</span><span>{fmt_price(high52)}</span>
        </div>
        """, unsafe_allow_html=True
    )

# ── Gráfico de precio ────────────────────────────────────────────────────────
def price_chart(bars, ticker):
    if not bars: return
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"], unit="ms")
    is_up = df["c"].iloc[-1] >= df["c"].iloc[0]
    color = "#1D9E75" if is_up else "#E24B4A"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["c"],
        mode="lines",
        line=dict(color=color, width=1.8),
        hovertemplate="%{x|%d %b %Y}<br>Cierre: $%{y:.2f}<extra></extra>"
    ))
    fig.update_layout(
        height=220,
        margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)",
                   tickfont=dict(size=10), showline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)",
                   tickfont=dict(size=10), tickprefix="$", side="right"),
        showlegend=False
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# ── UI principal ─────────────────────────────────────────────────────────────
st.markdown(
    "<h2 style='margin-bottom:4px'>📈 Stock Dashboard v2</h2>"
    "<p style='color:gray;font-size:13px;margin-bottom:20px'>"
    "Datos vía Polygon.io · End-of-day · Ingresa cualquier ticker del mercado US</p>",
    unsafe_allow_html=True
)

col_input, col_btn = st.columns([5, 1])
with col_input:
    ticker_raw = st.text_input("", placeholder="Ticker (ej. AAPL, TSLA, NKE, BNGO...)",
                                label_visibility="collapsed").strip().upper()
with col_btn:
    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    analizar = st.button("Analizar", use_container_width=True, type="primary")

if not ticker_raw:
    st.info("Ingresa un ticker arriba y presiona Analizar para ver el dashboard completo.")
    st.stop()

# ── Carga de datos ───────────────────────────────────────────────────────────
with st.spinner(f"Consultando Polygon.io para {ticker_raw}..."):
    info    = get_ticker_details(ticker_raw)
    prev    = get_prev_close(ticker_raw)
    bars90  = get_aggs(ticker_raw, 90)
    bars30  = get_aggs(ticker_raw, 30)
    news    = get_news(ticker_raw)

if not prev:
    st.error(f"No se encontraron datos para **{ticker_raw}**. Verifica que el ticker sea válido.")
    st.stop()

# ── Cálculos ─────────────────────────────────────────────────────────────────
price   = prev.get("c")
open_p  = prev.get("o")
high_d  = prev.get("h")
low_d   = prev.get("l")
volume  = prev.get("v")
vwap    = prev.get("vw")

chg_amt = (price - open_p) if price and open_p else None
chg_pct = ((price - open_p) / open_p * 100) if price and open_p else None

shares  = info.get("share_class_shares_outstanding") or info.get("weighted_shares_outstanding")
mktcap  = (price * shares) if price and shares else info.get("market_cap")

highs   = [b["h"] for b in bars90]
lows    = [b["l"] for b in bars90]
vols    = [b["v"] for b in bars30]
high52  = max(highs) if highs else None
low52   = min(lows)  if lows  else None
avg_vol = sum(vols) / len(vols) if vols else None

mc_label, mc_color = classify_mktcap(mktcap)
v_score, v_label, v_color = score_volatility(info.get("beta", 1.2), chg_pct or 0)
l_score, l_label, l_color = score_liquidity(avg_vol)
r_score, r_label, r_color = score_risk(info.get("beta", 1.2), mktcap)

b_strat, b_key, a_strat, a_key = gen_strategy(
    ticker_raw, mc_label, price, low52, high52
)

# ── Header ────────────────────────────────────────────────────────────────────
st.divider()
h1, h2 = st.columns([3, 1])
with h1:
    st.markdown(
        f"<h1 style='font-size:32px;margin:0'>{ticker_raw}</h1>"
        f"<p style='color:gray;margin:2px 0 8px;font-size:13px'>"
        f"{info.get('name', ticker_raw)} · "
        f"{info.get('primary_exchange','NYSE/NASDAQ')} · "
        f"{info.get('sic_description') or info.get('type','Equity')}</p>"
        f"<span style='background:{mc_color}22;color:{mc_color};"
        f"font-size:12px;font-weight:500;padding:4px 12px;border-radius:10px'>"
        f"{mc_label} · {fmt_big(mktcap)}</span>",
        unsafe_allow_html=True
    )
with h2:
    chg_sign  = "+" if (chg_pct or 0) >= 0 else ""
    chg_col   = "green" if (chg_pct or 0) >= 0 else "red"
    st.markdown(
        f"<div style='text-align:right'>"
        f"<div style='font-size:30px;font-weight:500'>{fmt_price(price)}</div>"
        f"<div style='color:{chg_col};font-size:13px;font-weight:500'>"
        f"{chg_sign}{fmt_price(chg_amt)} ({chg_sign}{(chg_pct or 0):.2f}%)</div>"
        f"<div style='color:gray;font-size:10px'>cierre anterior</div>"
        f"</div>",
        unsafe_allow_html=True
    )

# ── Métricas rápidas ──────────────────────────────────────────────────────────
st.markdown("#### Métricas del día")
c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("Apertura",    fmt_price(open_p))
c2.metric("Máximo",      fmt_price(high_d))
c3.metric("Mínimo",      fmt_price(low_d))
c4.metric("VWAP",        fmt_price(vwap))
c5.metric("Volumen",     fmt_vol(volume), f"Avg {fmt_vol(avg_vol)}")
c6.metric("Market cap",  fmt_big(mktcap))

# ── Rango 52 semanas ──────────────────────────────────────────────────────────
st.markdown("**Rango 52 semanas**")
range_bar(price, low52, high52)

# ── Gráfico ───────────────────────────────────────────────────────────────────
st.markdown("#### Precio histórico")
tab30, tab90 = st.tabs(["30 días", "90 días"])
with tab30: price_chart(bars30, ticker_raw)
with tab90: price_chart(bars90, ticker_raw)

# ── Indicadores ───────────────────────────────────────────────────────────────
st.markdown("#### Indicadores de análisis")
i1, i2, i3 = st.columns(3)

with i1:
    st.markdown(
        f"<div style='background:#f7f7f5;border-radius:10px;padding:14px'>"
        f"<div style='font-size:10px;color:gray;text-transform:uppercase;"
        f"letter-spacing:.05em;margin-bottom:4px'>b) Volatilidad</div>"
        f"<div style='font-size:16px;font-weight:500;color:{v_color}'>{v_label}</div>"
        f"<div style='height:4px;border-radius:2px;background:#e0e0e0;margin:8px 0 4px'>"
        f"<div style='height:4px;border-radius:2px;background:{v_color};"
        f"width:{v_score*20}%'></div></div>"
        f"<div style='font-size:11px;color:gray'>Score {v_score}/5</div></div>",
        unsafe_allow_html=True
    )

with i2:
    st.markdown(
        f"<div style='background:#f7f7f5;border-radius:10px;padding:14px'>"
        f"<div style='font-size:10px;color:gray;text-transform:uppercase;"
        f"letter-spacing:.05em;margin-bottom:4px'>c) Liquidez</div>"
        f"<div style='font-size:16px;font-weight:500;color:{l_color}'>{l_label}</div>"
        f"<div style='height:4px;border-radius:2px;background:#e0e0e0;margin:8px 0 4px'>"
        f"<div style='height:4px;border-radius:2px;background:{l_color};"
        f"width:{l_score*20}%'></div></div>"
        f"<div style='font-size:11px;color:gray'>Score {l_score}/5</div></div>",
        unsafe_allow_html=True
    )

with i3:
    st.markdown(
        f"<div style='background:#f7f7f5;border-radius:10px;padding:14px'>"
        f"<div style='font-size:10px;color:gray;text-transform:uppercase;"
        f"letter-spacing:.05em;margin-bottom:4px'>d) Riesgo</div>"
        f"<div style='font-size:16px;font-weight:500;color:{r_color}'>{r_label}</div>"
        f"<div style='height:4px;border-radius:2px;background:#e0e0e0;margin:8px 0 4px'>"
        f"<div style='height:4px;border-radius:2px;background:{r_color};"
        f"width:{r_score*20}%'></div></div>"
        f"<div style='font-size:11px;color:gray'>Score {r_score}/5</div></div>",
        unsafe_allow_html=True
    )

# ── Estrategias ───────────────────────────────────────────────────────────────
st.markdown("#### f) Estrategia recomendada")
s1, s2 = st.columns(2)

with s1:
    st.markdown("**🟦 Trader básico**")
    st.info(b_strat)
    st.caption(f"Clave: {b_key}")

with s2:
    st.markdown("**🟧 Trader avanzado**")
    st.warning(a_strat)
    st.caption(f"Clave: {a_key}")

# ── Noticias ──────────────────────────────────────────────────────────────────
st.markdown("#### Noticias recientes")
if news:
    for n in news:
        pub = n.get("published_utc","")[:10]
        sentiment = (n.get("insights") or [{}])[0].get("sentiment","neutral")
        icon = "🟢" if sentiment=="positive" else "🔴" if sentiment=="negative" else "🟡"
        with st.expander(f"{icon} {n.get('title','Sin título')} — {pub}"):
            st.write(n.get("description","Sin descripción disponible."))
            if n.get("article_url"):
                st.markdown(f"[Leer artículo completo]({n['article_url']})")
else:
    st.caption("Sin noticias recientes disponibles para este ticker.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Datos provistos por Polygon.io · End-of-day, ~15 min delay durante mercado abierto · "
    "Este dashboard es informativo y no constituye asesoría financiera."
)
