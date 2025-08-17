# shai/cli.py
from __future__ import annotations
import argparse, os, shutil
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

HISTFILE = os.path.expanduser("~/.shai_history")
try:
    readline.read_history_file(HISTFILE)
except Exception:
    pass


def line(text: str) -> str:
    """Return a centered dashed line with text spanning the terminal width."""
    width = shutil.get_terminal_size((80, 20)).columns
    pad = max(0, width - len(text) - 2)
    left = pad // 2
    right = pad - left
    return "-" * left + f" {text} " + "-" * right

def prompt_edit(cmd: str) -> str:
    """Pre-fill input with command and allow user to edit before execution."""
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

def shorten(cmd: str, length: int = 40) -> str:
    return cmd if len(cmd) <= length else cmd[:length-3] + "..."

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
    args = ap.parse_args(argv)

    cfg = load_settings()
    try:
        ensure_ollama_running()
    except RuntimeError as e:
        print(e)
        return 1
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

    ctx = gather_context(cfg, previous_query=query)

    suggestions = stream_suggestions(model, query, num, ctx, num_ctx, cfg.system_prompt, None, cfg.spinner)
    rows, misslists, new_flags = build_rows(suggestions, show_explain)
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
        style_cell, style_line = make_style_functions(new_flags)

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
                print(f"\n\x1b[1mRunning:\x1b[0m {cmd_to_run}\n")
                rc, out = run_and_capture(cmd_to_run)
                refresh_requires(chosen)
                if sub_idx == 0:
                    if is_installer_command(cmd_to_run):
                        ctx = gather_context(cfg, previous_query=query)
                        suggestions = stream_suggestions(model, query, num, ctx, num_ctx, cfg.system_prompt, None, cfg.spinner)
                        rows, misslists, new_flags = build_rows(suggestions, show_explain)
                        header = line("Suggestions")
                        break
                    return rc
                print(line("Next"))
                try:
                    follow = input("What do you want to do next? ").strip()
                except KeyboardInterrupt:
                    follow = ""
                ctx = gather_context(
                    cfg,
                    recent_output=out,
                    previous_query=query,
                    last_executed=cmd_to_run,
                    followup=follow,
                )
                new_sugs = stream_suggestions(model, query, num, ctx, num_ctx, cfg.system_prompt, None, cfg.spinner)
                suggestions = append_new(suggestions, new_sugs)
                rows, misslists, new_flags = build_rows(suggestions, show_explain)
                header = line(f"Suggestions (Follow up to {shorten(cmd_to_run)})")
                break
            continue

        # Explain
        if action == "submenu-selected" and sub_idx == 3:
            parts = explain_parts(model, chosen.command, num_ctx)
            rows_exp: List[List[str]] = []
            rows_exp.append(("Command", chosen.command))
            if getattr(chosen, "explanation_min", ""):
                rows_exp.append(("Summary", chosen.explanation_min))
            if parts:
                for p, d in parts:
                    rows_exp.append((p, d))
            elif getattr(chosen, "explanation_min", ""):
                toks = chosen.command.split()
                if toks:
                    rows_exp.append((toks[0], ""))
                    for t in toks[1:]:
                        rows_exp.append((t, ""))
                for p in [p.strip() for p in chosen.explanation_min.split(';') if p.strip()]:
                    rows_exp.append((p, ""))
            if chosen.requires:
                for b, pth in (chosen.requires or {}).items():
                    rows_exp.append((b, ("✓ "+pth) if pth else "✗ missing"))
            rows_exp.append(("[ Back (Esc) ]", ""))
            colspecs_exp = [
                ColSpec(header="Part", min_width=20, wrap=False, ellipsis=True),
                ColSpec(header="Explanation", min_width=40, wrap=True),
            ]
            grid_select(rows_exp, colspecs_exp, title=line("Explain"))
            continue

        # Comment
        if action == "submenu-selected" and sub_idx == 1:
            print(line("Comment"))
            print("Command: " + chosen.command)
            if getattr(chosen, "explanation_min", ""):
                print("Explanation:")
                print(chosen.explanation_min)
            try:
                note = input("\nYour comment / correction: ").strip()
            except KeyboardInterrupt:
                note = ""
            ctx = gather_context(
                cfg,
                previous_query=query,
                last_suggested=chosen.command,
                followup=note,
            )
            new_sugs = stream_suggestions(model, query, num, ctx, num_ctx, cfg.system_prompt, None, cfg.spinner)
            suggestions = append_new(suggestions, new_sugs)
            rows, misslists, new_flags = build_rows(suggestions, show_explain)
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
            new_sugs = stream_suggestions(model, query, num, ctx, num_ctx, cfg.system_prompt, None, cfg.spinner)
            suggestions = append_new(suggestions, new_sugs)
            rows, misslists, new_flags = build_rows(suggestions, show_explain)
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
                print(f"\n\x1b[1mRunning:\x1b[0m {cmd_to_run}\n")
                rc, _ = run_and_capture(cmd_to_run)
                refresh_requires(chosen)
                return rc

if __name__ == "__main__":
    raise SystemExit(main())

