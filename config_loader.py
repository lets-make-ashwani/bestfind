import os
import json

try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except ImportError:
    pass

def load_config():
    cfg_file = os.path.join(os.path.dirname(__file__), "config.json")
    base_cfg = {}
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                base_cfg = json.load(f)
        except:
            pass

    tg = base_cfg.get("telegram", {})
    gr = base_cfg.get("groq", {})
    mt = base_cfg.get("meta", {})
    pl = base_cfg.get("pipeline", {})

    config = {
        "telegram": {
            "api_id": int(os.environ.get("TELEGRAM_API_ID", tg.get("api_id", 28675574))),
            "api_hash": os.environ.get("TELEGRAM_API_HASH", tg.get("api_hash", "b4086c0ad6e85161beef7a23df4ef3b5")),
            "channel_username": os.environ.get("TELEGRAM_CHANNEL", tg.get("channel_username", "allterapost")),
            "string_session": os.environ.get("TELEGRAM_STRING_SESSION", tg.get("string_session", ""))
        },
        "groq": {
            "api_key": os.environ.get("GROQ_API_KEY", gr.get("api_key", "")),
            "transcription_model": os.environ.get("GROQ_TRANSCRIPTION_MODEL", gr.get("transcription_model", "whisper-large-v3")),
            "llm_model": os.environ.get("GROQ_LLM_MODEL", gr.get("llm_model", "openai/gpt-oss-120b"))
        },
        "meta": {
            "page_id": os.environ.get("META_PAGE_ID", mt.get("page_id", "255894584272909")),
            "page_name": os.environ.get("META_PAGE_NAME", mt.get("page_name", "best find")),
            "page_token": os.environ.get("META_PAGE_TOKEN", mt.get("page_token", "EAAQAsYzjZCT4BS8F9HXAWB0ALBRdQaOGORYJC2SQKZBPV4YIdVBDXjfmKAFm4KVZCzHQdY8874cFa9Wd6lp6D3QDACpek60i54zUNTTDSSgoUgnb23wYm4OzxYtCUBHf2zZBowoUUEgRNZBvCWxZAEaI8gjI7200ItV50QdscHVguJQB9eug9ZCZBZBKaV0AVE9juTSYcZAv0ZD")),
            "amazon_tag": os.environ.get("AMAZON_AFFILIATE_TAG", mt.get("amazon_tag", "tech6s-21"))
        },
        "pipeline": {
            "cadence_mode": os.environ.get("CADENCE_MODE", pl.get("cadence_mode", "7_DAY_EXPERIMENT")),
            "preserve_original_audio": True
        }
    }
    return config
