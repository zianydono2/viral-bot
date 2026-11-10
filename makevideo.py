import os, sys, re, time, random, subprocess, requests
import trendbot as tb  # uses the trendbot.py already on the machine

VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewNeural")
OUT = os.path.expanduser("~/viral/out")


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-600:]}")
    return r.stdout


# ---------- AI script ----------
def build_prompt(trends):
    return (
        "You run a viral short-video news channel for TikTok, YouTube Shorts, Instagram and Facebook.\n"
        "Host style: fast, funny, friendly-sarcastic, simple English.\n"
        "From the trends below, pick the 2 best topics for short videos (high interest, safe, easy to explain).\n"
        "Skip tragedies, deaths, violence, and unverified rumors. If unsure, say \"reportedly\".\n"
        "Topic areas we like: funny/viral internet moments, tech and AI news, sports moments.\n"
        "For each pick return JSON with these keys: topic, why_viral, hook (max 10 words), "
        "script (95-110 words, plain spoken sentences only, no emojis, no hashtags, ends with a question), "
        "caption, hashtags (list of 8), "
        "search_terms (list of 4 short generic stock-footage keywords, 1-2 words each, "
        "like 'stadium crowd' or 'robot hand'; no people's names, no brands).\n"
        "Return ONLY a JSON list of 2 objects. No other text, no markdown.\n\nTRENDS:\n" + "\n".join(trends)
    )


def ask_ai(trends):
    key = os.environ["OPENROUTER_API_KEY"]
    last_error = None
    for model in tb.MODELS:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": build_prompt(trends)}]},
                timeout=120,
            )
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code} {r.text[:300]}")
            picks = tb.parse_json_list(r.json()["choices"][0]["message"]["content"])
            print("model used:", model)
            return picks
        except Exception as e:
            last_error = e
            print(f"model {model} failed: {e}")
    raise SystemExit(f"all models failed, last error: {last_error}")


# ---------- footage ----------
def find_clip_url(term):
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": os.environ["PIXABAY_API_KEY"], "q": term, "per_page": 5, "safesearch": "true"},
            timeout=30,
        )
        hits = r.json().get("hits", [])
    except Exception as e:
        print("pixabay error:", type(e).__name__)
        return None
    random.shuffle(hits)
    for h in hits:
        v = h.get("videos", {})
        for size in ("large", "medium", "small"):
            u = v.get(size, {}).get("url")
            if u:
                return u
    return None


def download(url, path):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


# ---------- captions ----------
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,88,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,2,2,60,60,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(text, total, path):
    words = text.split()
    chunks = [" ".join(words[i:i + 3]) for i in range(0, len(words), 3)]
    weights = [len(c) + 2 for c in chunks]
    tot = sum(weights)
    t = 0.0
    lines = []
    for c, w in zip(chunks, weights):
        d = total * w / tot
        c = re.sub(r"[{}\\]", "", c).upper()
        lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Default,,0,0,0,,{c}")
        t += d
    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + "\n".join(lines) + "\n")


# ---------- build the video ----------
def clean(text):
    text = re.sub(r"[*#_`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def make_video(p, idx):
    work = os.path.join(OUT, f"{time.strftime('%Y%m%d_%H%M%S')}_{idx}")
    os.makedirs(work, exist_ok=True)

    hook = p["hook"].strip().rstrip(".!?") + "."
    narration = clean(f"{hook} {p['script']}")

    print("making voice...")
    run(["edge-tts", "--voice", VOICE, "--rate=+8%", "--text", narration, "--write-media", "voice.mp3"], cwd=work)
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", "voice.mp3"], cwd=work).strip())
    print(f"voice length: {dur:.1f}s")

    make_ass(narration, dur, os.path.join(work, "captions.ass"))

    print("finding footage...")
    terms = list(p.get("search_terms") or []) + ["city night", "technology", "crowd", "nature"]
    clips = []
    for term in terms:
        if len(clips) >= 4:
            break
        url = find_clip_url(term)
        if not url:
            continue
        raw = f"raw{len(clips)}.mp4"
        try:
            download(url, os.path.join(work, raw))
            clips.append(raw)
        except Exception as e:
            print("download failed:", type(e).__name__)
    if not clips:
        raise RuntimeError("no footage found")

    print(f"editing {len(clips)} clips...")
    per = dur / len(clips) + 0.05
    processed = []
    for i, raw in enumerate(clips):
        out = f"clip{i}.mp4"
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", raw, "-t", f"{per:.2f}",
             "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", out], cwd=work)
        processed.append(out)

    with open(os.path.join(work, "list.txt"), "w") as f:
        for c in processed:
            f.write(f"file '{c}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "joined.mp4"], cwd=work)

    run(["ffmpeg", "-y", "-i", "joined.mp4", "-i", "voice.mp3", "-vf", "ass=captions.ass",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", "final.mp4"], cwd=work)
    return os.path.join(work, "final.mp4")


# ---------- send to Telegram ----------
def send_video(path, p):
    tok = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = tb.get_chat_id()
    data = open(path, "rb").read()
    url = f"https://api.telegram.org/bot{tok}/sendVideo"
    for attempt in range(1, 6):
        try:
            r = requests.post(url, data={"chat_id": chat, "caption": p["topic"][:200]},
                              files={"video": ("video.mp4", data)}, timeout=300)
            if r.status_code == 200:
                return
            print(f"telegram sendVideo status {r.status_code}, attempt {attempt}")
        except Exception as e:
            print(f"telegram sendVideo error {type(e).__name__}, attempt {attempt}")
        time.sleep(3 * attempt)
    raise RuntimeError("could not send video to Telegram")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    trends = tb.google_trends() + tb.hacker_news()
    print(f"found {len(trends)} trends")
    if not trends:
        raise SystemExit("no trends found")
    picks = ask_ai(trends)
    for i, p in enumerate(picks[:count], 1):
        print(f"--- video {i}: {p['topic']}")
        try:
            path = make_video(p, i)
            send_video(path, p)
            tb.telegram(f"CAPTION: {p['caption']}\n{' '.join(p['hashtags'])}")
            print("video sent")
        except Exception as e:
            print("video failed:", e)
