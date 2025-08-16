import re, shutil, sys

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
def visible_len(s: str) -> int: return len(ANSI_RE.sub("", s))

def crop_visible(s: str, width: int, ellipsis=True) -> str:
    if width <= 0: return ""
    vis=i=0; out=[]
    while i < len(s) and vis < width:
        if s[i] == "\033":
            m = ANSI_RE.match(s, i)
            if m: out.append(m.group(0)); i=m.end(); continue
        out.append(s[i]); i+=1; vis+=1
    if ellipsis and visible_len(s) > width and width >= 2:
        while out and visible_len("".join(out)) >= width: out.pop()
        out.append("…")
    return "".join(out)

def ljust_visible(s: str, width: int) -> str:
    pad = max(0, width - visible_len(s))
    return s + (" " * pad)

USE_COLOR = sys.stdout.isatty()
def c(txt, code): return f"\033[{code}m{txt}\033[0m" if USE_COLOR else txt
BOLD   = lambda s: c(s, "1")
DIM    = lambda s: c(s, "2")
RED    = lambda s: c(s, "31")
GREEN  = lambda s: c(s, "32")
YELLOW = lambda s: c(s, "33")
CYAN   = lambda s: c(s, "36")

def term_size():
    ts = shutil.get_terminal_size((100, 24))
    return ts.columns, ts.lines
