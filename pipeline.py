import os
import sys
import json
import asyncio
import ist_time
import telegram_extractor
import caption_generator
import fb_publisher
import cadence_optimizer
import db_mongo
import config_loader

# UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(__file__)
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

def log_post_result(log_entry):
    os.makedirs(LOGS_DIR, exist_ok=True)
    history_file = os.path.join(LOGS_DIR, "post_history.json")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    history.append(log_entry)
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=4)
    except Exception:
        pass

    db_mongo.save_post_to_mongo(log_entry)

def cleanup_temporary_files(file_paths):
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                print(f"[🧹] Zero-Footprint: Cleaned up temporary file: {path}")
            except Exception as e:
                print(f"[!] Warning cleaning up file {path}: {e}")

def execute_pipeline_cycle():
    """
    Executes a complete publication cycle for 'best find':
    1. Downloads next video from Telegram (@allterapost)
    2. Generates high-converting FB caption with Amazon link (Original language preserved)
    3. Uploads directly to Facebook Page 'best find'
    4. Enforces strict publish confirmation verification
    5. Tags into the 7-day Cadence Optimizer
    """
    config = config_loader.load_config()
    print("\n" + "="*70)
    print(f"[{ist_time.now_ist_str()}] STARTING 'BEST FIND' AUTOMATION CYCLE")
    print("="*70)

    video_item = asyncio.run(telegram_extractor.get_next_video(config, DOWNLOAD_DIR))
    if not video_item:
        print("[!] No new video available to process. Pipeline waiting.")
        return False

    raw_video_path = video_item["file_path"]
    message_id = video_item["message_id"]
    raw_caption = video_item["raw_caption"]

    try:
        print(f"[*] Processing Video Msg #{message_id} (Language Preserved)...")

        # Step 2: Generate Viral Facebook Caption & Amazon Link
        caption_data = caption_generator.generate_viral_fb_caption(raw_video_path, raw_caption, config)
        product_name = caption_data["product_name"]
        amazon_link = caption_data["amazon_link"]
        summary = caption_data["summary"]
        fb_caption = f"{caption_data['caption']}\n\n#tg{message_id}"

        print("\n" + "-"*60)
        print("AI CONTENT & AMAZON AFFILIATE LINK GENERATED:")
        print(f"Product: {product_name}")
        print(f"Amazon Affiliate Link: {amazon_link}")
        print(f"Summary: {summary}")
        print(f"Facebook Caption Preview:\n{fb_caption[:180]}...")
        print("-"*60 + "\n")

        # Step 3: Publish to Facebook Page 'best find'
        fb_success, fb_id_or_err = fb_publisher.upload_facebook_video(raw_video_path, fb_caption, config)

        # Step 4: Strict Publish Confirmation Check
        if not fb_success:
            print("\n" + "!"*70)
            print(f"[!] PUBLISH CONFIRMATION FAILED FOR TELEGRAM MESSAGE #{message_id}")
            print(f"[-] Facebook upload error: {fb_id_or_err}")
            print(f"[!] PIPELINE BREAK: Message #{message_id} was NOT confirmed as published.")
            print(f"[!] Tracker NOT advanced. Video #{message_id} will be retried in the next cycle!")
            print("!"*70 + "\n")

            log_entry = {
                "timestamp": ist_time.now_ist_str(),
                "telegram_message_id": message_id,
                "product_name": product_name,
                "amazon_link": amazon_link,
                "summary": summary,
                "facebook": {"success": False, "result": str(fb_id_or_err)},
                "caption": fb_caption,
                "status": "FAILED_CONFIRMATION_WILL_RETRY"
            }
            log_post_result(log_entry)
            return False

        # Step 5: Mark as Processed (ONLY when verified published!)
        telegram_extractor.mark_video_processed(message_id, config)

        # Step 6: Record in 7-Day Cadence Experiment
        cadence_optimizer.record_post_in_experiment(message_id, fb_id_or_err, product_name)

        log_entry = {
            "timestamp": ist_time.now_ist_str(),
            "telegram_message_id": message_id,
            "product_name": product_name,
            "amazon_link": amazon_link,
            "summary": summary,
            "facebook": {"success": True, "result": str(fb_id_or_err)},
            "caption": fb_caption,
            "status": "CONFIRMED_PUBLISHED"
        }
        log_post_result(log_entry)

        print("\n" + "="*70)
        print(f"[+] CYCLE FINISHED SUCCESSFULLY FOR 'BEST FIND' (MSG #{message_id})")
        print("="*70 + "\n")
        return True

    finally:
        cleanup_temporary_files([raw_video_path])
