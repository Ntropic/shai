# shai/pm/install_ui.py
from __future__ import annotations
import subprocess
from typing import List, Tuple

from ..context.packages import available_pms, search_best_provider, install_command
from ..ui.table import ColSpec, grid_select

def _run_stream(cmd: str) -> int:
    proc = subprocess.Popen(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True
    )
    try:
        for line in proc.stdout:
            print(line, end="")
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        return proc.returncode
    return proc.wait()

def offer_installs_for_missing(missing_bins: List[str], pm_order: List[str], max_pkgs: int = 10) -> bool:
    """
    For each missing binary, search the configured package managers in order.
    Show skipped PMs (with 'no results') before showing the first that had hits.
    'Skip' always executes the original suggestion without installing.
    """
    installed_any = False
    managers = available_pms(pm_order)
    if not managers:
        print("\x1b[31mNo known package managers installed.\x1b[0m")
        input("\x1b[2mPress Enter to return…\x1b[0m")
        return False

    for binary in missing_bins:
        tried_msgs: List[str] = []
        results: List[Tuple[str,str]] = []
        pm_used, search_cmd = None, None

        for pm in managers:
            pm_used, search_cmd, results = search_best_provider(binary, [pm], max_results=max_pkgs)
            if results:
                break
            else:
                tried_msgs.append(f"[ {pm} ] → no results")

        if tried_msgs:
            print("\n\x1b[2mPackage manager attempts:\x1b[0m")
            for m in tried_msgs:
                print("   " + m)

        rows: List[Tuple[str, str]] = []
        if results:
            # Skip row always first
            rows.append(("[ Skip ]", f"skip installing '{binary}' and continue"))
            for pkg, desc in results[:max_pkgs]:
                rows.append((install_command(pm_used, pkg), desc or ""))
            title = f" Search: {search_cmd}  [via {pm_used}] "
        else:
            # No hits at all
            rows.append(("[ Skip ]", f"no results for '{binary}' → continue anyway"))
            title = f" No results for: {binary} "

        colspecs = [
            ColSpec("Install Command", min_width=36, wrap=False, ellipsis=True),
            ColSpec("Description",     min_width=24, wrap=True),
        ]

        def menu_for_row(i: int): return ["Run", "Back"]

        action, r, s = grid_select(
            rows, colspecs,
            row_menu_provider=menu_for_row,
            submenu_cols=2,
            title=title,
        )
        if action in ("quit",) or r is None:
            return installed_any

        # Always execute suggestion if Skip chosen
        if r == 0:
            continue  # skip installing, but don't block execution

        if action == "submenu-selected":
            if s == 0:  # Run
                chosen_cmd = rows[r][0]
                print("\n\x1b[1mRunning:\x1b[0m " + chosen_cmd + "\n")
                rc = _run_stream(chosen_cmd)
                installed_any = installed_any or (rc == 0)
                input("\x1b[2m\nPress Enter to return…\x1b[0m")
                continue
            if s == 1:  # Back
                continue

    return installed_any

