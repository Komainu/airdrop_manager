import pandas as pd
import re
from datetime import datetime

# Import logic from app.py by mock-defining or copying the relevant parts
# Since app.py has streamlit imports, it's easier to copy the logic here for testing.

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

def normalize_name(n):
    n = str(n).lower()
    n = re.sub(r'[\s　\-_,.\(\)\[\]]+', '', n)
    n = re.sub(r'[^\w\s]', '', n)
    return n

def test_merging():
    print("Testing merge_memos...")
    old = "・Task 1\n・Task 2"
    new = "・Task 2\n・Task 3"
    result = merge_memos(old, new)
    print(f"Result: {result}")
    assert "Task 1" in result
    assert "Task 2" in result
    assert "Task 3" in result
    assert result.count("Task 2") == 1
    
    print("Testing overlapping text...")
    old = "Go to the website and register your account."
    new = "register your account"
    result = merge_memos(old, new)
    print(f"Result (overlap): {result}")
    assert result.count("account") == 1

    print("Testing normalization...")
    name1 = "MegaCorp Global"
    name2 = " Mega-Corp (Global) 🚀"
    print(f"Norm 1: {normalize_name(name1)}")
    print(f"Norm 2: {normalize_name(name2)}")
    assert normalize_name(name1) == normalize_name(name2)
    
    print("\nAll tests passed!")

if __name__ == "__main__":
    test_merging()
