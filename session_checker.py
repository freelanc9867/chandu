# session_checker.py

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import openpyxl

def load_sessions(excel_file="sessions.xlsx"):
    wb = openpyxl.load_workbook(excel_file)
    ws = wb.active
    sessions = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        phone, api_id, api_hash, session_str = row
        if session_str and api_id and api_hash:
            sessions.append({
                "phone": phone,
                "api_id": int(api_id),
                "api_hash": str(api_hash),
                "session_str": session_str
            })
    wb.close()
    return sessions

ok = 0
fail = 0

print("\nSession validity check:\n")
for s in load_sessions():
    try:
        with TelegramClient(StringSession(s['session_str']), s['api_id'], s['api_hash']) as client:
            user = client.get_me()
            print(f"✅ {s['phone']} {user.username or user.first_name or user.id}")
            ok += 1
    except Exception as e:
        print(f"❌ {s['phone']} Error: {e}")
        fail += 1

print(f"\nSessions working: {ok}, invalid/expired: {fail}")
