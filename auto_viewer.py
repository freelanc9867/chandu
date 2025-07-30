import openpyxl
import asyncio
import json
import os
import random
import time
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import GetMessagesViewsRequest, SendReactionRequest # --- 1. ADDED IMPORT
from telethon.tl.types import ReactionEmoji # --- 1. ADDED IMPORT
from telethon.errors.rpcerrorlist import (
    UserNotParticipantError,
    FloodWaitError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    UserBannedInChannelError
)

# Constants
VERSION = "3.2.1" # Updated version
LOGS_DIR = "logs"
LOG_FILE = os.path.join(LOGS_DIR, f"view_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
LAST_SEEN_FILE = "last_seen.json"
SESSIONS_FILE = "sessions.xlsx"
CONFIG_FILE = "config.json"

# Create logs directory if it doesn't exist
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

def log(msg, print_to_console=True):
    """Logs a message to the console and the log file."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} - {msg}"
    if print_to_console:
        print(log_entry)
    with open(LOG_FILE, "a", encoding="utf-8") as lf:
        lf.write(f"{log_entry}\n")

def normalize_identifier(identifier):
    """
    Automatically formats a channel ID to the required -100... format if it's a positive number.
    Returns strings (usernames) or correctly formatted IDs as is.
    """
    try:
        numeric_id = int(identifier)
        if numeric_id > 0:
            return int(f"-100{numeric_id}")
        return numeric_id
    except (ValueError, TypeError):
        return identifier

def load_config():
    """Loads the configuration from config.json or returns defaults."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        log(f"✅ Configuration loaded from {CONFIG_FILE}")
        return config
    except FileNotFoundError:
        log(f"⚠️ {CONFIG_FILE} not found. Using default configuration.")
    except Exception as e:
        log(f"❌ Error loading config: {e}")
    # Default configuration with new reaction settings
    return {
        "target_channels": ["meta_ads_expert_nitin"],
        "check_interval": 10,
        "delay_between_posts_min": 2,
        "delay_between_posts_max": 5,
        "max_retries": 3,
        "dry_run": False,
        "avoid_re_viewing": True,
        # New Reaction Settings
        "enable_reactions": False,
        "max_reactions_per_post": 10,
        "available_reactions": ["👍", "❤️", "🔥", "🎉", "👏", "🥰", "💯"]
    }

def load_sessions(excel_file=SESSIONS_FILE):
    """Loads session data from the specified Excel file."""
    try:
        wb = openpyxl.load_workbook(excel_file)
        ws = wb.active
        sessions = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if len(row) >= 4 and all(row[:4]):
                sessions.append({
                    "phone": str(row[0]), "api_id": int(row[1]), "api_hash": str(row[2]),
                    "session_str": str(row[3]), "client": None, "is_active": False,
                    "error_count": 0, "success_count": 0, "last_used": 0, "request_count": 0
                })
        wb.close()
        log(f"✅ Loaded {len(sessions)} sessions from {excel_file}")
        return sessions
    except FileNotFoundError:
        log(f"❌ Sessions file not found: {excel_file}")
    except Exception as e:
        log(f"❌ Error loading sessions: {e}")
    return []

def save_last_seen(last_seen_data):
    """Saves the last seen post IDs to a JSON file."""
    try:
        with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(last_seen_data, f, indent=4)
    except Exception as e:
        log(f"❌ Error saving last seen data: {e}")

def load_last_seen():
    """Loads the last seen post IDs from a JSON file."""
    try:
        with open(LAST_SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log(f"❌ Error loading last seen data: {e}")
        return {}

def save_summary(sessions):
    """Saves a final summary of session performance to a JSON file."""
    summary_data = []
    for s in sessions:
        last_used_str = "Never"
        if s['last_used'] > 0:
            last_used_str = datetime.fromtimestamp(s['last_used']).strftime('%Y-%m-%d %H:%M:%S')
        summary_data.append({
            "phone": s["phone"],
            "success_count": s["success_count"],
            "error_count": s["error_count"],
            "last_used": last_used_str,
            "is_active_at_end": s["is_active"]
        })
    filename = os.path.join(LOGS_DIR, f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=4)
        log(f"📊 Final summary saved to {filename}")
    except Exception as e:
        log(f"❌ Failed to save final summary: {e}")

async def ensure_channel_access(client, channel_identifier, session_phone):
    """Ensures the client is a member of the channel, joining if necessary."""
    try:
        entity = await client.get_entity(channel_identifier)
        return entity
    except UserNotParticipantError:
        log(f"⚠️ Session {session_phone} is not in '{channel_identifier}'. Attempting to join...")
        if config.get("dry_run"):
            log(f"DRY RUN: Would have joined '{channel_identifier}'. Cannot proceed.")
            return None
        try:
            await client(JoinChannelRequest(channel_identifier))
            log(f"✅ Session {session_phone} successfully joined '{channel_identifier}'.")
            return await client.get_entity(channel_identifier)
        except UserBannedInChannelError:
            log(f"❌ Session {session_phone} is banned from '{channel_identifier}'.")
            return None
        except Exception as e:
            log(f"❌ Session {session_phone} failed to join '{channel_identifier}': {e}")
            return None
    except Exception as e:
        log(f"❌ Could not get entity for '{channel_identifier}' with session {session_phone}: {e}")
        return None

async def view_post_with_session(session, channel_identifier, post_ids):
    """Uses a single session to view a list of posts, with dry run support."""
    phone, client = session["phone"], session["client"]
    if not session["is_active"] or not client or not client.is_connected():
        return
    try:
        if config.get("dry_run"):
            log(f"DRY RUN: {phone} would view {len(post_ids)} post(s) in '{channel_identifier}'.")
            session["success_count"] += len(post_ids)
            return True
        await client(GetMessagesViewsRequest(peer=await client.get_entity(channel_identifier), id=post_ids, increment=True))
        session["success_count"] += len(post_ids)
        log(f"✅ {phone} viewed {len(post_ids)} post(s) in '{channel_identifier}'.")
        return True
    except Exception as e:
        log(f"❌ Error viewing posts with {phone}: {e}")
        session["error_count"] += 1
        return False

async def react_to_post_task(session, channel_identifier, post_id, reaction):
    """A wrapper for reacting to a post that includes delays and error handling."""
    phone = session["phone"]
    client = session["client"]
    delay = random.uniform(config.get("delay_between_posts_min", 1), config.get("delay_between_posts_max", 5))
    await asyncio.sleep(delay)
    try:
        if config.get("dry_run"):
            log(f"DRY RUN: {phone} would react with '{reaction}' to post {post_id}.")
            return
        
        # --- 2. THIS IS THE CORRECTED LINE ---
        await client(SendReactionRequest(
            peer=channel_identifier,
            msg_id=post_id,
            reaction=[ReactionEmoji(emoticon=reaction)]
        ))
        log(f"✅ {phone} reacted with '{reaction}' to post {post_id}.")
        
    except FloodWaitError as e:
        log(f"🌊 FloodWait for {phone} on reaction: waiting {e.seconds}s...")
        await asyncio.sleep(e.seconds + 5)
    except Exception as e:
        log(f"❌ Error reacting with {phone}: {e}")
        session["error_count"] += 1

async def initialize_clients(sessions):
    """Connects all clients and returns a list of active sessions."""
    log("🚀 Initializing all client sessions...")
    active_sessions = []
    for session in sessions:
        phone, api_id, api_hash, session_str = session['phone'], session['api_id'], session['api_hash'], session['session_str']
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        try:
            if config.get("dry_run"):
                log(f"DRY RUN: Would connect client for {phone}.")
                session.update({"client": client, "is_active": True})
                active_sessions.append(session)
                continue
            await client.connect()
            if not await client.is_user_authorized():
                log(f"❌ Session {phone} is not authorized. Skipping.")
                await client.disconnect()
                continue
            session.update({"client": client, "is_active": True})
            active_sessions.append(session)
            log(f"✅ Session {phone} connected and authorized.")
        except Exception as e:
            log(f"❌ Failed to initialize session {phone}: {e}")
    return active_sessions

async def continuous_monitor(sessions, config):
    """Main loop to monitor channels and dispatch viewing and reaction tasks."""
    last_seen = load_last_seen()
    target_channels = config.get("target_channels", [])
    
    while True:
        active_sessions = [s for s in sessions if s.get("is_active")]
        if not active_sessions:
            log("❌ No active sessions available. Stopping.")
            break
        
        check_session = random.choice(active_sessions)
        
        for identifier_from_config in target_channels:
            channel_identifier_for_api = normalize_identifier(identifier_from_config)
            channel_key = str(identifier_from_config)
            log(f"🔎 [{check_session['phone']}] Checking '{channel_key}' (API format: {channel_identifier_for_api})")
            
            try:
                checker_client = check_session["client"]
                if config.get("dry_run"):
                    new_post_ids = []
                else:
                    entity = await ensure_channel_access(checker_client, channel_identifier_for_api, check_session["phone"])
                    if not entity:
                        continue
                    min_id = last_seen.get(channel_key, 0) if config.get("avoid_re_viewing", True) else 0
                    messages = await checker_client.get_messages(entity, min_id=min_id, limit=100)
                    new_post_ids = [msg.id for msg in messages if msg.id > min_id]

                if new_post_ids:
                    log(f"Found {len(new_post_ids)} new posts in '{channel_key}'. Dispatching actions...")
                    
                    all_tasks = []
                    # 1. Create viewing tasks for all active sessions
                    view_tasks = [view_post_with_session(s, channel_identifier_for_api, new_post_ids) for s in active_sessions]
                    all_tasks.extend(view_tasks)

                    # 2. Create reaction tasks if enabled
                    if config.get("enable_reactions", False):
                        available_reactions = config.get("available_reactions", [])
                        if available_reactions:
                            for post_id in new_post_ids:
                                num_to_react = min(config.get("max_reactions_per_post", 10), len(active_sessions))
                                reacting_sessions = random.sample(active_sessions, num_to_react)
                                for session in reacting_sessions:
                                    chosen_reaction = random.choice(available_reactions)
                                    all_tasks.append(react_to_post_task(session, channel_identifier_for_api, post_id, chosen_reaction))

                    await asyncio.gather(*all_tasks)

                    last_seen[channel_key] = max(new_post_ids)
                    save_last_seen(last_seen)
                else:
                    log(f"👍 No new posts found in '{channel_key}'.")
                    
            except Exception as e:
                log(f"❌ Error in monitoring loop for '{channel_key}': {e}")
        
        interval = config.get("check_interval", 10)
        log(f"--- Sleeping for {interval} seconds before next check ---")
        await asyncio.sleep(interval)

async def main():
    """Main function to set up and run the bot."""
    log(f"--- This was developed by nitinshukla.bio ---")
    global config
    config = load_config()
    if config.get("dry_run"):
        log("🚱 DRY RUN MODE IS ACTIVE. No real actions will be performed.", True)
    all_sessions = load_sessions()
    if not all_sessions:
        return
    active_sessions = await initialize_clients(all_sessions)
    if not active_sessions:
        log("❌ No sessions could be initialized. Exiting.")
        return
    try:
        await continuous_monitor(active_sessions, config)
    finally:
        log("🔌 Shutting down...")
        if not config.get("dry_run"):
            disconnect_tasks = [s["client"].disconnect() for s in active_sessions if s.get("client") and s["client"].is_connected()]
            if disconnect_tasks:
                await asyncio.gather(*disconnect_tasks)
            log("✅ All clients disconnected.")
        save_summary(all_sessions)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\n⏹️ Process interrupted by user.")
    except Exception as e:
        log(f"💥 A fatal error occurred in main execution: {e}")
