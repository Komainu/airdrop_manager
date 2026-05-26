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

# --- 設定 ---
st.set_page_config(page_title="よてい帳", layout="wide")
st.markdown("<div style='font-size:1.1rem; font-weight:bold; margin-top:0; padding-top:0; padding-bottom:10px;'>🛡️ よてい帳 & AI秘書</div>", unsafe_allow_html=True)

# --- コンパクトUI用CSS ---
st.markdown("""
<style>
/* カード内の余白を削減 */
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    padding: 6px 10px !important;
}
/* マークダウン段落の余白 */
div[data-testid="stMarkdownContainer"] p {
    margin-bottom: 2px !important;
}
/* h5の余白 */
div[data-testid="stMarkdownContainer"] h5 {
    margin-top: 2px !important;
    margin-bottom: 2px !important;
}
/* divider の余白 */
hr {
    margin: 4px 0 !important;
}
/* ボタン行の余白 */
div[data-testid="stHorizontalBlock"] {
    gap: 4px !important;
    margin-bottom: 0px !important;
}
/* ステータスバナー (st.error/st.caption) の余白 */
div[data-testid="stAlert"] {
    padding: 4px 10px !important;
    margin-bottom: 4px !important;
}
div[data-testid="stCaptionContainer"] {
    margin-bottom: 2px !important;
}
/* ボタンの高さを詰める */
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

# --- モデル実在確認ロジック (安全版) ---
def get_working_model_name():
    try:
        all_models = list(genai.list_models())
        text_models = [m.name for m in all_models if 'generateContent' in m.supported_generation_methods]
        
        if not text_models: return "models/gemini-1.5-flash"

        stable_models = [
            m for m in text_models 
            if "exp" not in m and "2.0" not in m and "2.5" not in m and "gemma" not in m
        ]
        
        flash_15 = next((m for m in stable_models if "1.5" in m and "flash" in m), None)
        if flash_15: return flash_15
        
        pro_15 = next((m for m in stable_models if "1.5" in m and "pro" in m), None)
        if pro_15: return pro_15
        
        any_flash = next((m for m in stable_models if "flash" in m), None)
        if any_flash: return any_flash

        if stable_models: return stable_models[0]
        return text_models[0]
    except:
        return "models/gemini-1.5-flash"

# --- Telegram 通知機能 ---
def send_telegram_notification(message):
    """Telegram Botを使用して通知を送信する"""
    try:
        if "telegram" not in st.secrets:
            return False
            
        bot_token = st.secrets["telegram"]["bot_token"]
        chat_id = st.secrets["telegram"]["chat_id"]
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Telegram通知送信成功: {message[:30]}...")
        return True
    except Exception as e:
        print(f"Telegram通知送信エラー: {e}")
        return False

def send_telegram_project_notification(row):
    """個別プロジェクトのTelegram通知を送信する（画像と同じ形式）"""
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

# メール通知はGASに統一（Python側のメール機能は削除済み）

# --- データ保存・読み込み機能 (Google スプレッドシート版) ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/13-zUqQcSm-3zXHF33p5be940lVWgOVbbj2FoN9aVph8/edit"

def get_worksheet():
    """secrets.toml から認証情報を読み込み、スプレッドシートに接続する"""
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
        # なければ作成する
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
        # 新しいものが上に来るように逆順
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Xニュース読み込みエラー: {e}")
        return pd.DataFrame()


def save_tasks(df):
    """DataFrameの内容をスプレッドシートに書き込む（安全版）"""
    try:
        # 空のDataFrameでシートを上書きしない（データ消失防止）
        if df.empty:
            print("[save_tasks] ⚠️ DataFrameが空のため書き込みをスキップしました")
            return

        with _save_lock:  # 同時書き込みの競合を防止
            ws = get_worksheet()
            header = df.columns.values.tolist()
            rows = df.astype(str).values.tolist()
            all_data = [header] + rows

            # 現在のシートの行数を取得し、足りない場合は空行でパディングして上書きする
            # （clearとupdateの間に読み込みが走って空と誤認されるのを防ぐため、単一のupdateで上書き）
            try:
                sheet_data = ws.get_all_values()
                current_rows = len(sheet_data)
            except Exception:
                current_rows = 1000  # 取得失敗時は余裕を持たせる

            empty_row = [""] * len(header)
            if len(all_data) < current_rows:
                # 減った分の行を空文字で上書きする
                padding_count = current_rows - len(all_data)
                all_data.extend([empty_row] * padding_count)

            # 単一のAPI呼び出しでアトミックに更新（旧データの消去含む）
            ws.update(all_data)
            print(f"[save_tasks] ✅ {len(rows)} 件を保存しました")
    except Exception as e:
        print(f"[save_tasks] ❌ スプレッドシート保存エラー: {e}")

def load_tasks():
    required_cols = ["プロジェクト名", "期限", "タスク内容", "チェーン", "重要度", "ステータス", "ソースURL", "ピン留め", "登録日時", "資金調達額", "VC", "通知設定", "Telegram通知済み"]
    try:
        with _save_lock:  # 読み込み時もロックをかけて、保存処理中の中途半端な状態を読まないようにする
            ws = get_worksheet()
            data = ws.get_all_values()

        # シートが完全に空の場合（初回起動時）
        if not data:
            ws.append_row(required_cols)
            return pd.DataFrame(columns=required_cols)

        # 1行目をヘッダー、2行目以降をデータとして DataFrame を作成
        headers = data[0]
        records = data[1:]
        if not records:
            return pd.DataFrame(columns=required_cols)
        df = pd.DataFrame(records, columns=headers)

        # 1. 必須カラムの補完（メモリ上のDataFrameのみ修正、シートへの書き戻しはしない）
        need_sheet_update = False
        for col in required_cols:
            if col not in df.columns:
                # 新規通知フラグ列は既存行をTrueで初期化（誤爆防止）
                if col in ["Telegram通知済み"]:
                    df[col] = "True"
                    need_sheet_update = True
                else:
                    df[col] = None

        # カラム不足の場合は save_tasks() 経由で安全に書き戻す
        if need_sheet_update:
            print("[load_tasks] 新規カラムを補完しました（次回保存時にシートへ反映されます）")

        # --- 強力なデータクリーニング (Ver.11) ---

        # プロジェクト名を文字列化し、前後の空白を除去
        df["プロジェクト名"] = df["プロジェクト名"].astype(str).str.strip()

        # 「nan」「None」「空文字」の行を特定して削除
        invalid_names = ["nan", "none", "", "null"]
        df = df[~df["プロジェクト名"].str.lower().isin(invalid_names)]

        # 期限のクリーニング
        df["期限"] = df["期限"].astype(str).str.strip()
        df["期限"] = df["期限"].replace(["nan", "None", "NaT", ""], "未定")

        # 欠損値埋め
        fill_defaults = {
            "チェーン": "未定",
            "タスク内容": "",
            "重要度": "C",
            "ステータス": "未完了",
            "ピン留め": False,
            "登録日時": "2000-01-01T00:00:00",
            "資金調達額": "不明",
            "VC": "不明",
            "通知設定": False,
            "Telegram通知済み": "True"
        }
        df = df.fillna(fill_defaults)

        # 【重要】データ型の強制 (特にピン留め・通知設定は真偽値へ)
        df["ピン留め"] = df["ピン留め"].map(lambda x: str(x).lower() == 'true' if not isinstance(x, bool) else x)
        df["通知設定"] = df["通知設定"].map(lambda x: str(x).lower() == 'true' if not isinstance(x, bool) else x)
        df["登録日時"] = df["登録日時"].astype(str)

        # 【同期】ピン留め=True かつ 通知設定=False のプロジェクトを自動修正
        # GASメール通知は「通知設定」列を参照するため、ピン留め済みは必ず通知対象にする
        pin_notify_mismatch = (df["ピン留め"] == True) & (df["通知設定"] == False) & (df["ステータス"] != "完了")
        if pin_notify_mismatch.any():
            mismatched_names = df.loc[pin_notify_mismatch, "プロジェクト名"].tolist()
            df.loc[pin_notify_mismatch, "通知設定"] = True
            print(f"[通知同期] ピン留め済みプロジェクトの通知設定をONに修正: {mismatched_names}")
            # save_tasks() 経由で安全に書き戻す（load_tasks内で直接clear+updateしない）
            save_tasks(df)

        return df
    except Exception as e:
        st.error(f"スプレッドシート読み込みエラー: {e}")
        return pd.DataFrame(columns=required_cols)

# --- 自動ピン留めロジック ---
def auto_pin_urgent_tasks(df):
    """
    期限が3日以内の未完了タスクを自動でピン留めする。
    ただし、ユーザーが意図的に外した場合は再ピン留めしないようにステータス等で制御が必要だが、
    現状は実行の旅に上書きされるため、ログ出力で挙動を確認し、条件を厳格化する。
    """
    updated = False
    now = datetime.now()
    for index, row in df.iterrows():
        if row["ステータス"] == "完了": continue
        
        # すでにピン留めされている場合はスキップ
        if row.get("ピン留め"): continue

        deadline_str = str(row["期限"])
        if deadline_str not in ["未定", "nan", "None"]:
            try:
                # タイムゾーンを考慮してパース
                deadline = pd.to_datetime(deadline_str, utc=True).tz_localize(None)
                diff = deadline - now
                
                # 期限まで3日以内、かつ過去30日以内のもののみ対象
                if diff.total_seconds() > -86400 * 30 and diff.days < 3: 
                    # 【重要】ここでは自動でピン留めしない（ユーザーの意志を優先）
                    # あるいは「自動ピン留め」フラグが立っていない場合のみにする等の処置が必要
                    # 今回は一旦、自動ピン留めの条件を「重要度がSまたはA」かつ「期限間近」に限定し、勝手に増えないようにする
                    if row.get("重要度") in ["S", "A"] and not row["ピン留め"]:
                        # df.at[index, "ピン留め"] = True # コメントアウトして勝手にピン留めされるのを防ぐ
                        # updated = True
                        print(f"[Info] Project {row['プロジェクト名']} is urgent, but auto-pin is disabled to respect user choice.")
            except Exception as e: 
                pass
    
    if updated: 
        print(f"[AutoPin] {len(df[df['ピン留め'] == True])} tasks are currently pinned.")
        save_tasks(df)
        st.cache_data.clear()
        return True
    return False

# --- コールバック関数 (状態更新用) ---
def _get_cached_df():
    """session_stateにキャッシュされたDataFrameを返す（なければ読み込む）"""
    if st.session_state.cached_df is None:
        st.session_state.cached_df = load_tasks()
    return st.session_state.cached_df

def _invalidate_cache():
    """キャッシュを無効化する"""
    st.session_state.cached_df = None
    st.cache_data.clear()

def save_tasks_async(df_copy):
    """バックグラウンドで保存を実行する（ロックにより同時実行を防止）"""
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
        print(f"[Debug] Project: {p_name} | Pin toggled: {current_val} -> {not current_val}")
        
        # ピン留めON時 → GASのメール通知対象にする（通知設定=True）
        if not current_val:  # False→True（ピン留めON）
            df.at[row_index, "通知設定"] = True
            print(f"[GAS通知] {p_name} の通知設定をONにしました")
        
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found in dataframe!")

def on_complete_task(row_index):
    df = _get_cached_df()
    if row_index in df.index:
        p_name = df.at[row_index, "プロジェクト名"]
        df.at[row_index, "ステータス"] = "完了"
        print(f"[Debug] Project: {p_name} | Status set to: 完了")
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found!")

def on_revert_task(row_index):
    df = _get_cached_df()
    if row_index in df.index:
        p_name = df.at[row_index, "プロジェクト名"]
        df.at[row_index, "ステータス"] = "未完了"
        print(f"[Debug] Project: {p_name} | Status reverted to: 未完了")
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found!")

def on_delete_task(row_index):
    df = _get_cached_df()
    if row_index in df.index:
        p_name = df.at[row_index, "プロジェクト名"]
        df = df.drop(row_index)
        print(f"[Debug] Project: {p_name} | Task deleted")
        update_cached_and_save(df)
    else:
        print(f"❌ [Error] Row index {row_index} not found!")

# --- 名寄せ・重複排除ロジック (Ver.12) ---
def merge_memos(old_memo, new_memo):
    """メモの内容を重複なく統合する"""
    if not old_memo or str(old_memo).lower() in ["nan", "none"]: return new_memo
    if not new_memo or str(new_memo).lower() in ["nan", "none"]: return old_memo
    
    # 文章や箇条書きで分割 (改行、中黒、ハイフン、数字付きリストなどに対応)
    def split_memo(text):
        if not text: return []
        # 改行で分割し、さらに箇条書き記号で分割
        lines = re.split(r'[\n\r]+', str(text))
        sentences = []
        for line in lines:
            # 箇条書き記号 (・, -, *, 1.) を除去して分割
            parts = re.split(r'^[ \t]*[・\-\*\d\.]+[\s　]*', line)
            for p in parts:
                clean_p = p.strip()
                if clean_p:
                    sentences.append(clean_p)
        return sentences

    old_lines = split_memo(old_memo)
    new_lines = split_memo(new_memo)
    
    # 重複を除外 (順序を維持しつつ、内容の重複を避ける)
    combined = old_lines.copy()
    for n in new_lines:
        # すでに完全に一致する行があるか、もしくは内容が包含されている場合は追加しない
        is_duplicate = False
        for o in combined:
            if n == o or (len(n) > 10 and (n in o or o in n)):
                is_duplicate = True
                break
        if not is_duplicate:
            combined.append(n)
    
    return "・" + "\n・".join(combined) if combined else ""

def add_or_update_tasks(existing_df, new_tasks, notify=True):
    """新規タスクを既存データにマージまたは追加する。notify=Trueで通知送信。"""
    updated_count = 0
    created_count = 0
    
    def normalize_name(n):
        # 記号、スペース、絵文字を除去して小文字化
        n = str(n).lower()
        n = re.sub(r'[\s　\-_,.\(\)\[\]]+', '', n)
        # 絵文字除去 (簡易版)
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
            # 既存の更新
            # タスク内容の統合
            existing_df.at[target_idx, "タスク内容"] = merge_memos(
                existing_df.at[target_idx, "タスク内容"], 
                task.get("タスク内容", "")
            )
            # 期限の更新 (「未定」なら新しいものを採用)
            new_deadline = task.get("期限", "未定")
            if str(existing_df.at[target_idx, "期限"]) == "未定" and new_deadline != "未定":
                existing_df.at[target_idx, "期限"] = new_deadline
            
            # 重要度の更新 (より高いものを優先 S > A > B > C)
            rank_map = {"S": 4, "A": 3, "B": 2, "C": 1}
            old_rank = rank_map.get(existing_df.at[target_idx, "重要度"], 0)
            new_rank = rank_map.get(task.get("重要度", ""), 0)
            if new_rank > old_rank:
                existing_df.at[target_idx, "重要度"] = task.get("重要度")
            
            # 登録日時は更新せず古いものを維持する（NEWマークを勝手につけないため）
            
            # チェーンの補完
            if str(existing_df.at[target_idx, "チェーン"]) == "未定" and task.get("チェーン"):
                existing_df.at[target_idx, "チェーン"] = task.get("チェーン")
                
            updated_count += 1
        else:
            # 新規追加
            task["ステータス"] = "未完了"
            task["ピン留め"] = False
            task["通知設定"] = False  # 常にオフ固定
            if "ソースURL" not in task: task["ソースURL"] = None
            task["登録日時"] = datetime.now(timezone.utc).isoformat()
            
            # --- Telegram通知を即座に送信 ---
            if notify:
                send_telegram_project_notification(task)
            
            # Telegram通知済みはTrue（送信済み）
            task["Telegram通知済み"] = "True"
            
            existing_df = pd.concat([existing_df, pd.DataFrame([task])], ignore_index=True)
            created_count += 1
            
    save_tasks(existing_df)
    return existing_df, created_count, updated_count

# --- AI解析ロジック ---
def parse_memo_to_tasks(memo):
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
        出力形式: [{{ "プロジェクト名": "...", "期限": "YYYY/MM/DD HH:MM", "タスク内容": "...", "チェーン": "...", "重要度": "S/A/B/C", "ソースURL": "...", "資金調達額": "...", "VC": "..." }}]
        """
        response = model.generate_content(prompt)
        match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if match: 
            return json.loads(match.group())
        # フォールバック: ```json ブロックを試みる
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', response.text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        return None
    except Exception as e: 
        st.error(f"解析エラー: {e}")
        return None

# --- AIチャットロジック ---
def process_chat_command(user_input, current_df):
    try:
        model_name = get_working_model_name()
        model = genai.GenerativeModel(model_name)
        
        # 完了済みも含めてコンテキストに渡す
        tasks_text = current_df.to_string(index=False)
        now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        
        prompt = f"""
        あなたはタスク・予定管理秘書です。
        ユーザーと会話をし、必要であればタスクデータの「更新」または「新規作成」を行ってください。
        現在時刻: {now_str}

        ## 指示
        - ユーザーの要求に合わせてJSONブロックを出力しデータを操作してください。
        - **重要**: 似た名前のプロジェクトが既に存在する場合は、新規作成("create")ではなく既存の更新("update")を検討してください。完了済みのプロジェクトも含めて対象としてください。
        - 情報を追加する場合は、無関係なプロジェクトの情報が混入しないよう細心の注意を払ってください。
        - 完了済みのタスクを「未完了」に戻す指示があった場合は、update_field="ステータス", new_value="未完了" としてください。
        - 【最重要】タスク内容（メモ）について「要約して」「短くして」「簡潔にして」「書き換えて」「まとめて」と指示された場合は、update_field を必ず "タスク内容_上書き" にしてください。既存の内容を全て破棄し、new_value には簡潔にまとめた新しい文章のみを記載してください。単なる情報の追加・補足の場合のみ "タスク内容_追加" を使用してください。
        - **注意**: 全く同じデータ（プロジェクト名）を作成しようとしないでください。必ずリストを確認し、存在する場合は "update" アクションを使用してください。

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
        response = model.generate_content(prompt)
        response_text = response.text
        
        # === ここから置き換え ===
        action_data = None
        
        # パターン1: ```json ... ``` で囲まれている場合を幅広くキャッチ
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', response_text, re.DOTALL)
        
        # パターン2: 生のJSONテキストがそのまま出力された場合
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
                
        return response_text, action_data, model_name
        
    except Exception as e:
        return f"エラー: {e}", None, "Unknown"

# --- NEWマーク判定ロジック ---
def is_new_project(reg_date_val):
    if pd.isna(reg_date_val) or str(reg_date_val) in ["nan", "None", "", "2000-01-01T00:00:00"]:
        return False
        
    try:
        # 文字列の場合はdatetimeオブジェクトにパースする
        if isinstance(reg_date_val, str):
            # 様々なフォーマットに対応させる
            try:
                reg_date_val = datetime.fromisoformat(reg_date_val.replace('Z', '+00:00'))
            except:
                reg_date_val = pd.to_datetime(reg_date_val, utc=True)

        # タイムゾーン情報がない（naive）場合は、UTCとして扱う
        if hasattr(reg_date_val, 'tzinfo') and reg_date_val.tzinfo is None:
            reg_date_val = reg_date_val.replace(tzinfo=timezone.utc)
        elif not hasattr(reg_date_val, 'tzinfo'):
            reg_date_val = pd.to_datetime(reg_date_val, utc=True)

        # 現在時刻もタイムゾーンを合わせる (UTC)
        now = datetime.now(timezone.utc)
        
        # 登録から24時間以内か判定
        diff = now - reg_date_val
        return diff.total_seconds() < 86400
    except Exception as e:
        # print(f"DEBUG: is_new_project error: {e} for value {reg_date_val}")
        return False

# --- UIコンポーネント: カード表示 (コンパクト版) ---
def render_task_card(index, row):
    is_pinned = row.get("ピン留め", False)
    is_completed = row.get("ステータス") == "完了"
    is_new = is_new_project(row.get("登録日時")) and not is_completed
    
    # データを安全に取り出す
    p_name = str(row["プロジェクト名"])
    if p_name.lower() in ["nan", "none", ""]: p_name = "名称不明"
    
    p_chain = str(row["チェーン"])
    if p_chain.lower() in ["nan", "none", ""]: p_chain = ""
    
    p_deadline = str(row["期限"])
    if p_deadline.lower() in ["nan", "none", ""]: p_deadline = "未定"

    # 期限アラート計算
    time_alert = ""
    if not is_completed and p_deadline != "未定":
        try:
            deadline = pd.to_datetime(p_deadline, utc=True).tz_localize(None)
            diff = deadline - datetime.now()
            if diff.total_seconds() < 0: time_alert = "❌ 期限切れ"
            elif diff.days < 3: time_alert = f"🔥 残り{diff.days}日"
            else: time_alert = f"⏳{diff.days}日後"
        except: pass
    
    # 色・装飾
    if is_completed:
        title_color = "#888888"
        text_deco = "line-through"
    else:
        title_color = "#000000"
        text_deco = "none"

    title_prefix = ""
    if is_pinned and not is_completed: title_prefix += "🚨"
    if is_new: title_prefix += "🆕"

    # ジャンプ用のHTMLアンカー
    st.markdown(f"<div id='project-anchor-{index}'></div>", unsafe_allow_html=True)
    
    with st.container(border=True):
        # ── 行1: プロジェクト名 ＋ 期限 ＋ ボタン群 ──
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
            pin_label = "📌"
            pin_help = "ピン留め解除" if is_pinned else "ピン留め固定"
            st.button(pin_label, key=f"pin_{index}", help=pin_help, use_container_width=True, on_click=on_toggle_pin, args=(index,))
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

        # ── 行1.5: 資金調達額とVCの表示 ──
        p_funding = str(row.get("資金調達額", "不明"))
        p_vc = str(row.get("VC", "不明"))
        if p_funding != "不明" or p_vc != "不明":
            st.markdown(
                f"<div style='font-size:0.95rem; color:#333; background-color:#f8f9fa; padding:6px 10px; border-radius:6px; margin-top:4px; margin-bottom:6px;'>"
                f"💰 <b>資金調達:</b> {p_funding}　|　🤝 <b>VC:</b> {p_vc}"
                f"</div>",
                unsafe_allow_html=True
            )

        # ── 行2: タスク内容（バナー表示）──
        if is_new:
            st.markdown(
                "<div style='background:#e3f2fd; border-left:3px solid #2196f3; "
                "padding:4px 8px; border-radius:4px; font-size:0.85rem; color:#1565c0; margin-bottom:4px;'>"
                "✨ 新規追加 (24時間以内)</div>",
                unsafe_allow_html=True
            )
        elif is_pinned and not is_completed:
            st.markdown(
                "<div style='background:#fff3cd; border-left:3px solid #ff6b6b; "
                "padding:4px 8px; border-radius:4px; font-size:0.85rem; color:#d84315; margin-bottom:4px;'>"
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

# 旧 send_new_project_notification / check_and_notify_new_projects は削除
# check_and_notify_pending に統合済み（上部に定義）

# --- サイドバー (管理機能) ---
with st.sidebar:
    st.header("⚙️ 管理設定")
    
    if st.button("🔄 重複プロジェクトをクリーンアップ"):
        df = load_tasks()
        if not df.empty:
            with st.spinner("クリーンアップ中..."):
                initial_count = len(df)
                # 一旦リスト化して add_or_update_tasks で再構築
                all_tasks = df.to_dict('records')
                new_df = pd.DataFrame(columns=df.columns)
                # 再構築（クリーンアップ時は通知しない）
                new_df, _, _ = add_or_update_tasks(new_df, all_tasks, notify=False)
                final_count = len(new_df)
                st.success(f"クリーンアップ完了: {initial_count} 件 -> {final_count} 件")
                time.sleep(1)
                st.rerun()
    st.divider()

# --- メイン処理 ---
df = _get_cached_df()

# 通知は add_or_update_tasks 内で即座に送信済み（ポーリング不要）

# 自動ピン留めチェック
if not df.empty:
    if auto_pin_urgent_tasks(df):
        st.toast("🔥 期限が近いタスクを自動でピン留めしました！", icon="⚠️")
        st.session_state.cached_df = None
        df = _get_cached_df()

# --- プロジェクト一覧 (サマリー) & ジャンプ機能 ---
jump_target_id = None

with st.expander("📊 プロジェクト一覧 (クリックで詳細へ移動)", expanded=True):
    if not df.empty:
        # ソートロジック (一覧表用)
        temp_df = df.copy()
        
        # ソート用の一時列を作成
        temp_df["is_new"] = temp_df.apply(lambda row: is_new_project(row["登録日時"]) and row["ステータス"] != "完了", axis=1) # 完了済みはNEWにしない
        temp_df["is_completed"] = temp_df["ステータス"] == "完了"
        
        # 表示名作成 (nan対策済み)
        def make_display_name(row):
            name = str(row["プロジェクト名"])
            if name.lower() in ["nan", "none", ""]: name = "名称不明"
            prefix = "🆕 " if row["is_new"] else ""
            return prefix + name

        temp_df["表示名"] = temp_df.apply(make_display_name, axis=1)
        
        # 期限表示のクリーニング
        temp_df["期限"] = temp_df["期限"].astype(str).replace(["nan", "None", ""], "未定")
        
        temp_dates = pd.to_datetime(temp_df["期限"].replace("未定", "2099-12-31"), errors='coerce', utc=True)
        temp_df["sort_date"] = temp_dates.dt.tz_localize(None).fillna(pd.Timestamp.max)
        
        # 【修正】ソートロジック: 
        # 1. 完了フラグ(昇順) -> 未完了が先
        # 2. NEWフラグ(降順) -> 新しいものが先
        # 3. ピン留め(降順) -> ピンが先
        # 4. 期限(昇順) -> 近いものが先
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
tab1, tab2, tab3 = st.tabs(["📅 タスクリスト & 秘書チャット", "📝 新規一括登録", "🐦 AI X ニュース"])

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
                                        df.at[target_idx, "通知設定"] = True  # GASメール通知対象にする
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

    # --- カード一覧表示 (ソートして表示) ---
    if not df.empty:
        df["is_completed"] = df["ステータス"] == "完了"
        # 24時間以内の新規プロジェクト判定
        df["is_new"] = df.apply(lambda row: is_new_project(row.get("登録日時")) and not row["is_completed"], axis=1)
        
        # 期限のソート (未定は未来として扱う)
        temp_dates = pd.to_datetime(df["期限"].replace("未定", "2099-12-31"), errors='coerce', utc=True)
        df["sort_date"] = temp_dates.dt.tz_localize(None).fillna(pd.Timestamp.max)
        
        # ソート: 完了フラグ(昇順), NEWフラグ(降順), ピン(降順), 日付(昇順)
        all_sorted_df = df.sort_values(
            by=["is_completed", "is_new", "ピン留め", "sort_date"], 
            ascending=[True, False, False, True]
        )
        
        for index, row in all_sorted_df.iterrows():
            render_task_card(index, row)
        
        # --- スクロール実行用JavaScript ---
        if jump_target_id:
            js = f"""
            <script>
                var element = window.parent.document.getElementById('{jump_target_id}');
                if (element) {{
                    element.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                }} else {{
                    var elements = window.parent.document.querySelectorAll('[id^="project-anchor-"]');
                    for (var i = 0; i < elements.length; i++) {{
                        if (elements[i].id === '{jump_target_id}') {{
                            elements[i].scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                            break;
                        }}
                    }}
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
        if not api_key:
            st.error("APIキーが必要です")
        elif not raw_memo.strip():
            st.warning("テキストを入力してください")
        else:
            with st.spinner("解析中..."):
                new_tasks = parse_memo_to_tasks(raw_memo)
                if new_tasks:
                    current_df = load_tasks()
                    updated_df, c_count, u_count = add_or_update_tasks(current_df, new_tasks)
                    
                    msg = f"処理完了: 新規 {c_count} 件"
                    if u_count > 0:
                        msg += f" / 既存統合 {u_count} 件"
                    
                    # キャッシュとカウンターを正しく更新してからrerun
                    st.session_state.cached_df = updated_df
                    st.session_state.memo_counter += 1
                    
                    st.toast(msg, icon="✅")
                    st.rerun()
                else:
                    st.error("AIがプロジェクト情報を抽出できませんでした。テキストを確認してください。")

@st.cache_data(ttl=3600)
def translate_to_japanese_v2(text):
    if not text or len(str(text).strip()) == 0:
        return text
    # 直訳や中途半端な英語交じりを防ぐため、必ず翻訳APIを通す(すでに完全な日本語ならそのまま返るようにプロンプトで指示)
    try:
        model_name = get_working_model_name()
        model = genai.GenerativeModel(model_name)
        prompt = f"""
        以下のテキストを自然で分かりやすい日本語に翻訳してください。
        直訳ではなく、暗号資産のコンテキストに合わせたプロらしい滑らかな日本語にしてください。
        ※もし入力テキストが既に日本語である場合は、翻訳せずにそのまま出力してください。
        
        テキスト:
        {text}
        """
        response = model.generate_content(prompt)
        return response.text.strip()
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
            
            # 強制翻訳
            title_jp = translate_to_japanese_v2(title)
            reason_jp = translate_to_japanese_v2(reason)
            
            # カラフルで視認性の高いデザイン
            rank_styles = {
                "S": {
                    "bg": "linear-gradient(135deg, #ff9a9e 0%, #fecfef 99%, #fecfef 100%)",
                    "text_color": "#800000",
                    "badge_bg": "#d32f2f", "badge_text": "#ffffff", "icon": "🔥", "label": "Sランク (超重要)",
                    "shadow": "rgba(255, 107, 129, 0.3)"
                },
                "A": {
                    "bg": "linear-gradient(135deg, #f6d365 0%, #fda085 100%)",
                    "text_color": "#5d4037",
                    "badge_bg": "#f57c00", "badge_text": "#ffffff", "icon": "⭐", "label": "Aランク (重要)",
                    "shadow": "rgba(253, 160, 133, 0.3)"
                },
                "B": {
                    "bg": "linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%)",
                    "text_color": "#1a237e",
                    "badge_bg": "#1976d2", "badge_text": "#ffffff", "icon": "💠", "label": "Bランク (普通)",
                    "shadow": "rgba(142, 197, 252, 0.3)"
                },
                "C": {
                    "bg": "linear-gradient(135deg, #d4fc79 0%, #96e6a1 100%)",
                    "text_color": "#1b5e20",
                    "badge_bg": "#388e3c", "badge_text": "#ffffff", "icon": "✅", "label": "Cランク (参考)",
                    "shadow": "rgba(150, 230, 161, 0.3)"
                }
            }
            style = rank_styles.get(rank, {
                "bg": "linear-gradient(135deg, #fdfbfb 0%, #ebedee 100%)",
                "text_color": "#212121",
                "badge_bg": "#757575", "badge_text": "#ffffff", "icon": "📰", "label": f"{rank}ランク",
                "shadow": "rgba(0,0,0,0.1)"
            })
            
            with st.container(border=False):
                html_content = f"""
                <div style='
                    background: {style["bg"]}; 
                    padding: 20px; 
                    border-radius: 12px; 
                    box-shadow: 0 4px 15px {style["shadow"]}; 
                    margin-bottom: 12px; 
                    color: {style["text_color"]};
                    position: relative;
                '>
                    <div style='margin-bottom: 12px;'>
                        <span style='background-color: {style["badge_bg"]}; color: {style["badge_text"]}; padding: 4px 12px; border-radius: 20px; font-weight: bold; font-size: 0.85rem; box-shadow: 0 2px 4px rgba(0,0,0,0.2);'>
                            {style["icon"]} {style["label"]}
                        </span>
                    </div>
                    <div style='font-size: 1.3rem; font-weight: 800; margin-bottom: 16px; line-height: 1.5; text-shadow: 1px 1px 2px rgba(255,255,255,0.4);'>
                        {title_jp}
                    </div>
                """
                
                # AIの理由表示
                if reason_jp == "AI判定エラー":
                    html_content += f"<div style='background:rgba(255,255,255,0.7); padding:12px; border-radius: 8px; font-size:0.9rem; color: #d32f2f; font-weight:bold;'>⚠️ AI判定エラー: 評価の取得に失敗しました。詳細は元記事をご確認ください。</div>"
                elif reason_jp and str(reason_jp).lower() not in ["nan", "none", ""]:
                    html_content += f"<div style='background:rgba(255,255,255,0.65); padding:12px 16px; border-radius: 8px; font-size:0.95rem; color: #212121; line-height: 1.6; border-left: 4px solid {style['badge_bg']};'>💡 <b>AIの分析:</b><br>{reason_jp}</div>"
                
                # ニュースソースを右下に小さく配置
                html_content += f"""
                    <div style='text-align: right; margin-top: 16px; font-size: 0.7rem; opacity: 0.6; font-weight: bold;'>
                        🏛️ {account} &nbsp;|&nbsp; 🕒 {date_str}
                    </div>
                </div>
                """
                
                st.markdown(html_content, unsafe_allow_html=True)
                
                # ボタン配置
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

