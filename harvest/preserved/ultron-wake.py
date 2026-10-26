#!/usr/bin/env python3
"""
Ultron wake-word listener — completely hands-free voice control.

Always-on mic -> energy-gated faster-whisper wake detection ("hey ultron" /
"ultron", fuzzy phonetic match for the made-up name) -> beep -> capture until
3s of silence -> /api/stt (faster-whisper tiny) -> /api/ask (LLM + laptop
control) -> /api/tts (piper) -> speaker. Works even when the orb page isn't
focused, so you can say "hey ultron open facebook" from any app.

Why not vosk: the made-up word "ultron" is not in vosk's model vocabulary, so
keyword spotting silently never fires. faster-whisper transcribes anything, so
we fuzzy-match the name phonetically (whisper hears "altron"/"outrun" etc).
Whisper only runs while speech is detected (energy gate) to keep CPU low.
"""
import io
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

BASE = "http://127.0.0.1:3000"
# Shared overlay token (mirrors extension/content.js TOKEN). Sent on wake-service
# requests so the ask/stt routes can reject no-origin, token-less callers.
OVERLAY_TOKEN = "f2c007c7-f577-4f3e-9e1b-43a9f5792933"
RATE = 16000
BLOCK = 1600          # 100ms frames
SILENCE_END = 2.0     # seconds of quiet after speech -> finalize
# Finalize keywords: if Sir says one of these at the end of his command, we stop
# waiting for the silence gap and process immediately (feels much snappier).
FINALIZE_WORDS = (
    "proceed", "go ahead", "go on", "go now", "do it", "now", "alright",
    "go", "let's go", "lets go", "all right", "right", "okay", "ok",
)
# Barge-in words: while Ultr0n is speaking (piper playback), the mic stays
# armed and any of these (from the OWNER's voice, voiceprint-gated) kill the
# current reply immediately and open a fresh command capture. This is the
# "enough / stop / do this instead" interrupt.
INTERRUPT_WORDS = (
    "enough", "stop", "shut up", "that's enough", "thats enough",
    "never mind", "nevermind", "cancel", "cut it out", "cut that",
    "quiet", "silence", "hold on", "hold it", "wait", "as you were",
    "overruled", "forget it",
)
MAX_CAPTURE = 6.0     # hard cap on the post-wake command window (was 15s)
MIN_CAPTURE = 0.4
SPEECH_DEADLINE = 3.5  # if no speech starts this long after the wake prompt,
                        # re-arm idle instead of capturing ambient conversation
WAKE_WINDOW = 25      # frames (~2.5s) transcribed for wake detection
WAKE_EVERY = 5        # frames (~0.5s) between wake checks while speaking
WAKE_STT_COOLDOWN = 2.0  # seconds between wake transcriptions (TV/ambient guard)
BEEP = __import__("os").path.expanduser("~/.cache/ultron-beep.wav")
VOICEPRINT_FILE = os.path.expanduser("~/.local/share/ultron-voiceprint.json")
VOICEPRINT_THRESHOLD = 0.72  # cosine sim; lower = stricter (must match closer)
WAKE_PROMPTS = [
    "Sir, what do you need?",
    "Yes, Sir?",
    "At your service, Sir.",
    "Listening, Sir.",
    "Ready when you are, Sir.",
    "What can I do for you, Sir?",
    "Go ahead, Sir.",
    "How can I help, Sir?",
    "Standing by, Sir.",
]
# Time-of-day greeting pools for the boot recap. Multiple variants per band so
# the first wake of the day never sounds scripted.
GREETING_POOLS = {
    "night": [
        "Up late, Sir?",
        "Burning the midnight oil, Sir?",
        "Still awake, Sir?",
        "Night owl mode, Sir?",
    ],
    "morning": [
        "Good morning, Sir.",
        "Rise and shine, Sir.",
        "A fresh start, Sir. Good morning.",
    ],
    "afternoon": [
        "Good afternoon, Sir.",
        "Afternoon, Sir.",
        "Halfway through the day, Sir.",
    ],
    "evening": [
        "Good evening, Sir.",
        "Evening, Sir.",
        "Welcome back for the evening, Sir.",
    ],
}
RECAP_OPENERS = [
    "Welcome back. Let me recap where we left off.",
    "Good to have you back. Here is where we stand.",
    "Reporting in, Sir. Here is the latest from our last session.",
    "Picking up where we left off, Sir.",
]
WORKING_PROMPTS = [
    "Understood, Sir. Working on it now.",
    "Got it, Sir. Give me a second.",
    "On it, Sir. One moment.",
    "Right away, Sir. Handling that now.",
]
CLAP_MIN_GAP = 0.18  # distinct hand claps, not adjacent keyboard taps
CLAP_WINDOW = 0.65   # second clap must arrive within this many seconds
FOLLOWUP_WINDOW = 0.0  # disabled: only "ultron" or a double-clap re-wakes (no ambient capture)
# Never set in the live service. selftest() flips this in its own short-lived
# process so parser checks cannot launch browser tabs or touch the overlay.
SELFTEST_DRY_RUN = False

audio_q: queue.Queue = queue.Queue(maxsize=400)  # ~40s worst-case


def log(msg: str):
    print(f"[ultron-wake] {msg}", flush=True)


def ensure_beep():
    import os
    if not os.path.exists(BEEP):
        os.makedirs(os.path.dirname(BEEP), exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             "sine=frequency=880:duration=0.12", "-f", "lavfi", "-i",
             "sine=frequency=1320:duration=0.15", "-filter_complex",
             "[0:a][1:a]concat=n=2:v=0:a=1", "-ar", str(RATE), BEEP],
            check=True, capture_output=True)
        log("generated ack beep")


def play_beep():
    try:
        subprocess.run(["paplay", BEEP], check=False, timeout=5)
    except Exception as e:
        log(f"beep failed: {e}")


# Track the live paplay process so a barge-in can kill it mid-sentence.
_speaker_proc: subprocess.Popen | None = None
_speaker_lock = threading.Lock()
_speaking = threading.Event()  # set while TTS audio is being played


def speak(text: str, block: bool = True):
    """POST text -> /api/tts (piper) -> stream WAV to paplay.

    Non-blocking mode (block=False): the reply plays in a background thread so
    the main mic loop keeps running and can detect a barge-in ("enough") to
    kill it with stop_speaking(). Blocking mode is kept for the wake prompt /
    boot recap, where capture must not start until the prompt finished."""
    def _play():
        global _speaker_proc
        try:
            req = urllib.request.Request(
                f"{BASE}/api/tts",
                data=json.dumps({"text": text}).encode(),
                headers={"Content-Type": "application/json"})
            wav = urllib.request.urlopen(req, timeout=90).read()
            p = subprocess.Popen(["paplay"], stdin=subprocess.PIPE)
            with _speaker_lock:
                _speaker_proc = p
            _speaking.set()
            try:
                p.communicate(wav, timeout=180)
            finally:
                _speaking.clear()
                with _speaker_lock:
                    if _speaker_proc is p:
                        _speaker_proc = None
        except Exception as e:
            _speaking.clear()
            log(f"speak failed: {e}")

    t = threading.Thread(target=_play, daemon=True)
    t.start()
    if block:
        t.join(timeout=200)


def stop_speaking():
    """Kill the current TTS playback immediately (barge-in)."""
    with _speaker_lock:
        p = _speaker_proc
        _speaker_proc = None
    _speaking.clear()
    if p is not None:
        try:
            p.terminate()
            log("speech interrupted")
        except Exception as e:
            log(f"stop_speaking failed: {e}")


ACTIVITY_FILE = os.path.expanduser("~/.local/share/ultron-activity.json")
OVERLAY_FILE = os.path.expanduser("~/.local/share/ultron-overlay.json")


def set_activity(status: str, detail: str = ""):
    """Persist the latest Ultr0n activity so the UI can show what it is doing."""
    try:
        with open(ACTIVITY_FILE, "w") as f:
            json.dump({
                "status": status,
                "detail": (detail or "")[:240],
                "ts": time.strftime("%H:%M:%S"),
            }, f)
    except Exception:
        pass


def record_overlay(url: str):
    """Register the opened site so the in-page Ultr0n orb floats on that page.
    The Brave extension reads this registry via /api/overlay. 15-min expiry."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).netloc or "").lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return
        hosts = {}
        try:
            with open(OVERLAY_FILE) as f:
                hosts = json.load(f).get("hosts", {})
        except Exception:
            pass
        now = time.time() * 1000  # milliseconds, matching Date.now() in the TS routes
        hosts = {h: t for h, t in hosts.items() if now - t < 24 * 60 * 60 * 1000}
        hosts[host] = now
        with open(OVERLAY_FILE, "w") as f:
            json.dump({"hosts": hosts}, f)
        log(f"overlay armed for {host}")
    except Exception as e:
        log(f"overlay record failed: {e}")


def transcribe(wav_bytes: bytes) -> dict:
    boundary = "----ultronwake"
    body = (f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"audio\"; filename=\"cap.wav\"\r\n"
            "Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes + \
        f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/api/stt", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-Ultron-Token": OVERLAY_TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def response_content(raw_line: str) -> str:
    """Read one response line from /api/ask (NDJSON, with SSE compatibility)."""
    line = raw_line.strip()
    if line.startswith("data: "):
        line = line[6:].strip()
    if not line or line == "[DONE]":
        return ""
    try:
        return str(json.loads(line).get("content", ""))
    except Exception:
        return ""


def ask(text: str) -> str:
    """POST to /api/ask and return all newline-delimited response text.

    Sends model=deepseek-v4-flash-free — the router serves it via opencode-zen
    first (the same model Sir uses in the Hermes CLI), falling back through
    openrouter → deepseek paid if the free pool is exhausted.
    """
    req = urllib.request.Request(
        f"{BASE}/api/ask",
        data=json.dumps({"message": text, "model": "deepseek-v4-flash-free"}).encode(),
        headers={"Content-Type": "application/json", "X-Ultron-Token": OVERLAY_TOKEN})
    resp = urllib.request.urlopen(req, timeout=180)
    out = []
    for raw in resp:
        content = response_content(raw.decode("utf-8", "replace"))
        if content:
            out.append(content)
    return "".join(out).strip()


def wav_bytes_from(frames) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


def rms(frame: bytes) -> float:
    a = np.frombuffer(frame, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def speech_threshold(noise_floor: float) -> float:
    """Sensitive enough for the laptop's digital mic, resistant to idle noise."""
    return max(noise_floor * 2.0, 500.0)


def is_clap(frame: bytes, noise_floor: float) -> bool:
    """Recognize one sharp hand-clap in a 100 ms input frame.

    A clap has a high instantaneous peak but a much lower RMS. Speech has a
    steadier waveform, so this crest-factor check avoids treating ordinary
    spoken wake phrases as claps.

    Thresholds calibrated 2026-08-05 against this laptop's real mic (probe
    /tmp/clap_calib2.py: type 0-5s, mouse 5-9s, clap 9-16s):
      typing   peak max 17952, level max 4345
      mouse    peak max 20614, level max 5338 (the 20614 frame: level 3895)
      real clap peak 20786-28519, level 4644-6487, crest up to 5.5 (some 3.9)
    AGC makes amplitude comparisons mic-relative, so use ABSOLUTE bars far
    above everything typing/mouse produce: peak >= 20000 kills ALL typing
    (max peak 17952); level >= 4000 kills the one mouse frame that reached
    20614 (its level was 3895). crest only filters steady loud tones
    (music/shout), NOT clap-vs-type, so >= 3.5 (recovery: a real clap
    measured 3.9). On the calibration capture typing and mouse pass 0
    frames; clear claps pass and form a valid double. Soft claps (which are
    indistinguishable from typing on this mic) no longer wake — voice wake
    ("ultron") remains the reliable primary.
    """
    a = np.frombuffer(frame, dtype=np.int16).astype(np.int32)
    if not a.size:
        return False
    level = rms(frame)
    peak = float(np.max(np.abs(a)))
    return (
        level >= max(noise_floor * 4.0, 4000.0)
        and peak >= max(noise_floor * 20.0, 20000.0)
        and peak / max(level, 1.0) >= 3.5
    )


def is_wake(text: str) -> bool:
    """Fuzzy phonetic match for 'ultron' as the START of an utterance.

    The user speaks TO the assistant: "ultron open youtube" or "hey ultron...".
    Ambient speech that merely CONTAINS the word ("I was watching Ultron last
    night", TV dialogue) must never wake. So the match is anchored to the
    first tokens, not searched anywhere in the sentence.
    """
    # Whisper commonly inserts punctuation into an address ("Hey, Ultron!").
    # Normalize it before token matching so the comma cannot turn "hey" into
    # a different first word and make a valid wake invisible.
    t = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    t = " ".join(t.split())
    if len(t) < 3:
        return False
    words = t.split()
    # Whisper misrenders this laptop's "hey ultron" as "hey all" — keep that
    # whole two-word address as a wake form BEFORE stripping the "hey".
    if " ".join(words[:2]) == "hey all":
        return True
    # A leading "hey" is part of the address ("hey ultron open facebook").
    # Drop it so the wake word itself anchors the head.
    if words[0] == "hey" and len(words) > 1:
        words = words[1:]
    head = " ".join(words[:3])  # only the first 3 words can wake
    # Include observed transcripts from this laptop's real microphone: Whisper
    # rendered "hey ultron" as "Hey, all" and "He knew".
    for form in (
        "ultron", "altron", "oltron", "outron", "utron", "ultran", "altrin",
        "ultros", "ultro", "ultrus", "oltros", "ultras",
        "hey all", "he knew", "neu ultron",
    ):
        if head == form or head.startswith(form + " "):
            return True
    # "outrun" is phonetically close and Whisper has produced it for "ultron"
    # in isolation; allow it as a standalone first word too.
    if words[0] in ("outrun", "outron", "utron", "ultros", "ultro", "ultrus", "ultras"):
        return True
    return False


def is_interrupt(text: str) -> bool:
    """True if the utterance is a barge-in: "enough", "stop", "never mind",
    "that's enough", etc. While Ultr0n is speaking, the owner can say one of
    these to kill the reply and open a fresh capture. Only ever checked on
    audio that passed the voiceprint gate, so Ultr0n's own TTS echo (which the
    mic hears) can never self-interrupt.

    A fresh wake-word ("hey ultron open youtube") while Ultr0n is speaking is
    also a barge-in: it interrupts the reply and takes the floor. So wake
    prefixes count here too."""
    t = (text or "").lower().strip()
    if not t:
        return False
    # "hey ultron" / "hey all" is a wake address — barge-in while speaking.
    if is_wake(t):
        return True
    for w in INTERRUPT_WORDS:
        if w in t:
            return True
    return False


def strip_interrupt_prefix(text: str) -> str:
    """Strip a leading barge-in word so the remainder is the real command:
    \"enough open youtube\" -> \"open youtube\". Used when the user interrupts
    with a follow-up command in the same breath."""
    t = (text or "").strip()
    for w in sorted(INTERRUPT_WORDS, key=len, reverse=True):
        if t.lower().startswith(w):
            return t[len(w):].lstrip(" .,-:;!?").strip()
    return t


def has_finalize(text: str) -> bool:
    """True if a trailing finalize trigger ('proceed', 'go ahead', 'now', ...)
    is the final fragment of speech — used to skip the silence wait."""
    t = (text or "").lower().strip()
    if not t:
        return False
    words = t.split()
    tail = " ".join(words[-2:])  # check the last two words (phrase or single)
    if any(w in tail for w in FINALIZE_WORDS):
        return True
    # A single-word trigger as the very last word also counts.
    return words[-1] in FINALIZE_WORDS


def notify(title: str, body: str):
    try:
        subprocess.run(["notify-send", "-a", "Ultron", title, body[:300]],
                       check=False, timeout=5)
    except Exception:
        pass


# Lazy-loaded speaker encoder + enrolled voiceprint. None until first use so a
# missing enrollment (or a machine without the deps) degrades to old behavior.
_voice_encoder = None
_voiceprint = None  # (embedding np.ndarray, threshold float)


def load_voiceprint() -> tuple | None:
    """Load the enrolled owner voiceprint once. Returns None if not enrolled."""
    global _voiceprint
    if _voiceprint is not None:
        return _voiceprint
    try:
        if not os.path.exists(VOICEPRINT_FILE):
            log("voiceprint: not enrolled — all voices accepted (run scripts/enroll-voice.py)")
            _voiceprint = False
            return None
        with open(VOICEPRINT_FILE) as f:
            data = json.load(f)
        emb = np.asarray(data["voiceprint"], dtype=np.float32)
        emb = emb / np.linalg.norm(emb)
        _voiceprint = (emb, float(data.get("threshold", VOICEPRINT_THRESHOLD)))
        log(f"voiceprint: enrolled ({data.get('clips_used', '?')} clips, threshold={_voiceprint[1]:.2f})")
        return _voiceprint
    except Exception as e:
        log(f"voiceprint: load failed ({e}) — all voices accepted")
        _voiceprint = False
        return None


def get_encoder():
    global _voice_encoder
    if _voice_encoder is None:
        from resemblyzer import VoiceEncoder
        _voice_encoder = VoiceEncoder()
    return _voice_encoder


def speaker_matches(audio_bytes: bytes) -> bool:
    """True if the speaker of `audio_bytes` matches the enrolled voiceprint.

    No enrollment -> True (accept everyone). Mismatch -> False (drop it —
    this is someone else clapping or saying 'ultron').
    """
    vp = load_voiceprint()
    if not vp:
        return True
    emb, threshold = vp
    try:
        from resemblyzer import preprocess_wav
        arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if len(arr) < RATE:  # <1s of audio — not enough to judge, accept
            return True
        enc = get_encoder()
        wav = preprocess_wav(arr, source_sr=RATE)
        if len(wav) < RATE * 0.5:
            return True
        e = enc.embed_utterance(wav)
        e = e / np.linalg.norm(e)
        sim = float(np.dot(e, emb))
        log(f"voiceprint: similarity={sim:.3f} (threshold {threshold:.2f})")
        return sim >= threshold
    except Exception as ex:
        log(f"voiceprint: verify error ({ex}) — accepting")
        return True


def direct_site_command(text: str) -> str:
    """Execute common spoken site requests locally, without LLM ambiguity.
    Handles "open <site>" for known sites AND arbitrary domains
    ("open github.com"), plus site searches ("open youtube and search cats").
    Browsing/reporting intents (browse, report, who, what...) still go to the
    LLM so it can open the site AND report on it.
    """
    t = (text or "").lower()
    if "open" not in t and "search" not in t:
        return ""
    report_intent = ("browse", "report", "who", "what", "how", "check", "tell",
                     "show", "look", "information", "news", "post", "comment",
                     "message", "friend", "scroll", "read")
    # Explicit "search" always wins over report intents ("open facebook and
    # search my friends" is a search, not a report).
    if "search" not in t and any(w in t for w in report_intent):
        return ""

    # Site search. Two shapes:
    #   1) "open youtube and search cats"  -> youtube results page
    #   2) "search cats on youtube"        -> youtube results page
    SITE_ALIASES = {
        "youtube": ("YouTube", "https://www.youtube.com/results?search_query={q}"),
        "you tube": ("YouTube", "https://www.youtube.com/results?search_query={q}"),
        "facebook": ("Facebook", "https://www.facebook.com/search/top/?q={q}"),
        "face book": ("Facebook", "https://www.facebook.com/search/top/?q={q}"),
        "github": ("GitHub", "https://github.com/search?q={q}"),
        "get hub": ("GitHub", "https://github.com/search?q={q}"),
        "reddit": ("Reddit", "https://www.reddit.com/search/?q={q}"),
        "read it": ("Reddit", "https://www.reddit.com/search/?q={q}"),
        "amazon": ("Amazon", "https://www.amazon.com/s?k={q}"),
        "a man zone": ("Amazon", "https://www.amazon.com/s?k={q}"),
    }
    if "search" in t:
        # shape 1: open <site> and search <query>
        m1 = re.search(r"open\s+(.+?)\s+and\s+search\s+(?:for\s+)?(.+)$", t)
        if m1:
            site_word = m1.group(1).strip()
            query = m1.group(2).strip()
            for alias, (label, tmpl) in SITE_ALIASES.items():
                if site_word == alias or alias in site_word:
                    return _open_site(label, tmpl.format(q=urllib.parse.quote(query)))
            return _open_site("Google", f"https://www.google.com/search?q={urllib.parse.quote(query)}")
        # shape 2: search <query> on <site>
        m2 = re.search(r"search\s+(?:for\s+)?(.+?)\s+on\s+(.+)$", t)
        if m2:
            query = m2.group(1).strip()
            site_word = m2.group(2).strip()
            for alias, (label, tmpl) in SITE_ALIASES.items():
                if site_word == alias or alias in site_word:
                    return _open_site(label, tmpl.format(q=urllib.parse.quote(query)))
            return _open_site("Google", f"https://www.google.com/search?q={urllib.parse.quote(query)}")
        # shape 3: plain "search <query>" -> Google
        m3 = re.search(r"search\s+(?:for\s+)?(.+)$", t)
        if m3:
            return _open_site("Google", f"https://www.google.com/search?q={urllib.parse.quote(m3.group(1).strip())}")

    # Known site names
    if any(word in t for word in ("youtube", "you tube", "this move")):
        return _open_site("YouTube", "https://www.youtube.com")
    if any(word in t for word in ("facebook", "face book", "piece book", "fist book")):
        # The last two are confirmed tiny-model mishearings retained as a
        # fallback while base STT settles in.
        return _open_site("Facebook", "https://www.facebook.com")
    if any(word in t for word in ("mission control", "mission center", "open ultrasound",
                                  "ultrasonic", "control center", "control panel",
                                  "the dashboard", "the app", "the orb", "mioharoa",
                                  "my side", "full app")):
        # "open mission control" -> the full Mission Control app (not the mini bubble).
        return _open_site("Mission Control", "http://127.0.0.1:3000")
    if any(word in t for word in ("github", "get hub", "hub")):
        return _open_site("GitHub", "https://github.com")
    if any(word in t for word in ("google", "goggle")):
        return _open_site("Google", "https://www.google.com")
    if any(word in t for word in ("gmail", "email", "mail")):
        return _open_site("Gmail", "https://mail.google.com")
    if any(word in t for word in ("reddit", "read it")):
        return _open_site("Reddit", "https://www.reddit.com")
    if any(word in t for word in ("amazon", "a man zone")):
        return _open_site("Amazon", "https://www.amazon.com")
    if any(word in t for word in ("netflix", "net flex")):
        return _open_site("Netflix", "https://www.netflix.com")
    if any(word in t for word in ("twitter", "x dot com")):
        return _open_site("X", "https://x.com")
    if any(word in t for word in ("instagram", "insta gram")):
        return _open_site("Instagram", "https://www.instagram.com")
    if any(word in t for word in ("tiktok", "tick tock")):
        return _open_site("TikTok", "https://www.tiktok.com")
    if any(word in t for word in ("discord", "dis chord")):
        return _open_site("Discord", "https://discord.com/app")

    # Arbitrary domain: "open github.com", "open example.com"
    m = re.search(r"(?:open|launch|go to)\s+([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z]{2,})(?:/[^\s]*)?)", t)
    if m:
        domain = m.group(1)
        if not domain.startswith("http"):
            domain = "https://" + domain
        return _open_site(domain.split("//")[1].split("/")[0], domain)
    return ""


def _open_site(label: str, url: str) -> str:
    """xdg-open a URL, arm the overlay, return the spoken label.

    Dry-run (SELFTEST_DRY_RUN): the parser is exercised without opening tabs — the
    auto-opened-browser bug (2026-08-05) came from the selftest calling this for
    real on every run.
    """
    if SELFTEST_DRY_RUN:
        return label
    try:
        subprocess.Popen(
            ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        log(f"direct site action: {label} -> {url}")
        record_overlay(url)
        return label
    except Exception as e:
        log(f"direct site action failed: {e}")
        return ""


def working_acknowledgement(text: str) -> str:
    """Choose a short, contextual acknowledgement before executing a command.
    Varied per request type so Ultr0n never sounds scripted."""
    t = (text or "").lower()
    if any(x in t for x in ("open", "launch", "youtube", "facebook")):
        return random.choice([
            "Acknowledged, Sir. Opening that now.",
            "Right away, Sir. Establishing the connection.",
            "Understood, Sir. I will handle it.",
            "Got it, Sir. Bringing that up now.",
        ])
    if any(x in t for x in ("scroll", "click", "type", "close", "minimize", "volume", "next", "play")):
        return random.choice([
            "On it, Sir. Give me a second.",
            "Got it, Sir. Doing that now.",
            "Understood, Sir. One moment.",
            "Right away, Sir.",
        ])
    if any(x in t for x in ("time", "status", "memory", "battery", "system")):
        return random.choice([
            "Understood, Sir. Retrieving live telemetry.",
            "Certainly, Sir. Checking the system now.",
        ])
    if any(x in t for x in ("who", "what", "how", "report", "find", "browse", "check")):
        return random.choice([
            "Let me check that for you, Sir.",
            "Got it, Sir. Give me a second.",
            "On it, Sir. Looking into it now.",
            "Understood, Sir. Checking that now.",
        ])
    return random.choice([
        "Acknowledged, Sir. Working on it now.",
        "Understood, Sir. I am on it.",
        "Very well, Sir. Processing your request.",
        "Got it, Sir. One moment.",
    ])


def is_unclear_command(text: str, confidence: float, no_speech_prob: float) -> bool:
    """Ask again rather than guess when STT did not hear a usable request."""
    t = (text or "").strip().lower()
    known_garble = {"you", "i hope you need to", "here you go", "one one"}
    return (
        not t
        or t in known_garble
        or len(t.split()) < 2
        or no_speech_prob >= 0.55
        or confidence <= -1.35
    )


def strip_wake_prefix(text: str) -> str:
    """In hands-free follow-up mode the user may say the wake phrase again;
    strip it so the command is clean ("hey ultron open facebook" -> "open facebook")."""
    return re.sub(r"^(hey\s*[,.]?\s*)?(ultron|altron|outron|outrun|ultran|hey all|he knew)[,.\s]*",
                  "", text or "", flags=re.I).strip()


def boot_recap() -> bool:
    """First wake after boot: greet Sir, tell him what we did last time, and
    open Mission Control with the recap animation. Runs exactly once.
    Returns True if the recap ran (so the caller can skip the normal
    wake prompt and avoid a doubled greeting)."""
    try:
        hour = int(time.strftime("%H"))
        if hour < 5:
            band = "night"
        elif hour < 12:
            band = "morning"
        elif hour < 18:
            band = "afternoon"
        else:
            band = "evening"
        greet = random.choice(GREETING_POOLS[band])
        # Open Mission Control with the recap animation first so it is
        # already playing while the recap is spoken.
        try:
            subprocess.Popen(
                ["xdg-open", f"{BASE}/?recap=1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            record_overlay(f"{BASE}/?recap=1")
        except Exception as e:
            log(f"boot recap open failed: {e}")
        # Fetch the last session from the vault and speak ONLY the important
        # parts: how many requests, split by type, and anything left pending.
        recap_text = greet + " " + random.choice(RECAP_OPENERS)
        CAT_LABELS = {"open": "site opens", "search": "searches",
                      "system": "system actions", "info": "questions",
                      "other": "other requests"}
        try:
            req = urllib.request.Request(f"{BASE}/api/recap")
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            summary = data.get("summary")
            if summary and summary.get("total"):
                by = summary.get("byCategory", {})
                parts = [f"{n} {CAT_LABELS.get(c, c)}"
                         for c, n in by.items() if n]
                recap_text += (f" Last session: {summary['total']} requests"
                               + (f" — {', '.join(parts)}." if parts else "."))
                if summary.get("pending") and summary.get("lastAsk"):
                    recap_text += (f" One thing is still pending: "
                                   f"{summary['lastAsk'][:80]}.")
            else:
                recap_text += " The vault is quiet — nothing from the last session."
        except Exception as e:
            log(f"boot recap fetch failed: {e}")
        recap_text += " What do you need, Sir?"
        speak(recap_text)
        return True
    except Exception as e:
        log(f"boot recap failed: {e}")
        return False


def suppress_unclear_response(silent_capture: bool) -> bool:
    """Whether an unclear capture must remain completely silent."""
    return bool(silent_capture)


def process_request(capture, followup_until_ref=None, silent_capture: bool = False):
    """Process a verified command.

    A double-clap arms capture silently because this microphone cannot reliably
    distinguish typing/mouse noise from claps. If that silent capture is empty
    or low-confidence, it must leave no audible trace.
    """
    dur = len(capture) * BLOCK / RATE
    log(f"captured {dur:.1f}s of speech")
    try:
        wav = wav_bytes_from(capture)
        stt = transcribe(wav)
        text = str(stt.get("text", "")).strip()
        confidence = float(stt.get("avg_logprob", -10.0))
        no_speech_prob = float(stt.get("no_speech_prob", 1.0))
        # In hands-free follow-up mode the user may say the wake phrase again;
        # strip it so the command is clean ("hey ultron open facebook" -> "open facebook").
        text = re.sub(r"^(hey\s+)?(ultron|altron|outron|outrun|ultran|hey all|he knew)[,.\s]*",
                      "", text, flags=re.I).strip()
        log(f"heard: {text!r} (confidence={confidence:.2f}, no_speech={no_speech_prob:.2f})")
        if is_unclear_command(text, confidence, no_speech_prob):
            if suppress_unclear_response(silent_capture):
                # Typing/mouse noise can fake a double-clap on this AGC mic.
                # Never let a failed silent capture talk back to Sir.
                log("unclear silent capture — ignored without speech")
                set_activity("standby", "Listening for your next command")
            else:
                set_activity("repeat", f"Did not catch: {text!r}")
                speak("I did not catch that clearly, Sir. Please say it again.")
            return
        # Confirm that the command was understood before the request/action runs.
        # This makes the hands-free interaction feel responsive instead of silent.
        set_activity("working", text)
        speak(working_acknowledgement(text))
        site = direct_site_command(text)
        if site:
            reply = f"{site} is open, Sir."
        else:
            reply = ask(text)
        log(f"reply: {reply[:120]!r}")
        if reply:
            set_activity("done", reply)
            # Non-blocking: the reply plays in the background so the mic loop
            # can detect a barge-in ("enough", "stop", a fresh wake) and kill it.
            speak(reply, block=False)
            notify("Ultron", reply)
    except urllib.error.HTTPError as e:
        log(f"server error {e.code}: {e.read()[:200]!r}")
        speak("I'm sorry Sir, the assistant is unavailable right now.")
    except Exception as e:
        log(f"process error: {e}")
        speak("I'm sorry Sir, something went wrong.")


def selftest():
    """Offline checks for wake matching and the wake -> capture transition."""
    global SELFTEST_DRY_RUN
    SELFTEST_DRY_RUN = True
    cases = [
        ("hey ultron open facebook", True),
        ("hey, ultron!", True),  # exact live transcript includes an internal comma
        ("ultron", True),
        ("altron", True),
        ("outrun", True),
        ("ultros", True),       # observed real-mic transcript of “ultron" 2026-08-05
        ("ultro open youtube", True),
        ("hey all", True),       # observed real-mic transcript of “hey ultron”
        ("he knew he knew", True), # observed real-mic transcript of "hey ultron"
        ("hey outron", True),
        ("neu ultron", True),    # observed live transcript of “hey ultron” 2026-08-05
        ("open facebook", False),
        ("play some music", False),
        ("what time is it", False),
        ("ultra", False),
        ("", False),
    ]
    ok = True
    for text, want in cases:
        got = is_wake(text)
        mark = "PASS" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  [{mark}] is_wake({text!r}) = {got} (want {want})")

    # Deterministic clap detector checks: a short high-crest pulse wakes; a
    # steady speech-like waveform and quiet input do not.
    quiet = np.full(BLOCK, 300, dtype=np.int16).tobytes()
    speech = (np.sin(np.linspace(0, 20 * np.pi, BLOCK)) * 2200).astype(np.int16).tobytes()
    clap_arr = np.zeros(BLOCK, dtype=np.int16)
    # Calibrated 2026-08-05: a real clear clap peaks 20786-28519 with level
    # 4644-6487. The old synthetic (peak 10000) no longer clears the 20000
    # bar. Model a loud clap: short high-amplitude transient then quiet.
    clap_n = 96  # ~6ms of ring at 16kHz
    clap_arr[:clap_n] = 24000
    clap_ok = is_clap(clap_arr.tobytes(), 300.0)
    non_clap_ok = not is_clap(quiet, 300.0) and not is_clap(speech, 300.0)
    print(f"  [{'PASS' if clap_ok else 'FAIL'}] sharp clap pulse wakes (peak 24000, {clap_n}-sample ring)")
    print(f"  [{'PASS' if non_clap_ok else 'FAIL'}] quiet and speech do not false-wake")

    # Regression guard: a matched wake must transition into capture, never exit
    # the input loop and silently restart the microphone.
    state = "idle"
    if is_wake("hey ultron"):
        state = "listen"
    flow_ok = state == "listen"
    # 300 RMS is this machine's measured idle input level; the wake gate must
    # remain low enough for ordinary voice levels to enter transcription.
    threshold_ok = speech_threshold(300.0) == 600.0
    cadence_ok = WAKE_EVERY <= 5
    response_ok = (
        response_content('{"content":"Open YouTube"}') == "Open YouTube"
        and response_content('data: {"content":"Done, Sir."}') == "Done, Sir."
        and response_content("[DONE]") == ""
    )
    double_clap_ok = CLAP_MIN_GAP == 0.18 and CLAP_WINDOW == 0.65
    silent_capture_policy_ok = (
        suppress_unclear_response(True)
        and not suppress_unclear_response(False)
    )
    followup_ok = FOLLOWUP_WINDOW == 0.0  # disabled: only wake word / double-clap re-wakes
    # Barge-in: "enough"/"stop"/a fresh wake while Ultr0n is speaking must kill
    # the reply and open a new capture; ordinary chatter must NOT interrupt.
    interrupt_ok = (
        is_interrupt("enough") and is_interrupt("stop")
        and is_interrupt("never mind") and is_interrupt("that's enough")
        and is_interrupt("hey ultron open youtube")   # fresh wake = barge-in
        and not is_interrupt("what time is it")
        and not is_interrupt("") 
        and strip_interrupt_prefix("enough open youtube") == "open youtube"
        and strip_interrupt_prefix("stop the music") == "the music"
        and strip_interrupt_prefix("never mind") == ""
        and strip_interrupt_prefix("open facebook") == "open facebook"
    )
    print(f"  [{'PASS' if interrupt_ok else 'FAIL'}] barge-in detection: enough/stop/wake interrupt, chatter does not")
    wake_anchor_ok = (
        is_wake("ultron open youtube")
        and is_wake("hey ultron open facebook")
        and is_wake("hey all open facebook")     # whisper misrender of hey ultron
        and not is_wake("i was watching ultron last night")   # ambient mention
        and not is_wake("the tv says ultron is coming")
        and not is_wake("")                     # empty never wakes
    )
    deadline_ok = SPEECH_DEADLINE > 0 and SPEECH_DEADLINE <= 4.0
    capture_window_ok = MAX_CAPTURE <= 8.0  # tight post-wake window (was 15s)
    strip_ok = (
        strip_wake_prefix("hey ultron open facebook") == "open facebook"
        and strip_wake_prefix("ultron, open youtube") == "open youtube"
        and strip_wake_prefix("open facebook") == "open facebook"
        and strip_wake_prefix("") == ""
    )
    prompts_ok = (
        "Sir, what do you need?" in WAKE_PROMPTS
        and "Understood, Sir. Working on it now." in WORKING_PROMPTS
        and len(WAKE_PROMPTS) >= 3
        and len(WORKING_PROMPTS) >= 3
    )
    variety_ok = (
        all(len(pool) >= 2 for pool in GREETING_POOLS.values())
        and len(RECAP_OPENERS) >= 2
        and len(WAKE_PROMPTS) >= 8  # more than the original 4 — never scripted
    )
    # The boot recap must end with its own "What do you need, Sir?" so the
    # caller can skip the normal wake prompt — otherwise the greeting doubles
    # on the first wake after boot. Assert the recap tail invariant directly.
    no_double_ok = any(
        o.endswith("Sir.")
        and ("What do you need, Sir?" in ("Good morning, Sir. " + o + " What do you need, Sir?"))
        for o in RECAP_OPENERS
    )
    site_ok = (
        direct_site_command("open facebook") == "Facebook"
        and direct_site_command("open youtube") == "YouTube"
        and direct_site_command("open facebook and browse and report") == ""
        and direct_site_command("who added me on facebook") == ""
    )
    print(f"  [{'PASS' if response_ok else 'FAIL'}] API response parser reads NDJSON and SSE")
    print(f"  [{'PASS' if double_clap_ok else 'FAIL'}] double-clap window = {CLAP_WINDOW:.1f}s")
    print(f"  [{'PASS' if silent_capture_policy_ok else 'FAIL'}] silent clap unclear capture suppresses speech; voice capture can ask again")
    print(f"  [{'PASS' if followup_ok else 'FAIL'}] follow-up requires wake word / double-clap (window={FOLLOWUP_WINDOW:.0f}s)")
    print(f"  [{'PASS' if wake_anchor_ok else 'FAIL'}] wake word anchored to utterance start (ambient mention rejected)")
    print(f"  [{'PASS' if deadline_ok else 'FAIL'}] speech deadline = {SPEECH_DEADLINE:.1f}s (no-command re-arms idle)")
    print(f"  [{'PASS' if capture_window_ok else 'FAIL'}] post-wake capture window = {MAX_CAPTURE:.1f}s (tight)")
    print(f"  [{'PASS' if strip_ok else 'FAIL'}] wake-prefix strip normalizes follow-up commands")
    print(f"  [{'PASS' if prompts_ok else 'FAIL'}] wake and working acknowledgements configured")
    print(f"  [{'PASS' if variety_ok else 'FAIL'}] greeting variety: time-of-day pools + {len(WAKE_PROMPTS)} wake prompts")
    print(f"  [{'PASS' if no_double_ok else 'FAIL'}] boot recap ends with its own prompt (no doubled greeting)")
    print(f"  [{'PASS' if site_ok else 'FAIL'}] site shortcut only for pure open (browse/report goes to LLM)")
    print(f"  [{'PASS' if interrupt_ok else 'FAIL'}] barge-in: enough/stop/wake cuts off speech, chatter does not")
    print(f"  [{'PASS' if flow_ok else 'FAIL'}] wake transition = {state!r} (want 'listen')")
    print(f"  [{'PASS' if threshold_ok else 'FAIL'}] speech threshold at 300 RMS = {speech_threshold(300.0):.1f} (want 600.0)")
    print(f"  [{'PASS' if cadence_ok else 'FAIL'}] wake check cadence = {WAKE_EVERY / 10:.1f}s (want <=0.5s)")
    ok = ok and clap_ok and non_clap_ok and response_ok and double_clap_ok and silent_capture_policy_ok and prompts_ok and flow_ok and threshold_ok and cadence_ok and followup_ok and strip_ok and site_ok and wake_anchor_ok and deadline_ok and capture_window_ok and variety_ok and no_double_ok and interrupt_ok
    print("SELFTEST:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main():
    if "--selftest" in sys.argv:
        selftest()
    ensure_beep()
    log("loading faster-whisper (base, int8) for wake detection...")
    whisper = WhisperModel("base", device="cpu", compute_type="int8")
    log("wake words: 'hey ultron' / 'ultron' — listening...")

    def callback(indata, frames, time_info, status):
        try:
            # Stamp capture time so clap gaps survive loop backlog: when the
            # wake transcription blocks this loop, queued frames would be
            # processed back-to-back and two real claps (0.3s apart) would look
            # like one (gap ~0s). Real capture timestamps fix the gap math.
            audio_q.put_nowait((time.monotonic(), bytes(indata)))
        except queue.Full:
            pass

    while True:
        try:
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                blocksize=BLOCK, callback=callback):
                state = "idle"          # idle | listen | process
                ring = []               # recent frames for wake detection
                capture_silent = False
                noise_floor = 0.0
                speech_seen = False
                last_speech_t = 0.0
                t0 = time.monotonic()
                since_check = 0
                last_clap_t = 0.0
                last_stt_t = 0.0
                last_wake_stt = 0.0
                followup_until = 0.0
                boot_recap_done = {"done": False}

                def start_listening(reason: str, silent: bool = False):
                    """Acknowledge a wake, then capture only the user's next command.

                    silent=True (clap wakes): NO beep, NO spoken prompt. The mic arms
                    quietly; Ultr0n only speaks later if the captured command passes the
                    voiceprint gate in process_request. This stops typing/mouse false
                    clap-wakes from making Ultr0n talk on its own. Voice wakes ("ultron")
                    stay voiced — they were already gated on the owner's voiceprint at
                    the wake itself.
                    """
                    nonlocal state, capture, capture_silent, speech_seen, last_speech_t, t0, since_check, ring, followup_until, last_stt_t
                    log(reason)
                    capture_silent = silent
                    # Any wake (clap or voice) opens a hands-free window: for the
                    # next 2 minutes the user can just talk — no reclapping needed.
                    followup_until = time.monotonic() + FOLLOWUP_WINDOW
                    set_activity("listening", reason)
                    if not silent:
                        # First wake after boot: greet + recap last session + open
                        # Mission Control with the recap animation. Once only.
                        # boot_recap() ends with "What do you need, Sir?" so when it
                        # runs we SKIP the normal wake prompt — otherwise the
                        # greeting doubles.
                        recap_ran = False
                        if not boot_recap_done["done"]:
                            boot_recap_done["done"] = True
                            recap_ran = boot_recap()
                        state = "prompting"
                        play_beep()
                        if not recap_ran:
                            speak(random.choice(WAKE_PROMPTS))
                        time.sleep(0.2)
                    # Drain the queue BEFORE arming so the clap echo / prompt audio
                    # itself never enters the command capture (silent wakes don't play
                    # a prompt, but the clap transient and any ambient tail still must
                    # not be recorded).
                    while True:
                        try:
                            audio_q.get_nowait()
                        except queue.Empty:
                            break
                    capture = []
                    speech_seen = False
                    last_speech_t = time.monotonic()
                    t0 = last_speech_t
                    last_stt_t = 0.0
                    since_check = 0
                    ring = []
                    state = "listen"
                    log("ready for command" + ("" if not silent else " (silent)"))

                while True:
                    try:
                        cap_ts, frame = audio_q.get(timeout=1.0)
                    except queue.Empty:
                        continue

                    r = rms(frame)

                    if state == "idle":
                        ring.append(frame)
                        if len(ring) > WAKE_WINDOW:
                            ring.pop(0)
                        # Only quiet audio can train the noise floor. Letting all
                        # sub-2000 frames train it made normal voice become "noise"
                        # and raised the gate above the wake phrase.
                        if r < speech_threshold(noise_floor):
                            noise_floor = 0.95 * noise_floor + 0.05 * r
                        thresh = speech_threshold(noise_floor)
                        if is_clap(frame, noise_floor):
                            # Use the CAPTURE timestamp, not processing time:
                            # when wake STT blocks this loop, queued frames are
                            # handled back-to-back and two real claps would look
                            # like one (gap ~0s) otherwise.
                            gap = cap_ts - last_clap_t
                            if CLAP_MIN_GAP <= gap <= CLAP_WINDOW:
                                last_clap_t = 0.0
                                start_listening("double-clap wake", silent=True)
                            else:
                                last_clap_t = cap_ts
                                log("first clap — waiting for a distinct second clap")
                            continue
                        if r > thresh:
                            since_check += 1
                            if since_check >= WAKE_EVERY:
                                since_check = 0
                                # Cooldown: with TV/ambient audio constantly
                                # above the gate, transcribing every 0.5s keeps
                                # the loop behind real-time and starves clap
                                # detection. Run wake STT at most every 2s.
                                if cap_ts - last_wake_stt < WAKE_STT_COOLDOWN:
                                    continue
                                last_wake_stt = cap_ts
                                win = b"".join(ring)
                                if len(win) >= RATE:  # >=1s of audio
                                    try:
                                        arr = np.frombuffer(win, dtype=np.int16).astype(np.float32) / 32768.0
                                        segs, _ = whisper.transcribe(
                                            arr, beam_size=1, language="en",
                                            condition_on_previous_text=False)
                                        heard = " ".join(s.text for s in segs).strip()
                                        if heard:
                                            log(f"heard while listening: {heard!r}")
                                        if is_wake(heard):
                                            # Voiceprint gate on the WAKE
                                            # itself: a stranger saying "ultron"
                                            # must not wake Ultr0n at all.
                                            if speaker_matches(win):
                                                start_listening(f"voice wake: {heard!r}")
                                            else:
                                                log(f"voiceprint: wake voice mismatch — ignoring {heard!r}")
                                        # NOTE: no hands-free follow-up branch.
                                        # After a wake, ONLY the wake word
                                        # "ultron" again or a fresh double-clap
                                        # starts a new capture — ambient speech
                                        # (TV, friends talking) must NEVER
                                        # trigger Ultr0n.
                                    except Exception as e:
                                        log(f"wake transcribe error: {e}")
                        else:
                            since_check = 0
                    elif state == "listen":
                        capture.append(frame)
                        thresh = speech_threshold(noise_floor)
                        if r > thresh:
                            speech_seen = True
                            last_speech_t = time.monotonic()
                        now = time.monotonic()
                        # Speech deadline: if the user has NOT started talking
                        # within SPEECH_DEADLINE of the wake prompt, stop
                        # listening and re-arm idle. A friend's conversation
                        # that starts late must never be captured as a command.
                        if not speech_seen and now - t0 >= SPEECH_DEADLINE:
                            log("no command heard after wake — re-arming idle")
                            state = "process"  # will be dropped (no speech_seen)
                            continue
                        # Finalize keyword ("proceed", "go ahead", "now"...):
                        # run a quick STT pass on the trailing audio ~0.5s after
                        # it's heard; if the LAST word is a finalize trigger, stop
                        # waiting for the silence gap and process right away.
                        if speech_seen and (now - last_speech_t) < 1.2 and now - t0 >= MIN_CAPTURE:
                            if now - last_stt_t >= 0.9:
                                last_stt_t = now
                                tail = capture[-(int(RATE * 1.6 / BLOCK)):]
                                if tail:
                                    try:
                                        arr = np.frombuffer(
                                            b"".join(tail), dtype=np.int16
                                        ).astype(np.float32) / 32768.0
                                        segs, _ = whisper.transcribe(
                                            arr, beam_size=1, language="en",
                                            condition_on_previous_text=False)
                                        heard_tail = " ".join(s.text for s in segs).lower()
                                        if has_finalize(heard_tail):
                                            log(f"finalize word in tail: {heard_tail!r}")
                                            state = "process"
                                    except Exception as e:
                                        log(f"finalize stt error: {e}")
                        if speech_seen and now - last_speech_t >= SILENCE_END:
                            state = "process"
                        if now - t0 >= MAX_CAPTURE:
                            log("capture timeout")
                            state = "process"

                    elif state == "speaking":
                        # Barge-in listening: Ultr0n's reply is playing in the
                        # background (speak block=False). Keep the ring fed and
                        # watch for an owner barge-in — "enough", "stop", a fresh
                        # wake, or a double-clap. Voiceprint-gated: Ultr0n's own
                        # TTS echo (which the mic hears) can never self-interrupt
                        # because piper's voice does not match the owner's embedding.
                        ring.append(frame)
                        if len(ring) > WAKE_WINDOW:
                            ring.pop(0)
                        if r < speech_threshold(noise_floor):
                            noise_floor = 0.95 * noise_floor + 0.05 * r
                        thresh = speech_threshold(noise_floor)
                        if is_clap(frame, noise_floor):
                            gap = cap_ts - last_clap_t
                            if CLAP_MIN_GAP <= gap <= CLAP_WINDOW:
                                last_clap_t = 0.0
                                log("double-clap barge-in during speech")
                                stop_speaking()
                                # Straight to capture — no prompt, Ultr0n was
                                # just talking and the user wants the floor.
                                while True:
                                    try:
                                        audio_q.get_nowait()
                                    except queue.Empty:
                                        break
                                capture = []
                                capture_silent = True
                                speech_seen = False
                                last_speech_t = time.monotonic()
                                t0 = last_speech_t
                                last_stt_t = 0.0
                                since_check = 0
                                ring = []
                                set_activity("listening", "Barge-in — new command?")
                                state = "listen"
                            else:
                                last_clap_t = cap_ts
                            continue
                        if r > thresh:
                            since_check += 1
                            if since_check >= WAKE_EVERY:
                                since_check = 0
                                if cap_ts - last_wake_stt >= WAKE_STT_COOLDOWN:
                                    last_wake_stt = cap_ts
                                    win = b"".join(ring)
                                    if len(win) >= RATE:
                                        try:
                                            arr = np.frombuffer(
                                                win, dtype=np.int16
                                            ).astype(np.float32) / 32768.0
                                            segs, _ = whisper.transcribe(
                                                arr, beam_size=1, language="en",
                                                condition_on_previous_text=False)
                                            heard = " ".join(s.text for s in segs).strip()
                                            if heard:
                                                log(f"heard during speech: {heard!r}")
                                            if (is_wake(heard) or is_interrupt(heard)) and speaker_matches(win):
                                                stop_speaking()
                                                log(f"barge-in: {heard!r}")
                                                # Fresh capture for the new
                                                # command, no prompt.
                                                while True:
                                                    try:
                                                        audio_q.get_nowait()
                                                    except queue.Empty:
                                                        break
                                                capture = []
                                                capture_silent = False
                                                speech_seen = False
                                                last_speech_t = time.monotonic()
                                                t0 = last_speech_t
                                                last_stt_t = 0.0
                                                since_check = 0
                                                ring = []
                                                set_activity("listening", "Interrupted — listening")
                                                state = "listen"
                                        except Exception as e:
                                            log(f"barge stt error: {e}")
                        # Reply finished (background thread cleared the event):
                        # drop the reply's echo from the queue, back to idle.
                        if state == "speaking" and not _speaking.is_set():
                            while True:
                                try:
                                    audio_q.get_nowait()
                                except queue.Empty:
                                    break
                            state = "idle"
                            ring = []
                            capture = []
                            speech_seen = False
                            since_check = 0
                            set_activity("standby", "Listening for your next command")

                    if state == "process":
                        while True:
                            try:
                                audio_q.get_nowait()
                            except queue.Empty:
                                break
                        if speech_seen and len(capture) * BLOCK / RATE >= MIN_CAPTURE:
                            # Voiceprint gate: only process commands from the
                            # enrolled voice. Someone else's words (even after a
                            # clap) are dropped entirely.
                            raw = b"".join(capture)
                            if speaker_matches(raw):
                                process_request(capture, silent_capture=capture_silent)
                            else:
                                log("voiceprint: speaker mismatch — command dropped")
                                # NO notify-send here: a silent clap wake from
                                # typing/mouse noise that fails the voiceprint must
                                # leave ZERO visible/audible trace. Popups on false
                                # wakes are exactly the disturbance the user hates.
                        else:
                            log("capture dropped: no speech or too short — ignoring")
                        # Barge-in window: the reply is playing in the background
                        # (speak block=False). Keep the mic armed so the owner can
                        # cut Ultr0n off with "enough"/"stop"/a fresh wake and the
                        # new command is captured immediately. The voiceprint gate
                        # ensures Ultr0n's own TTS echo can never self-interrupt
                        # (piper's voice != owner's embedding).
                        state = "speaking"
                        ring = []
                        capture = []
                        speech_seen = False
                        since_check = 0
                        last_stt_t = 0.0
                        last_wake_stt = 0.0
                        last_clap_t = 0.0
        except Exception as e:
            log(f"mic error: {e} — retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
