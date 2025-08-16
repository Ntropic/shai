# shai/context/packages.py
"""
Package manager utilities for shai.

- Reads an ORDER you pass in (from config) and filters to installed PMs.
- Knows how to SEARCH per PM and parse human output into [(pkg, desc)].
- Returns the first PM with results (system PMs typically appear first in order).
- Builds install commands for a chosen PM/package name.

Public API:
    available_pms(pm_order: list[str]) -> list[str]
    search_best_provider(term: str, pm_order: list[str], max_results: int = 8)
        -> tuple[str, str, list[tuple[str,str]]]
        # returns (pm_used, search_cmd_str, results)
    install_command(pm: str, pkg: str) -> str
    is_known_pm(name: str) -> bool
"""

from __future__ import annotations
import re
import shutil
import subprocess
from typing import Dict, List, Tuple

# ---------------- Registry ----------------

# How to run a search (by "term") for each package manager
SEARCH_CMDS: Dict[str, List[str]] = {
    "pacman": ["pacman", "-Ss"],
    "yay":    ["yay", "-Ss"],
    "paru":   ["paru", "-Ss"],
    "apt":    ["apt", "search"],
    "dnf":    ["dnf", "search"],
    "zypper": ["zypper", "search"],
    "brew":   ["brew", "search"],
    "flatpak":["flatpak", "search"],
    "snap":   ["snap", "find"],
}

# How to install a package for each PM (format with .format(pkg=...))
INSTALL_CMDS: Dict[str, str] = {
    "pacman": "sudo pacman -S {pkg}",
    "yay":    "yay -S {pkg}",
    "paru":   "paru -S {pkg}",
    "apt":    "sudo apt install {pkg}",
    "dnf":    "sudo dnf install {pkg}",
    "zypper": "sudo zypper install {pkg}",
    "brew":   "brew install {pkg}",
    "flatpak":"flatpak install {pkg}",
    "snap":   "sudo snap install {pkg}",
}

# Parser style per PM & classification (kind helps if you ever want to reorder)
PM_INFO: Dict[str, Dict[str, str]] = {
    "pacman": {"parse": "pacman", "kind": "system"},
    "yay":    {"parse": "pacman", "kind": "helper"},
    "paru":   {"parse": "pacman", "kind": "helper"},
    "apt":    {"parse": "apt",    "kind": "system"},
    "dnf":    {"parse": "dnf",    "kind": "system"},
    "zypper": {"parse": "zypper", "kind": "system"},
    "brew":   {"parse": "brew",   "kind": "other"},
    "flatpak":{"parse": "flatpak","kind": "other"},
    "snap":   {"parse": "snap",   "kind": "other"},
}

def is_known_pm(name: str) -> bool:
    return name in SEARCH_CMDS and name in PM_INFO and name in INSTALL_CMDS or name in ("flatpak", "snap")

# ---------------- Availability & order ----------------

def available_pms(pm_order: List[str]) -> List[str]:
    """
    Filter the configured order to just PMs that are actually installed.
    Keeps the given order as-is (so if you put pacman before yay, pacman wins).
    """
    out: List[str] = []
    for pm in pm_order:
        if pm in SEARCH_CMDS and shutil.which(pm):
            out.append(pm)
    # Include any other known PMs present that weren't listed in config (rare)
    for pm in SEARCH_CMDS:
        if pm not in out and shutil.which(pm):
            out.append(pm)
    return out

# ---------------- Running & parsing searches ----------------

def _run(cmd: List[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception as e:
        return f"__ERROR__ {e}"

def _parse_results(pm: str, out: str) -> List[Tuple[str, str]]:
    """
    Parse human-readable search output into a list of (package_name, description).
    """
    if out.startswith("__ERROR__") or not out.strip():
        return []

    style = PM_INFO.get(pm, {}).get("parse", "")
    lines = [l.rstrip() for l in out.splitlines()]
    res: List[Tuple[str,str]] = []

    if style == "pacman":
        # pacman/yay/paru
        # repo/pkg  version
        #     description...
        i = 0
        while i < len(lines):
            m = re.match(r"^\s*([-\w]+)/([-\w+.@]+)\s+([\w.+:-]+)", lines[i])
            if m:
                pkg = m.group(2)
                desc = ""
                if i+1 < len(lines) and lines[i+1].startswith("    "):
                    desc = lines[i+1].strip()
                    i += 1
                res.append((pkg, desc))
            i += 1

    elif style == "apt":
        # apt search
        # ripgrep/jammy 13.0.0-1 amd64
        #   fast line-oriented search tool
        i = 0
        while i < len(lines):
            m = re.match(r"^([a-z0-9.+-]+)\/", lines[i])
            if m:
                pkg = m.group(1)
                desc = ""
                if i+1 < len(lines) and lines[i+1].startswith(" "):
                    desc = lines[i+1].strip()
                    i += 1
                res.append((pkg, desc))
            i += 1

    elif style == "dnf":
        # dnf search
        # ripgrep.x86_64 : A fast grep alternative
        for line in lines:
            m = re.match(r"^([a-z0-9.+_-]+)(?:\.[a-z0-9_]+)?\s*:\s*(.+)$", line, re.I)
            if m:
                res.append((m.group(1), m.group(2)))

    elif style == "zypper":
        # zypper search (table)
        for line in lines:
            if re.match(r"^\s*[|+]\s", line):
                cols = [c.strip() for c in line.strip("| ").split("|")]
                if len(cols) >= 2 and cols[1].lower() != "name":
                    name = cols[1]
                    desc = cols[-1] if len(cols) >= 3 else ""
                    res.append((name, desc))

    elif style == "brew":
        # brew search prints names in columns and sometimes headers with '==>'
        for line in lines:
            if line.strip().startswith("==>"):
                continue
            for name in line.split():
                if re.match(r"^[a-z0-9.+-]+$", name, re.I):
                    res.append((name, ""))

    elif style == "flatpak":
        # 'flatpak search foo' prints rows; take first column as the app id/name
        # Format varies with versions; we do a simple split and keep first token
        for line in lines:
            parts = line.split()
            if parts and not line.lower().startswith("name"):
                res.append((parts[0], " ".join(parts[1:])))

    elif style == "snap":
        # snap find:
        # Name   Version   Publisher   Notes   Summary
        if lines and "Name" in lines[0] and "Summary" in lines[0]:
            for line in lines[1:]:
                cols = re.split(r"\s{2,}", line.strip())
                if cols:
                    name = cols[0]
                    summary = cols[-1] if len(cols) >= 2 else ""
                    res.append((name, summary))

    # de-duplicate while preserving order
    seen = set()
    uniq: List[Tuple[str,str]] = []
    for p, d in res:
        if p not in seen:
            seen.add(p)
            uniq.append((p, d))
    return uniq

def search_one(pm: str, term: str, max_results: int = 8) -> Tuple[str, List[Tuple[str,str]]]:
    """
    Search a single package manager.
    Returns (search_cmd_str, results)
    """
    base = SEARCH_CMDS.get(pm)
    if not base:
        return "", []
    cmd = base + [term]
    out = _run(cmd)
    results = _parse_results(pm, out)[:max_results]
    return (" ".join(cmd), results)

def search_best_provider(term: str, pm_order: List[str], max_results: int = 8) -> Tuple[str, str, List[Tuple[str,str]]]:
    """
    Try PMs in *configured order*, filtered to installed ones.
    Return the first PM with results:
        (pm_used, search_cmd_str, [(pkg, desc), ...])
    """
    for pm in available_pms(pm_order):
        search_cmd, results = search_one(pm, term, max_results=max_results)
        if results:
            return pm, search_cmd, results
    return "", "", []

# ---------------- Install helpers ----------------

def install_command(pm: str, pkg: str) -> str:
    """
    Build the install command string for a given PM and package name.
    """
    tmpl = INSTALL_CMDS.get(pm)
    if not tmpl:
        return f"# install {pkg} with your package manager"
    return tmpl.format(pkg=pkg)

