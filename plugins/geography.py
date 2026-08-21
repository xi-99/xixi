# -*- coding: utf-8 -*-
"""
WORLD3/plugins/geography.py —— 地理隔离插件（独立，仅影响移动逻辑）。

在网格上生成地形矩阵 grid.terrain：
  0 = 平原（正常通行）
  1 = 山脉（不可通行：move_agent 拦截，返回 False）
  2 = 河流（通行额外消耗 2 × move_cost，散落回起点格——守恒）

移动拦截/耗能由内核 world_engine.move_agent 统一执行（检测 grid.terrain），
本插件只负责地形生成与渲染，不干预其他插件行为。

惰性初始化：on_tick 首次运行时（或地图尺寸变化时）重建地形。
"""
import numpy as np

DESCRIPTION = '地理隔离：山脉不可通行 / 河流通行耗能翻倍（独立）'

# 生态类插件默认禁用：避免改变既有实验的默认行为（侧边栏可手动开启）
DEFAULT_ENABLED = False
PRIORITY = 0

MOUNTAIN = 1
RIVER = 2
RIVER_COST_MULTIPLIER = 2.0


def register(world):
    pass


def _build_terrain(world):
    """按山脉/河流占比随机生成地形矩阵（河流优先于山脉）。"""
    size = world.size
    rng = world.rng
    mr = float(world.params.get('mountain_ratio', 0.15))
    rr = float(world.params.get('river_ratio', 0.1))
    t = np.zeros((size, size), dtype=np.int8)
    t[rng.random((size, size)) < mr] = MOUNTAIN
    t[rng.random((size, size)) < rr] = RIVER
    world.grid.terrain = t


def on_tick(agents, grid):
    world = grid.world
    terrain = getattr(grid, 'terrain', None)
    if terrain is None or terrain.shape[0] != world.size:
        _build_terrain(world)


def on_render(ax, agents, grid):
    """绘制山脉（深灰方块）与河流（蓝色半透明方块）。"""
    world = grid.world
    if not world.params.get('terrain_visible', True):
        return
    t = getattr(grid, 'terrain', None)
    if t is None:
        return
    ys, xs = np.nonzero(t == MOUNTAIN)
    if xs.size:
        ax.scatter(xs, ys, s=4, marker='s', c='#484f58', alpha=0.55,
                   linewidths=0, zorder=1)
    ys, xs = np.nonzero(t == RIVER)
    if xs.size:
        ax.scatter(xs, ys, s=4, marker='s', c='#58a6ff', alpha=0.35,
                   linewidths=0, zorder=1)


def ui_controls(world):
    """参数由 app.py 统一渲染（PARAM_SPECS 归属 geography）。"""
    pass
