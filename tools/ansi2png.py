#!/usr/bin/env python3
"""ansi2png - 把带 ANSI 颜色的终端输出渲染成「终端截图」PNG。

输入是原始 stdout（含 \\x1b[..m 颜色序列），输出是一张带窗口标题栏的
等宽字体图片，可像普通图片一样插入文档 / 上传。

依赖：rsvg-convert（brew install librsvg）。等宽字体用系统自带的
SF Mono / Menlo，无需安装。

日志落在本仓库的 assets/ 下，渲染出的截图同样写回 assets/，供 main.tex 引用。
以下命令均在 answer-pdf/ 根目录执行:

  tools/ansi2png.py assets/inference_log.txt -o assets/inference_log.png \\
      --title 'llama-server · inference'
  tools/ansi2png.py assets/build_log.txt --tail 40 --font-size 11 --theme dracula
  tools/ansi2png.py assets/chat_log_metal.txt --wrap 120 --scale 2 --copy
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------- ANSI 解析

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
# 其它 CSI（光标移动、清屏等）与 OSC（设置标题等）：渲染成静态图时丢弃
CSI_RE = re.compile(r"\x1b\[[0-9;:<=>?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
ESC_OTHER_RE = re.compile(r"\x1b[@-Z\\-_]")
# 展开 \b 与制表符后仍残留的控制字符：SVG 不允许，必须剔除。
# 必须保留两个字符，否则会打断后续解析：
#   \x0d(\r) —— parse_ansi 要用它还原「回车覆盖」，删掉那段逻辑就成了死代码
#   \x1b(ESC) —— parse_ansi 先保留颜色序列、之后再解析；删掉 ESC 会让 \x1b[1m 退化成
#               "[1m"，SGR_RE 再也匹配不上，表现为「所有颜色静默消失」
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")

# 素材日志里 emoji 是双重编码的：原 UTF-8 字节被按 latin-1 解读，再编码成 UTF-8，
# 于是 🚶(f0 9f 9a b6) 变成了 "ð\u009f\u009a¶"。把连续 latin-1 高位字符拼回去
# 重新解码即可还原；解码失败说明本就是合法文本，原样保留
MOJIBAKE_RUN_RE = re.compile(r"[\u0080-\u00ff]+")


def fix_mojibake(s: str) -> str:
    def repl(m):
        chunk = m.group(0)
        try:
            return chunk.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return chunk

    return MOJIBAKE_RUN_RE.sub(repl, s)


def xterm256(n: int) -> str:
    """xterm-256 色号 -> #rrggbb"""
    if n < 16:
        base = [
            "#000000", "#cd0000", "#00cd00", "#cdcd00",
            "#0000ee", "#cd00cd", "#00cdcd", "#e5e5e5",
            "#7f7f7f", "#ff0000", "#00ff00", "#ffff00",
            "#5c5cff", "#ff00ff", "#00ffff", "#ffffff",
        ]
        return base[n]
    if n < 232:
        n -= 16
        levels = [0, 95, 135, 175, 215, 255]
        r, g, b = levels[n // 36], levels[(n // 6) % 6], levels[n % 6]
        return f"#{r:02x}{g:02x}{b:02x}"
    v = 8 + (n - 232) * 10
    return f"#{v:02x}{v:02x}{v:02x}"


def parse_sgr(params: str, state: dict, theme: dict) -> None:
    """就地更新前景色 / 粗体状态。state 跨行保持，与真实终端一致。"""
    codes = [int(c) if c else 0 for c in params.split(";")] or [0]
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            state["fg"] = None
            state["bold"] = False
        elif c == 1:
            state["bold"] = True
        elif c == 22:
            state["bold"] = False
        elif 30 <= c <= 37:
            state["fg"] = theme["palette"][c - 30]
        elif 90 <= c <= 97:
            state["fg"] = theme["palette"][c - 90 + 8]
        elif c == 39:
            state["fg"] = None
        elif c == 38 or c == 48:
            is_fg = c == 38
            # 38;5;n  或  38;2;r;g;b
            if i + 1 < len(codes) and codes[i + 1] == 5 and i + 2 < len(codes):
                col = xterm256(codes[i + 2])
                i += 2
            elif i + 1 < len(codes) and codes[i + 1] == 2 and i + 4 < len(codes):
                col = "#%02x%02x%02x" % tuple(min(255, max(0, v)) for v in codes[i + 2:i + 5])
                i += 4
            else:
                col = None
            if is_fg and col:
                state["fg"] = col
        i += 1


def expand_tabs(s: str, tabstop: int = 8) -> str:
    out = []
    col = 0
    for ch in s:
        if ch == "\t":
            n = tabstop - (col % tabstop)
            out.append(" " * n)
            col += n
        else:
            out.append(ch)
            col += char_width(ch)
    return "".join(out)


def apply_backspaces(s: str) -> str:
    """把退格渲染成覆盖效果：progress/percent 那类原地刷新就是靠 \\b 打的。

    真实终端里退格只回退光标不擦除，后续字符覆盖在旧字符上；这里按同列
    覆盖近似还原，超出当前行长的部分直接追加。
    """
    if "\b" not in s:
        return s
    cur: list[str] = []
    for ch in s:
        if ch == "\b":
            if cur:
                cur.pop()
        else:
            cur.append(ch)
    return "".join(cur)


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(s: str) -> int:
    return sum(char_width(c) for c in s)


def parse_ansi(text: str, theme: dict, keep_cr: bool = False):
    """-> [[(text, fg_or_None), ...], ...]  每个内层列表是一行里的着色片段。"""
    # 双重编码要先修：否则 emoji 会退化成几个 latin-1 字符，既占错宽度也渲染成乱码
    text = fix_mojibake(text)
    text = OSC_RE.sub("", text)
    text = CSI_RE.sub(lambda m: m.group(0) if m.group(0).endswith("m") else "", text)
    text = ESC_OTHER_RE.sub("", text)
    # 退格覆盖要先于切行处理，否则跨片段的 \b 会被吃掉半截
    text = apply_backspaces(text)
    text = CTRL_RE.sub("", text)

    rows = []
    state = {"fg": None, "bold": False}
    for raw in text.split("\n"):
        if raw.endswith("\r"):
            raw = raw[:-1]
        if "\r" in raw:
            # 真实终端里 \r 会把光标拉回行首覆盖，静态图取最终可见的一段
            raw = raw if keep_cr else raw.split("\r")[-1]
        segs = []
        pos = 0
        for m in SGR_RE.finditer(raw):
            if m.start() > pos:
                segs.append((expand_tabs(raw[pos:m.start()]), state["fg"]))
            parse_sgr(m.group(1), state, theme)
            pos = m.end()
        if pos < len(raw):
            segs.append((expand_tabs(raw[pos:]), state["fg"]))
        rows.append([(t, f) for t, f in segs if t])
    return rows


# ---------------------------------------------------------------- 折行

def wrap_rows(rows, cols: int):
    out = []
    for segs in rows:
        cur, curw = [], 0
        for t, fg in segs:
            while t:
                if curw >= cols:
                    out.append(cur)
                    cur, curw = [], 0
                room = cols - curw
                take, w = 0, 0
                for ch in t:
                    cw = char_width(ch)
                    if w + cw > room:
                        break
                    w += cw
                    take += 1
                if take == 0:
                    out.append(cur)
                    cur, curw = [], 0
                    continue
                cur.append((t[:take], fg))
                curw += w
                t = t[take:]
        out.append(cur)
    return out


# ---------------------------------------------------------------- 主题

THEMES = {
    "onedark": {
        "bg": "#282c34", "fg": "#abb2bf",
        "chrome_bg": "#21252b", "chrome_fg": "#9da5b4", "border": "#181a1f",
        "palette": [
            "#282c34", "#e06c75", "#98c379", "#e5c07b",
            "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
            "#5c6370", "#e06c75", "#98c379", "#e5c07b",
            "#61afef", "#c678dd", "#56b6c2", "#ffffff",
        ],
    },
    "dracula": {
        "bg": "#282a36", "fg": "#f8f8f2",
        "chrome_bg": "#21222c", "chrome_fg": "#a9a9c2", "border": "#191a21",
        "palette": [
            "#21222c", "#ff5555", "#50fa7b", "#f1fa8c",
            "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
            "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5",
            "#d6acff", "#ff92df", "#a4ffff", "#ffffff",
        ],
    },
    "monokai": {
        "bg": "#272822", "fg": "#f8f8f2",
        "chrome_bg": "#1e1f1c", "chrome_fg": "#a0a09a", "border": "#141510",
        "palette": [
            "#272822", "#f92672", "#a6e22e", "#f4bf75",
            "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
            "#75715e", "#f92672", "#a6e22e", "#f4bf75",
            "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5",
        ],
    },
    "solarized-light": {
        "bg": "#fdf6e3", "fg": "#586e75",
        "chrome_bg": "#eee8d5", "chrome_fg": "#657b83", "border": "#d9d2be",
        "palette": [
            "#073642", "#dc322f", "#859900", "#b58900",
            "#268bd2", "#d33682", "#2aa198", "#eee8d5",
            "#002b36", "#cb4b16", "#586e75", "#657b83",
            "#839496", "#6c71c4", "#93a1a1", "#fdf6e3",
        ],
    },
    "github-light": {
        "bg": "#ffffff", "fg": "#24292f",
        "chrome_bg": "#f6f8fa", "chrome_fg": "#57606a", "border": "#d0d7de",
        "palette": [
            "#24292f", "#cf222e", "#116329", "#4d2d00",
            "#0969da", "#8250df", "#1b7c83", "#6e7781",
            "#57606a", "#a40e26", "#1a7f37", "#633c01",
            "#218bff", "#a475f9", "#3192aa", "#8c959f",
        ],
    },
}

# Menlo / SF Mono / DejaVu Sans Mono 的 advance width 都是 1233/2048 em
GLYPH_RATIO = 1233 / 2048  # ≈ 0.602
ASCENT = 0.928             # hhea ascent, em
DESCENT = 0.236


# ---------------------------------------------------------------- SVG 生成

def esc(s: str) -> str:
    # 出口兜底：连同 \r 一起清干净，杜绝非法字符漏进渲染器
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def round_rect_path(x, y, w, h, rt, rb) -> str:
    rt = min(rt, h / 2, w / 2)
    rb = min(rb, h / 2, w / 2)
    return (
        f"M {x + rt} {y} H {x + w - rt} A {rt} {rt} 0 0 1 {x + w} {y + rt} "
        f"V {y + h - rb} A {rb} {rb} 0 0 1 {x + w - rb} {y + h} "
        f"H {x + rb} A {rb} {rb} 0 0 1 {x} {y + h - rb} "
        f"V {y + rt} A {rt} {rt} 0 0 1 {x + rt} {y} Z"
    )


def build_svg(rows, o, theme, title) -> str:
    fs = o.font_size
    lh = fs * o.line_height
    cw = fs * o.glyph_ratio
    cols = max((display_width("".join(t for t, _ in r)) for r in rows), default=0)
    cols = max(cols, o.min_cols)
    bar_h = 0 if (o.no_titlebar or o.no_window) else round(fs * 2.4)
    pad = o.padding

    W = round(pad * 2 + cols * cw)
    body_h = round(pad * 2 + len(rows) * lh)
    H = body_h + bar_h

    base_y = bar_h + pad + (lh - fs * (ASCENT + DESCENT)) / 2 + fs * ASCENT
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
    ]
    if o.no_window:
        out.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{theme["bg"]}"/>')
    else:
        r = fs * 0.75
        out.append(
            f'<path d="{round_rect_path(0, 0, W, H, r, r)}" fill="{theme["bg"]}" '
            f'stroke="{theme["border"]}" stroke-width="1"/>'
        )
        if bar_h:
            out.append(
                f'<path d="{round_rect_path(0, 0, W, bar_h, r, 0)}" '
                f'fill="{theme["chrome_bg"]}"/>'
            )
            dots = ["#ff5f57", "#febc2e", "#28c840"]
            dx = fs * 1.1
            for k, col in enumerate(dots):
                out.append(
                    f'<circle cx="{dx + k * fs:.2f}" cy="{bar_h / 2:.2f}" '
                    f'r="{fs * 0.34:.2f}" fill="{col}"/>'
                )
            if title:
                out.append(
                    f'<text x="{W / 2:.1f}" y="{bar_h / 2 + fs * 0.36:.2f}" fill="{theme["chrome_fg"]}" '
                    f'font-family="{o.font_family}" font-size="{fs * 0.82:.2f}" '
                    f'text-anchor="middle">{esc(title)}</text>'
                )

    out.append(
        f'<g font-family="{o.font_family}" font-size="{fs}" '
        f'font-weight="{o.font_weight}" fill="{theme["fg"]}" xml:space="preserve">'
    )
    x0 = pad
    for i, segs in enumerate(rows):
        y = base_y + i * lh
        if not segs:
            continue
        spans = "".join(
            f'<tspan fill="{fg or theme["fg"]}">{esc(t)}</tspan>' for t, fg in segs
        )
        out.append(f'<text x="{x0:.1f}" y="{y:.2f}">{spans}</text>')
    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="ansi2png",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", help="日志文件路径，或 - 表示 stdin")
    p.add_argument("-o", "--output", help="输出 PNG 路径（默认 <input>.png）")
    p.add_argument("--title", help="窗口标题栏文字（默认取文件名）")
    p.add_argument("--theme", default="onedark", choices=sorted(THEMES))
    p.add_argument("--font", dest="font_family", default="SF Mono, Menlo, Monaco, Consolas, monospace",
                   help="CSS font-family 列表")
    p.add_argument("--font-weight", default="normal")
    p.add_argument("--font-size", type=float, default=13, help="逻辑字号 px（默认 13）")
    p.add_argument("--line-height", type=float, default=1.4, help="行高倍数（默认 1.4）")
    p.add_argument("--padding", type=float, default=16, help="内边距 px（默认 16）")
    p.add_argument("--scale", type=float, default=2, help="输出像素倍率（默认 2 = HiDPI）")
    p.add_argument("--head", type=int, help="只保留前 N 行")
    p.add_argument("--tail", type=int, help="只保留后 N 行")
    p.add_argument("--wrap", type=int, metavar="COLS", help="按列数硬折行")
    p.add_argument("--min-cols", type=int, default=0, help="窗口至少 N 列宽（默认自适应）")
    p.add_argument("--no-titlebar", action="store_true", help="去掉标题栏，保留圆角窗")
    p.add_argument("--no-window", action="store_true", help="纯色背景，无窗口装饰")
    p.add_argument("--keep-cr", action="store_true", help=r"保留 \r 之前的内容")
    p.add_argument("--svg-only", action="store_true", help="只输出 .svg，不做 PNG")
    p.add_argument("--print-svg", action="store_true", help="把 SVG 打到 stdout")
    p.add_argument("--copy", action="store_true", help="渲染后复制 PNG 到剪贴板（macOS）")
    p.add_argument("--glyph-ratio", type=float, default=GLYPH_RATIO,
                   help=f"等宽字符 advance/em（默认 {GLYPH_RATIO:.5f} = Menlo/SF Mono）")
    o = p.parse_args(argv)

    theme = THEMES[o.theme]

    if o.input == "-":
        text = sys.stdin.read()
        default_name = "stdin"
    else:
        src = Path(o.input)
        if not src.is_file():
            print(f"ansi2png: 找不到输入文件 {src}", file=sys.stderr)
            return 2
        text = src.read_text(encoding="utf-8", errors="replace")
        default_name = src.name

    rows = parse_ansi(text, theme, keep_cr=o.keep_cr)
    if o.head:
        rows = rows[: o.head]
    if o.tail:
        rows = rows[-o.tail:]
    if o.wrap:
        rows = wrap_rows(rows, o.wrap)

    title = o.title if o.title is not None else default_name
    svg = build_svg(rows, o, theme, title)

    out_png = Path(o.output) if o.output else (
        Path(default_name + ".png") if o.input == "-" else Path(o.input).with_suffix(".png")
    )
    svg_path = out_png.with_suffix(".svg")

    if o.print_svg:
        print(svg)
        return 0

    if o.svg_only:
        svg_path.write_text(svg, encoding="utf-8")
        print(f"SVG  -> {svg_path}")
        return 0

    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        svg_path.write_text(svg, encoding="utf-8")
        print("ansi2png: 未找到 rsvg-convert，已退回输出 SVG。", file=sys.stderr)
        print("  安装: brew install librsvg", file=sys.stderr)
        print(f"SVG  -> {svg_path}")
        return 1

    cmd = [rsvg, "-z", str(o.scale), "-o", str(out_png), "-"]
    try:
        subprocess.run(cmd, input=svg.encode(), check=True)
    except subprocess.CalledProcessError as e:
        print(f"ansi2png: rsvg-convert 失败: {e}", file=sys.stderr)
        return 1

    dims = "?"
    try:
        from struct import unpack
        with open(out_png, "rb") as fh:
            head = fh.read(33)
        dims = f"{unpack('>I', head[16:20])[0]}x{unpack('>I', head[20:24])[0]}"
    except Exception:
        pass
    size_kb = out_png.stat().st_size / 1024
    print(f"PNG  -> {out_png}  ({dims}, {size_kb:.0f} KB, {len(rows)} 行, {o.theme})")

    if o.copy and sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e",
             f'set the clipboard to (read (POSIX file "{out_png.resolve()}") as «class PNGf»)'],
            check=False,
        )
        print("已复制到剪贴板")
    return 0


if __name__ == "__main__":
    sys.exit(main())
