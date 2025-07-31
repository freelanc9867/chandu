import openpyxl
import asyncio
import json
import os
import random
import time
from datetime import datetime
from telethon import TelegramClient, types
from telethon.errors import FloodWaitError, UserNotParticipantError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import GetMessagesViewsRequest

VERSION = "2.0-MERGED"
LOGS_DIR = "logs"
LOG_FILE = os.path.join(LOGS_DIR, f"view_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
LAST_SEEN_FILE = "last_seen.json"
SESSIONS_FILE = "sessions.xlsx"
CONFIG_FILE = "config.json"

if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

def log(msg, print_to_console=True):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} - {msg}"
    if print_to_console:
        print(log_entry)
    with open(LOG_FILE, "a", encoding="utf-8") as lf:
        lf.write(f"{log_entry}\n")

# --- Helper for channel/private/group identifier normalization
def normalize_identifier(identifier):
    """If identifier is an integer >0, formats to -100, else returns as string (username or already -100id)"""
    try:
        numeric_id = int(identifier)
        if numeric_id > 0:
            return int(f"-100{numeric_id}")
        return numeric_id
    except (ValueError, TypeError):
        return identifier

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            log(f"✅ Configuration loaded from {CONFIG_FILE}")
            return config
    except Exception as e:
        log(f"❌ Error loading config: {e}")
        return {
            "target_channels": ["meta_ads_expert_nitin"],
            "continuous_monitoring": True,
            "check_interval": 10,
            "max_retries": 3,
            "delay_between_posts_min": 2,
            "delay_between_posts_max": 5,
            "session_rotation": True,
            "rate_limit_window": 60,
            "rate_limit_max_requests": 50
        }

def load_sessions(excel_file=SESSIONS_FILE):
    try:
        wb = openpyxl.load_workbook(excel_file)
        ws = wb.active
        sessions = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if len(row) >= 4 and all(row[:4]):
                sessions.append({
                    "phone": str(row[0]), "api_id": int(row[1]),
                    "api_hash": str(row[2]), "session_str": str(row[3]),
                    "last_used": 0, "success_count": 0, "error_count": 0,
                    "is_active": True, "request_count": 0
                })
        wb.close()
        log(f"✅ Loaded {len(sessions)} sessions from Excel")
        return sessions
    except Exception as e:
        log(f"❌ Error loading sessions: {e}")
        return []

def save_last_seen(last_seen_data):
    try:
        with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(last_seen_data, f, indent=2)
    except Exception as e:
        log(f"❌ Error saving last seen: {e}")

def load_last_seen():
    try:
        with open(LAST_SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

async def ensure_member(client, api_channel_id):
    """Gets the channel entity using username or formatted id. Auto-join if not member of private."""
    try:
        entity = await client.get_entity(api_channel_id)
        return entity
    except UserNotParticipantError:
        log(f"⚠️ Not a member of {api_channel_id}, attempting to join...")
        try:
            await client(JoinChannelRequest(api_channel_id))
            log(f"✅ Successfully joined {api_channel_id}")
            return await client.get_entity(api_channel_id)
        except Exception as join_error:
            log(f"❌ Failed to join {api_channel_id}: {join_error}")
            return None
    except Exception as e:
        log(f"❌ Channel access error for {api_channel_id}: {e}")
        return None

async def view_post(client, entity, post_id, phone):
    try:
        await client(GetMessagesViewsRequest(peer=entity, id=[post_id], increment=True))
        log(f"✅ {phone} viewed post {post_id}")
        return True
    except Exception as e:
        log(f"⚠️ View error for post {post_id}: {e}")
        return False

async def process_session_view(session, api_channel_id, post_ids):
    """Views one or more post ids as a given 'session' if possible"""
    phone = session["phone"]
    try:
        client = TelegramClient(StringSession(session["session_str"]), session["api_id"], session["api_hash"])
        async with client:
            if not await client.is_user_authorized():
                log(f"❌ Session {phone} not authorized")
                session["is_active"] = False
                return False

            entity = await ensure_member(client, api_channel_id)
            if not entity:
                return False

            for post_id in post_ids:
                if await view_post(client, entity, post_id, phone):
                    session["success_count"] += 1
        return True
    except FloodWaitError as e:
        log(f"⚠️ FloodWait {phone}: {e.seconds}s - Waiting!")
        await asyncio.sleep(e.seconds + 3)
        return False
    except Exception as e:
        log(f"❌ Session error for {phone}: {e}")
        session["error_count"] += 1
        session["is_active"] = False
        return False

async def check_for_new_posts(client, api_channel_id, channel_key, last_seen):
    """
    Only fetch only most recent 4 posts. Return only those not already seen.
    channel_key is string/not normalized - so last_seen works for both id/username
    """
    try:
        entity = await ensure_member(client, api_channel_id)
        if not entity:
            return []
        messages = await client.get_messages(entity, limit=4)  # Only fetch 4 latest posts!
        seen_id = last_seen.get(channel_key, 0)
        new_posts = [msg.id for msg in messages if msg.id > seen_id]
        if new_posts:
            log(f"🔄 Found {len(new_posts)} new posts in '{channel_key}': {new_posts}")
        return sorted(new_posts)
    except FloodWaitError as e:
        log(f"⚠️ FloodWait: {e.seconds}s - Waiting {e.seconds} seconds...")
        await asyncio.sleep(e.seconds + 5)
        return []
    except Exception as e:
        log(f"❌ Error checking for new posts in '{channel_key}': {e}")
        return []

async def continuous_monitor(sessions, config):
    log("🚀 Starting continuous monitoring...")
    last_seen = load_last_seen()
    target_channels = config.get("target_channels", [])
    session_index = 0

    while True:
        for identifier_from_config in target_channels:
            # Accept either username or channel id. Convert for Telethon.
            api_channel_id = normalize_identifier(identifier_from_config)
            channel_key = str(identifier_from_config)  # Use as key for last_seen

            check_session = sessions[session_index % len(sessions)]
            if not check_session["is_active"]:
                session_index += 1
                continue

            try:
                client = TelegramClient(
                    StringSession(check_session["session_str"]),
                    check_session["api_id"],
                    check_session["api_hash"]
                )
                async with client:
                    if not await client.is_user_authorized():
                        log(f"❌ Unauthorized session: {check_session['phone']}")
                        check_session['is_active'] = False
                        continue

                    new_posts = await check_for_new_posts(client, api_channel_id, channel_key, last_seen)
                    if new_posts:
                        log(f"📌 Viewing {len(new_posts)} new posts in '{channel_key}'")
                        active_sessions = [s for s in sessions if s["is_active"]]
                        tasks = [process_session_view(s, api_channel_id, new_posts) for s in active_sessions]
                        await asyncio.gather(*tasks)
                        last_seen[channel_key] = max(new_posts)
                        save_last_seen(last_seen)
            except Exception as e:
                log(f"❌ Monitor error for channel '{channel_key}': {e}")
            session_index += 1
            await asyncio.sleep(random.uniform(config.get("delay_between_posts_min", 2), config.get("delay_between_posts_max", 5)))

async def main():
    global config
    config = load_config()
    sessions = load_sessions()
    if not sessions:
        log("❌ No sessions found!")
        return
    await continuous_monitor(sessions, config)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\n⏹️ Monitoring stopped by user")
    except Exception as e:
        log(f"❌ Fatal error: {e}")
