import os
import sys
import time
import requests

# UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def upload_facebook_video(video_path, caption, config):
    """
    Uploads video file directly to the 'best find' Facebook Page.
    Verifies upload confirmation before returning success.
    """
    meta_cfg = config.get("meta", {})
    page_id = meta_cfg.get("page_id", "255894584272909")
    page_token = meta_cfg.get("page_token")
    if not page_id or not page_token:
        return False, "Missing Facebook Page ID or Access Token in config"

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"[*] Uploading video to Facebook Page 'best find' ({file_size_mb:.2f} MB)...")

    url = f"https://graph.facebook.com/v22.0/{page_id}/videos"

    try:
        with open(video_path, "rb") as video_file:
            files = {
                "source": (os.path.basename(video_path), video_file, "video/mp4")
            }
            data = {
                "description": caption,
                "access_token": page_token
            }
            response = requests.post(url, files=files, data=data, timeout=300)

        res_json = response.json()
        if response.status_code == 200 and "id" in res_json:
            video_id = res_json["id"]
            fb_url = f"https://www.facebook.com/{video_id}"
            print(f"[+] FACEBOOK VIDEO LIVE on 'best find': ID #{video_id}")
            return True, video_id
        else:
            err_msg = res_json.get("error", {}).get("message", str(res_json))
            print(f"[-] Facebook upload error: {err_msg}")
            return False, err_msg
    except Exception as e:
        print(f"[X] Facebook video upload failed with exception: {e}")
        return False, str(e)

def get_page_posted_ids(config):
    """
    Cross-checks live Facebook Page videos to find which Telegram video IDs (#tg...)
    have already been successfully published to 'best find'.
    """
    meta_cfg = config.get("meta", {})
    page_id = meta_cfg.get("page_id", "255894584272909")
    page_token = meta_cfg.get("page_token")
    if not page_id or not page_token:
        return set()

    url = f"https://graph.facebook.com/v22.0/{page_id}/videos"
    params = {"fields": "id,description", "access_token": page_token, "limit": 40}
    posted = set()
    try:
        import re
        r = requests.get(url, params=params, timeout=12)
        if r.status_code == 200:
            for item in r.json().get("data", []):
                desc = item.get("description", "")
                for m_id in re.findall(r"#tg(\d+)", desc):
                    posted.add(int(m_id))
    except Exception as e:
        print(f"[!] FB cross-check notice: {e}")
    return posted

def get_video_metrics(fb_video_id, config):
    """
    Fetches engagement metrics for a published video to feed the 7-day cadence experiment.
    """
    meta_cfg = config.get("meta", {})
    page_token = meta_cfg.get("page_token")
    if not page_token or not fb_video_id:
        return None

    try:
        url = f"https://graph.facebook.com/v22.0/{fb_video_id}"
        params = {
            "fields": "views,likes.summary(true),comments.summary(true),sharedposts.summary(true)",
            "access_token": page_token
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            views = data.get("views", 0)
            likes = data.get("likes", {}).get("summary", {}).get("total_count", 0)
            comments = data.get("comments", {}).get("summary", {}).get("total_count", 0)
            shares = data.get("sharedposts", {}).get("summary", {}).get("total_count", 0)
            engagement = likes + (comments * 2) + (shares * 3)
            return {
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "engagement_score": engagement
            }
    except Exception as e:
        pass
    return None
