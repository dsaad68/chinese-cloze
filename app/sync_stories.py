#!/usr/bin/env python3
"""Sync the authored stories (and their narration) into the PWA's data folder.

Copies every ``stories/*.json`` (the compact shape produced by
``generator/generate.py``) into ``app/data/stories/`` and regenerates
``app/data/stories/index.json`` — the lightweight manifest the app fetches at
boot and the service worker uses to precache the library for offline use.

Also mirrors each story's narration from ``stories/audio/<id>/`` (the
per-sentence ``sNN.mp3`` clips, the continuous ``full.mp3``, and ``manifest.json``)
into ``app/data/audio/<id>/`` so the read-along feature ships with the app. Each
``index.json`` entry gets a ``hasAudio`` flag.

Run once after setting up ``app/``, and again whenever the generator adds
stories or audio:  ``python3 app/sync_stories.py``
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
SRC = REPO_ROOT / "stories"
DST = APP_DIR / "data" / "stories"
AUDIO_SRC = SRC / "audio"
AUDIO_DST = APP_DIR / "data" / "audio"


def count_blanks(story: dict) -> int:
    """A blank is any token with a 4th element (the blank number)."""
    n = 0
    for sentence in story.get("sentences", []):
        for token in sentence:
            if len(token) > 3 and token[3] is not None:
                n += 1
    return n


def sort_key(entry: dict):
    """Natural order: by level, then by the numeric id suffix (2-2 before 2-10)."""
    parts = str(entry["id"]).split("-")
    nums = [int(p) if p.isdigit() else p for p in parts]
    return (entry.get("level", 0), nums)


def sync_audio(story_id: str) -> bool:
    """Mirror stories/audio/<id>/ into app/data/audio/<id>/. Returns True if audio exists.

    Copies manifest.json plus every mp3 clip. A directory is only counted as
    having audio when its manifest.json is present (the read-along loader keys on
    that manifest).
    """
    src = AUDIO_SRC / story_id
    if not (src / "manifest.json").is_file():
        return False
    dst = AUDIO_DST / story_id
    if dst.exists():
        shutil.rmtree(dst)  # clear stale clips so removed sentences don't linger
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.iterdir()):
        if f.suffix in (".mp3", ".json"):
            shutil.copyfile(f, dst / f.name)
    return True


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"source stories folder not found: {SRC}")
    DST.mkdir(parents=True, exist_ok=True)

    # Clear stale story copies (but keep index.json until we rewrite it).
    for old in DST.glob("*.json"):
        if old.name != "index.json":
            old.unlink()

    index = []
    n_audio = 0
    for path in sorted(SRC.glob("*.json")):
        story = json.loads(path.read_text(encoding="utf-8"))
        story_id = str(story.get("id") or path.stem)
        shutil.copyfile(path, DST / f"{story_id}.json")
        has_audio = sync_audio(story_id)
        n_audio += has_audio
        index.append({
            "id": story_id,
            "level": story.get("level"),
            "title": story.get("title", ""),
            "titlePinyin": story.get("titlePinyin", ""),
            "topicEn": story.get("topicEn", ""),
            "nBlanks": count_blanks(story),
            "hasAudio": has_audio,
        })

    index.sort(key=sort_key)
    (DST / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"synced {len(index)} stories -> {DST.relative_to(REPO_ROOT)}")
    print(f"synced audio for {n_audio}/{len(index)} stories -> {AUDIO_DST.relative_to(REPO_ROOT)}")
    print("index.json:", ", ".join(e["id"] for e in index))


if __name__ == "__main__":
    main()
