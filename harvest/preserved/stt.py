#!/usr/bin/env python3
"""
Backend STT (Speech-to-Text) service for Ultron Mission Control.
Receives WAV audio via stdin, returns transcribed text via stdout.

Uses faster-whisper with the "base" model for accurate local voice commands.
"""
import sys, json, os, tempfile
from faster_whisper import WhisperModel

MODEL_SIZE = "base"
model = None

def get_model():
    global model
    if model is None:
        model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return model

def transcribe(audio_bytes: bytes) -> dict:
    """Return text plus confidence signals from faster-whisper."""
    model = get_model()
    # Write to temp file — faster-whisper needs a file path or numpy array
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        segments, info = model.transcribe(
            tmp_path, beam_size=5, language="en", vad_filter=True,
            condition_on_previous_text=False,
        )
        parts = list(segments)
        text = " ".join(seg.text for seg in parts).strip()
        avg_logprob = (
            sum(float(seg.avg_logprob) for seg in parts) / len(parts)
            if parts else -10.0
        )
        no_speech_prob = (
            sum(float(seg.no_speech_prob) for seg in parts) / len(parts)
            if parts else 1.0
        )
        return {
            "text": text,
            "avg_logprob": round(avg_logprob, 3),
            "no_speech_prob": round(no_speech_prob, 3),
        }
    finally:
        os.unlink(tmp_path)

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--ping":
        print(json.dumps({"status": "ok", "model": MODEL_SIZE}))
        return

    audio_data = sys.stdin.buffer.read()
    if len(audio_data) < 44:
        print(json.dumps({"error": "No audio data received or too short"}))
        return
    try:
        result = transcribe(audio_data)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    main()
