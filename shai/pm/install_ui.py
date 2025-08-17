# shai/pm/install_ui.py
from __future__ import annotations
import subprocess
from typing import List, Tuple, Callable, Optional

from ..context.packages import available_pms, search_best_provider, install_command
from ..ui.table import ColSpec, grid_select

def _run_stream(cmd: str) -> int:
    """Run install command interactively, streaming output and allowing user input."""
    try:
        return subprocess.call(cmd, shell=True)
    except KeyboardInterrupt:
        return 130

def offer_installs_for_missing(
    missing_bins: List[str],
    pm_order: List[str],
    max_pkgs: int = 10,
    add_ignored: Optional[Callable[[str], None]] = None,
) -> tuple[bool, bool]:
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
        return False, installed_any

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
            rows.append(("[ Back (Esc) ]", "return to suggestions"))
            rows.append(("[ Continue ]", f"run without installing '{binary}'"))
            rows.append(("[ Add to exception list ]", f"ignore '{binary}' next time"))
            for pkg, desc in results[:max_pkgs]:
                rows.append((install_command(pm_used, pkg), desc or ""))
            title = f" Search: {search_cmd}  [via {pm_used}] "
        else:
            rows.append(("[ Back (Esc) ]", "return to suggestions"))
            rows.append(("[ Continue ]", f"run without installing '{binary}'"))
            rows.append(("[ Add to exception list ]", f"ignore '{binary}' next time"))
            title = f" No results for: {binary} "

        colspecs = [
            ColSpec("Install Command", min_width=36, wrap=False, ellipsis=True),
            ColSpec("Description",     min_width=24, wrap=True),
        ]

        action, r, _ = grid_select(
            rows, colspecs,
            title=title,
        )
        if action in ("quit",) or r is None or r == 0:
            return False, installed_any

        if r == 1:  # Continue without installing
            return True, installed_any
        if r == 2:  # Add to exception list
            if add_ignored:
                add_ignored(binary)
                print(f"\x1b[2mIgnored '{binary}' for future suggestions.\x1b[0m")
            continue

        chosen_cmd = rows[r][0]
        print("\n\x1b[1mRunning:\x1b[0m " + chosen_cmd + "\n")
        rc = _run_stream(chosen_cmd)
        installed_any = installed_any or (rc == 0)
        input("\x1b[2m\nPress Enter to return…\x1b[0m")
        # after installation continue to next binary

    return True, installed_any

