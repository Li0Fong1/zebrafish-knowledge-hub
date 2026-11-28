# -*- coding: utf-8 -*-
"""斑马鱼文献年度分布柱状图（1971~2026）。

数据来源：用户提供的中文柱状图（1971~1980 / 1981~1990 / 1991~2000 三个十年分箱，
2001~2026 逐年），数值逐柱放大核对后原样录入，未做平滑或补值。
版式：沿用「2002-2027」版 —— Times New Roman 罗马体、淡蓝实心柱、柱顶竖排数值、
L 形坐标轴、无网格、左上图例、斜线柱表示不完整年份（2026）。
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

# 三个十年分箱为区间合计；其后为逐年发表数
GROUPS = [('1971~1980', 16), ('1981~1990', 90), ('1991~2000', 839)]
YEARLY = {
    2001: 211,   2002: 263,   2003: 297,   2004: 355,   2005: 706,
    2006: 693,   2007: 771,   2008: 802,   2009: 958,   2010: 1001,
    2011: 1159,  2012: 1256,  2013: 1472,  2014: 1476,  2015: 1526,
    2016: 1706,  2017: 1784,  2018: 2008,  2019: 2212,  2020: 2412,
    2021: 2631,  2022: 2713,  2023: 2541,  2024: 2723,  2025: 2851,
    2026: 1748,
}
keys = [k for k, _ in GROUPS] + [str(y) for y in sorted(YEARLY)]
vals = [v for _, v in GROUPS] + [YEARLY[y] for y in sorted(YEARLY)]
assert len(keys) == 29 and len(vals) == 29
print('bars =', len(vals), '| peak =', max(vals), '| total =', sum(vals))

BLUE = '#8FB3CA'   # 参考图柱体主色
EDGE = '#5B8CA9'   # 斜线柱描边色

# 罗马体（Times New Roman），缺失时回退 DejaVu Serif
names = {f.name for f in font_manager.fontManager.ttflist}
SERIF = 'Times New Roman' if 'Times New Roman' in names else 'DejaVu Serif'
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': [SERIF, 'DejaVu Serif'],
    'hatch.linewidth': 0.7,
    'svg.fonttype': 'none',   # SVG 中保留可编辑文字
})

fig, ax = plt.subplots(figsize=(14.5, 5.9), dpi=300)
fig.patch.set_facecolor('white')
ax.set_facecolor('white')

x = list(range(len(keys)))
hatched = [i for i, k in enumerate(keys) if k == '2026']   # 2026 为未完年份
solid = [i for i in range(len(keys)) if i not in hatched]

ax.bar([x[i] for i in solid], [vals[i] for i in solid], width=0.62,
       color=BLUE, edgecolor='none', zorder=2)
ax.bar([x[i] for i in hatched], [vals[i] for i in hatched], width=0.62,
       facecolor='white', edgecolor=EDGE, hatch='////', linewidth=0.8, zorder=2)

# 柱顶数值：竖排、带千位分隔符
for i, v in enumerate(vals):
    ax.text(x[i], v + 55, f'{v:,}', rotation=90, ha='center', va='bottom',
            fontsize=7.5, color='#333333', zorder=3)

ax.set_ylim(0, 3300)
ax.set_xlim(-0.75, len(keys) - 0.25)
ax.set_yticks(range(0, 3001, 1000))
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f'{int(v):,}'))

# 稀疏刻度：前三个十年分箱用两行区间标签，其后每 5 年一个
tick_keys = ['1971~1980', '1981~1990', '1991~2000',
             '2005', '2010', '2015', '2020', '2025']
display = {'1971~1980': '1971~\n1980',
           '1981~1990': '1981~\n1990',
           '1991~2000': '1991~\n2000'}
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
          label='2026 (partial)'),
]
ax.legend(handles=legend_handles, loc='upper left', bbox_to_anchor=(0.0, 1.115),
          ncol=2, frameon=False, fontsize=9, handlelength=1.0,
          handleheight=1.0, columnspacing=1.6, handletextpad=0.6,
          borderpad=0, borderaxespad=0)

fig.subplots_adjust(left=0.078, right=0.992, top=0.86, bottom=0.17)

base = r'D:/PycharmProjects/斑马鱼基因问答体/斑马鱼文献数量年度分布_1971-2026'
fig.savefig(base + '.png', dpi=300, facecolor='white')
fig.savefig(base + '.svg', facecolor='white')
print('saved:', base + '.png', base + '.svg')
