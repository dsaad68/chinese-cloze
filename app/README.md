# app/ — Chinese Cloze PWA

An installable, fully-offline Progressive Web App built from the
`portotype/Chinese-Cloze.html` prototype, backed by the authored stories in the
repo-root `stories/` folder.

## Run locally

Serve this folder over HTTP (a service worker needs a real origin, not `file://`):

```sh
python3 app/serve.py
# open http://localhost:8899/
```

Use `serve.py` (not `python3 -m http.server`): it adds HTTP **Range** support, which the
read-along `<audio>` elements need to stream. Plain `http.server` answers a Range request
with a full `200` and no `Accept-Ranges`, so Chrome's media element stalls and audio won't
play. GitHub Pages supports Range natively, so this only matters for local testing.

Install it from the browser's address-bar "Install" affordance to run standalone / offline.

## Update the story library

The generator writes stories to the repo-root `stories/` folder (and their narration
to `stories/audio/<id>/`). To pull new or changed stories into the app, re-run the sync
script — it copies each story JSON into `data/stories/`, mirrors the narration into
`data/audio/<id>/`, and regenerates `data/stories/index.json` (the manifest the app
fetches at boot; each entry carries a `hasAudio` flag):

```sh
python3 app/sync_stories.py
```

Reload once while online; the service worker caches the new stories for offline use.
No need to touch `sw.js` — it reads the story list from `index.json`.

## How it works / what changed vs. the prototype

- **Offline boot.** The prototype's runtime fetched React + ReactDOM from unpkg at
  boot. React 18.3.1 is now self-hosted in `vendor/` and loaded via `<script>` tags
  in the outer `<head>`, so `window.React`/`window.ReactDOM` exist before the runtime
  boots and its loader short-circuits — nothing is fetched from the network.
- **Fonts.** Fraunces is self-hosted in `fonts/` (`fonts.css` + woff2); the Google
  Fonts links were removed. All CJK fonts resolve to system faces, as before.
- **Story loader.** `loadExternalStories()` fetches `data/stories/index.json` then each
  story and feeds them through the prototype's existing `expandPremade()`. These merge
  with the 3 built-in demo stories (which add HSK-3 coverage).
- **Read-along audio.** After answers are revealed, the "全文 · THE FULL STORY" card doubles as
  a read-along: it shows the story sentence-by-sentence (with pinyin and highlighted answers),
  and its header has **朗读 Read-along** (plays the sentence clips in sequence, highlighting the
  current sentence) and **连读 Continuous** (plays the whole `full.mp3`) buttons, plus a Prev/Next
  stepper. Tap any sentence to play it; tapping an answer shows its meaning/note instead. It's
  driven by each story's `data/audio/<id>/manifest.json` (per-sentence `sNN.mp3` clips + a
  continuous `full.mp3`); the audio controls hide for stories with no audio. Narration lives under
  `data/audio/` and is mirrored there by `sync_stories.py`.
- **Service worker** (`sw.js`): cache-first for the static shell, network-first (with
  cache fallback) for documents and `data/` JSON, and cache-first (populated on first play)
  for `data/audio/` — the 85 MB of narration isn't precached, but each clip is cached the
  first time it's played so it works offline afterward. Bump `CACHE` when the shell changes.
- **Generation.** The "Write me a new story" band is hidden (it required the Claude
  Artifacts `window.claude` API, unavailable standalone). The `onGenerate` code is
  intact — re-enable by setting `V.showGenerate=true` in the template and rebundling.
- **hsk-data** is intentionally not shipped (only the hidden generator used it); the
  two `hsk-data/*` fetches 404 harmlessly (they are `try/catch`-wrapped).

## Editing the app shell

`index.html` is a self-unpacking bundle: the app lives as a JSON-encoded template
string on line 180. Edit via the extract → edit → rebundle scripts in the session
scratchpad (`app_rebundle.py` / `app_edits.py`), not by hand.
