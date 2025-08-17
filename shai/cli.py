# shai/cli.py
from __future__ import annotations
import argparse
from typing import List

from .config import load_settings, add_ignored, get_ignored
from .ui.table import ColSpec, grid_select
from .pm.install_ui import offer_installs_for_missing
from .app.flow import (
    gather_context, build_rows, make_style_functions,
    append_new, is_installer_command, run_and_capture, refresh_requires,
    stream_suggestions,
)
from .llm.suggest import ensure_ollama_running

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

    # First batch
    ctx = gather_context(cfg, previous_query=query)
    print("Generating suggestions...\n")
    counter = {"i": 0}
    def _stream_cb(s):
        counter["i"] += 1
        if show_explain:
            print(f"{counter['i']}. {s.command}\n   {s.explanation_min}")
        else:
            print(f"{counter['i']}. {s.command}")
    suggestions = stream_suggestions(model, query, num, ctx, num_ctx, cfg.system_prompt, _stream_cb, spinner=cfg.spinner)
    if not suggestions:
        print("No suggestions returned."); return 2

    while True:
        rows, misslists, new_flags = build_rows(suggestions, show_explain)

        # Append SKIP row at the bottom
        if show_explain:
            rows.append(("[ Skip ]", "", "Exit without choosing"))
        else:
            rows.append(("[ Skip ]", ""))
        misslists.append([])  # no missing tools for skip
        new_flags.append(False)

        # Wider Command, narrow Status, small Explanation
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

        # submenu
        def menu_for_row(i: int):
            return ["Execute", "Comment", "Exec → Continue", "Explain", "Variations"]

        action, row_idx, sub_idx = grid_select(
            rows, colspecs,
            row_menu_provider=menu_for_row,
            submenu_cols=cfg.submenu_cols,
            title=" Suggestions ",
            style_fn=style_cell,
            line_style_fn=style_line,
        )
        if action in ("quit",) or row_idx is None:
            print("\x1b[2mDone.\x1b[0m"); return 0

        # If the user selected the SKIP row (last row), exit cleanly
        if row_idx == len(rows) - 1:
            print("\x1b[2mSkipped.\x1b[0m")
            return 0

        chosen = suggestions[row_idx]
        missing = misslists[row_idx]

        # Execute / Exec → Continue
        if action == "submenu-selected" and sub_idx in (0, 2):
            missing = [b for b in missing if b not in (cfg.ignored_bins or [])]
            if missing:
                installed_any = offer_installs_for_missing(missing, cfg.pm_order, cfg.n_suggestions, add_ignored)
                cfg.ignored_bins = get_ignored()
                if installed_any:
                    refresh_requires(chosen)
                continue

            if sub_idx == 0:
                # Execute: stream output; if installer, come back and prepend a NEW page
                print("\x1b[1mRunning:\x1b[0m " + chosen.command + "\n")
                rc, _ = run_and_capture(chosen.command)
                if is_installer_command(chosen.command):
                    refresh_requires(chosen)
                    ctx = gather_context(cfg, previous_query=query)
                    new_page: list = []
                    counter = {"i": len(suggestions)}
                    def _cb(s):
                        counter["i"] += 1
                        if show_explain:
                            print(f"{counter['i']}. {s.command}\n   {s.explanation_min}")
                        else:
                            print(f"{counter['i']}. {s.command}")
                    new_page = stream_suggestions(model, query, len(suggestions), ctx, num_ctx, cfg.system_prompt, _cb, spinner=cfg.spinner)
                    suggestions = append_new(suggestions, new_page)
                    continue
                return rc

            # Exec → Continue: stream output, then append new suggestions
            print("\x1b[1mRunning:\x1b[0m " + chosen.command + "\n")
            rc, out = run_and_capture(chosen.command)
            refresh_requires(chosen)
            try:
                follow = input("\nWhat do you want to do next? ").strip()
            except KeyboardInterrupt:
                follow = ""
            ctx = gather_context(
                cfg,
                recent_output=out,
                previous_query=query,
                last_executed=chosen.command,
                followup=follow,
            )
            new_page: list = []
            counter = {"i": len(suggestions)}
            def _cb(s):
                counter["i"] += 1
                if show_explain:
                    print(f"{counter['i']}. {s.command}\n   {s.explanation_min}")
                else:
                    print(f"{counter['i']}. {s.command}")
            new_page = stream_suggestions(model, query, len(suggestions), ctx, num_ctx, cfg.system_prompt, _cb, spinner=cfg.spinner)
            suggestions = append_new(suggestions, new_page)
            continue

        # Explain
        if action == "submenu-selected" and sub_idx == 3:
            print("\n\x1b[1mCommand:\x1b[0m " + chosen.command)
            print("\x1b[1mExplanation:\x1b[0m")
            if getattr(chosen, "explanation_min", ""):
                parts = [p.strip() for p in chosen.explanation_min.split(';') if p.strip()]
                for p in parts:
                    print(" - " + p)
                if not parts:
                    print(chosen.explanation_min)
            else:
                print("\x1b[2m(no explanation provided by the model)\x1b[0m")
            print("\x1b[1mTools:\x1b[0m")
            for b, p in (chosen.requires or {}).items():
                print(f"  {b:10} {'✓ '+p if p else '✗ missing'}")
            input("\x1b[2m\nPress Enter to return…\x1b[0m")
            continue

        # Comment → show command & explanation, accept note, append N new items
        if action == "submenu-selected" and sub_idx == 1:
            print("\n\x1b[1mCommand:\x1b[0m " + chosen.command)
            if getattr(chosen, "explanation_min", ""):
                print("\x1b[1mExplanation:\x1b[0m")
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
            new_page: list = []
            counter = {"i": len(suggestions)}
            def _cb(s):
                counter["i"] += 1
                if show_explain:
                    print(f"{counter['i']}. {s.command}\n   {s.explanation_min}")
                else:
                    print(f"{counter['i']}. {s.command}")
            new_page = stream_suggestions(model, query, len(suggestions), ctx, num_ctx, cfg.system_prompt, _cb, spinner=cfg.spinner)
            suggestions = append_new(suggestions, new_page)
            continue

        # Variations: ask for new variants of selected command
        if action == "submenu-selected" and sub_idx == 4:
            ctx = gather_context(
                cfg,
                previous_query=query,
                last_suggested=chosen.command,
                followup="variants",
            )
            new_page: list = []
            counter = {"i": len(suggestions)}
            def _cb(s):
                counter["i"] += 1
                if show_explain:
                    print(f"{counter['i']}. {s.command}\n   {s.explanation_min}")
                else:
                    print(f"{counter['i']}. {s.command}")
            new_page = stream_suggestions(model, query, len(suggestions), ctx, num_ctx, cfg.system_prompt, _cb, spinner=cfg.spinner)
            suggestions = append_new(suggestions, new_page)
            continue

        # fallback
        if action == "row-selected":
            print("\x1b[1mRunning:\x1b[0m " + chosen.command + "\n")
            rc, _ = run_and_capture(chosen.command)
            return rc

if __name__ == "__main__":
    raise SystemExit(main())

