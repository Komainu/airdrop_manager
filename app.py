import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import re
from datetime import datetime, timedelta, timezone
import os
import time
import threading

# --- スプレッドシート書き込み用ロック (競合防止) ---
_save_lock = threading.Lock()
import streamlit.components.v1 as components
import gspread
from google.oauth2.service_account import Credentials
import requests
import polymarket_dashboard
import btc_dashboard

# --- 設定 ---
st.set_page_config(page_title="よてい帳", layout="wide")
st.markdown("<div style='font-size:1.1rem; font-weight:bold; margin-top:0; padding-top:0; padding-bottom:10px;'>🛡️ よてい帳 & AI秘書</div>", unsafe_allow_html=True)

# --- コンパクトUI用CSS ---
st.markdown("""
<style>
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    padding: 6px 10px !important;
}
div[data-testid="stMarkdownContainer"] p {
    margin-bottom: 2px !important;
}
div[data-testid="stMarkdownContainer"] h5 {
    margin-top: 2px !important;
    margin-bottom: 2px !important;
}
hr {
    margin: 4px 0 !important;
}
div[data-testid="stHorizontalBlock"] {
    gap: 4px !important;
    margin-bottom: 0px !important;
}
div[data-testid="stAlert"] {
    padding: 4px 10px !important;
    margin-bottom: 4px !important;
}
div[data-testid="stCaptionContainer"] {
    margin-bottom: 2px !important;
}
div[data-testid="stButton"] button, div[data-testid="stLinkButton"] a {
    padding: 0.15rem 0.4rem !important;
    font-size: 0.75rem !important;
    min-height: 0rem !important;
    line-height: 1.2 !important;
}
</style>
""", unsafe_allow_html=True)

# APIキー管理
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

if api_key:
    genai.configure(api_key=api_key)

# --- セッション管理 ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "cached_df" not in st.session_state:
    st.session_state.cached_df = None
if "memo_counter" not in st.session_state:
    st.session_state.memo_counter = 0

# ===================================================
# ★★★ ここだけ修正: gemini-2.5-flash に変更 ★★★
# ===================================================
import time as _time

MODEL_PRIORITY = [
    "models/gemini-2.5-flash",
]

def get_working_model_name():
    return MODEL_PRIORITY[0]
# ===================================================

def _call_gemini_with_retry(model, prompt, max_retries=1):
    """429 Quota Error 対策"""
    last_error = None
    used_model_name = getattr(model, '_model_name', model.model_name if hasattr(model, 'model_name') else 'unknown')

    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            if "429" in err_str or "quota" in err_str or "resource" in err_str or "rate" in err_str:
                print(f"[Gemini] ⚠️ {used_model_name} でクォータ超過 (429)。")
                break
            _time.sleep(1)
            continue

    print(f"[Gemini] ❌ {used_model_name} が失敗。フォールバックモデルを試行します...")
    for fallback_name in MODEL_PRIORITY:
        if fallback_name == used_model_name:
            continue
        try:
            fallback_model = genai.GenerativeModel(fallback_name)
            response = fallback_model.generate_content(prompt)
            print(f"[Gemini] ✅ {fallback_name} で成功！")
            return response
        except Exception as e2:
            last_error = e2
            continue

    raise last_error

# --- Telegram 通知機能 ---
def send_telegram_notification(message):
    try:
        if "telegram" not in st.secrets:
            return False
        bot_token = st.secrets["telegram"]["bot_token"]
        chat_id = st.secrets["telegram"]["chat_id"]
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Telegram通知送信成功: {message[:30]}...")
        return True
    except Exception as e:
        print(f"Telegram通知送信エラー: {e}")
        return False

def send_telegram_project_notification(row):
    p_name = str(row.get("プロジェクト名", "不明"))
    p_deadline = str(row.get("期限", "不明"))
    if p_deadline.lower() in ["nan", "none", ""]: p_deadline = "不明"
    p_importance = str(row.get("重要度", "C"))
    p_funding = str(row.get("資金調達額", "不明"))
    if p_funding.lower() in ["nan", "none", ""]: p_funding = "不明"
    p_vc = str(row.get("VC", "不明"))
    if p_vc.lower() in ["nan", "none", ""]: p_vc = ""
    message = f"✨ 新規プロジェクト追加\nプロジェクト: {p_name}\n期限: {p_deadline}\n重要度: {p_importance}\n資金調達: {p_funding}"
    if p_vc and p_vc != "不明":
        message += f"（{p_vc}）"
    return send_telegram_notification(message)

# --- データ保存・読み込み機能 (Google スプレッドシート版) ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/13-zUqQcSm-3zXHF33p5be940lVWgOVbbj2FoN9aVph8/edit?pli=1&gid=0#gid=0"

def get_worksheet():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
    gc = gspread.authorize(credentials)
    sheet = gc.open_by_url(SHEET_URL)
    return sheet.sheet1

def get_x_news_worksheet():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
    gc = gspread.authorize(credentials)
    sheet = gc.open_by_url(SHEET_URL)
    try:
        return sheet.worksheet("Xニュース")
    except gspread.exceptions.WorksheetNotFound:
        return sheet.add_worksheet(title="Xニュース", rows="1000", cols="6")

def load_x_news():
    try:
        ws = get_x_news_worksheet()
        data = ws.get_all_values()
        if not data:
            headers = ["日時", "アカウント", "URL", "タイトル", "ランク", "理由"]
            ws.append_row(headers)
            return pd.DataFrame(columns=headers)
        headers = data[0]
        records = data[1:]
        if not records:
            return pd.DataFrame(columns=headers)
        df = pd.DataFrame(records, columns=headers)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Xニュース読み込みエラー: {e}")
        return pd.DataFrame()

def save_tasks(df):
    try:
        if df.empty:
            print("[save_tasks] ⚠️ DataFrameが空のため書き込みをスキップしました")
            return
        with _save_lock:
            ws = get_worksheet()
            header = df.columns.values.tolist()
            rows = df.astype(str).values.tolist()
            all_data = [header] + rows
            try:
                sheet_data = ws.get_all_values()
                current_rows = len(sheet_data)
            except Exception:
                current_rows = 1000
            empty_row = [""] * len(header)
            if len(all_data) < current_rows:
                padding_count = current_rows - len(all_data)
                all_data.extend([empty_row] * padding_count)
            try:
                ws.update(values=all_data, range_name="A1", value_input_option="RAW")
            except TypeError:
                ws.update("A1", all_data, value_input_option="RAW")
            print(f"[save_tasks] ✅ {len(rows)} 件を保存しました")
            try:
                st.session_state["save_error"] = None
            except:
                pass
    except Exception as e:
        err_msg = str(e)
        print(f"[save_tasks] ❌ スプレッドシート保存エラー: {err_msg}")
        try:
            st.session_state["save_error"] = err_msg
        except:
            pass

def load_tasks():
    required_cols = ["プロジェクト名", "期限", "タスク内容", "チェーン", "重要度", "ステータス", "ソースURL", "ピン留め", "登録日時", "資金調達額", "VC", "通知設定", "Telegram通知済み"]
    try:
        with _save_lock:
            ws = get_worksheet()
            data = ws.get_all_values()
        if not data:
            ws.append_row(required_cols)
            return pd.DataFrame(columns=required_cols)
        headers = data[0]
        records = data[1:]
        if not records:
            return pd.DataFrame(columns=required_cols)
        df = pd.DataFrame(records, columns=headers)
        df = df.loc[:, ~df.columns.duplicated()]
        need_sheet_update = False
        for col in required_cols:
            if col not in df.columns:
                if col in ["Telegram通知済み"]:
                    df[col] = "True"
                    need_sheet_update = True
                else:
                    df[col] = None
        if need_sheet_update:
            print("[load_tasks] 新規カラムを補完しました（次回保存時にシートへ反映されます）")
        df["プロジェクト名"] = df["プロジェクト名"].astype(str).str.strip()
        invalid_names = ["nan", "none", "", "null"]
        df = df[~df["プロジェクト名"].str.lower().isin(invalid_names)]
        df["期限"] = df["期限"].astype(str).str.strip()
        df["期限"] = df["期限"].replace(["nan", "None", "NaT", ""], "未定")
        fill_defaults = {
            "チェーン": "未定", "タスク内容": "", "重要度": "C", "ステータス": "未完了",
            "ピン留め": False, "登録日時": "2000-01-01T00:00:00", "資金調達額": "不明",
            "VC": "不明", "通知設定": False, "Telegram通知済み": "True"
        }
        df = df.fillna(fill_defaults)
        df["ピン留め"] = df["ピン留め"].map(lambda x: str(x).lower() == 'true' if not isinstance(x, bool) else x)
        df["通知設定"] = df["通知設定"].map(lambda x: str(x).lower() == 'true' if not isinstance(x, bool) else x)
        df["登録日時"] = df["登録日時"].astype(str)
        pin_notify_mismatch = (df["ピン留め"] == True) & (df["通知設定"] == False) & (df["ステータス"] != "完了")
        if pin_notify_mismatch.any():
            mismatched_names = df.loc[pin_notify_mismatch, "プロジェクト名"].tolist()
            df.loc[pin_notify_mismatch, "通知設定"] = True
            print(f"[通知同期] ピン留め済みプロジェクトの通知設定をONに修正: {mismatched_names}")
            save_tasks(df)
        return df
    except Exception as e:
        st.error(f"スプレッドシート読み込みエラー: {e}")
        return pd.DataFrame(columns=required_cols)

def auto_pin_urgent_tasks(df):
    updated = False
    now = datetime.now()
    for index, row in df.iterrows():
        if row["ステータス"] == "完了": continue
        if row.get("ピン留め"): continue
        deadline_str = str(row["期限"])
        if deadline_str not in ["未定", "nan", "None"]:
            try:
                deadline = pd.to_datetime(deadline_str, utc=True).tz_localize(None)
                diff = deadline - now
                if diff.total_seconds() > -86400 * 30 and diff.days < 3:
                    if row.get("重要度") in ["S", "A"] and not row["ピン留め"]:
                        print(f"[Info] Project {row['プロジェクト名']} is urgent, but auto-pin is disabled.")
            except Exception as e:
                pass
    if updated:
        print(f"[AutoPin] {len(df[df['ピン留め'] == True])} tasks are currently pinned.")
        save_tasks(df)
        st.cache_data.clear()
        return True
    return False

def _get_cached_df():
    if st.session_state.cached_df is None:
        st.session_state.cached_df = load_tasks()
    return st.session_state.cached_df

def _invalidate_cache():
    st.session_state.cached_df = None
    st.cache_data.clear()

def save_tasks_async(df_copy):
    if df_copy.empty:
        print("[save_tasks_async] ⚠️ 空のDataFrameのため保存をスキップ")
        return
    threading.Thread(target=save_tasks, args=(df_copy,), daemon=True).start()

def update_cached_and_save(df):
    st.session_state.cached_df = df
    save_tasks_async(df.copy())

def on_toggle_pin(row_index):
    df = _get_cached_df()
    if row_index in df.index:
        p_name = df.at[row_index, "プロジェクト名"]
        current_val = bool(df.at[row_index, "ピン留め"])
        df.at[row_index, "ピン留め"] = not current_val
        if not current_val:
            df.at[row_index, "通知設定"] = True
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found in dataframe!")

def on_complete_task(row_index):
    df = _get_cached_df()
    if row_index in df.index:
        df.at[row_index, "ステータス"] = "完了"
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found!")

def on_revert_task(row_index):
    df = _get_cached_df()
    if row_index in df.index:
        df.at[row_index, "ステータス"] = "未完了"
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found!")

def on_delete_task(row_index):
    df = _get_cached_df()
    if row_index in df.index:
        df = df.drop(row_index)
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found!")

def merge_memos(old_memo, new_memo):
    if not old_memo or str(old_memo).lower() in ["nan", "none"]: return new_memo
    if not new_memo or str(new_memo).lower() in ["nan", "none"]: return old_memo
    def split_memo(text):
        if not text: return []
        lines = re.split(r'[\n\r]+', str(text))
        sentences = []
        for line in lines:
            parts = re.split(r'^[ \t]*[・\-\*\d\.]+[\s　]*', line)
            for p in parts:
                clean_p = p.strip()
                if clean_p:
                    sentences.append(clean_p)
        return sentences
    old_lines = split_memo(old_memo)
    new_lines = split_memo(new_memo)
    combined = old_lines.copy()
    for n in new_lines:
        is_duplicate = False
        for o in combined:
            if n == o or (len(n) > 10 and (n in o or o in n)):
                is_duplicate = True
                break
        if not is_duplicate:
            combined.append(n)
    return "・" + "\n・".join(combined) if combined else ""

def parse_memo_locally(memo):
    print("[Parser] ⚠️ Geminiが使えないため、ローカルパーサーで解析します。")
    tasks = []
    paragraphs = re.split(r'\n\s*\n', memo.strip())
    for para in paragraphs:
        if not para.strip(): continue
        lines = para.strip().split('\n')
        first_line = lines[0].strip()
        url_match = re.search(r'https?://[^\s]+', para)
        source_url = url_match.group(0) if url_match else None
        project_name = first_line
        name_match = re.search(r'([A-Za-z0-9_]+)', first_line)
        if name_match:
            project_name = name_match.group(1)
        task = {
            "プロジェクト名": project_name[:30] if project_name else "不明なプロジェクト",
            "期限": "未定", "タスク内容": para.strip(), "チェーン": "未定",
            "重要度": "C", "ソースURL": source_url, "資金調達額": "不明", "VC": "不明"
        }
        tasks.append(task)
    return tasks

# --- AI解析ロジック (重複定義を解消・1つに統合) ---
def parse_memo_to_tasks(memo):
    cache_key = f"parse_memo_{hash(memo)}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    try:
        model_name = get_working_model_name()
        model = genai.GenerativeModel(model_name)
        now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        prompt = f"""
        タスク・予定管理秘書として、以下のテキストからプロジェクトやタスクごとの情報を抽出しJSONリストで出力してください。

        【重要指示】
        1. 複数の異なるプロジェクト情報が1つの要素に混ざらないように、プロジェクト名ごとに厳密に分割して出力すること。
        2. 全く同じ内容のタスクが重複している場合は、1つにまとめてください。
        3. プロジェクト名、期限、タスク内容を正確に抽出してください。
        4. プロジェクトの「資金調達額」と「参加VC（ベンチャーキャピタル）」の情報があれば抽出してください。ない場合は「不明」としてください。
        5. 【評価基準】「重要度」はAIの独自判断ではなく、入力テキストに含まれる「資金調達額の大きさ」や「VCの質（Tier1が含まれているか等）」を最重要視してS/A/B/Cで評価してください。判断材料がない場合は「C」としてください。

        現在時刻: {now_str}
        入力: {memo}
        出力形式: [{{"プロジェクト名": "...", "期限": "YYYY/MM/DD HH:MM", "タスク内容": "...", "チェーン": "...", "重要度": "S/A/B/C", "ソースURL": "...", "資金調達額": "...", "VC": "..."}}]
        """
        response = _call_gemini_with_retry(model, prompt)
        match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if match:
            res = json.loads(match.group())
            st.session_state[cache_key] = res
            return res
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', response.text, re.DOTALL)
        if json_match:
            res = json.loads(json_match.group(1))
            st.session_state[cache_key] = res
            return res
        res = parse_memo_locally(memo)
        st.session_state[cache_key] = res
        return res
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "quota" in err_msg.lower():
            st.warning("⚠️ AIの制限(クォータ超過)に達したため、ローカル解析で簡易登録しました。")
        else:
            st.warning(f"⚠️ AI解析エラーのため、ローカル解析で簡易登録しました。 ({e})")
        res = parse_memo_locally(memo)
        st.session_state[cache_key] = res
        return res

def add_or_update_tasks(existing_df, new_tasks, notify=True):
    updated_count = 0
    created_count = 0
    def normalize_name(n):
        n = str(n).lower()
        n = re.sub(r'[\s　\-_,.\(\)\[\]]+', '', n)
        n = re.sub(r'[^\w\s]', '', n)
        return n
    for task in new_tasks:
        name = str(task.get("プロジェクト名", "")).strip()
        if not name or name.lower() in ["nan", "none"]: continue
        norm_name = normalize_name(name)
        target_idx = None
        for idx, row in existing_df.iterrows():
            exist_name = str(row["プロジェクト名"]).strip()
            if normalize_name(exist_name) == norm_name:
                target_idx = idx
                break
        if target_idx is not None:
            existing_df.at[target_idx, "タスク内容"] = merge_memos(existing_df.at[target_idx, "タスク内容"], task.get("タスク内容", ""))
            new_deadline = task.get("期限", "未定")
            if str(existing_df.at[target_idx, "期限"]) == "未定" and new_deadline != "未定":
                existing_df.at[target_idx, "期限"] = new_deadline
            rank_map = {"S": 4, "A": 3, "B": 2, "C": 1}
            old_rank = rank_map.get(existing_df.at[target_idx, "重要度"], 0)
            new_rank = rank_map.get(task.get("重要度", ""), 0)
            if new_rank > old_rank:
                existing_df.at[target_idx, "重要度"] = task.get("重要度")
            if str(existing_df.at[target_idx, "チェーン"]) == "未定" and task.get("チェーン"):
                existing_df.at[target_idx, "チェーン"] = task.get("チェーン")
            updated_count += 1
        else:
            task["ステータス"] = "未完了"
            task["ピン留め"] = False
            task["通知設定"] = False
            if "ソースURL" not in task: task["ソースURL"] = None
            task["登録日時"] = datetime.now(timezone.utc).isoformat()
            if notify:
                send_telegram_project_notification(task)
            task["Telegram通知済み"] = "True"
            existing_df = pd.concat([existing_df, pd.DataFrame([task])], ignore_index=True)
            created_count += 1
    save_tasks(existing_df)
    return existing_df, created_count, updated_count

def process_chat_command(user_input, current_df):
    tasks_text = current_df.to_string(index=False)
    cache_key = f"chat_cmd_{hash(user_input + tasks_text)}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    try:
        model_name = get_working_model_name()
        model = genai.GenerativeModel(model_name)
        now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        prompt = f"""
        あなたはタスク・予定管理秘書です。
        ユーザーと会話をし、必要であればタスクデータの「更新」または「新規作成」を行ってください。
        現在時刻: {now_str}

        ## 指示
        - ユーザーの要求に合わせてJSONブロックを出力しデータを操作してください。
        - **重要**: 似た名前のプロジェクトが既に存在する場合は、新規作成("create")ではなく既存の更新("update")を検討してください。
        - 完了済みのタスクを「未完了」に戻す指示があった場合は、update_field="ステータス", new_value="未完了" としてください。
        - 【最重要】タスク内容について「要約して」「短くして」「書き換えて」と指示された場合は、update_field を必ず "タスク内容_上書き" にしてください。
        - **注意**: 全く同じデータ（プロジェクト名）を作成しようとしないでください。

        ## 現在のタスクリスト (完了済みを含む)
        {tasks_text}

        ## ユーザーの入力
        {user_input}

        ## アクション形式
        A: 既存タスク更新
        ```json
        {{
            "action": "update",
            "target_project_name": "（名前）",
            "update_field": "（タスク内容_追加 / タスク内容_上書き / 期限 / 重要度 / ピン留め / 削除 / ステータス）",
            "new_value": "（値）"
        }}
        ```

        B: 新規追加
        ```json
        {{
            "action": "create",
            "new_data": {{
                "プロジェクト名": "...",
                "期限": "YYYY/MM/DD HH:MM",
                "タスク内容": "...",
                "チェーン": "...",
                "重要度": "S/A/B/C",
                "ソースURL": "...",
                "資金調達額": "...",
                "VC": "..."
            }}
        }}
        ```
        """
        response = _call_gemini_with_retry(model, prompt)
        response_text = response.text
        action_data = None
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', response_text, re.DOTALL)
        raw_json_match = re.search(r'(\{[\s\S]*"action"[\s\S]*\})', response_text)
        if json_match:
            try:
                action_data = json.loads(json_match.group(1))
                response_text = response_text.replace(json_match.group(0), "").strip()
            except: pass
        elif raw_json_match:
            try:
                action_data = json.loads(raw_json_match.group(1))
                response_text = response_text.replace(raw_json_match.group(0), "").strip()
            except: pass
        res = (response_text, action_data, model_name)
        st.session_state[cache_key] = res
        return res
    except Exception as e:
        return f"エラー: {e}", None, "Unknown"

def is_new_project(reg_date_val):
    if pd.isna(reg_date_val) or str(reg_date_val) in ["nan", "None", "", "2000-01-01T00:00:00"]:
        return False
    try:
        if isinstance(reg_date_val, str):
            try:
                reg_date_val = datetime.fromisoformat(reg_date_val.replace('Z', '+00:00'))
            except:
                reg_date_val = pd.to_datetime(reg_date_val, utc=True)
        if hasattr(reg_date_val, 'tzinfo') and reg_date_val.tzinfo is None:
            reg_date_val = reg_date_val.replace(tzinfo=timezone.utc)
        elif not hasattr(reg_date_val, 'tzinfo'):
            reg_date_val = pd.to_datetime(reg_date_val, utc=True)
        now = datetime.now(timezone.utc)
        diff = now - reg_date_val
        return diff.total_seconds() < 86400
    except Exception as e:
        return False

def render_task_card(index, row):
    is_pinned = row.get("ピン留め", False)
    is_completed = row.get("ステータス") == "完了"
    is_new = is_new_project(row.get("登録日時")) and not is_completed
    p_name = str(row["プロジェクト名"])
    if p_name.lower() in ["nan", "none", ""]: p_name = "名称不明"
    p_chain = str(row["チェーン"])
    if p_chain.lower() in ["nan", "none", ""]: p_chain = ""
    p_deadline = str(row["期限"])
    if p_deadline.lower() in ["nan", "none", ""]: p_deadline = "未定"
    time_alert = ""
    if not is_completed and p_deadline != "未定":
        try:
            deadline = pd.to_datetime(p_deadline, utc=True).tz_localize(None)
            diff = deadline - datetime.now()
            if diff.total_seconds() < 0: time_alert = "❌ 期限切れ"
            elif diff.days < 3: time_alert = f"🔥 残り{diff.days}日"
            else: time_alert = f"⏳{diff.days}日後"
        except: pass
    if is_completed:
        title_color = "#888888"
        text_deco = "line-through"
    else:
        title_color = "#000000"
        text_deco = "none"
    title_prefix = ""
    if is_pinned and not is_completed: title_prefix += "🚨"
    if is_new: title_prefix += "🆕"
    st.markdown(f"<div id='project-anchor-{index}'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        if is_pinned and not is_completed:
            st.markdown(
                f"""<style>
div[data-testid="stVerticalBlockBorderWrapper"]:has(#pinned-card-{index}) {{
    background-color: #fff3e0 !important;
}}
div[data-testid="stVerticalBlockBorderWrapper"]:has(#pinned-card-{index}) div[data-testid="column"]:nth-of-type(3) button {{
    background-color: #ef4444 !important;
    border-color: #ef4444 !important;
    color: white !important;
}}
</style>
<span id="pinned-card-{index}"></span>""",
                unsafe_allow_html=True
            )
            
        c_name, c_dead, c_pin, c_act, c_del, c_src = st.columns([4.5, 1.5, 0.45, 0.45, 0.45, 0.45])
        with c_name:
            st.markdown(
                f"<h5 style='color:{title_color}; text-decoration:{text_deco}; margin:0; font-size:1.15rem;'>"
                f"{title_prefix} {p_name} <small style='color:{title_color}; font-weight:normal; font-size:0.9rem;'>({p_chain})</small>"
                f"</h5>",
                unsafe_allow_html=True
            )
        with c_dead:
            deadline_display = f"{p_deadline}"
            if time_alert:
                deadline_display += f" {time_alert}"
            st.markdown(deadline_display)
        with c_pin:
            pin_help = "ピン留め解除" if is_pinned else "ピン留め固定"
            st.button("📌", key=f"pin_{index}", help=pin_help, use_container_width=True, on_click=on_toggle_pin, args=(index,))
        with c_act:
            if is_completed:
                st.button("↩️", key=f"revert_{index}", help="未完了に戻す", use_container_width=True, on_click=on_revert_task, args=(index,))
            else:
                st.button("✅", key=f"d_{index}", help="完了にする", use_container_width=True, on_click=on_complete_task, args=(index,))
        with c_del:
            st.button("🗑️", key=f"rm_{index}", help="削除する", use_container_width=True, on_click=on_delete_task, args=(index,))
        with c_src:
            if row.get("ソースURL") and str(row["ソースURL"]).startswith("http"):
                st.link_button("🔗", row["ソースURL"], use_container_width=True)
        p_funding = str(row.get("資金調達額", "不明"))
        p_vc = str(row.get("VC", "不明"))
        if p_funding != "不明" or p_vc != "不明":
            st.markdown(
                f"<div style='font-size:0.95rem; color:#333; background-color:#f8f9fa; padding:6px 10px; border-radius:6px; margin-top:4px; margin-bottom:6px;'>"
                f"💰 <b>資金調達:</b> {p_funding}　|　🤝 <b>VC:</b> {p_vc}"
                f"</div>",
                unsafe_allow_html=True
            )
        if is_new:
            st.markdown(
                "<div style='background:#e3f2fd; border-left:3px solid #2196f3; "
                "padding:4px 8px; border-radius:4px; font-size:0.85rem; color:#1565c0; margin-bottom:4px;'>"
                "✨ 新規追加 (24時間以内)</div>",
                unsafe_allow_html=True
            )
        elif is_pinned and not is_completed:
            st.markdown(
                "<div style='background:#ff6d00; border-left:3px solid #d84315; "
                "padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:bold; margin-bottom:4px;'>"
                "🔥 優先タスク (ピン留め/期限直近)</div>",
                unsafe_allow_html=True
            )
        if is_completed:
            st.caption("✅ 完了済みプロジェクト")
        st.markdown(
            f"<div style='font-size:0.9rem; padding:4px 0; line-height:1.6; white-space:pre-wrap;'>{row['タスク内容']}</div>",
            unsafe_allow_html=True
        )
    return None

# --- サイドバー ---
with st.sidebar:
    st.header("⚙️ 管理設定")
    if "save_error" in st.session_state and st.session_state["save_error"]:
        st.error(f"スプレッドシート保存エラー: {st.session_state['save_error']}")
    if st.button("🔄 重複プロジェクトをクリーンアップ"):
        df = load_tasks()
        if not df.empty:
            with st.spinner("クリーンアップ中..."):
                initial_count = len(df)
                all_tasks = df.to_dict('records')
                new_df = pd.DataFrame(columns=df.columns)
                new_df, _, _ = add_or_update_tasks(new_df, all_tasks, notify=False)
                final_count = len(new_df)
                st.success(f"クリーンアップ完了: {initial_count} 件 -> {final_count} 件")
                time.sleep(1)
                st.rerun()
    st.divider()

# --- メイン処理 ---
df = _get_cached_df()

if not df.empty:
    if auto_pin_urgent_tasks(df):
        st.toast("🔥 期限が近いタスクを自動でピン留めしました！", icon="⚠️")
        st.session_state.cached_df = None
        df = _get_cached_df()

jump_target_id = None

with st.expander("📊 プロジェクト一覧 (クリックで詳細へ移動)", expanded=True):
    if not df.empty:
        temp_df = df.copy()
        temp_df["is_new"] = temp_df.apply(lambda row: is_new_project(row["登録日時"]) and row["ステータス"] != "完了", axis=1)
        temp_df["is_completed"] = temp_df["ステータス"] == "完了"
        def make_display_name(row):
            name = str(row["プロジェクト名"])
            if name.lower() in ["nan", "none", ""]: name = "名称不明"
            prefix = "🆕 " if row["is_new"] else ""
            return prefix + name
        temp_df["表示名"] = temp_df.apply(make_display_name, axis=1)
        temp_df["期限"] = temp_df["期限"].astype(str).replace(["nan", "None", ""], "未定")
        temp_dates = pd.to_datetime(temp_df["期限"].replace("未定", "2099-12-31"), errors='coerce', utc=True)
        temp_df["sort_date"] = temp_dates.dt.tz_localize(None).fillna(pd.Timestamp.max)
        temp_df = temp_df.sort_values(
            by=["is_completed", "is_new", "ピン留め", "sort_date"],
            ascending=[True, False, False, True]
        )
        display_cols = ["ピン留め", "表示名", "期限", "チェーン", "ステータス"]
        event = st.dataframe(
            temp_df[display_cols],
            use_container_width=True,
            height=200,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "ピン留め": st.column_config.CheckboxColumn("📌", width="small"),
                "表示名": st.column_config.TextColumn("プロジェクト (選択で移動)", width="medium"),
                "期限": st.column_config.TextColumn("期限", width="medium"),
                "チェーン": st.column_config.TextColumn("Chain", width="small"),
                "ステータス": st.column_config.TextColumn("状態", width="small"),
            }
        )
        if event and len(event.selection.rows) > 0:
            selected_row_pos = event.selection.rows[0]
            jump_target_index = temp_df.index[selected_row_pos]
            jump_target_id = f"project-anchor-{jump_target_index}"
    else:
        st.info("現在タスクはありません。")

st.divider()

# --- タブエリア ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 タスクリスト & 秘書チャット",
    "📝 新規一括登録",
    "🐦 AI X ニュース",
    "📊 Polymarket予測",
    "📈 BTCマクロ"
])

with tab1:
    with st.expander("💬 AI秘書と話す", expanded=False):
        st.caption("例: 「MegaCorpを追加して」「〇〇を未完了に戻して」")
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.write(msg["content"])
        if prompt := st.chat_input("秘書への指示を入力..."):
            if not api_key: st.error("APIキーが必要です")
            else:
                st.session_state.chat_history.append({"role": "user", "content": prompt})
                with st.chat_message("user"): st.write(prompt)
                with st.spinner("秘書が処理中..."):
                    reply_text, action, used_model = process_chat_command(prompt, df)
                    update_msg = ""
                    if action:
                        actions = action if isinstance(action, list) else [action]
                        for act in actions:
                            if not isinstance(act, dict): continue
                            action_type = act.get("action")
                            if action_type == "update":
                                target = act.get("target_project_name")
                                field = act.get("update_field")
                                value = act.get("new_value")
                                target_idx = None
                                for idx, row in df.iterrows():
                                    if str(target).lower() in str(row["プロジェクト名"]).lower():
                                        target_idx = idx
                                        break
                                if target_idx is not None:
                                    if field == "削除":
                                        df = df.drop(target_idx)
                                        update_msg += f"\n\n✅ **「{target}」を削除しました。**"
                                    elif field == "ステータス":
                                        df.at[target_idx, "ステータス"] = value
                                        update_msg += f"\n\n✅ **「{df.at[target_idx, 'プロジェクト名']}」のステータスを「{value}」に変更しました。**"
                                    elif field in ["タスク内容", "タスク内容_追加"]:
                                        df.at[target_idx, "タスク内容"] = merge_memos(df.at[target_idx, "タスク内容"], value)
                                        update_msg += f"\n\n✅ **「{df.at[target_idx, 'プロジェクト名']}」のメモを統合・追加しました。**"
                                    elif field == "タスク内容_上書き":
                                        df.at[target_idx, "タスク内容"] = value
                                        update_msg += f"\n\n✅ **「{df.at[target_idx, 'プロジェクト名']}」のメモを要約・上書きしました。**"
                                    elif field == "期限":
                                        df.at[target_idx, "期限"] = value
                                        update_msg += f"\n\n✅ **期限を更新しました。**"
                                    elif field == "ピン留め":
                                        df.at[target_idx, "ピン留め"] = True
                                        df.at[target_idx, "通知設定"] = True
                                        update_msg += "\n\n✅ **ピン留めしました。**"
                                    save_tasks(df)
                                    st.session_state.need_rerun = True
                                else:
                                    update_msg += f"\n\n⚠️ プロジェクト「{target}」が見つかりませんでした。"
                            elif action_type == "create":
                                new_data = act.get("new_data")
                                if new_data:
                                    df, c_count, u_count = add_or_update_tasks(df, [new_data])
                                    if u_count > 0:
                                        update_msg += f"\n\n✅ **既存の「{new_data.get('プロジェクト名', '')}」に情報を統合しました。**"
                                    else:
                                        update_msg += f"\n\n✅ **新規プロジェクト「{new_data.get('プロジェクト名', '')}」を追加しました！**"
                                    st.session_state.need_rerun = True
                    full_reply = reply_text + update_msg
                    st.session_state.chat_history.append({"role": "assistant", "content": full_reply})
                    with st.chat_message("assistant"): st.write(full_reply)
                    if st.session_state.get("need_rerun"):
                        st.session_state.cached_df = df
                        st.session_state.need_rerun = False
                        st.rerun()

    st.divider()

    if not df.empty:
        df["is_completed"] = df["ステータス"] == "完了"
        df["is_new"] = df.apply(lambda row: is_new_project(row.get("登録日時")) and not row["is_completed"], axis=1)
        temp_dates = pd.to_datetime(df["期限"].replace("未定", "2099-12-31"), errors='coerce', utc=True)
        df["sort_date"] = temp_dates.dt.tz_localize(None).fillna(pd.Timestamp.max)
        all_sorted_df = df.sort_values(
            by=["is_completed", "is_new", "ピン留め", "sort_date"],
            ascending=[True, False, False, True]
        )
        for index, row in all_sorted_df.iterrows():
            render_task_card(index, row)
        if jump_target_id:
            js = f"""
            <script>
                var element = window.parent.document.getElementById('{jump_target_id}');
                if (element) {{
                    element.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                }}
            </script>
            """
            components.html(js, height=0)
    else:
        st.info("タスクが登録されていません。")

with tab2:
    st.write("### 📝 テキストから一括登録")
    raw_memo = st.text_area("ここに情報を貼り付け", height=150, key=f"memo_input_{st.session_state.memo_counter}")
    if st.button("AIに登録させる", type="primary"):
        import time
        now = time.time()
        last_submit_time = st.session_state.get("last_submit_time", 0)
        
        if not api_key:
            st.error("APIキーが必要です")
        elif not raw_memo.strip():
            st.warning("テキストを入力してください")
        elif now - last_submit_time < 30:
            st.warning("⚠️ 連続投稿を防ぐため、30秒ほどお待ちください。")
        else:
            st.session_state.last_submit_time = now
            with st.spinner("解析中..."):
                new_tasks = parse_memo_to_tasks(raw_memo)
                if new_tasks:
                    current_df = load_tasks()
                    updated_df, c_count, u_count = add_or_update_tasks(current_df, new_tasks)
                    msg = f"処理完了: 新規 {c_count} 件"
                    if u_count > 0:
                        msg += f" / 既存統合 {u_count} 件"
                    st.session_state.cached_df = updated_df
                    st.session_state.memo_counter += 1
                    st.toast(msg, icon="✅")
                    st.rerun()
                else:
                    st.error("プロジェクト情報を抽出できませんでした。テキストを確認してください。")

@st.cache_data(ttl=86400, show_spinner=False)
def translate_to_japanese_v2(text):
    if not text or len(str(text).strip()) == 0:
        return text
    if re.search(r'[ぁ-んァ-ン一-龥]', str(text)):
        return text
    try:
        res = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "ja", "dt": "t", "q": text},
            timeout=5,
        )
        return "".join([x[0] for x in res.json()[0]])
    except Exception as e:
        return text

with tab3:
    st.write("### 🐦 AIが厳選したXニュース")
    st.caption("GASで定期取得し、Geminiが評価した重要ニュースを表示します。")
    col_a, col_b = st.columns([1, 4])
    with col_a:
        if st.button("🔄 更新", key="refresh_x_news", use_container_width=True):
            st.rerun()
    x_df = load_x_news()
    if not x_df.empty:
        for i, row in x_df.iterrows():
            date_str = str(row.get("日時", ""))
            account = str(row.get("アカウント", ""))
            url = str(row.get("URL", ""))
            title = str(row.get("タイトル", ""))
            rank = str(row.get("ランク", ""))
            reason = str(row.get("理由", ""))
            title_jp = translate_to_japanese_v2(title)
            reason_jp = translate_to_japanese_v2(reason)
            rank_styles = {
                "S": {"bg": "linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%)", "text_color": "#800000", "badge_bg": "#d32f2f", "badge_text": "#ffffff", "icon": "🔥", "label": "Sランク (超重要)", "shadow": "rgba(255, 107, 129, 0.3)"},
                "A": {"bg": "linear-gradient(135deg, #f6d365 0%, #fda085 100%)", "text_color": "#5d4037", "badge_bg": "#f57c00", "badge_text": "#ffffff", "icon": "⭐", "label": "Aランク (重要)", "shadow": "rgba(253, 160, 133, 0.3)"},
                "B": {"bg": "linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%)", "text_color": "#1a237e", "badge_bg": "#1976d2", "badge_text": "#ffffff", "icon": "💠", "label": "Bランク (普通)", "shadow": "rgba(142, 197, 252, 0.3)"},
                "C": {"bg": "linear-gradient(135deg, #d4fc79 0%, #96e6a1 100%)", "text_color": "#1b5e20", "badge_bg": "#388e3c", "badge_text": "#ffffff", "icon": "✅", "label": "Cランク (参考)", "shadow": "rgba(150, 230, 161, 0.3)"}
            }
            style = rank_styles.get(rank, {"bg": "linear-gradient(135deg, #fdfbfb 0%, #ebedee 100%)", "text_color": "#212121", "badge_bg": "#757575", "badge_text": "#ffffff", "icon": "📰", "label": f"{rank}ランク", "shadow": "rgba(0,0,0,0.1)"})
            with st.container(border=False):
                html_content = f"""
                <div style='background: {style["bg"]}; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px {style["shadow"]}; margin-bottom: 12px; color: {style["text_color"]}; position: relative;'>
                    <div style='margin-bottom: 12px;'>
                        <span style='background-color: {style["badge_bg"]}; color: {style["badge_text"]}; padding: 4px 12px; border-radius: 20px; font-weight: bold; font-size: 0.85rem;'>
                            {style["icon"]} {style["label"]}
                        </span>
                    </div>
                    <div style='font-size: 1.3rem; font-weight: 800; margin-bottom: 16px; line-height: 1.5;'>
                        {title_jp}
                    </div>
                """
                if reason_jp == "AI判定エラー":
                    html_content += f"<div style='background:rgba(255,255,255,0.7); padding:12px; border-radius: 8px; font-size:0.9rem; color: #d32f2f; font-weight:bold;'>⚠️ AI判定エラー</div>"
                elif reason_jp and str(reason_jp).lower() not in ["nan", "none", ""]:
                    html_content += f"<div style='background:rgba(255,255,255,0.65); padding:12px 16px; border-radius: 8px; font-size:0.95rem; color: #212121; line-height: 1.6; border-left: 4px solid {style['badge_bg']};'>💡 <b>AIの分析:</b><br>{reason_jp}</div>"
                html_content += f"""
                    <div style='text-align: right; margin-top: 16px; font-size: 0.7rem; opacity: 0.6; font-weight: bold;'>
                        🏛️ {account} &nbsp;|&nbsp; 🕒 {date_str}
                    </div>
                </div>
                """
                st.markdown(html_content, unsafe_allow_html=True)
                c1, c2, c3 = st.columns([1.5, 2, 2])
                with c1:
                    if url and url.startswith("http"):
                        st.link_button("🔗 記事を読む", url, use_container_width=True)
                with c2:
                    if st.button("➕ タスクに追加", key=f"add_x_{i}", use_container_width=True):
                        task_data = {
                            "プロジェクト名": f"{account}のニュース",
                            "期限": "未定",
                            "タスク内容": f"{title_jp}\n\nAIの理由: {reason_jp}\nURL: {url}",
                            "チェーン": "未定",
                            "重要度": rank if rank in ["S", "A", "B", "C"] else "C",
                            "ソースURL": url
                        }
                        current_df = _get_cached_df()
                        updated_df, _, _ = add_or_update_tasks(current_df, [task_data])
                        st.session_state.cached_df = updated_df
                        st.toast("✅ ニュースをタスクに追加しました！", icon="✅")
                        st.rerun()
    else:
        st.info("ニュースはまだありません。GASが実行されるとここに表示されます。")

with tab4:
    polymarket_dashboard.render_polymarket_dashboard()

with tab5:
    btc_dashboard.render_btc_dashboard()
