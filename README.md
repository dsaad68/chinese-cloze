# 中文完形填空 · Chinese Cloze

An installable, fully-offline reading app for learning Chinese through **完形填空 (cloze)**
exercises. Read a short, level-appropriate story, drag the right words into the blanks, then
reveal a full translation, per-blank explanations, and sentence-by-sentence **read-along audio**.

**▶ Live app: <https://dsaad68.github.io/chinese-cloze/>**

Stories are graded by HSK level (1–6 plus the combined 7–9 band), authored offline with an
LLM, and narrated with text-to-speech — so the whole library ships inside the app and works
with no network once installed.

## Features

- **Drag-to-fill cloze** — a shuffled word bank of the blank answers; every answer is distinct.
- **HSK-graded** — vocabulary and grammar scoped to the chosen level using the bundled HSK datasets.
- **Read-along narration** — per-sentence clips plus a continuous full-story track, with the
  current sentence highlighted. Tap any sentence to replay it; tap an answer to see its meaning.
- **Pinyin, glosses, explanations, and a natural English translation** for every story.
- **Installable PWA** — add it to your home screen and it runs standalone and fully offline;
  audio is cached the first time it plays.

## Repository layout

| Path | What it is |
| --- | --- |
| [`app/`](app/) | The installable, offline PWA served to GitHub Pages. Self-contained: self-hosted React + fonts, a service worker, and the story library under `app/data/`. |
| [`generator/`](generator/) | Two single-file [`uv`](https://docs.astral.sh/uv/) scripts: `generate.py` (authors story JSON via the OpenRouter API) and `story_tts.py` (renders ElevenLabs narration). |
| [`stories/`](stories/) | The authored story library — one `{level}-{id}.json` per story, plus narration under `stories/audio/<id>/`. The source of truth that `app/data/` is synced from. |
| [`hsk-data/`](hsk-data/) | HSK vocabulary and grammar datasets that scope generation to a level. |

## Run the app locally

A service worker needs a real HTTP origin (not `file://`):

```sh
python3 app/serve.py
# open http://localhost:8899/
```

Use `serve.py` rather than `python3 -m http.server` — it adds HTTP **Range** support, which the
read-along `<audio>` elements need to stream. GitHub Pages supports Range natively, so this only
matters for local testing. See [`app/README.md`](app/README.md) for how the PWA is built and how
to edit the bundle.

## Authoring stories

Stories are generated offline with the scripts in [`generator/`](generator/) (full docs in
[`generator/README.md`](generator/README.md)). In short:

```sh
export OPENROUTER_API_KEY="sk-or-..."      # story generation (OpenRouter)
export ELEVENLABS_API_KEY="..."            # narration (ElevenLabs)

# Author one HSK 2 story about food with ~15 blanks -> stories/2-1.json
uv run generator/generate.py --level 2 --topic "food" --blanks 15

# Narrate it (whole story + one clip per sentence) -> stories/audio/2-1/
uv run generator/story_tts.py 2-1
```

Then sync the library into the app and reload once while online to cache it:

```sh
python3 app/sync_stories.py
```

## Deployment

The app is published to GitHub Pages by the
[`Deploy PWA to GitHub Pages`](.github/workflows/deploy-pages.yml) workflow, which re-syncs
`stories/` into `app/data/` and uploads only the `app/` folder as the Pages artifact. Trigger a
deploy from the repository's **Actions** tab (or `gh workflow run deploy-pages.yml`).
