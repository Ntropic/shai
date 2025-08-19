# shai/cli.py
from __future__ import annotations
import argparse, os, shutil, re
from typing import List

import readline
from .config import load_settings, add_ignored, get_ignored
from .ui.table import ColSpec, grid_select
from .pm.install_ui import offer_installs_for_missing
from .app.flow import (
    gather_context, build_rows, make_style_functions,
    is_installer_command, run_and_capture, refresh_requires,
    stream_suggestions, append_new,
)
from .llm.suggest import ensure_ollama_running, explain_parts
from .util.ansi import visible_len, crop_visible, RED, YELLOW

BASH_HISTFILE = os.path.expanduser(os.environ.get("HISTFILE", "~/.bash_history"))
HISTFILE = os.path.expanduser("~/.shai_history")
try:
    readline.read_history_file(HISTFILE)
except Exception:
    pass


def line(text: str) -> str:
    """Return a centered dashed line with text spanning the terminal width."""
    width = shutil.get_terminal_size((80, 20)).columns
    pad = max(0, width - visible_len(text) - 2)
    left = pad // 2
    right = pad - left
    return "-" * left + f" {text} " + "-" * right

def highlight_risks(text: str, caution: list[str], danger: list[str]) -> str:
    for cmd in danger:
        text = re.sub(rf"\b{re.escape(cmd)}\b", RED(cmd), text)
    for cmd in caution:
        text = re.sub(rf"\b{re.escape(cmd)}\b", YELLOW(cmd), text)
    return text

def prompt_edit(cmd: str) -> str:
    """Pre-fill input with command and allow user to edit before execution."""
    os.system("clear")
    def hook():
        readline.insert_text(cmd)
        readline.redisplay()
    readline.set_startup_hook(hook)
    try:
        new = input("$ ")
    except KeyboardInterrupt:
        new = cmd
    finally:
        readline.set_startup_hook(None)
    if not new.strip():
        new = cmd
    readline.add_history(new)
    try:
        readline.write_history_file(HISTFILE)
    except Exception:
        pass
    return new

def append_shell_history(cmd: str) -> None:
    try:
        with open(BASH_HISTFILE, "a", encoding="utf-8") as f:
            f.write(cmd + "\n")
    except Exception:
        pass

def center_input(prompt: str) -> str:
    os.system("clear")
    rows, cols = shutil.get_terminal_size((80,20))
    print("\n" * max(0, rows//2 -1), end="", flush=True)
    print(prompt.center(cols))
    pad = cols // 2
    try:
        return input(" " * pad)
    except KeyboardInterrupt:
        return ""

def ask_followup() -> str:
    """Centered prompt for follow-up commands."""
    return center_input("What do you want to do next?").strip()

def classify_risk(cmd: str, caution: list[str], danger: list[str]) -> str:
    parts = cmd.strip().split()
    if not parts:
        return "none"
    if parts[0] in danger:
        return "danger"
    if parts[0] in caution:
        return "caution"
    return "none"

def confirm_risk(cmd: str, risk: str, caution: list[str], danger: list[str]) -> bool:
    title = line(f"Confirm {shorten(cmd)}")
    rows = [("[ Back ]",), ("[ Proceed ]",)]
    cs = [ColSpec(header="", min_width=30, wrap=False)]
    action, idx, _ = grid_select(rows, cs, title=title)
    return action == "row-selected" and idx == 1

def shorten(cmd: str, length: int = 40) -> str:
    return cmd if visible_len(cmd) <= length else crop_visible(cmd, length-3) + "..."

def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="shai",
        description="Natural language → CLI suggestions via Ollama.",
        epilog="Ensure the Ollama service is running (start with 'ollama serve').",
    )
    ap.add_argument("query", nargs="*", help="what you want to do (natural language)")
    ap.add_argument("-n","--num", type=int, help="number of suggestions")
    ap.add_argument("-e","--explain", action="store_true", help="show explanations")
    ap.add_argument("--no-explain", action="store_true", help="hide explanations")
    ap.add_argument("--model")
    ap.add_argument("--ctx", type=int, help="override context window (num_ctx)")
    ap.add_argument("-u","--unsafe", action="store_true", help="allow dangerous commands without confirm")
    args = ap.parse_args(argv)

    cfg = load_settings()
    try:
        ensure_ollama_running()
    except RuntimeError as e:
        print(e)
        return 1
    hl = lambda s: highlight_risks(s, cfg.caution_cmds or [], cfg.dangerous_cmds or [])
    model   = args.model or cfg.model
    num     = cfg.n_suggestions if args.num is None else max(1, args.num)
    num_ctx = cfg.num_ctx if args.ctx is None else max(512, int(args.ctx))
    if args.explain and args.no_explain: show_explain = True
    elif args.explain:                   show_explain = True
    elif args.no_explain:                show_explain = False
    else:                                show_explain = cfg.explain
    system_prompt = cfg.system_prompt
    if not show_explain:
        system_prompt = "\n".join(l for l in system_prompt.splitlines() if "explanation_min" not in l)

    query = " ".join(args.query).strip()
    if not query:
        query = center_input("What do you want to do?").strip()
        if not query:
            return 1

    ctx = gather_context(cfg, previous_query=query)

    suggestions = stream_suggestions(model, query, num, ctx, num_ctx, system_prompt, None, cfg.spinner)
    rows, misslists, new_flags = build_rows(suggestions, show_explain)
    risk_flags = [classify_risk(s.command, cfg.caution_cmds or [], cfg.dangerous_cmds or []) for s in suggestions]
    header = line("Suggestions")

    while True:
        if show_explain:
            colspecs = [
                ColSpec(header="Command",     min_width=56, wrap=False, ellipsis=True),
                ColSpec(header="Status",      min_width=12, wrap=True),
                ColSpec(header="Explanation", min_width=24, wrap=True),
            ]
        else:
            colspecs = [
                ColSpec(header="Command",     min_width=56, wrap=False, ellipsis=True),
                ColSpec(header="Status",      min_width=16, wrap=True),
            ]
        style_cell, style_line = make_style_functions(new_flags, risk_flags)

        def menu_for_row(i: int):
            return ["Execute", "Comment", "Exec → Continue", "Explain", "Variations", "Back"]

        action, row_idx, sub_idx = grid_select(
            rows, colspecs,
            row_menu_provider=menu_for_row,
            submenu_cols=cfg.submenu_cols,
            title=header,
            style_fn=style_cell,
            line_style_fn=style_line,
        )

        if action in ("quit",) or row_idx is None:
            print("\x1b[2mDone.\x1b[0m"); return 0

        chosen = suggestions[row_idx]
        missing = misslists[row_idx]

        # Execute / Exec → Continue
        if action == "submenu-selected" and sub_idx in (0, 2):
            while True:
                missing = [b for b in missing if b not in (cfg.ignored_bins or [])]
                if missing:
                    proceed, installed_any = offer_installs_for_missing(missing, cfg.pm_order, cfg.n_suggestions, add_ignored)
                    cfg.ignored_bins = get_ignored()
                    if not proceed:
                        break
                    if installed_any:
                        refresh_requires(chosen)
                        missing = [b for b, p in (chosen.requires or {}).items() if not p]
                        continue
                cmd_to_run = prompt_edit(chosen.command)
                risk = classify_risk(cmd_to_run, cfg.caution_cmds or [], cfg.dangerous_cmds or [])
                if (not args.unsafe) and risk != "none":
                    if not confirm_risk(cmd_to_run, risk, cfg.caution_cmds or [], cfg.dangerous_cmds or []):
                        break
                rc, out = run_and_capture(cmd_to_run)
                append_shell_history(cmd_to_run)
                refresh_requires(chosen)
                if sub_idx == 0:
                    if is_installer_command(cmd_to_run):
                        ctx = gather_context(cfg, previous_query=query)
                        suggestions = stream_suggestions(model, query, num, ctx, num_ctx, system_prompt, None, cfg.spinner)
                        rows, misslists, new_flags = build_rows(suggestions, show_explain)
                        risk_flags = [classify_risk(s.command, cfg.caution_cmds or [], cfg.dangerous_cmds or []) for s in suggestions]
                        header = line("Suggestions")
                        break
                    return rc
                follow = ask_followup()
                ctx = gather_context(
                    cfg,
                    recent_output=out,
                    previous_query=query,
                    last_executed=cmd_to_run,
                    followup=follow,
                )
                new_sugs = stream_suggestions(model, query, num, ctx, num_ctx, system_prompt, None, cfg.spinner)
                suggestions = append_new(suggestions, new_sugs)
                rows, misslists, new_flags = build_rows(suggestions, show_explain)
                risk_flags = [classify_risk(s.command, cfg.caution_cmds or [], cfg.dangerous_cmds or []) for s in suggestions]
                header = line(f"Suggestions (Follow up to {shorten(cmd_to_run)})")
                break
            continue

        # Explain
        if action == "submenu-selected" and sub_idx == 3:
            os.system("clear")
            print(line("Explain"))
            print(hl(chosen.command) + "\n")
            if getattr(chosen, "explanation_min", ""):
                print(hl(chosen.explanation_min) + "\n")
            parts = explain_parts(model, chosen.command, num_ctx, cfg.explain_prompt) if args.explain else []
            for t, d in parts:
                print(f"- {hl(t)}: {hl(d)}")
            input("\n[ Back ]")
            os.system("clear")
            header = line("Suggestions")
            continue

        # Comment
        if action == "submenu-selected" and sub_idx == 1:
            note = center_input("Your comment / correction:").strip()
            ctx = gather_context(
                cfg,
                previous_query=query,
                last_suggested=chosen.command,
                followup=note,
            )
            new_sugs = stream_suggestions(model, query, num, ctx, num_ctx, system_prompt, None, cfg.spinner)
            for s in new_sugs:
                setattr(s, "_is_new", True)
            suggestions = new_sugs
            rows, misslists, new_flags = build_rows(suggestions, show_explain)
            risk_flags = [classify_risk(s.command, cfg.caution_cmds or [], cfg.dangerous_cmds or []) for s in suggestions]
            header = line(f"Suggestions (Modified from {shorten(chosen.command)})")
            continue

        # Variations
        if action == "submenu-selected" and sub_idx == 4:
            ctx = gather_context(
                cfg,
                previous_query=query,
                last_suggested=chosen.command,
                followup="variants",
            )
            new_sugs = stream_suggestions(model, query, num, ctx, num_ctx, system_prompt, None, cfg.spinner)
            for s in new_sugs:
                setattr(s, "_is_new", True)
            suggestions = new_sugs
            rows, misslists, new_flags = build_rows(suggestions, show_explain)
            risk_flags = [classify_risk(s.command, cfg.caution_cmds or [], cfg.dangerous_cmds or []) for s in suggestions]
            header = line(f"Suggestions (Modified from {shorten(chosen.command)})")
            continue

        if action == "submenu-selected" and sub_idx == 5:
            continue

        if action == "row-selected":
            while True:
                missing = [b for b in missing if b not in (cfg.ignored_bins or [])]
                if missing:
                    proceed, installed_any = offer_installs_for_missing(missing, cfg.pm_order, cfg.n_suggestions, add_ignored)
                    cfg.ignored_bins = get_ignored()
                    if not proceed:
                        break
                    if installed_any:
                        refresh_requires(chosen)
                        missing = [b for b, p in (chosen.requires or {}).items() if not p]
                        continue
                cmd_to_run = prompt_edit(chosen.command)
                risk = classify_risk(cmd_to_run, cfg.caution_cmds or [], cfg.dangerous_cmds or [])
                if (not args.unsafe) and risk != "none":
                    if not confirm_risk(cmd_to_run, risk, cfg.caution_cmds or [], cfg.dangerous_cmds or []):
                        break
                rc, _ = run_and_capture(cmd_to_run)
                append_shell_history(cmd_to_run)
                refresh_requires(chosen)
                return rc

if __name__ == "__main__":
    raise SystemExit(main())

