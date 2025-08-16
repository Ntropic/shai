import curses, locale, shutil, textwrap
from dataclasses import dataclass
from typing import List, Sequence, Tuple, Optional, Dict, Any

from ..util.ansi import visible_len, crop_visible, ljust_visible

locale.setlocale(locale.LC_ALL, "")

@dataclass
class ColSpec:
    header: str
    min_width: int = 8
    max_width: int | None = None
    wrap: bool = True
    ellipsis: bool = True

def render_table(
    stdscr: Optional["curses._CursesWindow"],
    rows: Sequence[Sequence[str]],
    colspecs: Sequence[ColSpec],
    *,
    start_y: int = 0,
    start_x: int = 0,
    gap: int = 2,
    highlight_row: Optional[int] = None,
    highlight_cell: Optional[Tuple[int,int]] = None,
    header: Optional[str] = None,
    header_attr: int = 0,
    normal_attr: int = 0,
    highlight_attr: int = 0,
    max_height: Optional[int] = None,
) -> Dict[str, Any]:
    if stdscr is not None:
        h, w = stdscr.getmaxyx()
    else:
        ts = shutil.get_terminal_size((100,24)); w, h = ts.columns, ts.lines
    if max_height is None: max_height = h - start_y
    ncols = len(colspecs)
    assert all(len(r) == ncols for r in rows)

    headers = [cs.header for cs in colspecs]
    widths = [max(1, cs.min_width) for cs in colspecs]
    available = max(0, w - start_x)
    total_gap = gap * (ncols - 1)
    col_space = max(1, available - total_gap)

    samples: List[List[int]] = [[] for _ in range(ncols)]
    for row in rows:
        for j, cell in enumerate(row):
            samples[j].append(min(max(visible_len(cell), visible_len(headers[j])), 200))
    ideal = []
    for j, cs in enumerate(colspecs):
        target = max(cs.min_width, min(max(samples[j]) if samples[j] else cs.min_width, cs.max_width or 10**6))
        ideal.append(target)

    sum_ideal = sum(ideal) or 1
    if sum_ideal <= col_space:
        widths = [min(ideal[j], (colspecs[j].max_width or ideal[j])) for j in range(ncols)]
        leftover = col_space - sum(widths)
        wrap_cols = [j for j, cs in enumerate(colspecs) if cs.wrap]
        k = 0
        while leftover > 0 and wrap_cols:
            j = wrap_cols[k % len(wrap_cols)]
            cap = (colspecs[j].max_width or col_space)
            add = min(leftover, max(0, cap - widths[j]))
            if add == 0:
                k += 1
                if k > 3*len(wrap_cols): break
                continue
            widths[j] += add; leftover -= add; k += 1
    else:
        widths = [max(colspecs[j].min_width, int(col_space * (ideal[j] / sum_ideal))) for j in range(ncols)]
        diff = col_space - sum(widths); j = 0
        while diff != 0 and ncols:
            step = 1 if diff > 0 else -1
            candidates = [k for k, cs in enumerate(colspecs) if cs.wrap] or list(range(ncols))
            idx = candidates[j % len(candidates)]
            neww = widths[idx] + step
            if neww >= colspecs[idx].min_width and (colspecs[idx].max_width is None or neww <= colspecs[idx].max_width):
                widths[idx] = neww; diff -= step
            j += 1
            if j > 10000: break

    col_starts = [start_x]
    for j in range(1, ncols):
        col_starts.append(col_starts[-1] + widths[j-1] + gap)

    wrapped_cells: List[List[List[str]]] = []
    row_heights: List[int] = []
    for row in rows:
        lines_per_col: List[List[str]] = []
        row_h = 1
        for j, cell in enumerate(row):
            cs = colspecs[j]
            if cs.wrap:
                parts = "\n".join(textwrap.fill(part, width=widths[j], break_long_words=True)
                                  for part in str(cell).splitlines()).splitlines()
                lines = parts or [""]
            else:
                s = crop_visible(str(cell), widths[j], ellipsis=cs.ellipsis)
                lines = [ljust_visible(s, widths[j])]
            lines_per_col.append(lines); row_h = max(row_h, len(lines))
        wrapped_cells.append(lines_per_col)
        row_heights.append(row_h)

    total_height = sum(row_heights)

    if stdscr is not None:
        y = start_y
        if header:
            stdscr.addnstr(y, start_x, header, max(0, w - start_x), header_attr); y += 1
        for i, (lines_per_col, rheight) in enumerate(zip(wrapped_cells, row_heights)):
            row_attr = highlight_attr if (highlight_row is not None and i == highlight_row) else normal_attr
            for k in range(rheight):
                if y >= start_y + max_height: break
                for j in range(ncols):
                    xs = col_starts[j]
                    attr = row_attr if not (highlight_cell and highlight_cell == (i, j)) else highlight_attr
                    s = lines_per_col[j][k] if k < len(lines_per_col[j]) else ""
                    if colspecs[j].wrap:
                        s = s[:widths[j]]; s = ljust_visible(s, widths[j])
                    else:
                        s = crop_visible(s, widths[j], ellipsis=colspecs[j].ellipsis)
                        s = ljust_visible(s, widths[j])
                    stdscr.addnstr(y, xs, s, widths[j], attr)
                y += 1
            if y >= start_y + max_height: break

    return {"widths": widths, "col_starts": col_starts, "row_heights": row_heights, "total_height": total_height}

def grid_select(
    rows: Sequence[Sequence[str]],
    colspecs: Sequence[ColSpec],
    *,
    row_menu_provider: Optional[callable] = None,
    submenu_cols: int = 3,
    title: Optional[str] = None,
) -> Tuple[str, Optional[int], Optional[int]]:
    def inner(stdscr):
        curses.curs_set(0); curses.use_default_colors()
        try: curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        except Exception: pass
        HIL = curses.color_pair(1) | curses.A_BOLD
        NRM = curses.A_NORMAL
        DIM = curses.A_DIM

        sel_row, mode, sel_sub = 0, "rows", 0
        submenu_items: List[str] = []

        while True:
            stdscr.erase(); h, w = stdscr.getmaxyx()
            y = 0
            if title: stdscr.addnstr(y, 0, title, w, curses.A_BOLD); y += 1
            table_max_h = max(3, h - y - 3)

            hdr = "  ".join(cs.header for cs in colspecs)
            layout = render_table(stdscr, rows, colspecs,
                                  start_y=y, highlight_row=sel_row if mode=="rows" else None,
                                  header=hdr, header_attr=DIM, normal_attr=NRM, highlight_attr=HIL,
                                  max_height=table_max_h)
            y += min(layout["total_height"], table_max_h)

            if mode == "submenu":
                submenu_items = row_menu_provider(sel_row) if row_menu_provider else []
                cols = max(1, submenu_cols); gap = 3
                cell_w = max(8, (w - (cols-1)*gap)//cols)
                stdscr.addnstr(y, 0, " Select action (←/→, Enter, Esc):", w, DIM); y += 1
                rows_needed = (len(submenu_items)+cols-1)//cols
                for r in range(rows_needed):
                    x = 0
                    for c in range(cols):
                        idx = r*cols+c
                        if idx >= len(submenu_items): break
                        s = f"[ {submenu_items[idx]} ]"
                        attr = HIL if sel_sub == idx else NRM
                        stdscr.addnstr(y, x, s[:cell_w], cell_w, attr)
                        x += cell_w + gap
                    y += 1

            stdscr.addnstr(h-1, 0, " ↑/↓ move • Enter select • q/Esc quit • submenu: ←/→ move, Enter", w, DIM)
            stdscr.refresh()

            ch = stdscr.getch()
            if mode == "rows":
                if ch in (curses.KEY_UP, ord('k')):   sel_row = max(0, sel_row-1)
                elif ch in (curses.KEY_DOWN, ord('j')): sel_row = min(len(rows)-1, sel_row+1)
                elif ch in (10,13):
                    submenu_items = row_menu_provider(sel_row) if row_menu_provider else []
                    if submenu_items: mode, sel_sub = "submenu", 0
                    else: return ("row-selected", sel_row, None)
                elif ch in (27, ord('q')): return ("quit", None, None)
                elif ch == curses.KEY_RESIZE: pass
            else:
                cols = max(1, submenu_cols)
                if ch in (curses.KEY_LEFT, ord('h')):   sel_sub = max(0, sel_sub-1)
                elif ch in (curses.KEY_RIGHT, ord('l')): sel_sub = min(len(submenu_items)-1, sel_sub+1)
                elif ch in (curses.KEY_UP, ord('k')):    sel_sub = max(0, sel_sub - cols)
                elif ch in (curses.KEY_DOWN, ord('j')):  sel_sub = min(len(submenu_items)-1, sel_sub + cols)
                elif ch in (10,13): return ("submenu-selected", sel_row, sel_sub)
                elif ch in (27, ord('q')): mode = "rows"
                elif ch == curses.KEY_RESIZE: pass
    return curses.wrapper(inner)
