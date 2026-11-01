"""Prompt tokenisation: turns a prompt into weighted, normalised tags."""
from __future__ import annotations

import re

RE_LORA = re.compile(r"<\s*(lora|lyco|locon|hypernet)\s*:\s*([^:>]+)(?::\s*([+-]?[\d.]+))?[^>]*>", re.I)
RE_EMBED = re.compile(r"\bembedding:([\w\-.]+)", re.I)
# Attention syntax parser, compatible with Automatic1111's ``parse_prompt_attention``
RE_ATTENTION = re.compile(
    r"""\\\(|\\\)|\\\[|\\]|\\\\|\\|\(|\[|:\s*([+-]?[.\d]+)\s*\)|\)|]|[^\\()\[\]:]+|:""",
    re.X,
)
RE_BREAK = re.compile(r"\bBREAK\b|\bAND\b|\n")
RE_SPACES = re.compile(r"\s+")
MAX_TAG_LEN = 100


def extract_loras(prompt: str) -> list[tuple[str, float]]:
    out = []
    for m in RE_LORA.finditer(prompt or ""):
        try:
            w = float(m.group(3)) if m.group(3) else 1.0
        except ValueError:
            w = 1.0
        out.append((m.group(2).strip(), w))
    return out


def parse_attention(text: str) -> list[list]:
    """Split *text* into ``[fragment, weight]`` pairs."""
    res: list[list] = []
    round_brackets: list[int] = []
    square_brackets: list[int] = []

    def multiply(start: int, mult: float) -> None:
        for p in range(start, len(res)):
            res[p][1] *= mult

    for m in RE_ATTENTION.finditer(text):
        tok, weight = m.group(0), m.group(1)
        if tok.startswith("\\"):
            res.append([tok[1:], 1.0])
        elif tok == "(":
            round_brackets.append(len(res))
        elif tok == "[":
            square_brackets.append(len(res))
        elif weight is not None and round_brackets:
            try:
                multiply(round_brackets.pop(), float(weight))
            except ValueError:
                pass
        elif tok == ")" and round_brackets:
            multiply(round_brackets.pop(), 1.1)
        elif tok == "]" and square_brackets:
            multiply(square_brackets.pop(), 1 / 1.1)
        elif tok in (")", "]"):
            continue
        else:
            res.append([tok, 1.0])
    for pos in round_brackets:
        multiply(pos, 1.1)
    for pos in square_brackets:
        multiply(pos, 1 / 1.1)
    return res


def normalize_tag(tag: str) -> str:
    tag = tag.replace("_", " ").strip().lower()
    tag = tag.strip("{}[]() \t:;.|\"'")
    return RE_SPACES.sub(" ", tag)


def extract_tags(prompt: str) -> list[tuple[str, float]]:
    """Return unique ``(tag, weight)`` pairs from a prompt (weight = max seen)."""
    if not prompt:
        return []
    text = RE_LORA.sub(",", prompt)
    text = RE_BREAK.sub(",", text)
    text = text.replace("{", "").replace("}", "")  # NovelAI emphasis
    tags: dict[str, float] = {}
    current: list[tuple[str, float]] = []

    def flush() -> None:
        if not current:
            return
        name = normalize_tag("".join(t for t, _ in current))
        weight = max(current, key=lambda x: len(x[0].strip()))[1]
        current.clear()
        if name and len(name) <= MAX_TAG_LEN:
            w = round(weight, 3)
            tags[name] = max(tags.get(name, w), w)

    for frag, weight in parse_attention(text):
        parts = frag.split(",")
        for i, part in enumerate(parts):
            if i > 0:
                flush()
            if part:
                current.append((part, weight))
    flush()
    return list(tags.items())
