#!/usr/bin/env python3
"""
ALA Alignment Server — FastAPI wrapper around AutoLyrixAlign.

Accepts an audio file + lyrics, runs ALA via Singularity, returns
word-level timestamps and slide start times as JSON.

Run:
    pip install fastapi uvicorn python-multipart
    uvicorn ala_server:app --host 0.0.0.0 --port 8085

Endpoints:
    GET  /health          — check server and ALA are ready
    POST /align           — run alignment, returns JSON synchronously
"""

import re
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="ALA Alignment Server", version="1.0")

ALA_DIR = Path.home() / "alignment_test/tools/NUSAutoLyrixAlign"


# ── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    simg = ALA_DIR / "kaldi.simg"
    return {
        "status": "ok" if simg.exists() else "ala_missing",
        "ala_dir": str(ALA_DIR),
        "ala_ready": simg.exists(),
    }


# ── Align ────────────────────────────────────────────────────────────────────

@app.post("/align")
async def align(
    audio: UploadFile = File(...),
    lyrics: str = Form(...),
):
    """
    Align lyrics to audio using AutoLyrixAlign (Kaldi CNN-TDNN).

    Request (multipart/form-data):
        audio   — WAV audio file
        lyrics  — slide texts separated by "|||", one slide per segment
                  (blank slides should be included as empty strings)

    Response JSON:
        {
          "words": [{"word": str, "start": float, "end": float}, ...],
          "slides": [{"index": int, "time": float | null, "text": str}, ...],
          "elapsed": float   // seconds ALA took
        }

    "slides" is one entry per input slide (including blank ones).
    "time" is null for blank slides or if no words were matched.
    """
    simg = ALA_DIR / "kaldi.simg"
    if not simg.exists():
        raise HTTPException(status_code=503, detail="ALA not installed on this server")

    audio_bytes = await audio.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Parse slide texts (||| separator)
    slide_texts = [s.strip() for s in lyrics.split("|||")]

    # Build word list (one word per line) and track word counts per slide
    all_words = []
    word_counts = []
    for text in slide_texts:
        words = re.findall(r"[a-zA-Z']+", text)
        word_counts.append(len(words))
        all_words.extend(words)

    if not all_words:
        raise HTTPException(status_code=400, detail="No words found in lyrics")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Write audio
        audio_path = tmp / "input.wav"
        audio_path.write_bytes(audio_bytes)

        # Write lyrics (one word per line, uppercase — ALA expects this)
        lyrics_path = tmp / "lyrics.txt"
        lyrics_path.write_text("\n".join(w.upper() for w in all_words) + "\n")

        output_path = tmp / "output.txt"

        # Run ALA
        cmd = [
            "singularity", "exec",
            str(simg),
            "/bin/bash", "-c",
            f"cd {ALA_DIR} && ./RunAlignment.sh {audio_path} {lyrics_path} {output_path}"
        ]
        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        elapsed = time.time() - t0

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"ALA failed (exit {result.returncode}): {result.stderr[-500:]}"
            )

        if not output_path.exists():
            raise HTTPException(status_code=500, detail="ALA produced no output file")

        # Parse word timestamps
        word_timestamps = []
        for line in output_path.read_text().splitlines():
            parts = line.strip().split()
            if len(parts) >= 3:
                try:
                    word_timestamps.append({
                        "word": parts[2],
                        "start": float(parts[0]),
                        "end": float(parts[1]),
                    })
                except ValueError:
                    pass

    # Map word timestamps → slide start times (sequential word counting)
    slides_out = []
    cursor = 0
    for i, (text, wc) in enumerate(zip(slide_texts, word_counts)):
        if not text.strip() or wc == 0:
            slides_out.append({"index": i, "time": None, "text": text})
            continue
        if cursor < len(word_timestamps):
            slides_out.append({
                "index": i,
                "time": word_timestamps[cursor]["start"],
                "text": text,
            })
            cursor += wc
        else:
            slides_out.append({"index": i, "time": None, "text": text})

    return JSONResponse({
        "words": word_timestamps,
        "slides": slides_out,
        "elapsed": round(elapsed, 1),
        "word_count_expected": len(all_words),
        "word_count_returned": len(word_timestamps),
    })
