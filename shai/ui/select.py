"""UI: arrow-key selection for tables or grids."""
import sys, termios, tty
from typing import List, Callable, Optional

RESET = "\033[0m"
REV = "\033[7m"

def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(3)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch

def grid_select(rows:List[tuple],
                colspecs,
                row_menu_provider:Optional[Callable[[int],List[str]]]=None,
                submenu_cols:int=3,
                title:str="") -> tuple:
    """Interactive selection with arrow keys. Returns (action,row_idx,sub_idx)."""
    from .table import render_table
    highlight = 0
    while True:
        print("\033c", end="")  # clear
        if title:
            print(title)
        render_table(rows, colspecs, highlight=highlight)
        ch = getch()
        if ch == "\x1b[A":   # up
            highlight = max(0, highlight-1)
        elif ch == "\x1b[B": # down
            highlight = min(len(rows)-1, highlight+1)
        elif ch == "\n":     # enter
            if not row_menu_provider:
                return ("execute", highlight, None)
            opts = row_menu_provider(highlight)
            # show submenu horizontally
            sel = 0
            while True:
                print("\033c", end="")
                print(f"Row {highlight+1}:")
                for i,opt in enumerate(opts):
                    if i==sel: print(REV+opt+RESET, end="  ")
                    else: print(opt, end="  ")
                print()
                ch2 = getch()
                if ch2 == "\x1b[C": # right
                    sel = (sel+1)%len(opts)
                elif ch2 == "\x1b[D": # left
                    sel = (sel-1)%len(opts)
                elif ch2 == "\n":
                    return (opts[sel].lower(), highlight, sel)
        elif ch == "\x03": # ctrl-c
            raise KeyboardInterrupt

