"""Local speech-to-text with faster-whisper. The audio never leaves the machine.

Optional: install with ``pip install 'tg-guard[transcribe]'``. The default
model (large-v3-turbo, ~1.6 GB) is downloaded on first use into
``$TG_GUARD_HOME/models``.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time


def transcribe(jobs: list[tuple[str, pathlib.Path]], model_name: str, models_dir: pathlib.Path,
               language: str | None = None, as_json: bool = False) -> None:
    """Print one transcript per (label, path) job."""
    try:
        from faster_whisper import WhisperModel  # slow import; only when needed
    except ImportError:
        sys.exit("transcription needs the optional extra: pip install 'tg-guard[transcribe]'")
    models_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         download_root=str(models_dir))
    for label, path in jobs:
        t0 = time.time()
        segments, info = model.transcribe(str(path), language=language,
                                          vad_filter=True, beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip() or "[empty audio]"
        if as_json:
            print(json.dumps({"label": label, "file": str(path), "language": info.language,
                              "duration": round(info.duration, 1), "text": text},
                             ensure_ascii=False), flush=True)
        else:
            print(f"{label}  [{info.language}, {info.duration:.0f}s audio, "
                  f"{time.time() - t0:.0f}s]\n{text}\n", flush=True)
