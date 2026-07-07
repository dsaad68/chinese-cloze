# generator

A single-file [`uv`](https://docs.astral.sh/uv/) script that generates Chinese 完形填空
(cloze) reading exercises for [`../portotype/Chinese-Cloze.html`](../portotype/Chinese-Cloze.html).

Each story is written as **one JSON file** named `{level}-{id}.json` (e.g. `1-1.json`) into a
repo-root `stories/` folder, in the **compact shape** the app's built-in stories use.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (deps are declared inline in the
  script via PEP 723 — no manual install needed).
- An OpenRouter API key in the `OPENROUTER_API_KEY` environment variable:

  ```sh
  export OPENROUTER_API_KEY="sk-or-..."
  ```

## Usage

```sh
# One HSK 1 story about food, ~12 blanks -> stories/1-1.json
uv run generator/generate.py --level 1 --topic "food" --blanks 12

# Three HSK 2 stories, random topics -> stories/2-1.json, 2-2.json, 2-3.json
uv run generator/generate.py --level 2 --count 3

# Preview without writing
uv run generator/generate.py --level 3 --dry-run

# Full option list
uv run generator/generate.py --help
```

### Options

| Option | Default | Notes |
| --- | --- | --- |
| `--level, -l` | *(required)* | HSK `1`–`6`, or `7` for the combined HSK 7–9 band. |
| `--topic, -t` | random | Story topic. Omitted → the model picks a random everyday topic. |
| `--blanks, -b` | `15` | Target number of blanks. |
| `--length` | `medium` | Approximate story length: `short` (~5–8 sentences), `medium` (~10–14), `long` (~16–22). |
| `--sentences` | — | Exact target sentence count; overrides `--length`. |
| `--count, -n` | `1` | Stories to generate this run; ids auto-increment. |
| `--scope` | `upto` | `upto` = words ≤ level; `only` = words exactly at the level. |
| `--model, -m` | `deepseek/deepseek-v4-flash` | Any OpenRouter chat model id. |
| `--max-tokens` | `24000` | Completion budget. Reasoning models spend most of it on reasoning tokens *before* any JSON is emitted (an HSK 1 story used ~9k reasoning + ~3k content), so keep this generous. If a run errors with `finish_reason=length`, raise it. |
| `--out, -o` | `<repo>/stories` | Output directory. |
| `--id` | auto | Override the auto-assigned id (single story only). |
| `--pretty / --minified` | `--pretty` | Output JSON formatting. |
| `--dry-run` | off | Validate and print; write nothing. |
| `--api-key-env` | `OPENROUTER_API_KEY` | Env var holding the API key. |

## How it works

1. **Scope** — reads `../hsk-data/hsk-vocab.json` and `../hsk-data/hsk-grammar.json` and builds, for
   the level: an allowed-words list (a preferred-vocabulary constraint), and a bulleted list of
   grammar points **with their example words** (`能愿动词：会、能`), which the model is told to use
   exactly where they fit. Scoping mirrors the app's `allowedWords` / `grammarHint`; the example
   words come from the grammar file's `Content` column (capped per point).
2. **Generate** — calls OpenRouter (with `reasoning` enabled) using the app's own generation prompt,
   asking for the verbose `{sentences:[{tokens:[…]}], blanks:[…]}` shape.
3. **Validate + repair** — checks the JSON (contiguous `1..N` blanks, distinct answers, every token has
   pinyin + gloss). On failure it makes a **reasoning-preserving repair call** (passing the assistant's
   `reasoning_details` back unmodified) that tells the model exactly what to fix.
4. **Convert + write** — converts the verbose output into the compact PREMADE shape, assigns
   `{level}-{id}`, and writes `stories/{level}-{id}.json`.

## Output format

Compact shape, identical to the app's built-in `PREMADE` stories:

```json
{
  "id": "1-1",
  "level": 1,
  "title": "我的星期天",
  "titlePinyin": "Wǒ de xīngqītiān",
  "topicEn": "My Sunday",
  "sentences": [
    [["今天","jīntiān","today"],["天气","tiānqì","weather",1],["很","hěn","very"],["好","hǎo","good"],["。","",""]]
  ],
  "explanations": { "1": "“很好” describes the 天气 (weather)." },
  "translation": "Today the weather is very nice."
}
```

- A token is `[hanzi, pinyin, gloss]`, or `[hanzi, pinyin, gloss, blankNumber]` for a blank.
- Punctuation is `[char, "", ""]`.
- No distractors are stored: the app builds the word-bank by shuffling the blank answers, so all
  answers are distinct.

## Text-to-speech (`story_tts.py`)

`story_tts.py` renders ElevenLabs narration for a story. It reconstructs the full Chinese text (and
each sentence) from the story's tokens and calls the ElevenLabs text-to-speech API **directly** — it
is fully self-contained (no shell script) and does not touch `generate.py`. (The API contract mirrors
the [`elevenlabs-tts`](../../my-agent-skills/skills/elevenlabs-tts) skill.)

### Prerequisites

- `ELEVENLABS_API_KEY` set. Deps (`click`, `requests`) are declared inline for `uv` — no install.

### Usage

```sh
# Voice story 2-9 (whole story + one clip per sentence) with the default voice (Amy)
uv run generator/story_tts.py 2-9

# Several stories, a specific voice, slowed down for learners
uv run generator/story_tts.py 2-9 2-10 --voice james-gao --speed 0.85

# Whole-story clip only; or preview without spending credits
uv run generator/story_tts.py 2-9 --no-sentences
uv run generator/story_tts.py 2-9 --dry-run
```

Accepts story **ids** (`2-9`) or **paths** (`stories/2-9.json`). Existing clips are skipped unless
`--force`. Voices: `amy` (default), `anna-su`, `jason-chen`, `james-gao`; or any raw id via
`--voice-id`. `--speed` sets `voice_settings.speed` (`0.7`–`1.2`; below 1.0 = slower); it **defaults
to `0.9`**, slightly slowed for learners — pass `--speed 1.0` for normal pace. Model defaults to
`eleven_multilingual_v2` (stable, Chinese-capable).

### Output

Per story, under `stories/audio/{id}/`:

```
full.mp3          # whole story, one narration
s01.mp3 … sNN.mp3 # one clip per sentence (punctuation-only sentences skipped)
manifest.json     # { id, title, voice, model, speed, full, fullText, sentences:[{n, file, text}] }
```

The `manifest.json` maps each clip to its sentence text — ready for the HTML to wire up
sentence-by-sentence playback.

## Wiring note

The HTML currently **embeds** its stories in a `const PREMADE = [...]` array and does not yet load
external story files (its only `fetch()` calls are for `hsk-data/*`). These generated files match the
`PREMADE` shape, so you can use them by either:

- pasting a story object into the `PREMADE` array, or
- adding a small loader that fetches `stories/*.json` and passes each through the existing
  `expandPremade()`.

The `stories/audio/{id}/` clips + `manifest.json` are likewise ready for a future audio player in the
app (e.g. a "listen" button per story and per sentence).
