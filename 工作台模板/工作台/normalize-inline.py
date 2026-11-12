#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内联色归一：把 JS / HTML 里写死的颜色收进规范调色板。

- <svg>…</svg> 内的颜色是装饰插画 → 一律走中性阶（不留色相）
- 其余（数据系列、状态色）→ 走正常调色板映射
- 时段配色（TIME_GROUPS）单独替换成中性阶梯，四段仍可区分
"""
import re, glob, os, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("nz", os.path.join(HERE, "normalize-css.py"))
nz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nz)

NEUTRAL = ['F6F7F8', 'FFFFFF', 'FAFBFB', 'F4F6F7', 'EDEFF1', 'E2E5E8',
           'AEB4BB', '7E858E', '5A6068', '3A4046', '111316']

def neutral_of(hexstr):
    rgb = nz.hex2rgb(hexstr)
    best, dist = None, 1e9
    for t in NEUTRAL:
        tr = nz.hex2rgb(t)
        d = sum((a - b) ** 2 for a, b in zip(rgb, tr))
        if d < dist:
            best, dist = t, d
    return '#' + best

HEX = re.compile(r'#([0-9a-fA-F]{6})\b')

def map_svg(block):
    return HEX.sub(lambda m: neutral_of(m.group(1)), block)

TIME_RE = re.compile(r'\[\s*"(凌晨 00–06|上午 06–12|下午 12–18|晚间 18–24)"\s*,\s*"#[0-9a-fA-F]{6}"')
TIME_MAP = {'凌晨 00–06': '#AEB4BB', '上午 06–12': '#7E858E',
            '下午 12–18': '#3A4046', '晚间 18–24': '#111316'}

def fix_time_groups(s):
    def sub(m):
        return '["%s", "%s"' % (m.group(1), TIME_MAP[m.group(1)])
    return TIME_RE.sub(sub, s)

def transform(path):
    s = open(path, encoding='utf-8').read()
    orig = s

    s = fix_time_groups(s)

    # 逐段处理：svg 块中性化，其余走调色板
    out, last = [], 0
    for m in re.finditer(r'<svg[\s\S]*?</svg>', s):
        out.append(HEX.sub(lambda mm: nz.map_color(nz.hex2rgb(mm.group(1))), s[last:m.start()]))
        out.append(map_svg(m.group(0)))
        last = m.end()
    out.append(HEX.sub(lambda mm: nz.map_color(nz.hex2rgb(mm.group(1))), s[last:]))
    s = ''.join(out)

    if s != orig:
        open(path, 'w', encoding='utf-8').write(s)
        return True
    return False

if __name__ == '__main__':
    n = 0
    for f in sorted(glob.glob(os.path.join(HERE, '*.js'))) + sorted(glob.glob(os.path.join(HERE, '*.html'))):
        if transform(f):
            print('changed ' + os.path.basename(f))
            n += 1
    print('共 %d 个文件' % n)
