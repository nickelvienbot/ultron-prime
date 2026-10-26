#!/usr/bin/env python3
"""Enroll the owner's voiceprint for Ultr0n.

Records 4 short clips (3s each) with a beep cue before each, computes a
speaker embedding per clip, averages them, and saves the result to
~/.local/share/ultron-voiceprint.json. Run ONCE from the terminal while
sitting near the laptop mic:

    python3 scripts/enroll-voice.py

Say a command in each clip when you hear the beep, e.g. "ultron open
youtube", "open facebook", "check my tasks", "scroll down". 4 clips of
normal speaking voice are enough for a reliable voiceprint.

After enrollment, ultron-wake.py loads the voiceprint and only processes
wake/commands whose speaker matches (cosine >= VOICEPRINT_THRESHOLD).
Voices that fail the match are ignored entirely — even if they clap or
say "ultron".
"""
import json
import os
import sys
import time

import numpy as np
import sounddevice as sd

RATE = 16000
BLOCK = 1600
CLIP_SECONDS = 3.0
CLIPS = 4
BEEP = os.path.expanduser("~/.cache/ultron-beep.wav")
OUT = os.path.expanduser("~/.local/share/ultron-voiceprint.json")

CUES = [
    "say a command, for example: ultron, open youtube",
    "again, another command, for example: open facebook and search cats",
    "one more, for example: what time is it",
    "last one, for example: scroll down",
]


def beep():
    try:
        import subprocess
        subprocess.run(["paplay", BEEP], check=False, timeout=5)
    except Exception:
        pass


def record_clip(seconds: float) -> np.ndarray:
    frames = []
    def cb(indata, frames_, t, status):
        frames.append(bytes(indata))
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        blocksize=BLOCK, callback=cb):
        time.sleep(seconds)
    audio = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def main():
    from resemblyzer import VoiceEncoder, preprocess_wav
    enc = VoiceEncoder()
    print(f"Recording {CLIPS} clips of {CLIP_SECONDS}s each — speak when you hear the beep.")
    print("Sit near the mic and use your normal voice. Ambient room noise is fine.")
    print()

    clips = []
    for i in range(CLIPS):
        print(f"[{i+1}/{CLIPS}] {CUES[i]}")
        time.sleep(1.2)
        beep()
        audio = record_clip(CLIP_SECONDS)
        rms = float(np.sqrt(np.mean(audio * audio)))
        if rms < 0.01:
            print(f"  (clip {i+1} was quiet — keeping it anyway, but try to speak up)")
        clips.append(audio)
        print(f"  captured {CLIP_SECONDS}s, rms={rms:.3f}")

    # Embed each clip that has speech; average the embeddings.
    embs = []
    for i, audio in enumerate(clips):
        try:
            wav = preprocess_wav(audio, source_sr=RATE)
            if len(wav) < RATE * 0.5:  # <0.5s of actual audio — skip
                print(f"  clip {i+1}: too little audio, skipping")
                continue
            e = enc.embed_utterance(wav)
            embs.append(e)
            print(f"  clip {i+1}: embedding norm={np.linalg.norm(e):.3f}")
        except Exception as ex:
            print(f"  clip {i+1}: embed failed ({ex}), skipping")

    if len(embs) < 2:
        print("ERROR: fewer than 2 usable clips. Re-run and speak louder/clearer.")
        sys.exit(1)

    mean = np.mean(np.stack(embs), axis=0)
    mean = mean / np.linalg.norm(mean)
    data = {
        "voiceprint": mean.tolist(),
        "clips_used": len(embs),
        "threshold": 0.72,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2)
    print()
    print(f"Voiceprint saved: {OUT} ({len(embs)} clips averaged)")
    print("Ultr0n will now only respond to this voice.")


if __name__ == "__main__":
    main()
