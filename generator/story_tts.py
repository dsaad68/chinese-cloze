# /// script
# requires-python = ">=3.11"
# dependencies = ["click>=8", "requests>=2.31"]
# ///
"""Generate ElevenLabs text-to-speech audio for cloze stories.

Standalone companion to generate.py (which it does not touch). Reads a story JSON in
the compact PREMADE shape, reconstructs the full Chinese text and each sentence's text,
and calls the ElevenLabs text-to-speech API directly to render MP3 narration. Fully
self-contained — no external shell script. (The API contract mirrors the elevenlabs-tts
skill.)

Per story it writes:

    stories/audio/{id}/s01.mp3 …       # one clip per sentence (one API call each)
    stories/audio/{id}/full.mp3        # whole story — by default MERGED from the sentence
                                       #   clips with ffmpeg (no extra API call); --no-merge
                                       #   makes it a separate API call instead
    stories/audio/{id}/manifest.json   # maps clips -> sentence text

Prerequisites: ELEVENLABS_API_KEY set.

Examples:
    uv run generator/story_tts.py 2-9
    uv run generator/story_tts.py 2-9 2-10 --voice james-gao --speed 0.9
    uv run generator/story_tts.py stories/2-9.json --no-sentences
"""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import click
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
STORIES_DIR = REPO_ROOT / "stories"
DEFAULT_AUDIO_DIR = STORIES_DIR / "audio"

ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Chinese voices (label -> ElevenLabs voice id).
VOICES = {
    "amy": "bhJUNIXWQQ94l8eI2VUf",         # young, relaxed female (default)
    "anna-su": "9lHjugDhwqoxA5MhX0az",     # youthful, bright female
    "jason-chen": "DowyQ68vDpgFYdWVGjc3",  # male
    "james-gao": "4VZIsMPtgggwNg7OXbPY",   # calm, friendly male; storytelling
}
DEFAULT_VOICE = "amy"
# Multilingual v2 is the most stable model and explicitly supports Chinese — a better fit
# for plain narration than eleven_v3 (which is tuned for expressive dialogue).
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_FORMAT = "mp3_44100_128"

HAN_RE = re.compile(r"[㐀-䶿一-鿿]")


# --------------------------------------------------------------------------- #
# Text reconstruction (tolerant of compact [hanzi,…] and verbose {t,…} tokens)
# --------------------------------------------------------------------------- #
def token_hanzi(tok) -> str:
    if isinstance(tok, (list, tuple)) and tok:
        return str(tok[0] if tok[0] is not None else "")
    if isinstance(tok, dict):
        return str(tok.get("t", ""))
    return ""


def sentence_text(sentence) -> str:
    return "".join(token_hanzi(t) for t in (sentence or []))


def story_text(obj) -> str:
    return "".join(sentence_text(s) for s in obj.get("sentences", []))


def resolve_story(arg: str) -> Path:
    """Accept a path to a JSON file or a bare id like '2-9'."""
    p = Path(arg)
    if p.suffix == ".json" and p.exists():
        return p
    cand = STORIES_DIR / (arg if arg.endswith(".json") else f"{arg}.json")
    if cand.exists():
        return cand
    if p.exists():
        return p
    raise click.ClickException(f"story not found: {arg}")


# --------------------------------------------------------------------------- #
# ElevenLabs API
# --------------------------------------------------------------------------- #
def synthesize(text: str, out_file: Path, voice_id: str, fmt: str, model: str,
               speed: float | None, api_key: str, max_attempts: int = 3) -> None:
    """Render `text` to `out_file` (MP3) via the ElevenLabs TTS API.

    Retries transient failures (network errors, 429/5xx, empty body); raises on
    terminal errors (bad request/auth/voice).
    """
    out_file.parent.mkdir(parents=True, exist_ok=True)
    body: dict = {"text": text, "model_id": model}
    if speed is not None:
        body["voice_settings"] = {"speed": speed}
    headers = {"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    url = ELEVEN_TTS_URL.format(voice_id=voice_id)

    last_err = "unknown"
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, params={"output_format": fmt}, headers=headers,
                                 json=body, timeout=120)
        except requests.RequestException as exc:
            last_err = f"request error: {exc}"
        else:
            if resp.status_code < 300 and resp.content:
                out_file.write_bytes(resp.content)
                return
            if resp.status_code < 300:
                last_err = "empty audio body"
            elif resp.status_code in (408, 409, 429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                raise click.ClickException(
                    f"ElevenLabs HTTP {resp.status_code} for {out_file.name}: {resp.text[:300]}"
                )
        if attempt < max_attempts:
            time.sleep(2 * attempt)
    raise click.ClickException(f"ElevenLabs failed for {out_file.name} after "
                               f"{max_attempts} attempts: {last_err}")


def merge_clips(clips: list[Path], out_file: Path) -> None:
    """Concatenate `clips` (in order) into `out_file` with ffmpeg — no extra API call.

    Tries a lossless stream copy first (all clips share codec/params); falls back to
    re-encoding if the copy is rejected.
    """
    listing = "".join(f"file '{p.resolve().as_posix()}'\n" for p in clips)
    fd, listpath = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(listing)
        base = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listpath]
        copy = subprocess.run(base + ["-c", "copy", str(out_file)], capture_output=True, text=True)
        if copy.returncode == 0:
            return
        reenc = subprocess.run(base + ["-c:a", "libmp3lame", "-b:a", "128k", str(out_file)],
                               capture_output=True, text=True)
        if reenc.returncode != 0:
            raise click.ClickException(f"ffmpeg merge failed for {out_file.name}: {reenc.stderr[-300:]}")
    finally:
        os.unlink(listpath)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("stories", nargs=-1, required=True)
@click.option("--voice", type=click.Choice(list(VOICES)), default=DEFAULT_VOICE, show_default=True,
              help="Chinese voice to narrate with.")
@click.option("--voice-id", default=None, help="Raw ElevenLabs voice id (overrides --voice).")
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="ElevenLabs model id.")
@click.option("--format", "fmt", type=click.Choice(["mp3_44100_128", "mp3_44100_192"]),
              default=DEFAULT_FORMAT, show_default=True, help="Audio quality.")
@click.option("--speed", type=click.FloatRange(0.7, 1.2), default=0.8, show_default=True,
              help="Playback speed 0.7–1.2 (below 1.0 = slower). Default 0.8 (slowed for learners).")
@click.option("--sentences/--no-sentences", default=True, show_default=True,
              help="Render one clip per sentence.")
@click.option("--full/--no-full", default=True, show_default=True,
              help="Produce the whole-story full.mp3.")
@click.option("--merge/--no-merge", default=True, show_default=True,
              help="Build full.mp3 by concatenating the sentence clips (no extra API call). "
                   "With --no-merge, full.mp3 is a separate API call.")
@click.option("--out", "audio_dir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="Audio output directory (default: <repo>/stories/audio).")
@click.option("--force", is_flag=True, help="Regenerate even if an mp3 already exists.")
@click.option("--dry-run", is_flag=True, help="Show what would be generated; make no API calls.")
def main(stories, voice, voice_id, model, fmt, speed, sentences, full, merge, audio_dir, force, dry_run):
    """Render ElevenLabs TTS audio for one or more cloze stories (by id or path)."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key and not dry_run:
        raise click.ClickException("missing API key: set the ELEVENLABS_API_KEY environment variable.")

    if full and merge and not sentences:
        raise click.ClickException("--merge builds full.mp3 from sentence clips; don't combine it "
                                   "with --no-sentences (use --no-merge for a separate full clip).")

    vid = voice_id or VOICES[voice]
    vlabel = voice_id or voice
    audio_dir = audio_dir or DEFAULT_AUDIO_DIR
    speed_str = "normal" if speed is None else f"{speed}x"
    full_from = "merged" if (full and merge) else ("api" if full else None)

    click.echo(f"voice {vlabel} ({vid}) · model {model} · {fmt} · speed {speed_str} · "
               f"full={full_from}", err=True)

    def process(arg):
        path = resolve_story(arg)
        obj = json.loads(path.read_text(encoding="utf-8"))
        sid = str(obj.get("id") or path.stem)
        dest = audio_dir / sid
        sents = obj.get("sentences", [])
        full_path = dest / "full.mp3"

        # per-sentence clip jobs (name, text, path), in reading order
        sent_jobs: list[tuple[str, str, Path]] = []
        manifest_sentences = []
        if sentences:
            for i, s in enumerate(sents, 1):
                txt = sentence_text(s)
                if not HAN_RE.search(txt):      # skip punctuation-only sentences
                    continue
                fname = f"s{i:02d}.mp3"
                sent_jobs.append((fname, txt, dest / fname))
                manifest_sentences.append({"n": i, "file": fname, "text": txt})

        n_clips = len(sent_jobs) + (1 if full else 0)
        click.echo(f"\n{sid} — \"{obj.get('title','')}\" · {n_clips} clip(s) → {dest}", err=True)

        if dry_run:
            for fname, txt, _ in sent_jobs:
                click.echo(f"  [dry] {fname}: {txt}")
            if full:
                how = f"merge {len(sent_jobs)} sentence clips" if merge else "API full-story clip"
                click.echo(f"  [dry] full.mp3: ({how})")
            return

        # 1) sentence clips (one API call each)
        for fname, txt, out_file in sent_jobs:
            if out_file.exists() and not force:
                click.echo(f"  = {fname} (exists, skipped)")
                continue
            synthesize(txt, out_file, vid, fmt, model, speed, api_key)
            click.echo(f"  ✓ {fname}")

        # 2) full.mp3 — merged from sentence clips, or a separate API call
        if full:
            if full_path.exists() and not force:
                click.echo("  = full.mp3 (exists, skipped)")
            elif merge:
                clips = [p for _, _, p in sent_jobs if p.exists()]
                if not clips:
                    click.echo("  ! full.mp3 skipped (no sentence clips to merge)", err=True)
                else:
                    merge_clips(clips, full_path)
                    click.echo(f"  ✓ full.mp3 (merged {len(clips)} clips)")
            else:
                synthesize(story_text(obj), full_path, vid, fmt, model, speed, api_key)
                click.echo("  ✓ full.mp3")

        # 3) manifest
        manifest = {
            "id": sid,
            "title": obj.get("title", ""),
            "voice": vlabel,
            "voiceId": vid,
            "model": model,
            "format": fmt,
            "speed": speed,
            "full": "full.mp3" if full else None,
            "fullFrom": full_from,
            "fullText": story_text(obj),
            "sentences": manifest_sentences,
        }
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        click.echo("  ✓ manifest.json")

    made, failed = 0, 0
    for arg in stories:
        try:
            process(arg)
            made += 1
        except click.ClickException as exc:
            # In a batch, don't let one bad story abort the rest — log and move on.
            failed += 1
            click.echo(f"  ✗ {arg} skipped: {exc.format_message()}", err=True)
    if len(stories) > 1 or failed:
        click.echo(f"\ndone: {made} ok, {failed} failed", err=True)


if __name__ == "__main__":
    main()
