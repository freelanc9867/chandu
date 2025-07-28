
import openpyxl
import asyncio
import json
import os
import random
import time
from datetime import datetime
from telethon import TelegramClient, types
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import GetMessagesViewsRequest

# Constants
VERSION = "1.0.5"
LOGS_DIR = "logs"
LOG_FILE = os.path.join(LOGS_DIR, f"view_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
LAST_SEEN_FILE = "last_seen.json"
SESSIONS_FILE = "sessions.xlsx"
CONFIG_FILE = "config.json"

# Create logs directory if it doesn't exist
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

def log(msg, print_to_console=True):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} - {msg}"
    if print_to_console:
        print(log_entry)
    with open(LOG_FILE, "a", encoding="utf-8") as lf:
        lf.write(f"{log_entry}\n")

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            log(f"✅ Configuration loaded from {CONFIG_FILE}")
            return config
    except Exception as e:
        log(f"❌ Error loading config: {e}")
        return {
            "target_channels": ["meta_ads_expert_nitin"],  # Replace with your target channels
            "continuous_monitoring": True,
            "check_interval": 10,  # Check for new posts every 10 seconds
            "max_retries": 3,
            "delay_between_posts_min": 2,
            "delay_between_posts_max": 5,
            "session_rotation": True,
            "rate_limit_window": 60,  # 1 minute window
            "rate_limit_max_requests": 50  # Max 50 requests per window
        }

def load_sessions(excel_file=SESSIONS_FILE):
    try:
        wb = openpyxl.load_workbook(excel_file)
        ws = wb.active
        sessions = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            try:
                if len(row) >= 4:
                    phone, api_id, api_hash, session_str = row[:4]
                    if all([session_str, api_id, api_hash]):
                        sessions.append({
                            "phone": str(phone),
                            "api_id": int(api_id),
                            "api_hash": str(api_hash),
                            "session_str": str(session_str),
                            "last_used": 0,
                            "success_count": 0,
                            "error_count": 0,
                            "is_active": True,
                            "request_count": 0
                        })
            except Exception as e:
                log(f"❌ Error processing row: {e}")
        wb.close()
        log(f"✅ Loaded {len(sessions)} sessions from Excel")
        return sessions
    except Exception as e:
        log(f"❌ Error loading sessions: {e}")
        return []

async def ensure_member(client, channel_info):
    try:
        if channel_info.get('username'):
            entity = await client.get_entity(channel_info['username'])
        else:
            entity = await client.get_entity(types.PeerChannel(
                channel_id=channel_info['id']
            ))
        return entity
    except Exception as e:
        log(f"❌ Channel access error: {e}")
        return None

async def view_post(client, entity, post_id, phone):
    try:
        message = await client.get_messages(entity, ids=post_id)
        if not message:
            return False
        try:
            await client(GetMessagesViewsRequest(
                peer=entity,
                id=[post_id],
                increment=True
            ))
            log(f"✅ {phone} viewed post {post_id}")
            return True
        except Exception as e:
            log(f"⚠️ View error for post {post_id}: {e}")
            return False
    except Exception as e:
        log(f"❌ Error viewing post {post_id}: {e}")
        return False

async def process_session_view(session, channel_info, post_ids):
    phone = session["phone"]
    try:
        client = None
        # Create or reuse an existing client for the session
        if "client" not in session or not session["client"]:
            session["client"] = TelegramClient(
                StringSession(session["session_str"]),
                session["api_id"],
                session["api_hash"]
            )
        client = session["client"]
        await client.connect()
        if not await client.is_user_authorized():
            log(f"❌ Session {phone} not authorized")
            if "client" in session:
                await session["client"].disconnect()
            session["is_active"] = False
            return False
        entity = await ensure_member(client, channel_info)
        if not entity:
            log(f"❌ Unable to access channel with session {phone}")
            if client:
                await client.disconnect()
            return False

        # Implement rate limiting
        rate_limit_window = config.get("rate_limit_window", 60)
        rate_limit_max_requests = config.get("rate_limit_max_requests", 50)
        if session["request_count"] >= rate_limit_max_requests:
            delay = rate_limit_window - (time.time() - session["last_used"])
            if delay > 0:
                log(f"⚠️ Rate limit reached for {phone}, waiting {delay:.2f} seconds")
                await asyncio.sleep(delay)
            session["request_count"] = 0
            session["last_used"] = time.time()

        for post_id in post_ids:
            success = await view_post(client, entity, post_id, phone)
            if success:
                session["success_count"] = session.get("success_count", 0) + 1
                session["request_count"] += 1
        if client:
            await client.disconnect()
        return True
    except Exception as e:
        log(f"❌ Session error for {phone}: {e}")
        if "client" in session and session["client"]:
            try:
                await session["client"].disconnect()
            except:
                pass
        session["error_count"] = session.get("error_count", 0) + 1
        session["is_active"] = False
        return False

async def check_for_new_posts(client, channel_info, last_seen):
    try:
        entity = await ensure_member(client, channel_info)
        if not entity:
            return []
        current_max_id = last_seen.get(channel_info["username"], 0)
        new_posts = []
        # Get all posts since the last seen post
        messages = await client.get_messages(entity, min_id=current_max_id, limit=10)
        if messages:
            new_posts = [msg.id for msg in messages]
            log(f"🔄 Found {len(new_posts)} new posts: {new_posts}")
        return new_posts
    except FloodWaitError as e:
        log(f"⚠️ FloodWait: {e.seconds}s - Waiting {e.seconds} seconds...")
        await asyncio.sleep(e.seconds)
        return []
    except Exception as e:
        log(f"❌ Error checking for new posts: {e}")
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
    except:
        return {}

async def continuous_monitor(sessions, config):
    global monitoring_active
    monitoring_active = True
    log("🚀 Starting continuous monitoring...")
    try:
        last_seen = load_last_seen()
        target_channels = config.get("target_channels", [])
        if not target_channels:
            log("❌ No target channels specified in configuration")
            return
        session_index = 0
        while monitoring_active:
            try:
                for channel in target_channels:
                    if not monitoring_active:
                        break
                    channel_info = {"username": channel}
                    # Use a single session to check for new posts
                    check_session = sessions[session_index % len(sessions)]
                    if not check_session["is_active"]:
                        log(f"⚠️ Session {check_session['phone']} is not active")
                        continue
                    client = None
                    try:
                        client = TelegramClient(
                            StringSession(check_session["session_str"]),
                            check_session["api_id"],
                            check_session["api_hash"]
                        )
                        await client.connect()
                        if not await client.is_user_authorized():
                            log(f"❌ Session {check_session['phone']} not authorized")
                            continue
                        new_posts = await check_for_new_posts(client, channel_info, last_seen)
                        if new_posts:
                            log(f"🔄 Processing {len(new_posts)} new posts for {channel_info['username']}")
                            # Process all new posts with all active sessions
                            tasks = []
                            for session in sessions:
                                if session["is_active"]:
                                    tasks.append(
                                        asyncio.create_task(
                                            process_session_view(session, channel_info, new_posts)
                                        )
                                    )
                            # Wait until all tasks are completed
                            await asyncio.gather(*tasks)
                            # Update last seen data
                            last_seen[channel_info["username"]] = max(last_seen.get(channel_info["username"], 0), max(new_posts))
                            save_last_seen(last_seen)
                        # Increment session index for the next check
                        session_index += 1
                    finally:
                        if client and client.is_connected():
                            await client.disconnect()
                    # Short delay between checks to avoid overwhelming the API
                    delay_min = config.get("delay_between_posts_min", 2)
                    delay_max = config.get("delay_between_posts_max", 5)
                    await asyncio.sleep(random.uniform(delay_min, delay_max))
            except FloodWaitError as e:
                log(f"⚠️ FloodWait: {e.seconds}s - Waiting {e.seconds} seconds...")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                log(f"❌ Monitoring error: {e}")
                await asyncio.sleep(60)
    except KeyboardInterrupt:
        log("\n⏹️ Monitoring stopped by user")
        monitoring_active = False
    except Exception as e:
        log(f"❌ Fatal monitoring error: {e}")
        monitoring_active = False

async def main():
    global config
    config = load_config()
    sessions = load_sessions()
    if not sessions:
        log("❌ No sessions found!")
        return
    log(f"✅ Loaded {len(sessions)} sessions")
    if len(sessions) > 20:
        log(f"⚠️ Consider using proxies for more than 20 sessions")
    await continuous_monitor(sessions, config)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\n⏹️ Monitoring stopped by user")
    except Exception as e:
        log(f"❌ Fatal error: {e}")
# Simple HTTP server to keep Render Web Service alive
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running')

def start_ping_server():
    server = HTTPServer(('0.0.0.0', 8000), PingHandler)
    server.serve_forever()

# Start the ping server in a background thread
threading.Thread(target=start_ping_server, daemon=True).start()
