# -*- coding: utf-8 -*-
"""
WORLD3/plugins/focus.py —— 决心机制插件。

目标锁定与分心：选定猎场后死磕到底，忽略中途诱惑。
  - 锁定的不是"一个格子"，而是"一片猎场"：目标被吃光时在旧目标
    附近（patch_radius）补位，不算重新决策。
  - 只有四种情况才真正重新思考：猎场枯竭 / 耐心耗尽 / 能量危急 / 走神（p）。
无此插件则 Agent 不会觅食，只会原地打转。
"""
import numpy as np

DESCRIPTION = '决心机制：目标锁定、猎场补位、分心概率'

PATIENCE_FACTOR = 1.5    # 耐心预算 = 初始距离 × 系数 + 常数
PATIENCE_BASE = 20
EMERGENCY_DEFAULT = 15.0
WANDER_PERSIST = 5


def register(world):
    pass


def on_tick(agents, grid):
    world = grid.world
    rng = world.rng

    for a in agents:
        if not a['alive'] or a['kind'] != 'prey':
            continue
        # 出生时参数快照——换代生效
        p = a['params']
        view_range = int(p['view_range'])
        distract_prob = float(p.get('hesitation_prob', p.get('distract_prob', 0.5)))
        emergency = float(p.get('emergency_energy', EMERGENCY_DEFAULT))
        patch_radius = int(p.get('patch_radius', 3))
        size = world.size

        # ---- 1. 目标是否失效？----
        # 注意：走神检查放最前——p>0 时即使正站在目标上也会"重新思考"，
        # 否则驻留型 Agent 的犹豫会被 arrived→补位 路径永久影子化
        reason = None
        if a['target'] is not None:
            tx, ty = a['target']
            if rng.random() < distract_prob:
                reason = 'distracted'                  # 走神（分心概率）
            elif grid[ty, tx] <= 0:
                reason = 'eaten'                       # 目标被吃光/消失
            elif a['x'] == tx and a['y'] == ty:
                reason = 'arrived'                     # 已到达（脚下已吃空）
            elif a['steps'] > a['init_dist'] * PATIENCE_FACTOR + PATIENCE_BASE:
                reason = 'patience'                    # 耐心耗尽
            elif a['energy'] < emergency:
                reason = 'emergency'                   # 危急：就近求生

        if reason is not None:
            # 目标被吃光/到达后吃空：先在猎场内补位，不打断决心
            if reason in ('eaten', 'arrived') and a['energy'] >= emergency:
                if _patch_retarget(a, grid, patch_radius):
                    _seek_step(a, grid, rng)
                    continue
            # 其余情况：真正重新思考
            a['target'] = None
            emergency_now = a['energy'] < emergency
            _pick_target(a, grid, view_range, emergency=emergency_now)
            a['switch_count'] += 1
        elif a['target'] is None:
            # 首次决策
            emergency_now = a['energy'] < emergency
            _pick_target(a, grid, view_range, emergency=emergency_now)
            a['switch_count'] += 1

        # ---- 2. 执行 ----
        if a['target'] is not None:
            _seek_step(a, grid, rng)
        else:
            _wander_step(a, grid, rng)


def _pick_target(agent, grid, view_range, emergency=False):
    """真正重新决策：紧急时最近优先，平时价值/距离打分。"""
    cells = _perceive(agent, grid, view_range)
    if not cells:
        agent['target'] = None
        return False
    if emergency:
        best = min(cells, key=lambda c: c[3])               # 求生：最近
    else:
        best = max(cells, key=lambda c: c[2] / (1.0 + c[3]))  # 价值/距离
    agent['target'] = (best[0], best[1])
    agent['init_dist'] = best[3]
    agent['steps'] = 0
    agent['last_dist'] = best[3]
    return True


def _patch_retarget(agent, grid, patch_radius):
    """猎场补位：旧目标附近找替代目标，不算重新决策。"""
    world = grid.world
    tx, ty = agent['target']
    size = world.size
    best = None
    best_score = -1.0
    for dy in range(-patch_radius, patch_radius + 1):
        for dx in range(-patch_radius, patch_radius + 1):
            wx = (tx + dx) % size
            wy = (ty + dy) % size
            v = grid[wy, wx]
            if v <= 0:
                continue
            d = world.dist_xy(agent['x'], agent['y'], wx, wy)
            score = float(v) / (1.0 + d)
            if score > best_score:
                best_score = score
                best = (wx, wy, d)
    if best is None:
        return False
    agent['target'] = (best[0], best[1])
    agent['init_dist'] = best[2]
    agent['steps'] = 0
    agent['last_dist'] = best[2]
    return True


def _perceive(agent, grid, view_range):
    """视野内的能量格列表: [(x, y, 能量, 距离)]（含脚下过滤）"""
    world = grid.world
    size = world.size
    out = []
    x0, y0 = agent['x'], agent['y']
    for dy in range(-view_range, view_range + 1):
        for dx in range(-view_range, view_range + 1):
            d = max(abs(dx), abs(dy))
            if d == 0:
                continue
            wx = (x0 + dx) % size
            wy = (y0 + dy) % size
            v = grid[wy, wx]
            if v > 0:
                out.append((wx, wy, float(v), d))
    return out


def _seek_step(agent, grid, rng):
    """朝锁定目标走一步（切比雪夫方向，走短边）"""
    world = grid.world
    size = world.size
    tx, ty = agent['target']
    if agent['x'] == tx and agent['y'] == ty:
        return
    dx = 0
    if tx != agent['x']:
        dx = 1 if (tx - agent['x']) % size <= size // 2 else -1
    dy = 0
    if ty != agent['y']:
        dy = 1 if (ty - agent['y']) % size <= size // 2 else -1
    if dx != 0 and dy != 0 and rng.random() < 0.5:
        dy = 0
    world.move_agent(agent, dx, dy)
    agent['steps'] += 1
    agent['last_dist'] = world.dist_xy(agent['x'], agent['y'], tx, ty)


def _wander_step(agent, grid, rng):
    """无目标游荡：保持方向 WANDER_PERSIST tick，避免原地打转"""
    world = grid.world
    if agent['wander_ticks'] <= 0:
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1),
                (1, 1), (1, -1), (-1, 1), (-1, -1)]
        agent['wander_dx'], agent['wander_dy'] = dirs[int(rng.integers(0, 8))]
        agent['wander_ticks'] = WANDER_PERSIST
    agent['wander_ticks'] -= 1
    world.move_agent(agent, agent['wander_dx'], agent['wander_dy'])


def on_render(ax, agents, grid):
    pass


def ui_controls(world):
    import streamlit as st
    st.caption('进阶参数（决心机制）')
    world.params['patch_radius'] = st.slider(
        '猎场补位半径', 1, 6, int(world.params.get('patch_radius', 3)),
        help='目标被吃光时，在旧目标附近多少格内找替代（同一片猎场）')
    world.params['emergency_energy'] = st.slider(
        '危急阈值', 5.0, 50.0, float(world.params.get('emergency_energy', 15.0)),
        step=1.0, help='能量低于此值转入就近求生（重新思考的合法理由）')
