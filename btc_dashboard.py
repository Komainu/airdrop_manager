import streamlit as st
import requests

def fetch_btc_data():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "bitcoin", "vs_currencies": "usd",
                  "include_market_cap": "true", "include_24hr_vol": "true", "include_24hr_change": "true"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()["bitcoin"]
        return {"price": d["usd"], "market_cap": d["usd_market_cap"],
                "volume_24h": d["usd_24h_vol"], "change_24h": d["usd_24h_change"]}
    except:
        return None

def fetch_dominance():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        r.raise_for_status()
        return round(r.json()["data"]["market_cap_percentage"]["btc"], 2)
    except:
        return None

def fetch_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1&format=json", timeout=10)
        r.raise_for_status()
        entry = r.json()["data"][0]
        return {"value": int(entry["value"]), "classification": entry["value_classification"]}
    except:
        return None

def fetch_stablecoin():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "tether,usd-coin", "vs_currencies": "usd",
                  "include_market_cap": "true", "include_24hr_change": "true"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        usdt = d.get("tether", {}).get("usd_market_cap", 0)
        usdc = d.get("usd-coin", {}).get("usd_market_cap", 0)
        change = d.get("tether", {}).get("usd_24h_change", 0)
        return {"usdt": usdt, "usdc": usdc, "total": usdt + usdc, "change_24h": change}
    except:
        return None

def fetch_funding_rate():
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        params = {"symbol": "BTCUSDT", "limit": 1}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0]["fundingRate"]) * 100
        return None
    except:
        return None

def fetch_fred_series(series_id, api_key, limit=10):
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {"series_id": series_id, "api_key": api_key, "file_type": "json",
                  "sort_order": "desc", "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        return [{"date": o["date"], "value": float(o["value"])}
                for o in obs if o["value"] != "."]
    except:
        return []

def fmt_large(val):
    if val is None: return "---"
    if val >= 1e12: return f"${val/1e12:.2f}T"
    if val >= 1e9:  return f"${val/1e9:.2f}B"
    if val >= 1e6:  return f"${val/1e6:.2f}M"
    return f"${val:,.0f}"

def fmt_pct(val, decimals=2):
    if val is None: return "---"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.{decimals}f}%"

def render_btc_dashboard():
    st.markdown("### 📈 BTCマクロダッシュボード")
    fred_key = ""
    try:
        fred_key = st.secrets.get("FRED_API_KEY", "")
    except:
        pass
    if not fred_key:
        fred_key = st.text_input(
            "🔑 FRED API Key（任意・金利データ表示に必要）",
            type="password", key="fred_key_input",
            help="無料取得: https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    if st.button("🔄 データ更新", key="btc_refresh"):
        if "btc_cache" in st.session_state:
            del st.session_state["btc_cache"]
    if "btc_cache" not in st.session_state:
        with st.spinner("データ取得中..."):
            cache = {
                "btc":       fetch_btc_data(),
                "dominance": fetch_dominance(),
                "fng":       fetch_fear_greed(),
                "stable":    fetch_stablecoin(),
                "funding":   fetch_funding_rate(),
                "fred":      {}
            }
            if fred_key:
                cache["fred"] = {
                    "sofr":  fetch_fred_series("SOFR",     fred_key, 120),
                    "iorb":  fetch_fred_series("IORB",     fred_key, 120),
                    "effr":  fetch_fred_series("EFFR",     fred_key, 120),
                    "dgs10": fetch_fred_series("DGS10",    fred_key, 10),
                    "dxy":   fetch_fred_series("DTWEXBGS", fred_key, 10),
                    "m2":    fetch_fred_series("M2SL",     fred_key, 24),
                }
            st.session_state.btc_cache = cache
    cache   = st.session_state.btc_cache
    btc     = cache.get("btc")
    fng     = cache.get("fng")
    stable  = cache.get("stable")
    funding = cache.get("funding")
    dom     = cache.get("dominance")
    fred    = cache.get("fred", {})
    st.markdown("#### 💰 BTCリアルタイム指標")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        price  = btc["price"]      if btc else None
        change = btc["change_24h"] if btc else None
        st.metric("BTC価格", f"${price:,.0f}" if price else "---",
                  delta=fmt_pct(change) if change is not None else None)
    with c2:
        st.metric("時価総額", fmt_large(btc["market_cap"] if btc else None))
    with c3:
        st.metric("24h出来高", fmt_large(btc["volume_24h"] if btc else None))
    with c4:
        st.metric("BTC優位性", f"{dom:.1f}%" if dom else "---")
    st.divider()
    st.markdown("#### 🧭 市場センチメント")
    c1, c2, c3 = st.columns(3)
    with c1:
        if fng:
            val = fng["value"]
            cls_map = {
                "Extreme Fear": ("🔴 極度の恐怖", "#ef4444"),
                "Fear":         ("🟠 恐怖",       "#f97316"),
                "Neutral":      ("🟡 中立",       "#eab308"),
                "Greed":        ("🟢 強欲",       "#22c55e"),
                "Extreme Greed":("🟢 極度の強欲", "#10b981"),
            }
            label, color = cls_map.get(fng["classification"], (fng["classification"], "#94a3b8"))
            st.markdown(f'<div style="background:#1e2028;border-radius:12px;padding:20px;text-align:center;border:1px solid {color}44;"><div style="font-size:0.8rem;color:#94a3b8;">😱 Fear & Greed</div><div style="font-size:3rem;font-weight:900;color:{color};">{val}</div><div style="font-size:1rem;color:{color};font-weight:700;">{label}</div></div>', unsafe_allow_html=True)
        else:
            st.warning("Fear & Greedデータ取得失敗")
    with c2:
        if stable:
            ch = stable["change_24h"]
            ch_color = "#10b981" if ch >= 0 else "#ef4444"
            st.markdown(f'<div style="background:#1e2028;border-radius:12px;padding:20px;border:1px solid #33363f;"><div style="font-size:0.8rem;color:#94a3b8;">💵 ステーブルコイン時価総額</div><div style="font-size:2rem;font-weight:800;color:#f1f5f9;">{fmt_large(stable["total"])}</div><div style="font-size:0.8rem;color:#94a3b8;margin-top:8px;">USDT: {fmt_large(stable["usdt"])}<br>USDC: {fmt_large(stable["usdc"])}</div><div style="font-size:0.85rem;color:{ch_color};margin-top:4px;">24h: {fmt_pct(ch)}</div></div>', unsafe_allow_html=True)
        else:
            st.warning("ステーブルコインデータ取得失敗")
    with c3:
        if funding is not None:
            if funding > 0.05:
                fc, fs, fb = "#ef4444", "🔴 ロング過熱 — 警戒", "#2d1515"
            elif funding > 0.01:
                fc, fs, fb = "#f97316", "🟡 やや強気 — 注意", "#2d2010"
            elif funding >= -0.01:
                fc, fs, fb = "#22c55e", "🟢 中立 — 安定", "#122d1a"
            else:
                fc, fs, fb = "#3b82f6", "🔵 ショート優勢", "#10192d"
            st.markdown(f'<div style="background:{fb};border-radius:12px;padding:20px;border:1px solid {fc}55;"><div style="font-size:0.8rem;color:#94a3b8;">📊 Binance Funding Rate</div><div style="font-size:2.2rem;font-weight:900;color:{fc};">{funding:.4f}%</div><div style="font-size:0.9rem;font-weight:700;color:{fc};margin-top:6px;">{fs}</div></div>', unsafe_allow_html=True)
        else:
            st.warning("Funding Rateデータ取得失敗")
    if fred and any(fred.values()):
        st.divider()
        st.markdown("#### 🏦 マクロ金利指標（FRED）")
        sofr  = fred.get("sofr",  [])
        iorb  = fred.get("iorb",  [])
        effr  = fred.get("effr",  [])
        dgs10 = fred.get("dgs10", [])
        dxy   = fred.get("dxy",   [])
        m2    = fred.get("m2",    [])
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1: st.metric("SOFR",     f"{sofr[0]['value']:.2f}%"  if sofr  else "---")
        with c2: st.metric("IORB",     f"{iorb[0]['value']:.2f}%"  if iorb  else "---")
        with c3: st.metric("EFFR",     f"{effr[0]['value']:.2f}%"  if effr  else "---")
        with c4: st.metric("米10年債", f"{dgs10[0]['value']:.2f}%" if dgs10 else "---")
        with c5: st.metric("DXY",      f"{dxy[0]['value']:.2f}"    if dxy   else "---")
        if sofr and iorb:
            spread_bps = (sofr[0]["value"] - iorb[0]["value"]) * 100
            if spread_bps <= 0:
                sc, sl, st2 = "#10b981", "🟢 安全 — 流動性良好", "SOFRがIORB以下。ドル調達コストが低く流動性ストレスなし。BTC上昇サイン。"
            elif spread_bps <= 5:
                sc, sl, st2 = "#eab308", "🟡 注意 — やや逼迫", "SOFRがIORBをやや上回る。ドル調達コスト上昇の初期段階。"
            else:
                sc, sl, st2 = "#ef4444", "🔴 警戒 — ドル不足", "SOFRがIORBを大幅に上回る。ドル流動性逼迫。BTC売り圧力が強まる可能性。"
            st.markdown(f'<div style="background:#1e2028;border:1px solid {sc}55;border-radius:12px;padding:16px;margin-top:8px;"><b style="color:{sc};">SOFR−IORB: {spread_bps:.1f} bps</b> &nbsp; {sl}<br><span style="color:#94a3b8;font-size:0.85rem;">{st2}</span></div>', unsafe_allow_html=True)
        if m2 and len(m2) >= 2:
            import plotly.graph_objects as go
            st.markdown("##### 💵 米国M2マネーサプライ")
            chart_data = list(reversed(m2))
            fig = go.Figure(go.Scatter(
                x=[d["date"] for d in chart_data],
                y=[d["value"] / 1000 for d in chart_data],
                mode="lines", fill="tozeroy",
                line=dict(color="#3b82f6", width=2),
                fillcolor="rgba(59,130,246,0.1)"
            ))
            fig.update_layout(height=200, margin=dict(l=0,r=0,t=10,b=0),
                paper_bgcolor="#1e2028", plot_bgcolor="#1e2028",
                font=dict(color="#94a3b8"),
                xaxis=dict(showgrid=False), yaxis=dict(tickprefix="$", ticksuffix="T"))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("💡 FRED APIキーを入力するとSOFR・IORB・DXY・M2などのマクロ金利指標が表示されます。\n無料取得: https://fred.stlouisfed.org/docs/api/api_key.html")
