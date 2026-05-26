import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
import json
import re
from datetime import datetime, timezone, timedelta

# ── 除外キーワード ─────────────────────────────────────────
EXCLUDE_KW = [
    "temperature","°C","FC","vs.","O/U","cricket","IPL","soccer","weather",
    "Celsius","halftime","Baron","Spread","Game","Goals","Score","Match","Set ",
    "Singapore","Jakarta","Guangzhou","Helsinki","Seoul","Kuala","Busan","Jeddah",
    "Paris","lowest","highest","Tamworth","Braintree","Samson","Bondar","Nagasaki",
    "Gamba","DetonatioN","Esports","Melbourne","Yeovil","Solihull","Spezia",
    "V-Varen","tennis","Tennis","Arminia","Bielefeld",
]

# ── データ取得 ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def fetch_markets(order, limit=500, ascending="false"):
    resp = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"active": "true", "limit": limit, "order": order, "ascending": ascending},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

def parse_date(d_str):
    if not d_str: return None
    try: return datetime.fromisoformat(d_str.replace("Z", "+00:00"))
    except Exception: return None

def extract_project_name(text):
    if not text: return ""
    text = str(text)
    text = re.sub(r'^(Will|Did|Does|Is|Are)\s+', '', text, flags=re.IGNORECASE)
    m = re.search(r'^(.+?)\s+(FDV|airdrop|token|launch|sell|price|exceed|reach|hit|list|listing)\b', text, flags=re.IGNORECASE)
    if m: return m.group(1).strip()
    words = text.split()
    return " ".join(words[:min(2, len(words))])

def build_df(markets):
    rows = []
    now_utc = datetime.now(timezone.utc)
    for m in markets:
        prices = m.get("outcomePrices", [])
        if isinstance(prices, str): prices = json.loads(prices)
        yp = float(prices[0]) * 100 if prices else None
        if yp is None: continue
        desc = (m.get("description") or "").split(".")[0].strip()[:90]
        end_dt = parse_date(m.get("endDate"))
        hours_left = (end_dt - now_utc).total_seconds() / 3600 if end_dt else None
        q = m.get("question", "")
        project_name = extract_project_name(q)
        if project_name and not q.startswith(f"【{project_name}】"): q = f"【{project_name}】 {q}"
        rows.append({
            "question": q, "desc": desc, "vol24h": float(m.get("volume24hr") or 0),
            "yes_prob": yp, "no_prob": 100 - yp, "change": float(m.get("oneDayPriceChange") or 0),
            "spread": float(m.get("spread") or 0), "liquidity": float(m.get("liquidity") or 0),
            "end_date": end_dt, "hours_left": hours_left,
        })
    return pd.DataFrame(rows)

@st.cache_data(ttl=300, show_spinner=False)
def fetch_events(slugs, limit=100):
    all_events = []
    for slug in slugs:
        try:
            resp = requests.get(
                "https://gamma-api.polymarket.com/events",
                params={"active": "true", "closed": "false", "tag_slug": slug, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            all_events.extend(resp.json())
        except Exception: pass
    seen = set()
    unique_events = []
    for e in all_events:
        if e.get("id") not in seen:
            seen.add(e.get("id"))
            unique_events.append(e)
    return unique_events

def build_df_from_events(events):
    rows = []
    now_utc = datetime.now(timezone.utc)
    for e in events:
        for m in e.get("markets", []):
            prices = m.get("outcomePrices", [])
            if isinstance(prices, str):
                try: prices = json.loads(prices)
                except: continue
            yp = float(prices[0]) * 100 if prices else None
            if yp is None: continue
            desc = (m.get("description") or "").split(".")[0].strip()[:90]
            end_dt = parse_date(m.get("endDate"))
            hours_left = (end_dt - now_utc).total_seconds() / 3600 if end_dt else None
            title = e.get("title", "")
            q = m.get("question", "")
            project_name = extract_project_name(title if title else q)
            if project_name and not q.startswith(f"【{project_name}】"): q = f"【{project_name}】 {q}"
            rows.append({
                "event_title": title, "question": q, "desc": desc, "vol24h": float(m.get("volume24hr") or 0),
                "yes_prob": yp, "no_prob": 100 - yp, "change": 0.0, "liquidity": float(m.get("liquidity") or 0),
                "end_date": end_dt, "hours_left": hours_left,
            })
    df = pd.DataFrame(rows)
    if not df.empty: df = df.drop_duplicates(subset=["question"]).copy()
    return df

def filter_kw(df):
    if df.empty: return df
    return df[~df["question"].str.contains("|".join(EXCLUDE_KW), case=False, na=False)]

def preprocess_for_translation(text):
    if not text: return text
    text = re.sub(r'\$(\d+(?:\.\d+)?)B\b', r'\1 Billion USD', text)
    text = re.sub(r'\$(\d+(?:\.\d+)?)M\b', r'\1 Million USD', text)
    text = re.sub(r'\$(\d+(?:\.\d+)?)T\b', r'\1 Trillion USD', text)
    text = re.sub(r'\$(\d+(?:\.\d+)?)k\b', r'\1 Thousand USD', text, flags=re.IGNORECASE)
    return text

@st.cache_data(ttl=3600, show_spinner=False)
def translate_text(text):
    if not text: return ""
    text = preprocess_for_translation(text)
    try:
        res = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "ja", "dt": "t", "q": text},
            timeout=5,
        )
        return "".join([x[0] for x in res.json()[0]])
    except Exception: return text

def translate_df(df):
    df = df.copy()
    df["question_ja"] = [translate_text(q) for q in df["question"]]
    df["desc_ja"] = [translate_text(d) for d in df["desc"]]
    return df

# ── Plotlyレイアウト（よてい帳のデザインに合わせる） ──────────────────────────
# ダークテーマ過ぎない、少し落ち着いたブルー系の背景で統一
LAYOUT_BASE = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="'Noto Sans JP','Meiryo',sans-serif", color="#333", size=14),
    hoverlabel=dict(
        bgcolor="#ffffff", bordercolor="#ccc",
        font=dict(size=13, color="#333", family="'Noto Sans JP',sans-serif"), align="left",
    ),
    margin=dict(l=10, r=30, t=40, b=40),
    yaxis=dict(tickfont=dict(size=13), gridcolor="#eee", automargin=True),
    xaxis=dict(gridcolor="#eee"),
)

def chart_top20(df, sort_by="vol24h"):
    if sort_by == "event" and "event_title" in df.columns:
        dp = df.sort_values(["event_title", "yes_prob"], ascending=[False, False]).reset_index(drop=True)
    else: dp = df.sort_values("vol24h", ascending=True).reset_index(drop=True)
        
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Yes（起こる）", y=dp["question_ja"].str[:60], x=dp["yes_prob"], orientation="h",
        marker_color="#1a73e8", text=[f"Yes {v:.0f}%" if v > 8 else "" for v in dp["yes_prob"]],
        textposition="inside", insidetextanchor="middle", textfont=dict(size=13, color="white"),
        hovertemplate=[
            f"<b>{{r['question_ja']}}</b><br><span style='color:#666;font-size:11px'>{{r['desc_ja']}}</span><br><br>"
            f"✅ Yes: <b>{{r['yes_prob']:.1f}}%</b>　❌ No: <b>{{r['no_prob']:.1f}}%</b><br>"
            f"💰 24h出来高: <b>${{r['vol24h']/1e6:.2f}}M</b><extra></extra>"
            for _, r in dp.iterrows()
        ],
    ))
    fig.add_trace(go.Bar(
        name="No（起こらない）", y=dp["question_ja"].str[:60], x=dp["no_prob"], orientation="h",
        marker_color="#e84040", text=[f"No {v:.0f}%" if v > 8 else "" for v in dp["no_prob"]],
        textposition="inside", insidetextanchor="middle", textfont=dict(size=13, color="white"),
        hovertemplate=[
            f"<b>{{r['question_ja']}}</b><br>✅ Yes: <b>{{r['yes_prob']:.1f}}%</b>　❌ No: <b>{{r['no_prob']:.1f}}%</b><extra></extra>"
            for _, r in dp.iterrows()
        ],
    ))
    layout = {**LAYOUT_BASE}
    layout.update(
        barmode="stack", bargap=0.4, height=max(len(dp) * 60 + 100, 400),
        legend=dict(orientation="h", x=0.5, y=1.08, xanchor="center", bgcolor="rgba(255,255,255,0.8)", bordercolor="#ccc", borderwidth=1),
        xaxis=dict(range=[0, 100], ticksuffix="%", title_text="確率 (%)", title_font=dict(size=13)),
    )
    fig.update_layout(**layout)
    return fig

def chart_surge(df):
    dp = df.sort_values("change", ascending=True).reset_index(drop=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=dp["question_ja"].str[:60], x=dp["change"] * 100, orientation="h",
        marker=dict(
            color=dp["yes_prob"], colorscale=[[0, "#e84040"], [0.5, "#f5a623"], [1, "#2ecc71"]], cmin=0, cmax=100,
            colorbar=dict(title=dict(text="Yes%", font=dict(color="#333", size=12)), tickfont=dict(color="#333", size=11), thickness=14),
        ),
        text=[f"+{v*100:.0f}pt → Yes {y:.0f}%" for v, y in zip(dp["change"], dp["yes_prob"])],
        textposition="outside", textfont=dict(size=13, color="#333"),
        hovertemplate=[
            f"<b>{{r['question_ja']}}</b><br>📈 24h変化: <b>+{{r['change']*100:.1f}}%pt</b><br>✅ Yes: <b>{{r['yes_prob']:.1f}}%</b><extra></extra>"
            for _, r in dp.iterrows()
        ],
    ))
    layout = {**LAYOUT_BASE}
    layout.update(
        showlegend=False, bargap=0.45, height=max(len(dp) * 70 + 100, 300),
        xaxis=dict(range=[0, max(dp["change"] * 100) * 1.4 + 5], ticksuffix="pt", title_text="24h 確率変化 (%pt)"),
        margin=dict(l=10, r=120, t=40, b=40),
    )
    fig.update_layout(**layout)
    return fig

def chart_countdown(df):
    dp = df.sort_values("hours_left", ascending=False).reset_index(drop=True)
    def color(h): return "#e84040" if h <= 12 else "#f5a623" if h <= 48 else "#1a73e8"
    jst = timezone(timedelta(hours=9))
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=dp["question_ja"].str[:60], x=dp["hours_left"], orientation="h",
        marker_color=[color(h) for h in dp["hours_left"]],
        text=[f"あと {h:.1f}h" for h in dp["hours_left"]],
        textposition="outside", textfont=dict(size=13, color="#333"),
        hovertemplate=[
            f"<b>{{r['question_ja']}}</b><br>⏰ 解決: <b>{{r['end_date'].astimezone(jst).strftime('%m/%d %H:%M')}} JST</b><br>"
            f"残り <b>{{r['hours_left']:.1f}}</b> 時間<br>✅ Yes: <b>{{r['yes_prob']:.1f}}%</b><extra></extra>"
            for _, r in dp.iterrows()
        ],
    ))
    layout = {**LAYOUT_BASE}
    layout.update(
        showlegend=False, bargap=0.45, height=max(len(dp) * 70 + 100, 300),
        xaxis=dict(title_text="残り時間 (時間)"), margin=dict(l=10, r=100, t=40, b=40),
    )
    fig.update_layout(**layout)
    return fig

def chart_contested(df):
    dp = df.sort_values("vol24h", ascending=True).reset_index(drop=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=dp["question_ja"].str[:60], x=dp["yes_prob"] - 50, base=50, orientation="h",
        marker_color=["#1a73e8" if v >= 50 else "#e84040" for v in dp["yes_prob"]],
        text=[f"Yes {v:.1f}%" for v in dp["yes_prob"]],
        textposition="outside", textfont=dict(size=13, color="#333"),
        hovertemplate=[
            f"<b>{{r['question_ja']}}</b><br>⚖️ Yes: <b>{{r['yes_prob']:.1f}}%</b> (スプレッド: {{r['spread']}})<br>"
            f"💰 24h出来高: <b>${{r['vol24h']/1e6:.2f}}M</b><extra></extra>"
            for _, r in dp.iterrows()
        ],
    ))
    fig.add_vline(x=50, line_width=2, line_dash="dash", line_color="#333", opacity=0.4)
    layout = {**LAYOUT_BASE}
    layout.update(
        showlegend=False, bargap=0.45, height=max(len(dp) * 70 + 100, 300),
        xaxis=dict(range=[30, 70], ticksuffix="%", title_text="Yes確率 (%)"),
        margin=dict(l=10, r=80, t=40, b=40),
    )
    fig.update_layout(**layout)
    return fig

def render_polymarket_dashboard():
    # CSS scoped slightly for this dashboard
    st.markdown("""
    <style>
    .pm-kpi-row { display: flex; gap: 16px; justify-content: center; flex-wrap: wrap; margin-bottom: 1.4rem; }
    .pm-kpi-card {
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
        border: 1px solid #e0e0e0; border-radius: 14px;
        padding: 18px 28px; min-width: 200px; text-align: center;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
    }
    .pm-kpi-value { font-size: 1.8rem; font-weight: 700; color: #1a73e8; }
    .pm-kpi-label { font-size: 0.8rem; color: #555; margin-top: 4px; font-weight: bold; }
    .pm-section-card {
        background: #ffffff; border: 1px solid #f0f0f0;
        border-radius: 14px; padding: 16px;
        box-shadow: 0 2px 12px rgba(0,0,0,0.05);
        margin-bottom: 12px;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<h3>📊 Polymarket リアルタイムダッシュボード</h3>', unsafe_allow_html=True)
    ts = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    st.caption(f'データ取得元: Polymarket Gamma API　｜　最終更新: {ts} JST　｜　チャートのバーにホバーで詳細表示')
    
    col1, _ = st.columns([1, 4])
    with col1:
        if st.button("🔄 データを更新", key="pm_refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("📡 Polymarket APIからデータ取得中…"):
        all_raw = fetch_markets("volume24hr", limit=500)
        rising_raw = fetch_markets("oneDayPriceChange", limit=200)
        crypto_raw = fetch_events(["crypto", "bitcoin", "cryptocurrency"], limit=100)
        tge_raw = fetch_events(["token-launches", "token", "airdrop", "public-sale", "ido", "listing"], limit=100)

    df_all = build_df(all_raw)
    df_rising = build_df(rising_raw)
    df_crypto = build_df_from_events(crypto_raw)
    df_tge = build_df_from_events(tge_raw)

    if not df_crypto.empty:
        tge_kws = ["airdrop", "public sale", "ido", "listing", "tge", "token launch", "launch"]
        tge_from_crypto = df_crypto[df_crypto["question"].str.contains("|".join(tge_kws), case=False, na=False)]
        if not tge_from_crypto.empty:
            df_tge = pd.concat([df_tge, tge_from_crypto]).drop_duplicates(subset=["question"]).copy()

    df_all_f = filter_kw(df_all)
    df_rising_f = filter_kw(df_rising)
    df_crypto_f = filter_kw(df_crypto)
    df_tge_f = filter_kw(df_tge)

    df_top20 = df_all_f.head(20).copy()
    df_surge = df_rising_f[(df_rising_f["change"] > 0.05) & (df_rising_f["yes_prob"].between(5, 97))].sort_values("change", ascending=False).head(7).copy()
    df_soon = df_all_f[(df_all_f["hours_left"].notna()) & (df_all_f["hours_left"] > 0) & (df_all_f["hours_left"] <= 7 * 24) & (df_all_f["yes_prob"].between(5, 95))].sort_values("hours_left", ascending=True).head(7).copy()
    df_close = df_all_f[(df_all_f["yes_prob"] >= 38) & (df_all_f["yes_prob"] <= 62)].sort_values("vol24h", ascending=False).head(7).copy()

    total_vol = df_all_f["vol24h"].sum() if not df_all_f.empty else 0
    max_change = df_rising_f["change"].max() * 100 if not df_rising_f.empty else 0
    n_contested = len(df_all_f[(df_all_f["yes_prob"] >= 40) & (df_all_f["yes_prob"] <= 60)])

    st.markdown(f"""
    <div class="pm-kpi-row">
        <div class="pm-kpi-card">
            <div class="pm-kpi-value" style="color:#1a73e8">💰 ${total_vol/1e6:.1f}M</div>
            <div class="pm-kpi-label">24h 総出来高</div>
        </div>
        <div class="pm-kpi-card">
            <div class="pm-kpi-value" style="color:#2ecc71">📊 {len(df_all_f)}</div>
            <div class="pm-kpi-label">アクティブ市場</div>
        </div>
        <div class="pm-kpi-card">
            <div class="pm-kpi-value" style="color:#f5a623">🔥 +{max_change:.1f}pt</div>
            <div class="pm-kpi-label">最大24h変動</div>
        </div>
        <div class="pm-kpi-card">
            <div class="pm-kpi-value" style="color:#9c27b0">⚖️ {n_contested}</div>
            <div class="pm-kpi-label">拮抗市場 (40-60%)</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    with st.spinner("🌐 テキストを日本語に翻訳中…"):
        df_top20 = translate_df(df_top20)
        df_surge = translate_df(df_surge)
        df_soon = translate_df(df_soon)
        df_close = translate_df(df_close)

    main_tab1, main_tab2, main_tab3 = st.tabs(["🌐 全体トップ", "₿ Crypto予測", "🚀 TGE・トークンローンチ"])

    with main_tab1:
        sub1, sub2, sub3, sub4 = st.tabs(["📊 注目マーケット TOP20", "🔥 急上昇トピック", "⏰ まもなく解決", "⚖️ 拮抗マーケット"])
        with sub1:
            st.markdown('<div class="pm-section-card">', unsafe_allow_html=True)
            if not df_top20.empty: st.plotly_chart(chart_top20(df_top20), use_container_width=True, key="pm_top20")
            else: st.info("データがありません")
            st.markdown('</div>', unsafe_allow_html=True)
        with sub2:
            st.markdown('<div class="pm-section-card">', unsafe_allow_html=True)
            if not df_surge.empty: st.plotly_chart(chart_surge(df_surge), use_container_width=True, key="pm_surge")
            else: st.info("条件に合うマーケットがありません")
            st.markdown('</div>', unsafe_allow_html=True)
        with sub3:
            st.markdown('<div class="pm-section-card">', unsafe_allow_html=True)
            if not df_soon.empty: st.plotly_chart(chart_countdown(df_soon), use_container_width=True, key="pm_soon")
            else: st.info("7日以内に解決するマーケットがありません")
            st.markdown('</div>', unsafe_allow_html=True)
        with sub4:
            st.markdown('<div class="pm-section-card">', unsafe_allow_html=True)
            if not df_close.empty: st.plotly_chart(chart_contested(df_close), use_container_width=True, key="pm_contest")
            else: st.info("拮抗マーケットがありません")
            st.markdown('</div>', unsafe_allow_html=True)

    with main_tab2:
        st.markdown('<div class="pm-section-card">', unsafe_allow_html=True)
        if not df_crypto_f.empty:
            filter_prob = st.checkbox("注目マーケットのみ（確率 5%〜95%）を表示", value=True, key="pm_crypto_filter")
            df_c = df_crypto_f
            if filter_prob: df_c = df_c[df_c["yes_prob"].between(5, 95)]
            df_c = df_c.sort_values("vol24h", ascending=False).head(30)
            with st.spinner("翻訳中..."): df_c = translate_df(df_c)
            st.plotly_chart(chart_top20(df_c, sort_by="event"), use_container_width=True, key="pm_crypto_chart")
        else: st.info("クリプト関連マーケットがありません")
        st.markdown('</div>', unsafe_allow_html=True)

    with main_tab3:
        st.markdown('<div class="pm-section-card">', unsafe_allow_html=True)
        if not df_tge_f.empty:
            df_t = df_tge_f.sort_values("vol24h", ascending=False).head(30)
            with st.spinner("翻訳中..."): df_t = translate_df(df_t)
            st.plotly_chart(chart_top20(df_t, sort_by="event"), use_container_width=True, key="pm_tge_chart")
            
            st.markdown("#### 📅 解決予定日別分布 (タイムライン)")
            if not df_t.empty and "end_date" in df_t.columns:
                df_t_valid = df_t.dropna(subset=["end_date"])
                if not df_t_valid.empty:
                    max_vol = df_t_valid["vol24h"].max() if df_t_valid["vol24h"].max() > 0 else 1
                    fig_tl = go.Figure()
                    fig_tl.add_trace(go.Scatter(
                        x=df_t_valid["end_date"], y=df_t_valid["yes_prob"], mode="markers",
                        marker=dict(
                            size=df_t_valid["vol24h"] / max_vol * 40 + 10,
                            color=df_t_valid["yes_prob"], colorscale=[[0, "#e84040"], [0.5, "#f5a623"], [1, "#2ecc71"]],
                            showscale=True, colorbar=dict(title=dict(text="Yes%", font=dict(color="#333", size=12)))
                        ),
                        hovertemplate=[
                            f"<b>{{r['question_ja']}}</b><br>解決予定: {{r['end_date'].strftime('%Y/%m/%d') if pd.notnull(r['end_date']) else '不明'}}<br>"
                            f"✅ Yes: <b>{{r['yes_prob']:.1f}}%</b><extra></extra>"
                            for _, r in df_t_valid.iterrows()
                        ]
                    ))
                    tl_layout = {**LAYOUT_BASE}
                    tl_layout.update(
                        height=500, xaxis_title="解決予定日", yaxis_title="Yes確率 (%)",
                        yaxis=dict(range=[-10, 110], gridcolor="#eee"), margin=dict(l=10, r=10, t=40, b=40)
                    )
                    fig_tl.update_layout(**tl_layout)
                    st.plotly_chart(fig_tl, use_container_width=True, key="pm_timeline")
        else: st.info("TGE関連マーケットがありません")
        st.markdown('</div>', unsafe_allow_html=True)
