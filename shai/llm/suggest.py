from dataclasses import dataclass
from typing import List, Dict, Any
import json, os

from ..util.shellparse import extract_commands, which_map
import urllib.request

try:
    import ollama as pyollama
    HAS_OLLAMA = True
except Exception:
    pyollama = None
    HAS_OLLAMA = False

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

def is_ollama_running(host: str = OLLAMA_HOST) -> bool:
    try:
        if HAS_OLLAMA:
            pyollama.list()
            return True
        urllib.request.urlopen(host + "/api/tags", timeout=2)
        return True
    except Exception:
        return False

def ensure_ollama_running():
    if not is_ollama_running():
        raise RuntimeError(
            f"Ollama is not running at {OLLAMA_HOST}.\n"
            "Start it with 'ollama serve' or see https://ollama.ai for installation instructions."
        )

@dataclass
class Suggestion:
    command: str
    explanation_min: str = ""
    requires: Dict[str, str] | None = None

DEFAULT_SYSTEM_PROMPT = (
    "You are a Linux CLI assistant.\n"
    "Return STRICT JSON:\n\n"
    "{\"suggestions\":[\n"
    "  {\"command\":\"...\", \"explanation_min\":\"...\"},\n"
    "  {\"command\":\"...\", \"explanation_min\":\"...\"}\n"
    "]}\n\n"
    "Rules:\n"
    "- Exactly N suggestions (provided in the user's JSON).\n"
    "- Single-line commands. Prefer safe flags (--dry-run, -n) where possible.\n"
    "- Prefer tools detected in context; if uncommon tool is useful, still use it."
)

def _chat(model: str, messages: list, num_ctx: int, force_json: bool = True) -> str:
    options = {"num_ctx": int(num_ctx)}
    if HAS_OLLAMA:
        kwargs = {"model": model, "messages": messages, "options": options}
        if force_json: kwargs["format"] = "json"
        r = pyollama.chat(**kwargs)
        return r.get("message", {}).get("content", "")
    # HTTP fallback
    import urllib.request
    body = {"model": model, "messages": messages, "options": options}
    if force_json: body["format"] = "json"
    req = urllib.request.Request(OLLAMA_HOST + "/api/chat",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("message", {}).get("content", "")

def _strip_code_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)
        if len(s) >= 3:
            return s[1].split("\n", 1)[-1] if s[1].startswith(("bash","sh")) else s[1]
    return s

def request_suggestions(model: str, query: str, n: int, context: Dict[str,Any], num_ctx: int,
                        system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> List[Suggestion]:
    user_payload = {"N": n, "USER_QUERY": query, "CONTEXT": context}
    messages = [{"role":"system","content": system_prompt},
                {"role":"user","content": json.dumps(user_payload, ensure_ascii=False)}]
    raw = _chat(model, messages, num_ctx, True)

    out: List[Suggestion] = []
    # JSON-first
    try:
        data = json.loads(raw)
        items = data.get("suggestions", [])[:n]
        for it in items:
            cmd = _strip_code_fences((it or {}).get("command","")).strip()
            if not cmd: continue
            sug = Suggestion(command=cmd, explanation_min=(it or {}).get("explanation_min","").strip())
            bins = extract_commands(sug.command)
            sug.requires = which_map(bins)
            out.append(sug)
        if out:
            return out
    except Exception:
        pass

    # fallback: parse code fences/plain lines
    text = _strip_code_fences(raw)
    lines = [l.strip("` ").strip() for l in text.splitlines() if l.strip()]
    for l in lines[:n]:
        if not l: continue
        sug = Suggestion(command=l, explanation_min="")
        sug.requires = which_map(extract_commands(l))
        out.append(sug)
    return out


def explain_parts(model: str, command: str, num_ctx: int) -> List[tuple[str, str]]:
    """Break a shell command into components and explain each."""
    prompt = (
        "You are a Linux CLI assistant.\n"
        "Given a shell command, break it into the executable and each flag or arg.\n"
        "Return STRICT JSON: {\"parts\":[{\"text\":\"ls\",\"desc\":\"list directory contents\"}]}"
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": command},
    ]
    raw = _chat(model, messages, num_ctx, True)
    try:
        data = json.loads(raw)
        parts = []
        for p in data.get("parts", []):
            text = (p or {}).get("text", "").strip()
            if not text:
                continue
            parts.append((text, (p or {}).get("desc", "").strip()))
        return parts
    except Exception:
        return []
