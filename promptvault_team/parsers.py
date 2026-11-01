"""Generator-agnostic metadata parsing.

Every parser converts raw PNG text chunks into a :class:`ParsedMeta` with a
normalised set of parameter keys (``steps``, ``sampler``, ``scheduler``,
``cfg_scale``, ``seed``, ``width``, ``height``, ``model``, ``denoise`` ...).
Unknown formats fall back to heuristics so any PNG carrying a prompt works.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .tokens import extract_loras

STANDARD_KEYS = {
    "steps": "steps", "sampler": "sampler", "sampler_name": "sampler",
    "cfg scale": "cfg_scale", "cfg_scale": "cfg_scale", "cfg": "cfg_scale", "scale": "cfg_scale",
    "guidance_scale": "cfg_scale", "seed": "seed", "noise_seed": "seed",
    "model": "model", "model_name": "model", "ckpt_name": "model", "base_model": "model",
    "model hash": "model_hash", "schedule type": "scheduler", "scheduler": "scheduler",
    "denoising strength": "denoise", "denoise": "denoise", "strength": "denoise",
    "clip skip": "clip_skip", "width": "width", "height": "height",
}
PROMPT_KEYS = ("prompt", "positive_prompt", "positive", "Prompt", "full_prompt", "prompt_text")
NEGATIVE_KEYS = ("negative_prompt", "negative", "uc", "negativePrompt", "Negative prompt",
                 "full_negative_prompt")


@dataclass
class ParsedMeta:
    generator: str = "none"
    prompt: str = ""
    negative: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    loras: list[tuple[str, float]] = field(default_factory=list)
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def model(self) -> str | None:
        m = self.params.get("model")
        return str(m) if m not in (None, "") else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["model"] = self.model
        return d


# ------------------------------------------------------------------ helpers

def _coerce(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"[+-]?\d+", s):
            try:
                return int(s)
            except ValueError:
                return s
        if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)(e[+-]?\d+)?", s, re.I):
            return float(s)
        return s
    return v


def _snake(key: str) -> str:
    return re.sub(r"[^\w]+", "_", key.strip().lower()).strip("_")


def normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None or isinstance(v, (dict, list)) and not v:
            continue
        nk = STANDARD_KEYS.get(k.strip().lower(), _snake(k))
        if isinstance(v, dict) and nk == "model":
            v = v.get("model_name") or v.get("name") or v.get("key") or json.dumps(v)
        out.setdefault(nk, _coerce(v) if not isinstance(v, (dict, list)) else v)
    size = out.pop("size", None)
    if isinstance(size, str) and "x" in size and "width" not in out:
        w, _, h = size.partition("x")
        out["width"], out["height"] = _coerce(w), _coerce(h)
    return out


def _json(text: str) -> Any:
    if not text or text.lstrip()[:1] not in "{[":
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):  # e.g. [{"prompt": "...", "weight": 1}]
        return ", ".join(_as_text(x.get("prompt") if isinstance(x, dict) else x) for x in v)
    if isinstance(v, dict):
        for k in ("caption", "base_caption", "prompt", "text"):
            if k in v:
                return _as_text(v[k])
    return str(v)


# ------------------------------------------------------------- A1111 / Forge

RE_PARAM = re.compile(r'\s*(\w[\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')


def parse_a1111_text(text: str) -> tuple[str, str, dict[str, Any]]:
    lines = text.strip().replace("\r\n", "\n").split("\n")
    params_line = ""
    if lines and len(RE_PARAM.findall(lines[-1])) >= 3:
        params_line = lines.pop()
    pos: list[str] = []
    neg: list[str] = []
    in_neg = False
    for line in lines:
        if line.startswith("Negative prompt:"):
            in_neg = True
            line = line[len("Negative prompt:"):].strip()
        (neg if in_neg else pos).append(line)
    params: dict[str, Any] = {}
    for k, v in RE_PARAM.findall(params_line):
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            try:
                v = json.loads(v)
            except ValueError:
                v = v[1:-1]
        params[k.strip()] = v
    return "\n".join(pos).strip(), "\n".join(neg).strip(), params


KNOWN_GENERATORS = [
    ("gemini", ["gemini"]),
    ("imagen", ["imagen"]),
    ("midjourney", ["midjourney"]),
    ("dall-e", ["dall-e", "dalle"]),
    ("novelai", ["novelai"]),
    ("invokeai", ["invokeai", "invoke"]),
    ("fooocus", ["fooocus"]),
    ("forge", ["forge"]),
    ("comfyui", ["comfyui", "comfy"]),
    ("firefly", ["firefly"]),
]


def detect_generator_name(text: dict[str, str], raw_params: dict[str, Any] | None = None) -> str | None:
    comb = " ".join([
        text.get("Software", ""),
        text.get("software", ""),
        text.get("Source", ""),
        text.get("source", ""),
        text.get("Generator", ""),
        text.get("generator", ""),
        text.get("Author", ""),
        str(text.get("Comment", "")),
        str((raw_params or {}).get("model", "")),
        str((raw_params or {}).get("generator", "")),
    ]).lower()

    for gen, patterns in KNOWN_GENERATORS:
        if any(p in comb for p in patterns):
            return gen

    sw = text.get("Software") or text.get("software") or ""
    if sw.strip():
        return _snake(sw)
    return None


def parse_a1111(text: dict[str, str]) -> ParsedMeta | None:
    value = text.get("parameters")
    if not value:
        return None
    detected = detect_generator_name(text)
    data = _json(value)
    if isinstance(data, dict):  # Fooocus / others store JSON in "parameters"
        meta = parse_generic_json(data)
        meta.generator = detected or ("fooocus" if text.get("fooocus_scheme") or "Fooocus" in value else "json")
        return meta

    prompt, negative, params = parse_a1111_text(value)
    has_a1111_params = bool(
        params.get("Steps") or params.get("Sampler") or params.get("Seed") or
        params.get("CFG scale") or params.get("Version")
    )

    # If explicitly identified as Gemini, Midjourney, etc., or plain prompt without A1111 parameters
    if detected and detected not in ("a1111", "forge") and not (has_a1111_params and "Version" in params):
        meta = ParsedMeta(detected, prompt, negative, normalize_params(params))
        meta.loras = extract_loras(prompt)
        return meta

    # If there are no A1111 parameters or negative prompt (just plain prompt text stored in 'parameters'):
    if not has_a1111_params and not negative:
        gen = detected or "generic"
        meta = ParsedMeta(gen, prompt, negative, normalize_params(params))
        meta.loras = extract_loras(prompt)
        return meta

    version = str(params.get("Version", ""))
    gen = "forge" if version.startswith("f") else "a1111"
    if "ComfyUI" in version:
        gen = "comfyui"
    elif detected:
        gen = detected
    meta = ParsedMeta(gen, prompt, negative, normalize_params(params))
    meta.loras = extract_loras(prompt)
    return meta


# ------------------------------------------------------------------ ComfyUI

SAMPLER_HINTS = ("ksampler", "sampler")
TEXT_INPUT_KEYS = ("text", "text_g", "prompt", "positive", "string", "value", "text_l", "wildcard_text",
                   "populated_text")


def _is_link(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int)) and isinstance(v[1], int)


def _resolve_text(graph: dict, node_id: Any, polarity: str, depth: int = 0) -> str:
    node = graph.get(str(node_id))
    if not node or depth > 25:
        return ""
    inputs = node.get("inputs", {})
    for key in TEXT_INPUT_KEYS:
        v = inputs.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if _is_link(v):
            t = _resolve_text(graph, v[0], polarity, depth + 1)
            if t:
                return t
    # follow conditioning-like links (ControlNet, combine, concat, guidance ...)
    for key, v in inputs.items():
        if not _is_link(v):
            continue
        k = key.lower()
        if k == polarity or "conditioning" in k or k in ("cond", "base", "guider"):
            t = _resolve_text(graph, v[0], polarity, depth + 1)
            if t:
                return t
    return ""


def parse_comfy_graph(graph: dict) -> ParsedMeta:
    meta = ParsedMeta("comfyui")
    params: dict[str, Any] = {}
    samplers = [(nid, n) for nid, n in graph.items()
                if isinstance(n, dict) and any(h in str(n.get("class_type", "")).lower() for h in SAMPLER_HINTS)
                and ("positive" in n.get("inputs", {}) or "guider" in n.get("inputs", {}))]
    if samplers:
        _, sampler = samplers[0]
        inp = sampler.get("inputs", {})
        if _is_link(inp.get("positive")):
            meta.prompt = _resolve_text(graph, inp["positive"][0], "positive")
        if _is_link(inp.get("negative")):
            meta.negative = _resolve_text(graph, inp["negative"][0], "negative")
        if not meta.prompt and _is_link(inp.get("guider")):
            meta.prompt = _resolve_text(graph, inp["guider"][0], "positive")
            g = graph.get(str(inp["guider"][0]), {}).get("inputs", {})
            if _is_link(g.get("negative")):
                meta.negative = _resolve_text(graph, g["negative"][0], "negative")
        for k in ("seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"):
            if k in inp and not _is_link(inp[k]):
                params[k] = inp[k]
    if not meta.prompt:  # fallback: any text encoder
        texts = [n["inputs"].get("text") for n in graph.values()
                 if isinstance(n, dict) and "cliptextencode" in str(n.get("class_type", "")).lower()
                 and isinstance(n.get("inputs", {}).get("text"), str)]
        if texts:
            meta.prompt = texts[0]
            if len(texts) > 1:
                meta.negative = texts[1]
    for n in graph.values():
        if not isinstance(n, dict):
            continue
        ct = str(n.get("class_type", "")).lower()
        inp = n.get("inputs", {})
        if "checkpointloader" in ct or ct in ("unetloader", "unet loader"):
            name = inp.get("ckpt_name") or inp.get("unet_name")
            if isinstance(name, str):
                params.setdefault("model", name)
        elif "lora" in ct and isinstance(inp.get("lora_name"), str):
            w = inp.get("strength_model", 1.0)
            meta.loras.append((inp["lora_name"], float(w) if isinstance(w, (int, float)) else 1.0))
        elif "emptylatent" in ct or "emptysd3latent" in ct:
            for k in ("width", "height"):
                if isinstance(inp.get(k), int):
                    params.setdefault(k, inp[k])
        elif not params.get("seed") and isinstance(inp.get("noise_seed", inp.get("seed")), int) \
                and "noise" in ct:
            params["seed"] = inp.get("noise_seed", inp.get("seed"))
    meta.params = normalize_params(params)
    meta.loras += [l for l in extract_loras(meta.prompt) if l not in meta.loras]
    return meta


def parse_comfy_workflow(workflow: dict) -> ParsedMeta:
    meta = ParsedMeta("comfyui")
    texts = []
    for n in workflow.get("nodes", []):
        t = str(n.get("type", "")).lower()
        wv = n.get("widgets_values") or []
        if "cliptextencode" in t and wv and isinstance(wv[0], str):
            texts.append(wv[0])
        elif "checkpointloader" in t and wv and isinstance(wv[0], str):
            meta.params["model"] = wv[0]
    if texts:
        meta.prompt = texts[0]
        meta.negative = texts[1] if len(texts) > 1 else ""
    return meta


def parse_comfyui(text: dict[str, str]) -> ParsedMeta | None:
    graph = _json(text.get("prompt", ""))
    if isinstance(graph, dict) and any(isinstance(v, dict) and "class_type" in v for v in graph.values()):
        return parse_comfy_graph(graph)
    wf = _json(text.get("workflow", ""))
    if isinstance(wf, dict) and "nodes" in wf:
        return parse_comfy_workflow(wf)
    return None


# ------------------------------------------------------------------ NovelAI

def parse_novelai(text: dict[str, str]) -> ParsedMeta | None:
    comment = _json(text.get("Comment", ""))
    software = text.get("Software", "")
    if not (software.startswith("NovelAI") or (isinstance(comment, dict) and "uc" in comment)):
        return None
    comment = comment if isinstance(comment, dict) else {}
    meta = ParsedMeta("novelai")
    meta.prompt = _as_text(comment.get("prompt")) or text.get("Description", "")
    if not meta.prompt and isinstance(comment.get("v4_prompt"), dict):
        meta.prompt = _as_text(comment["v4_prompt"].get("caption"))
    meta.negative = _as_text(comment.get("uc"))
    params = {k: v for k, v in comment.items()
              if k not in ("prompt", "uc", "v4_prompt", "v4_negative_prompt") and not isinstance(v, (dict, list))}
    if text.get("Source"):
        params["model"] = text["Source"]
    meta.params = normalize_params(params)
    return meta


# ------------------------------------------------------------------ InvokeAI

def parse_invokeai(text: dict[str, str]) -> ParsedMeta | None:
    data = _json(text.get("invokeai_metadata", ""))
    if isinstance(data, dict):
        meta = parse_generic_json(data)
        meta.generator = "invokeai"
        return meta
    legacy = _json(text.get("sd-metadata", ""))
    if isinstance(legacy, dict):
        img = legacy.get("image", {}) or {}
        prompt = _as_text(img.get("prompt"))
        negative = ""
        m = re.match(r"^(.*?)\s*\[(.*)\]\s*$", prompt, re.S)
        if m:
            prompt, negative = m.group(1), m.group(2)
        params = {k: v for k, v in img.items() if k != "prompt" and not isinstance(v, (dict, list))}
        if legacy.get("model_weights"):
            params["model"] = legacy["model_weights"]
        return ParsedMeta("invokeai", prompt, negative, normalize_params(params))
    dream = text.get("Dream")
    if dream:
        m = re.match(r'^"(.*)"\s*(.*)$', dream, re.S)
        prompt, rest = (m.group(1), m.group(2)) if m else (dream, "")
        negative = ""
        nm = re.match(r"^(.*?)\s*\[(.*)\]\s*$", prompt, re.S)
        if nm:
            prompt, negative = nm.group(1), nm.group(2)
        flags = dict(re.findall(r"-(\w)\s+(\S+)", rest))
        names = {"s": "steps", "S": "seed", "W": "width", "H": "height", "C": "cfg_scale", "A": "sampler"}
        params = {names[k]: v for k, v in flags.items() if k in names}
        return ParsedMeta("invokeai", prompt, negative, normalize_params(params))
    return None


# ------------------------------------------------------------------ generic

def parse_generic_json(data: dict) -> ParsedMeta:
    meta = ParsedMeta("json")
    for k in PROMPT_KEYS:
        if data.get(k):
            meta.prompt = _as_text(data[k])
            break
    for k in NEGATIVE_KEYS:
        if data.get(k):
            meta.negative = _as_text(data[k])
            break
    used = set(PROMPT_KEYS) | set(NEGATIVE_KEYS)
    params = {k: v for k, v in data.items() if k not in used and (not isinstance(v, (dict, list)) or k == "model")}
    meta.params = normalize_params(params)
    loras = data.get("loras")
    if isinstance(loras, list):
        for l in loras:
            if isinstance(l, dict):
                name = l.get("lora", {}).get("model_name") if isinstance(l.get("lora"), dict) else l.get("name")
                if name:
                    meta.loras.append((str(name), float(l.get("weight", 1.0) or 1.0)))
            elif isinstance(l, (list, tuple)) and l:
                meta.loras.append((str(l[0]), float(l[1]) if len(l) > 1 else 1.0))
    meta.loras += [l for l in extract_loras(meta.prompt) if l not in meta.loras]
    return meta


def parse_generic(text: dict[str, str]) -> ParsedMeta | None:
    detected = detect_generator_name(text)
    # 1) any JSON value containing a prompt-like key
    for key, value in text.items():
        data = _json(value)
        if isinstance(data, dict) and any(k in data for k in PROMPT_KEYS):
            meta = parse_generic_json(data)
            meta.generator = detected or _software(text) or "json"
            return meta
    # 2) any value in A1111 format
    for key, value in text.items():
        if "Steps:" in value and ("Sampler:" in value or "Seed:" in value):
            p, n, params = parse_a1111_text(value)
            meta = ParsedMeta(detected or _software(text) or "a1111", p, n, normalize_params(params))
            meta.loras = extract_loras(p)
            return meta
    # 3) free text descriptions (Midjourney, Gemini, DALL-E exports, custom scripts)
    for key in ("Description", "description", "Comment", "comment", "prompt", "Prompt", "Title", "UserComment"):
        if text.get(key, "").strip():
            meta = ParsedMeta(detected or _software(text) or "generic", text[key].strip())
            meta.loras = extract_loras(meta.prompt)
            return meta
    return None


def _software(text: dict[str, str]) -> str:
    sw = text.get("Software") or text.get("software") or ""
    return _snake(sw.split()[0]) if sw.strip() else ""


PARSERS: list[Callable[[dict[str, str]], ParsedMeta | None]] = [
    parse_invokeai, parse_comfyui, parse_novelai, parse_a1111, parse_generic,
]


def parse_metadata(text: dict[str, str]) -> ParsedMeta:
    """Detect the format of PNG text chunks and return normalised metadata."""
    for parser in PARSERS:
        try:
            meta = parser(text)
        except Exception:  # never let one bad parser break a scan
            meta = None
        if meta is not None and (meta.prompt or meta.params):
            meta.raw = dict(text)
            meta.prompt = meta.prompt.strip()
            meta.negative = meta.negative.strip()
            return meta
    return ParsedMeta("none", raw=dict(text))
