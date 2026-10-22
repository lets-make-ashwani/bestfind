import os
import sys
import json
import re
import urllib.parse
import subprocess
from groq import Groq

# UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_AMAZON_TAG = "tech6s-21"

def clean_search_term(text):
    if not text:
        return "useful home gadgets"
    cleaned = re.sub(r'[^\w\s]', ' ', text)
    words = [w.strip() for w in cleaned.split() if len(w.strip()) > 1][:6]
    return " ".join(words) if words else "useful home gadgets"

def generate_amazon_link(product_name, tag=None):
    amazon_tag = (tag or DEFAULT_AMAZON_TAG).strip()
    clean_q = clean_search_term(product_name)
    encoded = urllib.parse.quote_plus(clean_q)
    return f"https://www.amazon.in/s?k={encoded}&tag={amazon_tag}", clean_q

def extract_audio_scratch(video_path, scratch_dir):
    os.makedirs(scratch_dir, exist_ok=True)
    audio_path = os.path.join(scratch_dir, "temp_audio.mp3")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-b:a", "64k",
        "-ar", "16000",
        audio_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return audio_path

def generate_viral_fb_caption(video_path, raw_caption, config):
    """
    Listens to video speech via Groq Whisper and generates the exact
    Facebook winning caption style used by Gadgets Mankind with Amazon buy link on Line 1.
    Original video & audio language remain 100% UNCHANGED.
    """
    groq_cfg = config.get("groq", {})
    api_key = groq_cfg.get("api_key")
    if not api_key:
        raise ValueError("Missing GROQ_API_KEY in configuration")

    client = Groq(api_key=api_key)
    scratch_dir = os.path.join(os.path.dirname(video_path), "scratch")

    audio_path = None
    transcribed_text = raw_caption or ""

    try:
        # Step 1: Extract temporary low-bitrate audio for Whisper transcription
        audio_path = extract_audio_scratch(video_path, scratch_dir)
        print("[*] Transcribing audio with Groq Whisper...")
        with open(audio_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), f.read()),
                model=groq_cfg.get("transcription_model", "whisper-large-v3"),
                response_format="verbose_json"
            )
        detected_text = getattr(transcription, "text", "") or ""
        if detected_text:
            transcribed_text = f"{transcribed_text}\n{detected_text}".strip()
            print(f"    - Heard: {detected_text[:120]}...")
    except Exception as e:
        print(f"[!] Audio transcription note (falling back to caption): {e}")
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except:
                pass

    # Step 2: Use Groq LLM to identify product name and craft the exact Gadgets Mankind Facebook caption
    llm_model = groq_cfg.get("llm_model", "openai/gpt-oss-120b")
    prompt = f"""
You are an expert social media copywriter for the Facebook Page "Best Find" (focusing on useful home gadgets & daily life-improving products).
Analyze this product video audio/text:
"{transcribed_text}"

TASK:
1. Identify the exact commercial product name (3-5 words in English, e.g. "Butterfly Floor Cleaning Mop", "Universal Water Pressure Nozzle", "Reusable Shower Drain Cover").
2. Write a 1-sentence hook calling out the frustrating daily problem this product solves (starts with 🚫 emoji).
3. Write a 1-sentence punchy benefit explaining why this product is a game-changer and saves time/money/effort.

OUTPUT JSON FORMAT ONLY:
{{
  "product_name": "Exact Commercial Product Title",
  "hook": "Stop struggling with clogged drains that ruin your bathroom floor!",
  "benefit": "catches all hair and debris with a tight suction seal so your pipes stay crystal clear.",
  "summary": "Short 1-line product summary"
}}
"""

    completion = client.chat.completions.create(
        model=llm_model,
        messages=[
            {"role": "system", "content": "You are a professional viral commerce copywriter. Respond ONLY with valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.4
    )

    data = json.loads(completion.choices[0].message.content)
    product_name = data.get("product_name", "Useful Home Gadget").strip()
    hook = data.get("hook", "Stop struggling with everyday household hassles!").strip()
    benefit = data.get("benefit", "makes your daily cleaning and chores 10x faster and effortless.").strip()
    summary = data.get("summary", product_name).strip()

    # Generate targeted Amazon Affiliate Link with tag=tech6s-21
    amazon_tag = config.get("meta", {}).get("amazon_tag", DEFAULT_AMAZON_TAG)
    buy_link, clean_q = generate_amazon_link(product_name, amazon_tag)

    # Format the exact Facebook caption style matching Gadgets Mankind:
    # Line 1: Direct 1-Click Amazon Buy Link
    # Line 2: Problem / Curiosity Hook
    # Line 3: Product Name — Benefit sentence
    # Line 4: Targeted hashtags
    fb_caption = (
        f"🛒 BUY LINK ➔ {buy_link}\n\n"
        f"{hook}\n"
        f"{product_name} — {benefit}\n\n"
        f"#BestFind #AmazonFinds #HomeGadgets #UsefulProducts #AmazonDeals #BestHomeProducts"
    )

    return {
        "product_name": product_name,
        "clean_search_query": clean_q,
        "amazon_link": buy_link,
        "summary": summary,
        "caption": fb_caption
    }
