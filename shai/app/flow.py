# shai/app/flow.py
from __future__ import annotations
import os, platform, shutil, subprocess, sys, threading, time
from typing import List, Tuple, Dict, Any, Callable

from ..llm.suggest import request_suggestions, Suggestion
from ..util.shellparse import extract_commands, which_map

INSTALLERS = {"pacman","yay","paru","apt","dnf","zypper","brew","flatpak","snap"}

# ───────── spinner ─────────
def _spinner_run(stop: threading.Event, label: str = "processing"):
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not stop.is_set():
        sys.stdout.write(f"\r{label} {frames[i % len(frames)]}")
        sys.stdout.flush()
        time.sleep(0.1); i += 1
    sys.stdout.write("\r" + " "*(len(label)+8) + "\r"); sys.stdout.flush()

def with_spinner(fn, label: str, *args, **kwargs):
    stop = threading.Event()
    t = threading.Thread(target=_spinner_run, args=(stop,label), daemon=True)
    t.start()
    try:
        return fn(*args, **kwargs)
    finally:
        stop.set(); t.join()

# ─────── context ───────
def _stdin_capture(enabled=True):
    if not enabled: return ""
    if sys.stdin and not sys.stdin.isatty():
        data = sys.stdin.read()
        return "\n".join(l.rstrip() for l in data.splitlines())[-4000:]
    return ""

def gather_context(cfg,
                   recent_output: str = "",
                   previous_query: str = "",
                   last_executed: str = "",
                   last_suggested: str = "",
                   followup: str = "") -> Dict[str, Any]:
    """Everything here becomes JSON for the model."""
    ctx = {
        "os": platform.system(),
        "shell": os.environ.get("SHELL"),
        "editor": os.environ.get("VISUAL") or os.environ.get("EDITOR"),
        "pm_order": cfg.pm_order,
        "stdin": _stdin_capture(cfg.use_stdin),
        "num_ctx": cfg.num_ctx,
        "previous_query": previous_query,
        "last_executed": last_executed,
        "last_suggested": last_suggested,
        "recent_output": (recent_output or "")[-4000:],
        "user_followup": followup,
    }
    ctx["package_managers"] = [pm for pm in cfg.pm_order if shutil.which(pm)]
    return ctx

def is_installer_command(cmd: str) -> bool:
    bins = extract_commands(cmd)
    return bool(bins and bins[0] in INSTALLERS)

# ───── suggestions / ranking ─────
def fetch_suggestions(model: str, query: str, n: int, ctx: dict, num_ctx: int, system_prompt: str, spinner=True) -> List[Suggestion]:
    if spinner:
        return with_spinner(request_suggestions, "processing", model, query, n, ctx, num_ctx, system_prompt)
    return request_suggestions(model, query, n, ctx, num_ctx, system_prompt)

def stream_suggestions(model: str, query: str, n: int, ctx: dict, num_ctx: int,
                       system_prompt: str, callback: Callable[[Suggestion], None],
                       spinner: bool = True) -> List[Suggestion]:
    collected: List[Suggestion] = []
    for i in range(max(0, n)):
        batch = fetch_suggestions(model, query, 1, ctx, num_ctx, system_prompt, spinner and i == 0)
        if not batch:
            break
        s = batch[0]
        setattr(s, "_is_new", True)
        collected.append(s)
        try:
            callback(s)
        except Exception:
            pass
    return collected

def annotate_requires(s: Suggestion) -> Tuple[List[str], int, int]:
    req = s.requires or {}
    missing = [b for b, p in req.items() if not p]
    return missing, len(missing), len(s.command)

def append_new(old_items: List[Suggestion], new_items: List[Suggestion]) -> List[Suggestion]:
    for s in new_items:
        setattr(s, "_is_new", True)
    return old_items + new_items

# ───── rows for table ─────
def build_rows(suggestions: List[Suggestion], show_explain: bool):
    """
    Returns:
      rows: tuples for table
      misslists: per-row list of missing binaries
      new_flags: per-row bool indicating newly added
    """
    rows, misslists, new_flags = [], [], []
    for s in suggestions:
        missing, _, _ = annotate_requires(s)
        misslists.append(missing)
        new_flags.append(bool(getattr(s, "_is_new", False)))
        status = "✓✓" if not missing else "\n".join(f"✗ {b}" for b in missing)
        if show_explain:
            expl = s.explanation_min or ""
            rows.append((s.command, status, expl))
        else:
            rows.append((s.command, status))
    return rows, misslists, new_flags

# ───── per-cell styling hooks ─────
def make_style_functions(new_flags: List[bool]):
    import curses
    def style_cell(row, col, text):
        if col == 0:  # command
            if 0 <= row < len(new_flags) and new_flags[row]:
                return curses.color_pair(6) | curses.A_BOLD
            return curses.color_pair(2)
        if col == 1:  # status
            t = (text or "")
            if t.strip().startswith("✓"):
                return curses.color_pair(3)
            if "✗" in t:
                return curses.color_pair(4) | curses.A_BOLD
        if col == 2:  # explanation
            return curses.A_DIM
        return 0
    def style_line(row, col, line_idx, line_text):
        return 0
    return style_cell, style_line

# ───── streaming runner ─────
def run_and_capture(cmd: str) -> tuple[int, str]:
    """
    Stream stdout/stderr to terminal AND capture for context.
    Returns (exit_code, captured_output).
    """
    proc = subprocess.Popen(
        cmd, shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True
    )
    out_lines = []
    try:
        for line in proc.stdout:
            print(line, end="")   # live stream
            out_lines.append(line)
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        return proc.returncode, "".join(out_lines)
    proc.wait()
    return proc.returncode, "".join(out_lines)

def refresh_requires(s: Suggestion):
    s.requires = which_map(list((s.requires or {}).keys()))

