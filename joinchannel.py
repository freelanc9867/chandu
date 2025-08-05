import asyncio
import json
import openpyxl
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import FloodWaitError
import random
import time

async def bulk_join_channel():
    # Load sessions from Excel
    wb = openpyxl.load_workbook("sessions.xlsx")
    ws = wb.active
    sessions = []
    
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            phone, api_id, api_hash, session_str = row[:4]
            if all([session_str, api_id, api_hash]):
                sessions.append({
                    "phone": str(phone),
                    "api_id": int(api_id),
                    "api_hash": str(api_hash),
                    "session_str": str(session_str)
                })
        except:
            continue
    
    wb.close()
    
    # Get channel to join
    channel_username = input("Enter channel username (without @): ").strip()
    if not channel_username:
        print("❌ No channel provided!")
        return
    
    print(f"📱 Loaded {len(sessions)} sessions")
    print(f"🎯 Target channel: @{channel_username}")
    
    successful_joins = 0
    failed_joins = 0
    
    for i, session in enumerate(sessions, 1):
        client = None
        try:
            print(f"\n[{i}/{len(sessions)}] Joining with {session['phone']}...")
            
            client = TelegramClient(
                StringSession(session["session_str"]),
                session["api_id"],
                session["api_hash"]
            )
            
            await client.connect()
            
            if not await client.is_user_authorized():
                print(f"❌ {session['phone']} not authorized")
                failed_joins += 1
                continue
            
            # Join the channel
            try:
                await client(JoinChannelRequest(channel_username))
                print(f"✅ {session['phone']} joined successfully!")
                successful_joins += 1
                
            except FloodWaitError as e:
                print(f"⚠️ {session['phone']} hit flood wait: {e.seconds}s")
                if e.seconds < 300:  # Wait if less than 5 minutes
                    await asyncio.sleep(e.seconds + 5)
                    try:
                        await client(JoinChannelRequest(channel_username))
                        print(f"✅ {session['phone']} joined after wait!")
                        successful_joins += 1
                    except:
                        failed_joins += 1
                else:
                    failed_joins += 1
                    
            except Exception as e:
                print(f"❌ {session['phone']} failed: {e}")
                failed_joins += 1
            
            # Random delay between joins
            await asyncio.sleep(random.uniform(3, 8))
            
        except Exception as e:
            print(f"❌ Error with {session['phone']}: {e}")
            failed_joins += 1
            
        finally:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
    
    print(f"\n📊 RESULTS:")
    print(f"✅ Successful joins: {successful_joins}")
    print(f"❌ Failed joins: {failed_joins}")
    print(f"📈 Success rate: {(successful_joins/(successful_joins+failed_joins)*100):.1f}%")

if __name__ == "__main__":
    asyncio.run(bulk_join_channel())
