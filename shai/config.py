from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os

# ---------- tiny TOML-ish parser ----------
def _parse_tomlish(text: str) -> dict:
    data, section = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            data.setdefault(section, {})
            continue
        if "=" not in line:
            continue
        k, v = [s.strip() for s in line.split("=", 1)]
        # booleans / numbers / strings
        if v.lower() in ("true", "false"):
            val = (v.lower() == "true")
        elif v.startswith(("'", '"')) and v.endswith(("'", '"')):
            val = v[1:-1]
        else:
            try:
                val = float(v) if "." in v else int(v)
            except Exception:
                val = v
        if section:
            data[section][k] = val
        else:
            data[k] = val
    return data

def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

DEFAULT_TEXT = """\
# shai config
[model]
name = "qwen2.5-coder:3b"
ctx = 8192   # context window tokens for the model

[suggestions]
n = 3
explain = false

[ui]
spinner = true

[context]
history_lines = 30
use_stdin = true

[pm]
order = "pacman,apt,dnf,zypper,brew,flatpak,snap,yay,paru"
"""

@dataclass
class Settings:
    model: str = "qwen2.5-coder:3b"
    num_ctx: int = 8192                 # << only knob for LLM now
    n_suggestions: int = 3
    explain: bool = False
    spinner: bool = True
    submenu_cols: int = 3
    history_lines: int = 30
    use_stdin: bool = True
    pm_order: list[str] | None = None

def _config_path() -> Path:
    env = os.environ.get("SHAI_CONFIG")
    if env:
        return Path(env)
    base = _xdg_config_home() / "shai"
    cfg = base / "config"
    toml = base / "config.toml"
    return cfg if cfg.exists() or not toml.exists() else toml

def ensure_default_config() -> Path:
    base = _xdg_config_home() / "shai"
    base.mkdir(parents=True, exist_ok=True)
    cfg = base / "config"
    if not cfg.exists() and not (base / "config.toml").exists():
        cfg.write_text(DEFAULT_TEXT, encoding="utf-8")
    return cfg

def load_settings() -> Settings:
    ensure_default_config()
    p = _config_path()
    text = p.read_text(encoding="utf-8") if p.exists() else DEFAULT_TEXT
    d = _parse_tomlish(text)

    s = Settings()

    m = d.get("model", {})
    s.model = str(m.get("name", s.model))
    try: s.num_ctx = int(m.get("ctx", s.num_ctx))
    except Exception: pass

    sg = d.get("suggestions", {})
    try: s.n_suggestions = int(sg.get("n", s.n_suggestions))
    except Exception: pass
    s.explain = bool(sg.get("explain", s.explain))

    ui = d.get("ui", {})
    s.spinner = bool(ui.get("spinner", s.spinner))
    try: s.submenu_cols = int(ui.get("submenu_cols", s.submenu_cols))
    except Exception: pass

    cx = d.get("context", {})
    try: s.history_lines = int(cx.get("history_lines", s.history_lines))
    except Exception: pass
    s.use_stdin = bool(cx.get("use_stdin", s.use_stdin))

    pm = d.get("pm", {})
    order = pm.get("order", "")
    if isinstance(order, str) and order.strip():
        s.pm_order = [x.strip() for x in order.split(",") if x.strip()]
    else:
        s.pm_order = ["pacman","apt","dnf","zypper","brew","flatpak","snap","yay","paru"]

    return s
