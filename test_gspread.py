import gspread
from google.oauth2.service_account import Credentials
import toml
import os

with open(r"c:\Users\xakah\OneDrive\ドキュメント\airdrop_manager\.streamlit\secrets.toml", "r", encoding="utf-8") as f:
    secrets = toml.load(f)

creds_dict = secrets["gcp_service_account"]
if "\\n" in creds_dict["private_key"]:
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
gc = gspread.authorize(credentials)

SHEET_URL = "https://docs.google.com/spreadsheets/d/13-zUqQcSm-3zXHF33p5be940lVWgOVbbj2FoN9aVph8/edit"

try:
    sheet = gc.open_by_url(SHEET_URL)
    worksheet = sheet.sheet1
    print("SUCCESS")
except Exception as e:
    import traceback
    with open("error.txt", "w", encoding="utf-8") as f:
        traceback.print_exc(file=f)
    print("ERROR WRITTEN TO error.txt")
