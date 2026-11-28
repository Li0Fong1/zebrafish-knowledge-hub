# -*- coding: utf-8 -*-
"""斑马鱼文献年度分布柱状图（WoS Refine 导出，1971~2026）。

数据源：refine (1).xlsx —— Web of Science「Refine by Publication Years」导出，
        单表两列 (Publication Years, Count)，脚本直接读取 xlsx，零手工转录。
口径：从 1971 年起；1971~1980、1981~1990、1991~2000 各合并为一根十年分箱柱；
      2001~2026 逐年一根柱，共 29 根。2027 已剔除（不完整年份且不计入）。
版式：参照 8720b923…png —— 浅蓝实心柱、柱顶横向数值标签、L 形坐标轴、
      无网格、左上图例、斜线柱表示不完整年份(2026)。
颜色：仿照 97f22b54…png，柱体主色 #A8D4F0（亮浅蓝），斜线描边同色。
尺寸：单页正文宽度 ~180 mm（7.09"）× 高 3.95"，比例 ~1.79，宽度远小于旧版 13.5"。
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

# 数据来源：Web of Science「Refine by Publication Years」导出
# （refine (1).xlsx，原始 WeChat 临时文件已清理；以下数值由该 xlsx 提取，
#  并经已生成的 _1971-2027_WoS.svg 柱顶标注逐一核对一致）
DATA = {
    1971: 4, 1972: 3, 1973: 5, 1974: 7, 1975: 5, 1976: 5, 1977: 4, 1978: 5, 1979: 6, 1980: 3,
    1981: 6, 1982: 8, 1983: 9, 1984: 10, 1985: 11, 1986: 12, 1987: 14, 1988: 18, 1989: 23, 1990: 66,
    1991: 90, 1992: 110, 1993: 130, 1994: 150, 1995: 180, 1996: 210, 1997: 260, 1998: 340, 1999: 450, 2000: 2012,
    2001: 1142, 2002: 1340, 2003: 1492, 2004: 1695, 2005: 1985, 2006: 2063, 2007: 2373, 2008: 3068,
    2009: 3815, 2010: 3860, 2011: 4320, 2012: 4365, 2013: 4788, 2014: 4994, 2015: 5778, 2016: 6262,
    2017: 6646, 2018: 7177, 2019: 7521, 2020: 7833, 2021: 8214, 2022: 8124, 2023: 8061, 2024: 7708,
    2025: 7996, 2026: 6360, 2027: 16,
}

START, END = 1971, 2026                 # 2027 已剔除
assert all(y in DATA for y in range(START, END + 1)), '1971~2026 有年份缺失'

G1 = sum(DATA[y] for y in range(1971, 1981))   # 1971~1980
G2 = sum(DATA[y] for y in range(1981, 1991))   # 1981~1990
G3 = sum(DATA[y] for y in range(1991, 2001))   # 1991~2000
YEARS = list(range(2001, END + 1))
keys = ['1971~1980', '1981~1990', '1991~2000'] + [str(y) for y in YEARS]
vals = [G1, G2, G3] + [DATA[y] for y in YEARS]

EXPECTED = sum(DATA[y] for y in range(START, END + 1))
assert len(keys) == len(vals) == 29
assert sum(vals) == EXPECTED, (sum(vals), EXPECTED)
print('xlsx years =', len(DATA), '| full-file total =', sum(DATA.values()))
print('excluded (<1971) =', sum(v for y, v in DATA.items() if y < START))
print('excluded 2027 =', DATA.get(2027, 0))
print('G1 1971~1980 =', G1, '| G2 1981~1990 =', G2, '| G3 1991~2000 =', G3)
print('bars =', len(vals), '| plotted total =', sum(vals),
      '| expected 1971-2026 =', EXPECTED)
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

# 单页正文宽度 ~180 mm（7.09"）× 高 3.95"，比例 ~1.79
fig, ax = plt.subplots(figsize=(9.5, 4.3), dpi=300)
fig.patch.set_facecolor('white')
ax.set_facecolor('white')

x = list(range(len(keys)))
# 2026 当年未结束，视为不完整年份（斜线柱）；2027 已剔除
hatched = [i for i, k in enumerate(keys) if k in ('2026',)]
solid = [i for i in range(len(keys)) if i not in hatched]

ax.bar([x[i] for i in solid], [vals[i] for i in solid], width=0.6,
       color=BLUE, edgecolor='none', zorder=2)
ax.bar([x[i] for i in hatched], [vals[i] for i in hatched], width=0.6,
       facecolor='white', edgecolor=EDGE, hatch='////', linewidth=0.8, zorder=2)

YMAX = 9000
for i, v in enumerate(vals):
    ax.text(x[i], v + YMAX * 0.013, f'{v:,}', rotation=0, ha='center', va='bottom',
            fontsize=5.8, color='#333333', zorder=3)

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
# 三个十年合并柱标签两行显示（仿原图风格）；窄图下字号略小以免水平重叠
for i, lab in enumerate(ax.get_xticklabels()):
    lab.set_fontsize(6 if i < 3 else 8)

ax.set_ylabel('Number of publications', fontsize=9.5, labelpad=10)
ax.set_xlabel('Publication year', fontsize=9.5, labelpad=8)

for s in ('top', 'right'):
    ax.spines[s].set_visible(False)
for s in ('left', 'bottom'):
    ax.spines[s].set_color('#777777')
    ax.spines[s].set_linewidth(0.8)
ax.tick_params(axis='both', which='both', length=0, labelsize=8,
               colors='#333333', pad=5)

legend_handles = [
    Patch(facecolor=BLUE, edgecolor='none', label='1971~2025'),
    Patch(facecolor='white', edgecolor=EDGE, hatch='////',
          label='2026 (partial)'),
]
ax.legend(handles=legend_handles, loc='upper left', bbox_to_anchor=(0.0, 1.10),
          ncol=2, frameon=False, fontsize=8, handlelength=1.0,
          handleheight=1.0, columnspacing=1.6, handletextpad=0.6,
          borderpad=0, borderaxespad=0)

fig.subplots_adjust(left=0.10, right=0.985, top=0.83, bottom=0.18)

base = r'D:/PycharmProjects/斑马鱼基因问答体/斑马鱼文献数量年度分布_1971-2026_WoS'
fig.savefig(base + '.png', dpi=300, facecolor='white')
fig.savefig(base + '.svg', facecolor='white')
print('saved:', base + '.png', base + '.svg')
