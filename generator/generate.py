# /// script
# requires-python = ">=3.11"
# dependencies = ["click>=8", "requests>=2.31"]
# ///
"""Generate Chinese 完形填空 (cloze) reading exercises for portotype/Chinese-Cloze.html.

Each generated story is written as one JSON file named ``{level}-{id}.json`` in the
compact "PREMADE" shape the app's built-in stories use. Generation is constrained to
the HSK vocabulary/grammar for the chosen level (from repo-root ``hsk-data/``), mirroring
the app's own ``allowedWords`` / ``grammarHint`` scoping.

Run with uv (deps are declared inline above):

    uv run generator/generate.py --level 1 --topic "food" --blanks 12

The OpenRouter API key is read from the ``OPENROUTER_API_KEY`` environment variable.
"""

import json
import os
import re
import time
from pathlib import Path

import click
import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
# Reasoning models spend most of the completion budget on reasoning tokens before any JSON
# is emitted (observed ~9k reasoning + ~3k content for an HSK 1 story), so the default is high.
DEFAULT_MAX_TOKENS = 24000
SYSTEM_PROMPT = "You are a meticulous HSK Chinese teacher. You output only valid minified JSON."

# Resolve the repo root as the parent of this script's directory (generator/ lives at repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
HSK_DIR = REPO_ROOT / "hsk-data"

# Cap the preferred-vocabulary list handed to the model (mirrors the app's `allow.slice(0,900)`).
MAX_ALLOWED_WORDS = 900
# Cap the grammar hint list (mirrors the app's `if(out.length>=18) break`).
MAX_GRAMMAR_HINTS = 18
# Cap example words shown per grammar point (from the `Content` column) to bound prompt size.
MAX_GRAMMAR_EXAMPLES = 12

# HSK display labels, mirroring the app's LEVELS table (7 == the combined "HSK 7–9" band).
LEVEL_DISP = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7–9"}

# Story-length presets (used by --length; overridden by an explicit --sentences count).
LENGTH_CLAUSE = {
    "short": "of 1–2 short paragraphs (about 5–8 sentences)",
    "medium": "of 2–3 short paragraphs (about 10–14 sentences)",
    "long": "of 3–4 paragraphs (about 16–22 sentences)",
}


# --------------------------------------------------------------------------- #
# HSK data loading + scoping (replicates the app's allowedWords / grammarHint)
# --------------------------------------------------------------------------- #
def _lvl_int(raw) -> int | None:
    """Turn an HSK level (int like 3, or a band string like "7-9") into a comparable int.

    Matches the app's ``parseInt(lvl, 10)`` so that "7-9" collapses to 7.
    """
    try:
        return int(str(raw).split("-")[0].strip())
    except (ValueError, AttributeError):
        return None


def load_hsk() -> tuple[dict, list]:
    """Load hsk-vocab.json (object keyed by headword) and hsk-grammar.json (array).

    Fails soft: a missing/unreadable file yields an empty structure and a warning, so
    generation still works (just without the vocabulary/grammar constraint).
    """
    vocab: dict = {}
    grammar: list = []
    vpath = HSK_DIR / "hsk-vocab.json"
    gpath = HSK_DIR / "hsk-grammar.json"
    try:
        vocab = json.loads(vpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        click.echo(f"warning: could not read {vpath}: {exc}", err=True)
    try:
        grammar = json.loads(gpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        click.echo(f"warning: could not read {gpath}: {exc}", err=True)
    return vocab, grammar


def allowed_words(vocab: dict, level: int, scope: str) -> list[str]:
    """Headwords in scope for `level`. `upto`: 1..level; `only`: exactly level."""
    out: list[str] = []
    for word, entry in vocab.items():
        n = _lvl_int(entry.get("lvl") if isinstance(entry, dict) else None)
        if n is None:
            continue
        in_scope = (1 <= n <= level) if scope == "upto" else (n == level)
        if in_scope:
            out.append(word)
    return out


def grammar_points(grammar: list, level: int, scope: str) -> list[tuple[str, str]]:
    """In-scope grammar points as (name, examples), deduped by name and capped.

    `name` is the point's `Details` (falling back to `Category`); `examples` is the
    point's `Content` example words (whitespace-stripped, capped at MAX_GRAMMAR_EXAMPLES
    items). Mirrors the app's grammarHint scoping but also carries the example words so the
    model can use them exactly.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for g in grammar:
        lv = _lvl_int(g.get("Level"))
        if lv is None:
            continue
        in_scope = (1 <= lv <= level) if scope == "upto" else (lv == level)
        if not in_scope:
            continue
        name = re.sub(r"\s+", "", g.get("Details") or g.get("Category") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        content = re.sub(r"\s+", "", g.get("Content") or "")
        items = [x for x in re.split(r"[、；;,，]", content) if x]
        examples = "、".join(items[:MAX_GRAMMAR_EXAMPLES])
        out.append((name, examples))
        if len(out) >= MAX_GRAMMAR_HINTS:
            break
    return out


# --------------------------------------------------------------------------- #
# Prompt construction (reuses the app's proven generation prompt)
# --------------------------------------------------------------------------- #
def build_prompt(level: int, scope: str, topic: str | None, n_blanks: int,
                 vocab: dict, grammar: list, length: str = "medium",
                 sentences: int | None = None) -> str:
    disp = LEVEL_DISP[level]
    label = f"HSK {disp} only" if scope == "only" else (
        f"HSK 1 to {disp}" if level == 7 else f"HSK 1–{disp}")
    scope_line = (
        f"Vocabulary scope: content words MUST be HSK level {disp} words; "
        f"surrounding text may use HSK {disp} or below."
        if scope == "only" else
        f"Vocabulary scope: use HSK {disp} words and below."
    )
    length_clause = (
        f"of approximately {sentences} sentences, organized into short paragraphs"
        if sentences else LENGTH_CLAUSE[length]
    )
    topic_clause = (
        f"about this topic: {topic}" if topic else
        "on a RANDOM everyday topic (daily routine, family, school, friends, shopping, food, "
        "weather, weekend, or travel)"
    )
    topic_line = f"Write ONE natural, connected story {length_clause}, {topic_clause}."
    gpoints = grammar_points(grammar, level, scope)
    allow = allowed_words(vocab, level, scope)

    parts = [
        f"Create a Chinese 完形填空 (cloze) reading exercise for {label} learners.",
        "",
        scope_line,
        topic_line,
        "Keep grammar simple and appropriate for the level.",
    ]
    if gpoints:
        parts.append(
            "Lean on these grammar points, and use the listed example words exactly "
            "where they fit naturally (名称：例词):"
        )
        for name, examples in gpoints:
            parts.append(f"- {name}：{examples}" if examples else f"- {name}")
    parts += [
        f"Remove EXACTLY {n_blanks} words and turn each into a numbered blank. "
        "Each removed word must fit only one blank.",
        "Break EVERY sentence into word tokens (punctuation is its own token with empty pinyin/gloss).",
        "",
        "Return ONLY minified JSON, no markdown fences, no commentary, in EXACTLY this shape:",
        '{"title":"汉字标题","titlePinyin":"pinyin with tone marks","topicEn":"short English topic",'
        '"sentences":[{"tokens":[{"t":"我","py":"wǒ","g":"I"},{"t":"叫","py":"jiào","g":"to be called","blank":1}]}],'
        '"blanks":[{"n":1,"answer":"叫","py":"jiào","g":"to be called","explanation":"one short sentence on why it fits"}],'
        '"translation":"full natural English translation"}',
        "",
        f"Rules: blanks numbered 1..{n_blanks} in reading order; every token has py (tone marks) and "
        f"g (short English gloss); blank tokens carry \"blank\":n and their \"t\" equals the answer; "
        f"{n_blanks} DISTINCT answer words (no repeats); keep the story coherent.",
    ]
    if allow:
        parts.append(
            "Preferred vocabulary (stay within this list for content words): "
            + " ".join(allow[:MAX_ALLOWED_WORDS])
        )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# OpenRouter call
# --------------------------------------------------------------------------- #
def _parse_openrouter_body(resp) -> dict | None:
    """Parse an OpenRouter response body into a dict, or None if it can't be parsed.

    During long (reasoning) generations OpenRouter injects ``: OPENROUTER PROCESSING``
    SSE keep-alive comment lines so the connection doesn't time out — these make the
    body not-pure-JSON and break ``resp.json()``. Strip comment/blank lines and recover
    the JSON object.
    """
    try:
        return resp.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        pass
    text = resp.text or ""
    cleaned = "\n".join(
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(":")
    )
    a, b = cleaned.find("{"), cleaned.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(cleaned[a:b + 1])
    except json.JSONDecodeError:
        return None


def call_openrouter(model: str, messages: list, api_key: str, max_tokens: int,
                    max_attempts: int = 3) -> tuple[dict, str]:
    """POST to OpenRouter and return (assistant message dict, finish_reason).

    Reasoning models spend a large slice of the completion budget on reasoning tokens
    before any content is emitted, so `max_tokens` must be generous (see --max-tokens).
    Retries transient failures (network errors, 429/5xx, truncated or heartbeat-laced
    bodies); raises on terminal errors (4xx, API error payloads).
    """
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "reasoning": {"enabled": True},
        "max_tokens": max_tokens,
    })
    last_err = "unknown"
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                data=payload,
                timeout=300,
            )
        except requests.RequestException as exc:
            last_err = f"request error: {exc}"
        else:
            if resp.status_code == 200:
                data = _parse_openrouter_body(resp)
                if data is None:
                    last_err = f"unparseable body: {(resp.text or '')[:200]!r}"
                elif "error" in data:
                    # Terminal API-level error (bad model, quota, etc.) — don't waste retries.
                    raise click.ClickException(f"OpenRouter error: {json.dumps(data['error'])[:500]}")
                else:
                    try:
                        choice = data["choices"][0]
                        return choice["message"], choice.get("finish_reason", "")
                    except (KeyError, IndexError, TypeError):
                        last_err = f"unexpected response: {json.dumps(data)[:200]}"
            elif resp.status_code in (408, 409, 429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                raise click.ClickException(
                    f"OpenRouter returned HTTP {resp.status_code}: {resp.text[:500]}"
                )
        if attempt < max_attempts:
            click.echo(
                f"    ⚠ transient OpenRouter issue ({last_err}); retrying "
                f"({attempt}/{max_attempts - 1})", err=True
            )
            time.sleep(2 * attempt)
    raise click.ClickException(f"OpenRouter failed after {max_attempts} attempts: {last_err}")


# --------------------------------------------------------------------------- #
# Parse + validate (same contract the app's normalizeAI enforces)
# --------------------------------------------------------------------------- #
def parse_json_object(content: str | None) -> dict:
    """Slice first { .. last } and json.loads it (matches normalizeAI's leniency)."""
    txt = (content or "").strip()
    a, b = txt.find("{"), txt.rfind("}")
    if a >= 0 and b > a:
        txt = txt[a:b + 1]
    return json.loads(txt)  # may raise json.JSONDecodeError


def _extract_tokens(sentence) -> list:
    """A sentence may be {"tokens":[...]} (verbose) or a bare list of tokens (drift)."""
    if isinstance(sentence, dict):
        toks = sentence.get("tokens")
        return toks if isinstance(toks, list) else []
    if isinstance(sentence, list):
        return sentence
    return []


def _normalize_token(t) -> dict | None:
    """Normalize a token into {t, py, g, blank?}. Tolerates the verbose dict form
    ({t,py,g,blank?}) and the compact list form ([hanzi,py,gloss,blank?]). Returns
    None if the token can't be interpreted."""
    if isinstance(t, dict):
        if "t" not in t:
            return None
        tok = {"t": str(t.get("t", "")), "py": str(t.get("py", "")), "g": str(t.get("g", ""))}
        blank = t.get("blank")
    elif isinstance(t, list) and t:
        tok = {"t": str(t[0]),
               "py": str(t[1]) if len(t) > 1 else "",
               "g": str(t[2]) if len(t) > 2 else ""}
        blank = t[3] if len(t) > 3 else None
    else:
        return None
    if blank not in (None, ""):
        try:
            tok["blank"] = int(blank)
        except (ValueError, TypeError):
            return None
    return tok


def validate_story(obj: dict) -> tuple[list, list]:
    """Validate the verbose story. Returns (sentences, blanks) or raises ValueError.

    Enforces: non-empty sentences; every token has t/py/g; blank ordinals are a
    contiguous 1..N set increasing in reading order; each blank token's t equals
    its answer; all answers distinct. Tolerant of common shape drift (bare-list
    sentences, compact-list tokens); anything it can't interpret raises ValueError.
    """
    if not isinstance(obj, dict):
        raise ValueError(f"top-level JSON is not an object (got {type(obj).__name__})")
    sentences = obj.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("`sentences` is missing or empty")

    norm_sentences: list[dict] = []
    ordinals: list[int] = []       # blank numbers in reading order
    token_answers: dict[int, str] = {}
    for si, s in enumerate(sentences):
        tokens = _extract_tokens(s)
        if not tokens:
            raise ValueError(f"sentence {si} has no tokens")
        norm_tokens = []
        for ti, t in enumerate(tokens):
            tok = _normalize_token(t)
            if tok is None:
                raise ValueError(f"sentence {si} token {ti} is malformed: {t!r}")
            bn = tok.get("blank")
            if bn is not None:
                ordinals.append(bn)
                token_answers[bn] = tok["t"]
                if not tok["t"]:
                    raise ValueError(f"blank {bn} has an empty answer token")
            norm_tokens.append(tok)
        norm_sentences.append({"tokens": norm_tokens})

    if not ordinals:
        raise ValueError("no blanks found")
    if ordinals != sorted(ordinals):
        raise ValueError(f"blank ordinals not in reading order: {ordinals}")
    n = len(ordinals)
    if set(ordinals) != set(range(1, n + 1)):
        raise ValueError(f"blank ordinals are not a contiguous 1..{n} set: {sorted(set(ordinals))}")

    # Build blanks list, preferring the model's blanks[] metadata but falling back to tokens.
    raw_blanks = obj.get("blanks") or []
    by_n = {}
    for x in raw_blanks:
        if not isinstance(x, dict):
            continue
        try:
            by_n[int(x.get("n"))] = x
        except (ValueError, TypeError):
            continue
    blanks = []
    for bn in range(1, n + 1):
        meta = by_n.get(bn, {})
        answer = str(meta.get("answer") or token_answers[bn])
        if answer != token_answers[bn]:
            raise ValueError(f"blank {bn}: answer {answer!r} != blank token {token_answers[bn]!r}")
        blanks.append({
            "n": bn,
            "answer": answer,
            "py": str(meta.get("py", "")),
            "g": str(meta.get("g", "")),
            "explanation": str(meta.get("explanation", "")),
        })

    answers = [b["answer"] for b in blanks]
    if len(set(answers)) != len(answers):
        raise ValueError(f"answers are not distinct: {answers}")

    return norm_sentences, blanks


# --------------------------------------------------------------------------- #
# Verbose -> compact PREMADE shape
# --------------------------------------------------------------------------- #
def to_compact(obj: dict, sentences: list, blanks: list, story_id: str, level: int) -> dict:
    """Assemble the compact PREMADE-shape story object that expandPremade consumes."""
    compact_sentences = []
    for s in sentences:
        toks = []
        for tk in s["tokens"]:
            row = [tk["t"], tk["py"], tk["g"]]
            if "blank" in tk:
                row.append(tk["blank"])
            toks.append(row)
        compact_sentences.append(toks)

    explanations = {str(b["n"]): b["explanation"] for b in blanks if b["explanation"]}

    return {
        "id": story_id,
        "level": level,
        "title": str(obj.get("title", "新练习")),
        "titlePinyin": str(obj.get("titlePinyin", "")),
        "topicEn": str(obj.get("topicEn", "Generated story")),
        "sentences": compact_sentences,
        "explanations": explanations,
        "translation": str(obj.get("translation", "")),
    }


def dumps_story(obj: dict, pretty: bool) -> str:
    """Serialize a compact story. Pretty mode mirrors the built-in PREMADE style:
    one sentence (and one explanation) per line, tokens kept inline."""
    if not pretty:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    def c(v):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    lines = ["{"]
    for key in ("id", "level", "title", "titlePinyin", "topicEn"):
        lines.append(f"  {c(key)}: {c(obj[key])},")

    sents = obj["sentences"]
    lines.append('  "sentences": [')
    for i, s in enumerate(sents):
        lines.append(f"    {c(s)}{',' if i < len(sents) - 1 else ''}")
    lines.append("  ],")

    items = list(obj["explanations"].items())
    if items:
        lines.append('  "explanations": {')
        for i, (k, v) in enumerate(items):
            lines.append(f"    {c(k)}: {c(v)}{',' if i < len(items) - 1 else ''}")
        lines.append("  },")
    else:
        lines.append('  "explanations": {},')

    lines.append(f'  "translation": {c(obj["translation"])}')
    lines.append("}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# id assignment
# --------------------------------------------------------------------------- #
def next_id(out_dir: Path, level: int) -> int:
    """Next free integer id for `level` by scanning existing {level}-{n}.json files."""
    pat = re.compile(rf"^{level}-(\d+)\.json$")
    highest = 0
    if out_dir.is_dir():
        for p in out_dir.iterdir():
            m = pat.match(p.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


# --------------------------------------------------------------------------- #
# Single-story generation (call -> validate -> repair loop)
# --------------------------------------------------------------------------- #
def generate_one(model: str, api_key: str, prompt: str, n_blanks: int, max_tokens: int,
                 max_repairs: int = 2) -> tuple[dict, list, list]:
    """Return (verbose_obj, sentences, blanks). Retries with a reasoning-preserving repair call."""
    messages = [{"role": "user", "content": prompt}]
    problems = None
    for attempt in range(max_repairs + 1):
        if problems is not None:
            # Reasoning-preserving repair: keep the assistant message + reasoning_details,
            # then tell the model exactly what to fix (the user's two-call pattern).
            click.echo(f"    ↻ repair attempt {attempt}: {problems}", err=True)
        msg, finish_reason = call_openrouter(model, messages, api_key, max_tokens)
        content = msg.get("content")
        # Reasoning models can burn the whole budget on reasoning and emit no content.
        # Retrying with the same budget won't help — fail fast with actionable guidance.
        if not (content or "").strip() and finish_reason == "length":
            raise click.ClickException(
                f"model output truncated (finish_reason=length) with empty content — the "
                f"reasoning consumed all {max_tokens} tokens. Raise --max-tokens and retry."
            )
        try:
            obj = parse_json_object(content)
            sentences, blanks = validate_story(obj)
        except (json.JSONDecodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
            problems = str(exc) or exc.__class__.__name__
            if attempt >= max_repairs:
                raise click.ClickException(f"story invalid after {max_repairs} repair(s): {problems}")
            messages = [
                {"role": "user", "content": prompt},
                {
                    "role": "assistant",
                    "content": content,
                    "reasoning_details": msg.get("reasoning_details"),
                },
                {
                    "role": "user",
                    "content": (
                        f"Your JSON had this problem: {problems}. "
                        f"Return corrected minified JSON only, in the exact same shape, "
                        f"with {n_blanks} distinct blanks numbered 1..{n_blanks}."
                    ),
                },
            ]
            continue
        if len(blanks) != n_blanks:
            click.echo(
                f"    note: model produced {len(blanks)} blanks (requested {n_blanks})", err=True
            )
        return obj, sentences, blanks
    raise click.ClickException("unreachable")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--level", "-l", type=click.IntRange(1, 7), required=True,
              help="HSK level 1–6, or 7 for the combined HSK 7–9 band.")
@click.option("--topic", "-t", default=None,
              help="Story topic. If omitted, the model picks a random everyday topic.")
@click.option("--blanks", "-b", type=click.IntRange(1, 40), default=15, show_default=True,
              help="Target number of blanks.")
@click.option("--length", type=click.Choice(["short", "medium", "long"]), default="medium",
              show_default=True, help="Approximate story length.")
@click.option("--sentences", "n_sentences", type=click.IntRange(3, 60), default=None,
              help="Exact target number of sentences (overrides --length).")
@click.option("--count", "-n", type=click.IntRange(1, 100), default=1, show_default=True,
              help="Number of stories to generate this run (ids auto-increment).")
@click.option("--scope", type=click.Choice(["upto", "only"]), default="upto", show_default=True,
              help="Vocabulary scope: words up to the level, or exactly the level.")
@click.option("--model", "-m", default=DEFAULT_MODEL, show_default=True, help="OpenRouter model id.")
@click.option("--max-tokens", type=click.IntRange(2000, 64000), default=DEFAULT_MAX_TOKENS,
              show_default=True,
              help="Completion token budget. Reasoning models need a large budget "
                   "(reasoning tokens are spent before any JSON is emitted).")
@click.option("--out", "-o", "out_dir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="Output directory (default: <repo>/stories).")
@click.option("--id", "story_id", default=None,
              help="Override the auto-assigned id (single story only).")
@click.option("--pretty/--minified", default=True, show_default=True,
              help="Indent the output JSON, or write it minified.")
@click.option("--dry-run", is_flag=True, help="Validate and print; do not write files.")
@click.option("--api-key-env", default="OPENROUTER_API_KEY", show_default=True,
              help="Environment variable holding the OpenRouter API key.")
def main(level, topic, blanks, length, n_sentences, count, scope, model, max_tokens, out_dir,
         story_id, pretty, dry_run, api_key_env):
    """Generate HSK cloze stories as {level}-{id}.json for Chinese-Cloze.html."""
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise click.ClickException(
            f"missing API key: set the {api_key_env} environment variable."
        )
    if story_id and count > 1:
        raise click.ClickException("--id can only be used with --count 1.")

    out_dir = out_dir or (REPO_ROOT / "stories")
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    vocab, grammar = load_hsk()
    allow_n = len(allowed_words(vocab, level, scope))
    click.echo(
        f"HSK {LEVEL_DISP[level]} ({scope}) · {allow_n} words in scope · model {model}", err=True
    )

    made, failed = 0, 0
    for i in range(count):
        prompt = build_prompt(level, scope, topic, blanks, vocab, grammar, length, n_sentences)
        click.echo(f"[{i + 1}/{count}] generating…", err=True)
        try:
            obj, sentences, blank_list = generate_one(model, api_key, prompt, blanks, max_tokens)

            sid = story_id or f"{level}-{next_id(out_dir, level)}"
            compact = to_compact(obj, sentences, blank_list, sid, level)
            text = dumps_story(compact, pretty)

            if dry_run:
                click.echo(text)
                click.echo(
                    f"— dry run: would write {out_dir / (sid + '.json')} "
                    f"— \"{compact['title']}\" ({len(blank_list)} blanks)", err=True
                )
                made += 1
                continue

            path = out_dir / f"{sid}.json"
            path.write_text(text + "\n", encoding="utf-8")
            rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
            click.echo(f"✓ {rel} — \"{compact['title']}\" ({len(blank_list)} blanks)")
            made += 1
        except click.ClickException as exc:
            # In a batch, don't let one bad story abort the rest — log and move on.
            failed += 1
            click.echo(f"✗ [{i + 1}/{count}] skipped: {exc.format_message()}", err=True)

    if count > 1 or failed:
        click.echo(f"\ndone: {made} generated, {failed} failed", err=True)
    if made == 0:
        raise click.ClickException("no stories were generated")


if __name__ == "__main__":
    main()
