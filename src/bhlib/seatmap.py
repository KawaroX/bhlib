from __future__ import annotations

from collections import Counter

# ANSI 8-color codes.
# 0=black 1=red 2=green 3=yellow 4=blue 5=magenta 6=cyan 7=white 8=bright-black (grey)
_STATUS_COLOR = {
    "1": 2,   # 空闲 -> green
    "6": 1,   # 使用中 -> red
    "7": 3,   # 临时离开 -> yellow
    "2": 4,   # 已预约 -> blue
}
_DEFAULT_COLOR = 8

_STATUS_NAME = {
    "1": "空闲",
    "6": "使用中",
    "7": "临时离开",
    "2": "已预约",
}

# Rendering parameters.
CELL_W = 3                 # each seat is a 3-char cell
STRIDE_X = CELL_W + 1      # 1-char gap between adjacent grid columns

# Clustering thresholds in coordinate units. Seats whose x / y differ by less
# than the threshold share a grid column / row. Chosen so that:
#   - sub-pixel jitter inside a single seat "table" collapses to one row,
#   - distinct table rows (~3 units apart vertically) stay separate,
#   - adjacent seat columns (~1.5 units apart horizontally) stay separate.
X_CLUSTER = 0.6
Y_CLUSTER = 0.5


def _ansi_bg(code: int) -> str:
    return f"10{code - 8}" if code >= 8 else f"4{code}"


def _fnum(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _seat_label(no: str, cell_w: int) -> str:
    n = (no or "").strip()
    if not n:
        return " " * cell_w
    if n.isdigit():
        nn = n.lstrip("0") or "0"
        return (nn[-cell_w:]).rjust(cell_w)
    return (n[:cell_w]).ljust(cell_w)


def _cluster(values: list[float], threshold: float) -> dict[float, int]:
    """Greedy 1-D clustering: consecutive values within `threshold` share an index."""
    if not values:
        return {}
    uniq = sorted(set(values))
    index_of: dict[float, int] = {}
    cur = 0
    groups: list[list[float]] = [[uniq[0]]]
    index_of[uniq[0]] = 0
    for v in uniq[1:]:
        if v - groups[-1][-1] < threshold:
            groups[-1].append(v)
        else:
            cur += 1
            groups.append([v])
        index_of[v] = cur
    return index_of


def render_seat_map(
    seats: list[dict],
    *,
    compress_blank_rows: bool = True,
) -> str:
    """Render a cluster-aligned seat map. Each seat is a 3-char colored cell."""
    if not seats:
        return "(no seats)"

    geom: list[tuple[float, float, str, str]] = []
    for s in seats:
        x = _fnum(s.get("point_x"))
        y = _fnum(s.get("point_y"))
        status = str(s.get("status") or "")
        no = str(s.get("no") or "")
        geom.append((x, y, status, no))

    if not geom:
        return "(no seats with geometry)"

    area_name = ""
    for s in seats:
        n = str(s.get("area_name") or "").strip()
        if n:
            area_name = n
            break

    x_index = _cluster([g[0] for g in geom], X_CLUSTER)
    y_index = _cluster([g[1] for g in geom], Y_CLUSTER)

    num_gx = (max(x_index.values()) + 1) if x_index else 1
    num_gy = (max(y_index.values()) + 1) if y_index else 1

    cols = num_gx * STRIDE_X
    # Extra rows in case two seats share the exact same (gx, gy) and need to stack.
    rows = num_gy + 4

    char_grid: list[list[str]] = [[" "] * cols for _ in range(rows)]
    color_grid: list[list[int | None]] = [[None] * cols for _ in range(rows)]

    # Stable order: lower numbers drawn first. Same-cell collisions bump the
    # later (higher-numbered) seat down.
    geom_sorted = sorted(
        geom, key=lambda g: (int(g[3]) if g[3].isdigit() else 10**9, g[3])
    )
    status_counts: Counter = Counter()

    for x, y, status, no in geom_sorted:
        gx = x_index[x]
        gy = y_index[y]
        color = _STATUS_COLOR.get(status, _DEFAULT_COLOR)
        label = _seat_label(no, CELL_W)
        start = gx * STRIDE_X
        gy_try = gy
        while gy_try < rows:
            occupied = any(
                color_grid[gy_try][start + i] is not None
                for i in range(CELL_W)
                if 0 <= start + i < cols
            )
            if not occupied:
                break
            gy_try += 1
        if gy_try >= rows:
            continue
        for i, ch in enumerate(label):
            cx = start + i
            if 0 <= cx < cols:
                char_grid[gy_try][cx] = ch
                color_grid[gy_try][cx] = color
        status_counts[status] += 1

    has_content = [any(c is not None for c in color_grid[r]) for r in range(rows)]

    out_lines: list[str] = []
    blank_allowed = True
    for r in range(rows):
        if has_content[r]:
            out_lines.append(_render_row(color_grid[r], char_grid[r], cols))
            blank_allowed = True
        else:
            if compress_blank_rows:
                if blank_allowed and any(has_content[r + 1 :]):
                    out_lines.append("")
                    blank_allowed = False
            else:
                out_lines.append("")

    while out_lines and not out_lines[0].strip():
        out_lines.pop(0)
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    reset = "\x1b[0m"
    legend_bits: list[str] = []
    for st in ("1", "2", "6", "7"):
        cnt = status_counts.get(st, 0)
        if cnt <= 0:
            continue
        color = _STATUS_COLOR.get(st, _DEFAULT_COLOR)
        name = _STATUS_NAME.get(st, f"status={st}")
        legend_bits.append(f"\x1b[30;{_ansi_bg(color)}m   {reset} {name} × {cnt}")
    for st, cnt in status_counts.items():
        if st in ("1", "2", "6", "7") or cnt <= 0:
            continue
        color = _STATUS_COLOR.get(st, _DEFAULT_COLOR)
        name = _STATUS_NAME.get(st, f"status={st}")
        legend_bits.append(f"\x1b[30;{_ansi_bg(color)}m   {reset} {name} × {cnt}")
    legend = "  ".join(legend_bits)

    title = area_name if area_name else "座位图"
    parts = [title] + out_lines
    if legend:
        parts.append(legend)
    return "\n".join(parts)


def _render_row(color_row: list, char_row: list, cols: int) -> str:
    reset = "\x1b[0m"
    buf: list[str] = []
    last_color: int | None = -1  # type: ignore[assignment]
    for c in range(cols):
        color = color_row[c]
        ch = char_row[c]
        if color is None:
            if last_color is not None:
                buf.append(reset)
                last_color = None
            buf.append(ch)
        else:
            if color != last_color:
                buf.append(f"\x1b[30;{_ansi_bg(color)}m")
                last_color = color
            buf.append(ch)
    if last_color is not None:
        buf.append(reset)
    return "".join(buf).rstrip()
