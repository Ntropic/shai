# shai/cli.py
import argparse
import subprocess
import sys
import threading
import time
from typing import List, Tuple

from .config import load_settings
from .util.ansi import BOLD, DIM, GREEN, RED
from .util.shellparse import extract_commands, which_map
from .ui.table import ColSpec, grid_select
from .llm.suggest import request_suggestions

INSTALLERS = {"pacman","yay","paru","apt","dnf","zypper","brew","flatpak","snap"}

# ───────────────────────── spinner ─────────────────────────
def _spinner_run(stop: threading.Event, label: str = "processing"):
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not stop.is_set():
        sys.stdout.write(f"\r{label} {frames[i % len(frames)]}")
        sys.stdout.flush()
        time.sleep(0.1)
        i += 1
    # clear line
    sys.stdout.write("\r" + " " * (len(label) + 2) + "\r")
    sys.stdout.flush()

def _with_spinner(fn, label: str, *args, **kwargs):
    stop = threading.Event()
    t = threading.Thread(target=_spinner_run, args=(stop,label), daemon=True)
    t.start()
    try:
        return fn(*args, **kwargs)
    finally:
        stop.set()
        t.join()

# ─────────────────────── context helpers ───────────────────
def _stdin_capture(enabled=True):
    if not enabled:
        return ""
    if sys.stdin and not sys.stdin.isatty():
        data = sys.stdin.read()
        return "\n".join(l.rstrip() for l in data.splitlines())[-4000:]
    return ""

def _gather_context(cfg, recent_output: str = ""):
    import os, platform, shutil
    ctx = {
        "os": platform.system(),
        "shell": os.environ.get("SHELL"),
        "editor": os.environ.get("VISUAL") or os.environ.get("EDITOR"),
        "pm_order": cfg.pm_order,
        "stdin": _stdin_capture(cfg.use_stdin),
        "num_ctx": cfg.num_ctx,
        "recent_output": recent_output[-4000:] if recent_output else "",
    }
    ctx["package_managers"] = [pm for pm in cfg.pm_order if shutil.which(pm)]
    return ctx

def _is_installer_command(cmd: str) -> bool:
    bins = extract_commands(cmd)
    return bool(bins and bins[0] in INSTALLERS)

# ───────────────────── suggestions + rows ──────────────────
def _get_suggestions(model: str, query: str, n: int, ctx: dict, num_ctx: int, use_spinner: bool):
    if use_spinner:
        return _with_spinner(request_suggestions, "processing", model, query, n, ctx, num_ctx)
    return request_suggestions(model, query, n, ctx, num_ctx)

def _build_rows(suggestions, show_explain: bool):
    """Return rows (plain text only) and a parallel list of missing-tools lists."""
    rows: List[Tuple[str, ...]] = []
    misslists: List[List[str]] = []
    for s in suggestions:
        missing = [b for b, p in (s.requires or {}).items() if not p]
        misslists.append(missing)
        if show_explain:
            status = ("missing: " + ", ".join(missing)) if missing else "all tools present"
            if s.explanation_min:
                status = f"{status}\n{s.explanation_min}"
            rows.append((s.command, status))
        else:
            message = s.command
            if missing:
                message += f"\nmissing: {', '.join(missing)}"
            rows.append((message,))
    return rows, misslists

# ────────────────────── curses styling fns ─────────────────
def style_cell(row, col, text):
    """
    Return curses attributes for a given cell.
    We set pairs in grid_select: 2=cyan, 3=green, 4=red.
    """
    import curses
    if col == 0:
        return curses.color_pair(2)  # command in cyan
    if col == 1:
        t = (text or "").lower()
        if t.startswith("missing:"):
            return curses.color_pair(4) | curses.A_BOLD
        if t.startswith("all tools present"):
            return curses.color_pair(3)
    return 0

def style_line(row, col, line_idx, line_text):
    """Dim explanation lines (line >= 1) in the status column."""
    import curses
    if col == 1 and line_idx >= 1:
        return curses.A_DIM
    return 0

# ─────────────────────────── CLI main ──────────────────────
def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shai", description="Natural language → CLI suggestions via Ollama.")
    ap.add_argument("query", nargs="*", help="what you want to do (natural language)")
    ap.add_argument("-n","--num", type=int, help="number of suggestions")
    ap.add_argument("-e","--explain", action="store_true", help="show explanations")
    ap.add_argument("--no-explain", action="store_true", help="hide explanations")
    ap.add_argument("--model")
    ap.add_argument("--ctx", type=int, help="override context window (num_ctx)")
    args = ap.parse_args(argv)

    cfg = load_settings()

    model   = args.model or cfg.model
    num     = cfg.n_suggestions if args.num is None else max(1, args.num)
    num_ctx = cfg.num_ctx if args.ctx is None else max(512, int(args.ctx))

    if args.explain and args.no_explain: show_explain = True
    elif args.explain:                   show_explain = True
    elif args.no_explain:                show_explain = False
    else:                                show_explain = cfg.explain

    query = " ".join(args.query).strip()
    if not query:
        print("Example: shai -n 3 --ctx 8192 'find big .log files and summarize'"); return 1

    # 1st suggestion pass
    recent_output = ""
    ctx = _gather_context(cfg, recent_output)
    suggestions = _get_suggestions(model, query, num, ctx, num_ctx, use_spinner=True)
    if not suggestions:
        print(RED("No suggestions returned.")); return 2

    while True:
        rows, misslists = _build_rows(suggestions, show_explain)

        # columns
        if show_explain:
            colspecs = [
                ColSpec(header="Command", min_width=20, wrap=False, ellipsis=True),
                ColSpec(header="Status / Explanation", min_width=20, wrap=True),
            ]
        else:
            colspecs = [ColSpec(header="Command", min_width=20, wrap=True)]

        def menu_for_row(i: int): return ["Execute", "Explain", "Comment", "Execute → continue"]

        action, row_idx, sub_idx = grid_select(
            rows, colspecs,
            row_menu_provider=menu_for_row,
            submenu_cols=3,
            title=" Suggestions ",
            style_fn=style_cell,
            line_style_fn=style_line,
        )

        if action in ("quit",) or row_idx is None:
            print(DIM("Done.")); return 0

        chosen = suggestions[row_idx]
        missing = misslists[row_idx]

        # ── submenu actions
        if action == "submenu-selected":
            # Execute or Execute → continue
            if sub_idx in (0, 3):
                if missing:
                    print(RED(f"\nMissing tools: {', '.join(missing)}"))
                    print(DIM("Package install suggestions not yet implemented."))
                    input(DIM("\nPress Enter to return…"))
                    continue

                # Run command
                if sub_idx == 3:
                    # Execute → continue: capture output, then recompute suggestions
                    print(BOLD("Running: ") + chosen.command + "\n")
                    run = subprocess.run(chosen.command, shell=True, text=True,
                                         capture_output=True)
                    out = (run.stdout or "") + (("\n" + run.stderr) if run.stderr else "")
                    # refresh requires (in case execution installed something)
                    chosen.requires = which_map(list((chosen.requires or {}).keys()))
                    # recompute suggestions with updated context
                    ctx = _gather_context(cfg, out)
                    suggestions = _get_suggestions(model, query, num, ctx, num_ctx, use_spinner=True)
                    if not suggestions:
                        print(RED("No new suggestions returned.")); return run.returncode
                    # loop back to show updated table
                    continue
                else:
                    # plain Execute: run and exit unless it's an installer (then return)
                    print(BOLD("Running: ") + chosen.command + "\n")
                    rc = subprocess.call(chosen.command, shell=True)
                    if _is_installer_command(chosen.command):
                        input(DIM("\nPress Enter to return…"))
                        # recompute suggestions after installer (maybe tools now present)
                        ctx = _gather_context(cfg, "")
                        suggestions = _get_suggestions(model, query, num, ctx, num_ctx, use_spinner=True)
                        continue
                    return rc

            # Explain
            if sub_idx == 1:
                print(BOLD("\nExplanation:"))
                if chosen.explanation_min:
                    print(chosen.explanation_min)
                else:
                    print(DIM("(no explanation provided by the model)"))
                print(BOLD("\nTools:"))
                for b,p in (chosen.requires or {}).items():
                    print(f"  {b:10} {'✓ '+p if p else '✗ missing'}")
                input(DIM("\nPress Enter to return…"))
                continue

            # Comment
            if sub_idx == 2:
                try:
                    note = input("\nYour comment about this suggestion: ").strip()
                except KeyboardInterrupt:
                    note = ""
                if note:
                    print(GREEN("Thanks! Comment captured (stdout)."))
                else:
                    print(DIM("No comment entered."))
                input(DIM("\nPress Enter to return…"))
                continue

        # fallback: whole-row select executes
        if action == "row-selected":
            print(BOLD("Running: ") + chosen.command + "\n")
            return subprocess.call(chosen.command, shell=True)

if __name__ == "__main__":
    raise SystemExit(main())

