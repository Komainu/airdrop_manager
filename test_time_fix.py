import pandas as pd
from datetime import datetime, timedelta, timezone
import sys
import os

# Add current directory to path to import app
sys.path.append(os.getcwd())
from app import is_new_project

def test():
    # 1. 24時間以内 (UTC)
    recent_utc = datetime.now(timezone.utc).isoformat()
    print(f"Testing recent UTC ({recent_utc}): {is_new_project(recent_utc)}")
    
    # 2. 24時間以上前 (UTC)
    old_utc = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    print(f"Testing old UTC ({old_utc}): {is_new_project(old_utc)}")
    
    # 3. ナイーブな文字列 (UTCとして扱われるはず)
    recent_naive = datetime.now().isoformat()
    print(f"Testing recent naive ({recent_naive}): {is_new_project(recent_naive)}")
    
    # 4. 24時間以上前のナイーブな文字列
    old_naive = (datetime.now() - timedelta(hours=25)).isoformat()
    print(f"Testing old naive ({old_naive}): {is_new_project(old_naive)}")
    
    # 5. 無効な入力
    print(f"Testing None: {is_new_project(None)}")
    print(f"Testing empty: {is_new_project('')}")

if __name__ == "__main__":
    test()
