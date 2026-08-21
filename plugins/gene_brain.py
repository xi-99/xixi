# -*- coding: utf-8 -*-
"""
WORLD3/plugins/gene_brain.py —— 数字基因大脑插件（v2.0 基因纪元）。

替换 v1 的决心机制（plugins/focus.py）：
  - 没有任何 if-else 觅食规则；
  - 感知 4 个标量 → 感知机（纯权重）→ softmax 采样 → 移动；
  - 行为完全由 genome 决定（出生随机，进化引擎负责优化）。

感知输入（4 维，见 evolution.py）：
  x0 最近食物距离 / 视野（归一化 0~1；视野内无食物 = 1.0）
  x1 最近食物方向 sin 分量（归一化 -1~1；无食物 = 0）
  x2 最近食物方向 cos 分量（归一化 -1~1；无食物 = 0）
  x3 当前能量 / 300（归一化 0~1）

输出 5 个动作概率：上 / 下 / 左 / 右 / 驻留。
进食、代谢、死亡判定仍由 metabolism.py 负责——本插件只管"往哪走"。

与 focus.py 互斥：focus 启用时本插件自动让位（硬编码脑优先），
UI 的"Agent 模式"开关会替你切换两者，不会出现双重移动。
"""
import numpy as np

DESCRIPTION = '数字基因大脑：感知机（随机权重）驱动移动，供进化引擎自然选择'

import sys
import os
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
from evolution import (ENERGY_SCALE, forward, random_genome, sample_move,
                       softmax)  # noqa: E402


def register(world):
    pass


def _nearest_food(agent, grid, world, view_range):
    """
    视野内最近的（含能量）格：返回 (距离, 有符号 dx, 有符号 dy)。
    环形最短路径；无食物返回 None。向量化实现，避免逐格 Python 循环。
    """
    size = world.size
    x0, y0 = agent['x'], agent['y']
    r = view_range
    ys = (y0 + np.arange(-r, r + 1)) % size
    xs = (x0 + np.arange(-r, r + 1)) % size
    patch = grid.data[np.ix_(ys, xs)]
    nz = np.nonzero(patch)
    if nz[0].size == 0:
        return None
    ox = nz[1] - r                      # 原始 x 偏移（未考虑环绕）
    oy = nz[0] - r
    ax, ay = np.abs(ox), np.abs(oy)
    # 有符号最短偏移：若绕行更近，符号取反
    sx = np.where(ax <= size - ax, ox, -np.sign(ox) * (size - ax))
    sy = np.where(ay <= size - ay, oy, -np.sign(oy) * (size - ay))
    d = np.maximum(np.abs(sx), np.abs(sy))          # 切比雪夫距离
    k = int(np.argmin(d))
    return float(d[k]), float(sx[k]), float(sy[k])


def _inputs(agent, grid, world):
    """4 维感知向量。"""
    p = agent['params']
    view_range = int(p['view_range'])
    e_norm = min(float(agent['energy']) / ENERGY_SCALE, 1.0)
    near = _nearest_food(agent, grid, world, view_range)
    if near is None:
        return np.array([1.0, 0.0, 0.0, e_norm])
    d, sx, sy = near
    r = max(view_range, 1)
    return np.array([min(d / r, 1.0), sx / r, sy / r, e_norm])


def on_tick(agents, grid):
    world = grid.world
    # 互斥保护：硬编码脑启用时让位（UI 开关保证互斥，这里是双保险）
    if world.enabled.get('focus', True):
        return
    rng = world.rng
    for a in agents:
        if not a['alive'] or a['kind'] != 'prey':
            continue
        genome = a.get('genome')
        if genome is None:
            genome = random_genome(rng)      # 兜底：出生即随机本能
            a['genome'] = genome
        dx, dy = sample_move(genome, _inputs(a, grid, world), rng)
        if dx != 0 or dy != 0:
            world.move_agent(a, dx, dy)
        a['last_action'] = (dx, dy)          # 渲染/调试用


def on_render(ax, agents, grid):
    """叠加绘制：每个基因脑 Agent 的"当前意图"（淡金色短线，驻留不画）。"""
    for a in agents:
        if not a['alive'] or a['kind'] != 'prey':
            continue
        act = a.get('last_action')
        if not act or (act[0] == 0 and act[1] == 0):
            continue
        ax.plot([a['x'], a['x'] + act[0] * 0.8],
                [a['y'], a['y'] + act[1] * 0.8],
                color='#e3b341', alpha=0.35, linewidth=0.7)


def ui_controls(world):
    """进化参数由 app.py 统一渲染（PARAM_SPECS 归属 gene_brain）。"""
    pass
