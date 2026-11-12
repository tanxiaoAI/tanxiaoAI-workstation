#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把工作台 CSS 收敛到《工作台视觉规范 v1.0》。

只改「值」，不改结构：选择器、声明顺序、布局属性一律原样。
颜色 → 26 个 token；字号 → 6 级；圆角 → 6 种；投影 → 2 档。
"""
import re, sys, glob, os, colorsys

TOKENS = {
    'neutral': ['F6F7F8', 'FFFFFF', 'FAFBFB', 'F4F6F7', 'EDEFF1', 'E2E5E8',
                'AEB4BB', '7E858E', '5A6068', '3A4046', '111316'],
    'accent':  ['1A6B4E', '0F4A36', 'C9DED6', 'EDF4F1'],
    'warn':    ['B45309', 'FDF5EA', 'F0DCBE'],
    'danger':  ['A83C31', 'FDF1EF', 'F0D5D1'],
    'info':    ['2F5D8A', 'EEF3F9', 'D6E2EF'],
}

def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) == 8:
        h = h[:6]
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def rgb2hex(r, g, b):
    return '#%02X%02X%02X' % (max(0, min(255, int(round(r)))),
                              max(0, min(255, int(round(g)))),
                              max(0, min(255, int(round(b)))))

def hsl(rgb):
    r, g, b = [x / 255 for x in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, s, l

def nearest(rgb, family):
    """在同族里按 RGB 距离找最近的 token。"""
    best, dist = None, 1e9
    for t in TOKENS[family]:
        tr = hex2rgb(t)
        d = sum((a - b) ** 2 for a, b in zip(rgb, tr))
        if d < dist:
            best, dist = t, d
    return best

def map_color(rgb, sel=''):
    h, sat, l = hsl(rgb)
    chroma = max(rgb) - min(rgb)

    if l < 0.16:               # 任何极暗色都归墨色，不保留色相
        return '#111316'
    if l > 0.985:
        return '#FFFFFF'
    if chroma <= 3:            # 真正的灰
        return '#' + nearest(rgb, 'neutral')

    # 近白带一点点染色：默认当灰（卡片底、区块底不该有颜色）；
    # 只有状态标签才保留那一点点绿
    if l > 0.86 and chroma <= 20:
        if re.search(r'(tag|badge|status|pill|chip|ok\b|success|done|pass|'
                     r'info|warn|danger|error|alert|notice|hint)', sel):
            if 75 <= h < 175:
                return '#EDF4F1'
            if h >= 340 or h < 20:
                return '#FDF1EF'
            if 20 <= h < 55:
                return '#FDF5EA'
            if 175 <= h < 260:
                return '#EEF3F9'
        return '#' + nearest(rgb, 'neutral')

    pale = l >= 0.78
    if 250 <= h < 340:         # 紫灰：一律当灰，不做色相
        return '#' + nearest(rgb, 'neutral')
    if not pale and chroma <= 26:   # 低彩度的中深色（灰蓝 / 灰绿文字）也是灰
        return '#' + nearest(rgb, 'neutral')

    if 75 <= h < 175:          # 绿
        if pale:
            return '#EDF4F1' if l > 0.90 else '#C9DED6'
        return '#1A6B4E' if l > 0.45 else '#111316'
    if h >= 340 or h < 20:     # 红
        if pale:
            return '#FDF1EF' if l > 0.90 else '#F0D5D1'
        return '#A83C31'
    if 20 <= h < 55:           # 橙
        if pale:
            return '#FDF5EA' if l > 0.90 else '#F0DCBE'
        return '#B45309'
    if 175 <= h < 260:         # 蓝
        if pale:
            return '#EEF3F9' if l > 0.90 else '#D6E2EF'
        return '#2F5D8A'
    return '#' + nearest(rgb, 'neutral')

HEX_RE = re.compile(r'#([0-9a-fA-F]{3,8})\b')
RGBA_RE = re.compile(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)')

def is_prop_token(prefix):
    return bool(re.search(r'([#\w-]+)$', prefix))

def repl_hex(m):
    raw = m.group(1)
    if len(raw) not in (3, 6, 8):
        return m.group(0)
    try:
        rgb = hex2rgb(raw)
    except ValueError:
        return m.group(0)
    return map_color(rgb, selector_at(m.string, m.start()))

def repl_rgba(m):
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    a = m.group(4)
    # 纯白 / 纯黑的透明叠加保持黑白，只统一成墨白体系
    h, s, l = hsl((r, g, b))
    if s < 0.10 or 260 <= h < 340 or l < 0.16:
        nr, ng, nb = hex2rgb('111316' if l < 0.5 else 'FFFFFF')
    else:
        nr, ng, nb = hex2rgb(map_color((r, g, b), selector_at(m.string, m.start())))
    if a is None:
        return 'rgb(%d, %d, %d)' % (nr, ng, nb)
    return 'rgba(%d, %d, %d, %s)' % (nr, ng, nb, a)

def selector_at(s, pos):
    i = s.rfind('{', 0, pos)
    if i < 0:
        return ''
    j = s.rfind('}', 0, i)
    return ' '.join(s[j + 1:i].split())

FS_STEPS = [12, 14, 16, 20, 24, 32]

def map_fs(v):
    for step in FS_STEPS:
        if v <= step:
            return step
    return 32

RAD_ROLE = [
    (r'(avatar|dot\b|bullet|circle)', '50%'),
    (r'(modal|dialog|drawer|sheet|popup|popover|toast)', '20px'),
    (r'(input|select|textarea|field|search)', '10px'),
    (r'(chip|num\b|kbd)', '6px'),
    (r'(nav-item|nav-link|nav-cell|tab\b|sidebar-item)', '10px'),
    (r'(btn|button|tag|badge|pill|seg\b|seg-|step|switch|toggle)', '999px'),
    (r'(card|panel|tile|board|queue|table|acct|skill|plan)', '14px'),
]

def map_radius_value(val, sel):
    val = val.strip()
    if 'var(' in val or '%' in val or 'inherit' in val:
        return None
    if re.fullmatch(r'0(px)?', val):
        return None
    parts = re.split(r'\s+', val)
    if len(parts) == 1:
        m = re.fullmatch(r'([\d.]+)px', parts[0])
        if not m:
            return None
        n = float(m.group(1))
        for pat, out in RAD_ROLE:
            if re.search(pat, sel):
                # 抽屉/弹窗只换数值，不改成药丸
                return out
        if n <= 8:
            return '6px'
        if n <= 12:
            return '10px'
        if n <= 17:
            return '14px'
        return '20px'
    out = []
    changed = False
    for p in parts:
        if p == '0':
            out.append('0')
            continue
        m = re.fullmatch(r'([\d.]+)px', p)
        if not m:
            return None
        n = float(m.group(1))
        if n == 0:
            out.append('0')
            continue
        if n <= 8:
            out.append('6px')
        elif n <= 12:
            out.append('10px')
        elif n <= 17:
            out.append('14px')
        else:
            out.append('20px')
        changed = True
    return ' '.join(out) if changed else None

FLOAT_RE = re.compile(r'(modal|dialog|drawer|sheet|popup|popover|dropdown|menu|toast|tooltip|overlay)')

def map_shadow(val, sel):
    if 'inset' in val:
        return None
    if val.strip() in ('none', 'inherit'):
        return None
    if FLOAT_RE.search(sel):
        return 'var(--e2)'
    return 'none'

def transform(path, stats):
    s = open(path, encoding='utf-8').read()
    orig = s

    # --- 颜色（先做，后续按选择器判断不受影响） ---
    s = RGBA_RE.sub(repl_rgba, s)
    s = HEX_RE.sub(repl_hex, s)

    # --- 逐条声明 ---
    def on_decl(m):
        prop = m.group(1)
        val = m.group(2)
        pos = m.start()
        sel = selector_at(m.string, pos)
        if prop == 'font-size':
            mm = re.fullmatch(r'\s*([\d.]+)px\s*(!important)?\s*', val)
            if mm:
                n = float(mm.group(1))
                out = map_fs(n)
                if out != n:
                    stats['fs'] += 1
                imp = ' !important' if mm.group(2) else ''
                return 'font-size: %dpx%s' % (out, imp)
        if prop == 'border-radius':
            out = map_radius_value(val, sel)
            if out and out != val.strip():
                stats['rad'] += 1
                return 'border-radius: ' + out
        if prop == 'box-shadow':
            out = map_shadow(val, sel)
            if out and out.strip() != val.strip():
                stats['shadow'] += 1
                return 'box-shadow: ' + out
        return m.group(0)

    s = re.sub(r'(--paper\s*:\s*)[^;]+;', lambda m: m.group(1) + '#FFFFFF;', s)

    s = re.sub(r'(?<![\w-])(font-size|border-radius|box-shadow)\s*:\s*([^;}]+)', on_decl, s)

    if s != orig:
        open(path, 'w', encoding='utf-8').write(s)
        return True
    return False

def main():
    root = os.path.dirname(os.path.abspath(__file__))
    files = [f for f in sorted(glob.glob(os.path.join(root, '*.css')))
             if os.path.basename(f) not in ('tokens.css', 'theme-2.css')]
    stats = {'fs': 0, 'rad': 0, 'shadow': 0}
    for f in files:
        changed = transform(f, stats)
        print(('  changed  ' if changed else '  same     ') + os.path.basename(f))
    print('改动：字号 %d 处，圆角 %d 处，投影 %d 处' % (stats['fs'], stats['rad'], stats['shadow']))

if __name__ == '__main__':
    main()
