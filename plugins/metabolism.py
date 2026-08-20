# -*- coding: utf-8 -*-
"""
WORLD3/plugins/metabolism.py —— 基础代谢插件。

实现：走路消耗能量、吃能量点补充能量、基础代谢、能量死亡判定。
能量守恒：走路耗能→回到离开的格；基础代谢→散落到 1~3 格外；
死亡时负余额归还网格。无此插件则 Agent 永生。
"""

DESCRIPTION = '基础代谢：走路耗能、吃能量点、能量死亡判定'

EAT_RATE = 2.0          # 每 tick 最多进食量
METABOLISM = 0.1        # 基础代谢 / tick（散落到 1~3 格外）


def register(world):
    pass


def on_tick(agents, grid):
    world = grid.world

    for a in agents:
        if not a['alive']:
            continue
        # 捕食者由饥饿驱动（predator 插件管生死），代谢插件只管绿色觅食者；
        # 但移动标志要复位，避免状态残留
        if a['kind'] == 'predator':
            a['walked_this_tick'] = False
            continue
        move_cost = float(a['params']['move_cost'])   # 出生时快照，换代生效

        # ---- 1. 走路耗能（散落回离开的格，守恒）----
        if a['walked_this_tick'] and a['last_pos'] is not None:
            ox, oy = a['last_pos']
            a['energy'] -= move_cost
            grid[oy, ox] += move_cost
            a['spent'] += move_cost

        # ---- 2. 基础代谢（散落到 1~3 格外，不能原地自循环）----
        a['energy'] -= METABOLISM
        world.scatter(a['x'], a['y'], METABOLISM, radius=3)
        a['spent'] += METABOLISM

        # ---- 3. 吃脚下能量点（只有绿色觅食者吃）----
        if a['kind'] == 'prey':
            cell = grid[a['y'], a['x']]
            if cell > 0:
                take = min(cell, EAT_RATE)
                grid[a['y'], a['x']] = cell - take
                a['energy'] += take
                a['eaten'] += take

        # ---- 4. 能量死亡判定（负余额归还网格，守恒）----
        if a['energy'] <= 0:
            grid[a['y'], a['x']] += a['energy']
            a['energy'] = 0.0
            a['alive'] = False
            a['death_cause'] = '能量耗尽'

        # ---- 5. 复位移动标志 ----
        a['walked_this_tick'] = False


def on_render(ax, agents, grid):
    pass


def ui_controls(world):
    pass
