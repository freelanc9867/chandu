import openpyxl
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# Load sessions from Excel
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

# Function to send message from one session
async def send_from_session(session, target_username, message):
    phone = session["phone"]
    try:
        client = TelegramClient(
            StringSession(session["session_str"]),
            session["api_id"],
            session["api_hash"]
        )
        await client.connect()

        if not await client.is_user_authorized():
            print(f"❌ Session not authorized: {phone}")
            await client.disconnect()
            return False

        await client.send_message(target_username, message)
        print(f"✅ Sent from {phone}")
        await client.disconnect()
        return True
    except SessionPasswordNeededError:
        print(f"❌ 2FA enabled for {phone}, skipping.")
    except Exception as e:
        print(f"❌ Error with {phone}: {e}")
    return False

# Main runner with asyncio.gather()
async def send_all_messages(target_username, message):
    sessions = load_sessions()
    print(f"🚀 Sending to @{target_username} from {len(sessions)} sessions...")
    tasks = [send_from_session(session, target_username, message) for session in sessions]
    results = await asyncio.gather(*tasks)
    print(f"\n🎯 Total successful messages: {sum(results)}")

if __name__ == "__main__":
    target = input("👤 Enter recipient username (without @): ").strip()
    msg = input("💬 Enter your message: ").strip()
    asyncio.run(send_all_messages(target, msg))
