# -*- coding: utf-8 -*-
"""斑马鱼文献年度分布柱状图（WoS Refine 导出，1971~2027）。

数据源：refine (1).xlsx —— Web of Science「Refine by Publication Years」导出，
        单表两列 (Publication Years, Count)，脚本直接读取 xlsx，零手工转录。
口径：从 1971 年起；1971~1980、1981~1990、1991~2000 各合并为一根十年分箱柱；
      2001~2027 逐年一根柱，共 30 根。
版式：参照 35bd9495…png —— 浅蓝实心柱、柱顶横向数值标签、L 形坐标轴、
      无网格、左上图例、斜线柱表示不完整年份(2026~2027)。
颜色：仿照 97f22b54…png，柱体主色 #A8D4F0（亮浅蓝），斜线描边同色。
"""
import re
import zipfile
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

XLSX = (r'D:/微信/聊天记录/xwechat_files/wxid_o5raoih31agz22_bfd5/temp/'
        r'RWTemp/2026-11/c98514ad57383e4de52269dad3bd574d/refine (1).xlsx')
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def read_year_counts(path):
    """用标准库解析 xlsx，返回 {年份: 篇数}。"""
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    out = {}
    for row in root.findall('.//' + NS + 'row'):
        cells = {}
        for c in row.findall(NS + 'c'):
            col = re.match(r'[A-Z]+', c.get('r')).group()
            v = c.find(NS + 'v')
            val = '' if v is None else (v.text or '')
            if c.get('t') == 'inlineStr':
                val = ''.join(t.text or '' for t in c.iter(NS + 't'))
            cells[col] = val
        a, b = cells.get('A', ''), cells.get('B', '')
        if a.isdigit() and b.isdigit():
            assert int(a) not in out, 'xlsx 中存在重复年份: %s' % a
            out[int(a)] = int(b)
    return out


DATA = read_year_counts(XLSX)
START, END = 1971, 2027
assert all(y in DATA for y in range(START, END + 1)), '1971~2027 有年份缺失'

G1 = sum(DATA[y] for y in range(1971, 1981))   # 1971~1980
G2 = sum(DATA[y] for y in range(1981, 1991))   # 1981~1990
G3 = sum(DATA[y] for y in range(1991, 2001))   # 1991~2000
YEARS = list(range(2001, END + 1))
keys = ['1971~1980', '1981~1990', '1991~2000'] + [str(y) for y in YEARS]
vals = [G1, G2, G3] + [DATA[y] for y in YEARS]

EXPECTED = sum(DATA[y] for y in range(START, END + 1))
assert len(keys) == len(vals) == 30
assert sum(vals) == EXPECTED, (sum(vals), EXPECTED)
print('xlsx years =', len(DATA), '| full-file total =', sum(DATA.values()))
print('excluded (<1971) =', sum(v for y, v in DATA.items() if y < START))
print('G1 1971~1980 =', G1, '| G2 1981~1990 =', G2, '| G3 1991~2000 =', G3)
print('bars =', len(vals), '| plotted total =', sum(vals),
      '| expected 1971-2027 =', EXPECTED)
print('peak =', max(vals), 'at', keys[vals.index(max(vals))])

BLUE = '#A8D4F0'          # 亮浅蓝（仿照 97f22b54…png 采样主色 (168,212,240)）
EDGE = '#A8D4F0'           # 斜线柱描边同色

names = {f.name for f in font_manager.fontManager.ttflist}
SERIF = 'Times New Roman' if 'Times New Roman' in names else 'DejaVu Serif'
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': [SERIF, 'DejaVu Serif'],
    'hatch.linewidth': 0.7,
    'svg.fonttype': 'none',
})

fig, ax = plt.subplots(figsize=(13.5, 6.0), dpi=300)
fig.patch.set_facecolor('white')
ax.set_facecolor('white')

x = list(range(len(keys)))
# 2026 当年未结束、2027 提前上线，均视为不完整年份（斜线柱）
hatched = [i for i, k in enumerate(keys) if k in ('2026', '2027')]
solid = [i for i in range(len(keys)) if i not in hatched]

ax.bar([x[i] for i in solid], [vals[i] for i in solid], width=0.62,
       color=BLUE, edgecolor='none', zorder=2)
ax.bar([x[i] for i in hatched], [vals[i] for i in hatched], width=0.62,
       facecolor='white', edgecolor=EDGE, hatch='////', linewidth=0.8, zorder=2)

YMAX = 9000
for i, v in enumerate(vals):
    ax.text(x[i], v + YMAX * 0.013, f'{v:,}', rotation=0, ha='center', va='bottom',
            fontsize=7.5, color='#333333', zorder=3)

ax.set_ylim(0, YMAX)
ax.set_xlim(-0.75, len(keys) - 0.25)
ax.set_yticks([0, 2000, 4000, 6000, 8000, 9000])
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f'{int(v):,}'))

display = {'1971~1980': '1971~\n1980', '1981~1990': '1981~\n1990',
            '1991~2000': '1991~\n2000'}
tick_keys = ['1971~1980', '1981~1990', '1991~2000',
             '2005', '2010', '2015', '2020', '2025', '2026']
ax.set_xticks([keys.index(k) for k in tick_keys])
ax.set_xticklabels([display.get(k, k) for k in tick_keys])

ax.set_ylabel('Number of publications', fontsize=11, labelpad=12)
ax.set_xlabel('Publication year', fontsize=11, labelpad=10)

for s in ('top', 'right'):
    ax.spines[s].set_visible(False)
for s in ('left', 'bottom'):
    ax.spines[s].set_color('#777777')
    ax.spines[s].set_linewidth(0.8)
ax.tick_params(axis='both', which='both', length=0, labelsize=9,
               colors='#333333', pad=6)

legend_handles = [
    Patch(facecolor=BLUE, edgecolor='none', label='1971~2025'),
    Patch(facecolor='white', edgecolor=EDGE, hatch='////',
          label='2026~2027 (partial)'),
]
ax.legend(handles=legend_handles, loc='upper left', bbox_to_anchor=(0.0, 1.115),
          ncol=2, frameon=False, fontsize=9, handlelength=1.0,
          handleheight=1.0, columnspacing=1.6, handletextpad=0.6,
          borderpad=0, borderaxespad=0)

fig.subplots_adjust(left=0.055, right=0.992, top=0.86, bottom=0.17)

base = r'D:/PycharmProjects/斑马鱼基因问答体/斑马鱼文献数量年度分布_1971-2027_WoS'
fig.savefig(base + '.png', dpi=300, facecolor='white')
fig.savefig(base + '.svg', facecolor='white')
print('saved:', base + '.png', base + '.svg')
