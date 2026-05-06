import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import google.generativeai as genai
import json
import re
from datetime import datetime

# --- 設定 ---
st.set_page_config(page_title="IPO 7サイト統合 (復元版)", layout="wide")
st.title("📊 IPO 7サイト統合コンセンサス")

# APIキー管理
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

if api_key:
    genai.configure(api_key=api_key)

def get_available_model():
    try:
        models = genai.list_models()
        valid_models = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        best_model = next((m for m in valid_models if "flash" in m and "1.5" in m), None)
        if not best_model:
            best_model = valid_models[0] if valid_models else "models/gemini-1.5-flash"
        return best_model
    except:
        return "models/gemini-1.5-flash"

def fetch_site_content_light(name, url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=8)
        response.encoding = response.apparent_encoding
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "footer", "nav", "header", "iframe"]): tag.decompose()
            text = soup.get_text(strip=True)
            limit = 6000 if "uikabu" in url or "ipoget" in url else 4000
            return True, f"--- SOURCE: {name} ---\n{text[:limit]}\n"
        return False, ""
    except: return False, ""

def calculate_grade(score):
    if score is None: return "-"
    if score >= 4.0: return "S"
    if score >= 3.5: return "A"
    if score >= 3.0: return "B"
    if score >= 2.6: return "C+"
    if score >= 2.3: return "C"
    if score >= 2.0: return "C-"
    if score >= 1.5: return "D"
    return "E"

TARGET_SITES = [
    ("Uikabu", "https://uikabu.com/ipo-schedule-2026/"),
    ("IPO Get", "https://ipoget.com/"),
    ("やさしいIPO", "https://www.ipokiso.com/company/index.html"),
    ("96ut.com", "https://96ut.com/ipo/yoso.php"),
    ("庶民のIPO", "https://ipokabu.net/yotei/"),
    ("Kabusyo", "https://kabusyo.com/ipo/schedule.html"),
    ("IPO Mechanic", "https://ipomechanic.com/")
]

if st.button("最新データを取得・分析"):
    if not api_key: st.error("APIキーが必要です")
    else:
        status_box = st.empty()
        progress_bar = st.progress(0)
        best_model_name = get_available_model()
        combined_text = ""
        success_count = 0
        for i, (name, url) in enumerate(TARGET_SITES):
            is_success, content = fetch_site_content_light(name, url)
            combined_text += content + "\n"
            if is_success: success_count += 1
            progress_bar.progress((i + 1) / len(TARGET_SITES))
        
        try:
            model = genai.GenerativeModel(best_model_name)
            now_str = datetime.now().strftime("%Y年%m月%d日")
            prompt = f"現在は {now_str} です。以下のIPO情報から最新銘柄（最大6件）を抽出しJSON形式のリストで出力せよ。評価の平均算出ルール: S=5, A=4, B=3, C=2, D=1。\n\n{combined_text[:50000]}"
            response = model.generate_content(prompt)
            match = re.search(r'\[.*\]', response.text, re.DOTALL)
            if match:
                data = json.loads(match.group())
                df = pd.DataFrame(data)
                if "平均スコア" in df.columns:
                    df["平均スコア"] = pd.to_numeric(df["平均スコア"], errors='coerce')
                    df["総合評価"] = df["平均スコア"].apply(calculate_grade)
                st.dataframe(df, use_container_width=True)
                status_box.success("✅ 解析完了")
            else:
                st.error("JSONデータの抽出に失敗しました。")
        except Exception as e: st.error(f"解析エラー: {e}")
