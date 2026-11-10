import os, json, html, requests
import xml.etree.ElementTree as ET


def load_env():
    for line in open(os.path.expanduser("~/.viral.env")):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


load_env()
UA = {"User-Agent": "Mozilla/5.0 (viral-trend-bot)"}

MODELS = [
    os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
]


def google_trends():
    out = []
    try:
        r = requests.get("https://trends.google.com/trending/rss?geo=US", headers=UA, timeout=20)
        for it in ET.fromstring(r.content).iter("item"):
            tr = next((c.text for c in it if c.tag.endswith("approx_traffic")), "")
            out.append(f"[GoogleTrends] {html.unescape(it.findtext('title') or '')} ({tr} searches)")
    except Exception as e:
        print("google trends failed:", e)
    return out[:15]


def hacker_news():
    try:
        r = requests.get("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=10", timeout=20).json()
        return [f"[HackerNews] {h['title']} ({h.get('points', 0)} pts)" for h in r["hits"]]
    except Exception as e:
        print("hn failed:", e)
        return []


def build_prompt(trends):
    return (
        "You run a viral short-video news channel for TikTok, YouTube Shorts, Instagram and Facebook.\n"
        "Host style: fast, funny, friendly-sarcastic, simple English.\n"
        "From the trends below, pick the 2 best topics for short videos (high interest, safe, easy to explain).\n"
        "Skip tragedies, deaths, violence, and unverified rumors. If unsure, say \"reportedly\".\n"
        "Topic areas we like: funny/viral internet moments, tech and AI news, sports moments.\n"
        "For each pick return JSON with these keys: topic, why_viral, hook (max 10 words), "
        "script (95-110 words, ends with a question to drive comments), caption, "
        "hashtags (list of 8), visuals (list of 4 simple scene ideas).\n"
        "Return ONLY a JSON list of 2 objects. No other text, no markdown.\n\nTRENDS:\n" + "\n".join(trends)
    )


def parse_json_list(text):
    text = text.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON list in reply")
    return json.loads(text[start:end + 1])


def ask_ai(trends):
    key = os.environ["OPENROUTER_API_KEY"]
    last_error = None
    for model in MODELS:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": build_prompt(trends)}]},
                timeout=120,
            )
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code} {r.text[:300]}")
            text = r.json()["choices"][0]["message"]["content"]
            picks = parse_json_list(text)
            print("model used:", model)
            return picks
        except Exception as e:
            last_error = e
            print(f"model {model} failed: {e}")
    raise SystemExit(f"all models failed, last error: {last_error}")


def telegram(text):
    tok = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat:
        u = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=20).json()
        chat = str(u["result"][-1]["message"]["chat"]["id"])
        with open(os.path.expanduser("~/.viral.env"), "a") as f:
            f.write(f"\nTELEGRAM_CHAT_ID={chat}\n")
        os.environ["TELEGRAM_CHAT_ID"] = chat
    for i in range(0, len(text), 3900):
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data={"chat_id": chat, "text": text[i:i + 3900]},
            timeout=20,
        )


if __name__ == "__main__":
    trends = google_trends() + hacker_news()
    print(f"found {len(trends)} trends")
    if not trends:
        raise SystemExit("no trends found")
    picks = ask_ai(trends)
    for n, p in enumerate(picks, 1):
        msg = (
            f"VIDEO IDEA {n}\n\nTopic: {p['topic']}\nWhy: {p['why_viral']}\n\n"
            f"HOOK: {p['hook']}\n\nSCRIPT:\n{p['script']}\n\n"
            f"CAPTION: {p['caption']}\n{' '.join(p['hashtags'])}\n\n"
            "SCENES:\n- " + "\n- ".join(p["visuals"])
        )
        telegram(msg)
    print("sent to telegram")
