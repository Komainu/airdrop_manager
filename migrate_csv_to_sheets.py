"""既存のCSVデータをGoogleスプレッドシートに移行するスクリプト"""
import gspread
from google.oauth2.service_account import Credentials
import toml
import pandas as pd

# --- 認証 ---
with open(r"c:\Users\xakah\OneDrive\ドキュメント\airdrop_manager\.streamlit\secrets.toml", "r", encoding="utf-8") as f:
    secrets = toml.load(f)

creds_dict = secrets["gcp_service_account"]
if "\\n" in creds_dict["private_key"]:
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
gc = gspread.authorize(credentials)

SHEET_URL = "https://docs.google.com/spreadsheets/d/13-zUqQcSm-3zXHF33p5be940lVWgOVbbj2FoN9aVph8/edit"
sheet = gc.open_by_url(SHEET_URL)
ws = sheet.sheet1

# --- CSV読み込み ---
csv_path = r"c:\Users\xakah\OneDrive\ドキュメント\airdrop_manager\airdrop_tasks.csv"
df = pd.read_csv(csv_path)

print(f"CSV行数: {len(df)}")
print(f"カラム: {list(df.columns)}")

# NaN を空文字に変換
df = df.fillna("")

# すべてを文字列化
header = df.columns.values.tolist()
rows = df.astype(str).values.tolist()

# --- スプレッドシートに書き込み ---
ws.clear()
ws.update([header] + rows)

print(f"✅ {len(df)} 件のデータをスプレッドシートに移行しました！")
