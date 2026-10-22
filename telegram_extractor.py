import os
import sys
import json
import asyncio
import re
from telethon import TelegramClient
from telethon.sessions import StringSession
import db_mongo
import fb_publisher
import ist_time

# UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TRACKER_FILE = os.path.join(os.path.dirname(__file__), "logs", "tracker.json")

def load_tracker():
    # 1. MongoDB Atlas
    mongo_tracker = db_mongo.load_tracker_from_mongo()
    if mongo_tracker:
        return mongo_tracker

    # 2. Local file
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "channel": "allterapost",
        "last_processed_id": 28824,
        "processed_ids": [28823, 28824]
    }

def save_tracker(data):
    os.makedirs(os.path.dirname(TRACKER_FILE), exist_ok=True)
    try:
        with open(TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass
    db_mongo.save_tracker_to_mongo(data)

async def sync_tracker_from_cloud(client):
    try:
        async for msg in client.iter_messages("me", limit=15):
            if msg.text and "[BEST_FIND_TRACKER]" in msg.text:
                json_part = msg.text.replace("[BEST_FIND_TRACKER]", "").strip()
                data = json.loads(json_part)
                print(f"[☁️] Synced 'Best Find' Tracker from Telegram Cloud: Last ID #{data.get('last_processed_id')}")
                return data
    except Exception as e:
        print(f"[!] Cloud tracker sync notice: {e}")
    return None

async def save_tracker_to_cloud(client_or_config, tracker_data):
    client = None
    should_disconnect = False
    if isinstance(client_or_config, dict):
        tg = client_or_config.get("telegram", {})
        string_sess = tg.get("string_session")
        client = TelegramClient(StringSession(string_sess), tg.get("api_id"), tg.get("api_hash"))
        await client.connect()
        should_disconnect = True
    else:
        client = client_or_config

    try:
        content = f"[BEST_FIND_TRACKER]\n{json.dumps(tracker_data)}"
        await client.send_message("me", content)
        print(f"[☁️] Persistent Cloud Tracker updated in Saved Messages (ID #{tracker_data.get('last_processed_id')})")
    except Exception as e:
        print(f"[!] Error persisting tracker to Telegram Cloud: {e}")
    finally:
        if should_disconnect and client:
            await client.disconnect()

def _run_coroutine(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

async def get_next_video(config, download_dir):
    """
    Connects to Telegram channel, syncs tracker state,
    cross-checks live Facebook Page 'best find',
    and downloads the oldest unposted video.
    """
    tg_cfg = config.get("telegram", {})
    api_id = tg_cfg.get("api_id")
    api_hash = tg_cfg.get("api_hash")
    channel_name = tg_cfg.get("channel_username", "allterapost")
    string_sess = tg_cfg.get("string_session")

    if string_sess:
        client = TelegramClient(StringSession(string_sess), api_id, api_hash)
    else:
        session_file = os.path.join(os.path.dirname(__file__), "telethon_session")
        client = TelegramClient(session_file, api_id, api_hash)

    await client.connect()
    if not await client.is_user_authorized():
        await client.start()

    print(f"[*] Accessing channel: {channel_name}...")
    channel = await client.get_entity(channel_name)

    # 1. Sync state from Cloud & Mongo
    tracker = load_tracker()
    cloud_tracker = await sync_tracker_from_cloud(client)
    if cloud_tracker:
        cloud_last_id = cloud_tracker.get("last_processed_id", 0)
        local_last_id = tracker.get("last_processed_id", 0)
        tracker["last_processed_id"] = max(cloud_last_id, local_last_id)
        merged_set = set(tracker.get("processed_ids", [])) | set(cloud_tracker.get("processed_ids", []))
        tracker["processed_ids"] = sorted(list(merged_set))
        save_tracker(tracker)

    # 2. Cross-check live Facebook Page videos
    fb_posted_ids = fb_publisher.get_page_posted_ids(config)

    # Check for any previously failed posts in history
    failed_ids = []
    try:
        recent_history = db_mongo.load_recent_history_from_mongo(limit=25) or []
        for post in recent_history:
            t_id = post.get("telegram_message_id")
            if not t_id:
                continue
            fb_s = post.get("facebook", {}).get("success", False)
            if not fb_s and int(t_id) not in fb_posted_ids:
                failed_ids.append(int(t_id))
    except Exception:
        pass

    last_id = tracker.get("last_processed_id", 28824)
    processed_set = set(tracker.get("processed_ids", [])) | fb_posted_ids | {28823, 28824}

    if failed_ids:
        failed_ids = sorted(list(set(failed_ids)))
        lowest_failed = min(failed_ids)
        print(f"[!] ⚠️ Found {len(failed_ids)} previously failed/unposted video(s): {failed_ids}")
        for fid in failed_ids:
            processed_set.discard(fid)
        search_min_id = lowest_failed - 1
    else:
        search_min_id = last_id

    print(f"[*] Searching for oldest unposted video after #{search_min_id} (Skipping {len(processed_set)} known IDs)...")

    os.makedirs(download_dir, exist_ok=True)
    target_msg = None

    async for msg in client.iter_messages(channel, min_id=search_min_id, reverse=True, limit=100):
        if msg.id in processed_set:
            continue

        is_video = False
        if msg.video:
            is_video = True
        elif msg.media and hasattr(msg.media, "document"):
            mime = getattr(msg.media.document, "mime_type", "")
            if mime.startswith("video/"):
                is_video = True

        if is_video:
            target_msg = msg
            break

    if not target_msg:
        print("[*] No new unprocessed videos found in channel history.")
        await client.disconnect()
        return None

    print(f"[*] Found Video Message ID: {target_msg.id} (Date: {target_msg.date})")
    file_path = os.path.join(download_dir, f"video_msg_{target_msg.id}.mp4")

    def progress_callback(current, total):
        pct = (current / total) * 100
        mb_cur = current / (1024 * 1024)
        mb_tot = total / (1024 * 1024)
        print(f"Downloading: {pct:.1f}% ({mb_cur:.1f}/{mb_tot:.1f} MB)", end="\r")

    print(f"[*] Downloading original video to {file_path}...")
    await client.download_media(target_msg, file=file_path, progress_callback=progress_callback)
    print(f"\n[+] Download complete: {file_path}")

    raw_caption = target_msg.text or ""
    await client.disconnect()

    return {
        "file_path": file_path,
        "message_id": target_msg.id,
        "raw_caption": raw_caption,
        "date": str(target_msg.date)
    }

def mark_video_processed(message_id, config=None):
    tracker = load_tracker()
    tracker["last_processed_id"] = max(tracker.get("last_processed_id", 0), message_id)
    p_set = set(tracker.get("processed_ids", []))
    p_set.add(message_id)
    tracker["processed_ids"] = sorted(list(p_set))
    save_tracker(tracker)
    db_mongo.save_tracker_to_mongo(tracker)
    if config:
        try:
            _run_coroutine(save_tracker_to_cloud(config, tracker))
        except Exception:
            pass

def reset_tracker_last_id(new_last_id, config=None):
    tracker = load_tracker()
    tracker["last_processed_id"] = new_last_id
    tracker["processed_ids"] = [i for i in tracker.get("processed_ids", []) if i <= new_last_id]
    save_tracker(tracker)
    db_mongo.save_tracker_to_mongo(tracker)
    if config:
        try:
            _run_coroutine(save_tracker_to_cloud(config, tracker))
        except Exception:
            pass
    print(f"[🔄] 'Best Find' Tracker reset to Message ID #{new_last_id}")
    return tracker
