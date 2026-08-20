# -*- coding: utf-8 -*-
"""
WORLD3/plugins/predator.py —— 红色捕食者插件。

攻击方式：攻击消耗型。绿色 Agent 带 HP（默认 10），每次攻击扣
攻击力（1~5），HP 归零死亡；攻击消耗捕食者 1 点能量（散落回网格，守恒），
攻击成功后吸取猎物剩余能量的 50%，其余 50% 散落回网格。

行为驱动：饥饿驱动型。捕食者有饥饿值（0~100）：
  - > 阈值（默认 70）：主动追踪最近的绿色 Agent
  - ≤ 30：随机游走
  - 攻击成功：饥饿值大幅下降（-30）
  - 饥饿值到 100：捕食者饿死
"""
import numpy as np

DESCRIPTION = '红色捕食者：饥饿驱动猎杀绿色 Agent'

HUNGER_RATE = 0.05      # 饥饿增长 / tick
HUNGER_DROP = 30.0      # 攻击成功饥饿下降
ATTACK_COST = 1.0       # 每次攻击消耗能量
ATTACK_COOLDOWN = 3     # 攻击冷却（tick）


def register(world):
    pass


def on_tick(agents, grid):
    world = grid.world
    rng = world.rng

    predators = [a for a in agents if a['alive'] and a['kind'] == 'predator']
    prey = [a for a in agents if a['alive'] and a['kind'] == 'prey']
    if not predators:
        return

    for p in predators:
        # 出生时参数快照——换代生效
        pp = p['params']
        threshold = float(pp['hunger_threshold'])
        attack_power = float(pp['attack_power'])

        # ---- 1. 饥饿增长 ----
        p['hunger'] += HUNGER_RATE

        # ---- 2. 攻击冷却递减 ----
        if p['attack_cooldown'] > 0:
            p['attack_cooldown'] -= 1

        # ---- 3. 行为选择：猎杀 or 游走 ----
        if p['hunger'] > threshold and prey:
            target = min(prey, key=lambda q: world.dist(p, q))
            d = world.dist(p, target)
            if d <= 1:
                _attack(p, target, grid, attack_power)
            else:
                _move_toward(p, target, grid, rng)
                p['target'] = (target['x'], target['y'])
        elif p['hunger'] <= 30.0:
            _wander(p, grid, rng)
            p['target'] = None
        else:
            # 30 < 饥饿 ≤ 阈值：漫无目的徘徊
            _wander(p, grid, rng)
            p['target'] = None

        # ---- 4. 饿死判定 ----
        if p['hunger'] >= 100.0:
            p['alive'] = False
            p['death_cause'] = '饿死'


def _attack(predator, target, grid, attack_power):
    """攻击：扣 HP；HP 归零则猎物死亡，能量按 50/50 分配（守恒）"""
    if predator['attack_cooldown'] > 0:
        return
    world = grid.world

    # 攻击消耗（散落回网格，守恒）
    predator['energy'] -= ATTACK_COST
    grid[predator['y'], predator['x']] += ATTACK_COST
    predator['spent'] += ATTACK_COST

    target['hp'] -= attack_power
    predator['attack_cooldown'] = ATTACK_COOLDOWN

    if target['hp'] <= 0:
        # 猎物死亡：能量 50% 归捕食者，50% 散落回网格
        leftover = target['energy']
        predator['energy'] += leftover * 0.5
        world.scatter(target['x'], target['y'], leftover * 0.5, radius=2)
        target['energy'] = 0.0
        target['alive'] = False
        target['death_cause'] = '被猎杀'
        # 捕食者进食：饥饿大幅下降
        predator['hunger'] = max(0.0, predator['hunger'] - HUNGER_DROP)


def _move_toward(predator, target, grid, rng):
    """朝目标走一步（切比雪夫，走短边）"""
    world = grid.world
    size = world.size
    dx = 0
    if target['x'] != predator['x']:
        dx = 1 if (target['x'] - predator['x']) % size <= size // 2 else -1
    dy = 0
    if target['y'] != predator['y']:
        dy = 1 if (target['y'] - predator['y']) % size <= size // 2 else -1
    if dx != 0 and dy != 0 and rng.random() < 0.5:
        dy = 0
    world.move_agent(predator, dx, dy)


def _wander(predator, grid, rng):
    """随机游走：保持方向 5 tick"""
    world = grid.world
    if predator['wander_ticks'] <= 0:
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1),
                (1, 1), (1, -1), (-1, 1), (-1, -1)]
        predator['wander_dx'], predator['wander_dy'] = dirs[int(rng.integers(0, 8))]
        predator['wander_ticks'] = 5
    predator['wander_ticks'] -= 1
    world.move_agent(predator, predator['wander_dx'], predator['wander_dy'])


def on_render(ax, agents, grid):
    """叠加绘制：捕食者→猎物追踪线（淡红色）"""
    import matplotlib.lines as mlines
    for a in agents:
        if not a['alive'] or a['kind'] != 'predator':
            continue
        if a['target'] is not None:
            tx, ty = a['target']
            ax.plot([a['x'], tx], [a['y'], ty], color='red', alpha=0.15,
                    linewidth=0.8, linestyle='--')


def ui_controls(world):
    import streamlit as st
    st.caption('捕食者参数')
    world.params['predator_count'] = st.slider(
        '捕食者初始数量', 1, 20, int(world.params.get('predator_count', 5)),
        help='重置/新实验时生效')
    world.params['attack_power'] = st.slider(
        '攻击力（每次扣 HP）', 1, 5, int(world.params.get('attack_power', 3)),
        help='绿色 Agent HP=10，攻击 3 次致死')
    world.params['hunger_threshold'] = st.slider(
        '饥饿触发阈值', 50, 90, int(world.params.get('hunger_threshold', 70)),
        help='饥饿值高于此值开始追踪最近猎物')
