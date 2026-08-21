# -*- coding: utf-8 -*-
"""
WORLD3/plugins/social_ecology.py —— 社会生态插件（依赖 plugin_utils，可选依赖 lifecycle）。

为 Agent 增加家族与社会倾向：
  - family_id：随机 32 位整数（出生分配，或从 social_parent 继承）；
  - social_trait：selfish（自私）/ altruist（利他）/ cooperator（合作）。

人性决策（PRIORITY = -100，先于决心插件 focus 执行；focus 尊重
social_takeover 标志，避免一 tick 两动）：
  - selfish：优先自己进食，不分享（即不干预 focus 的觅食决策）；
  - altruist：视野内发现能量低于 20% 基准的同伴时，主动让出脚下食物
    （能量从网格转移给同伴，自己额外消耗 转移量 × 利他惩罚强度，
    消耗散落回网格——严格守恒）；
  - cooperator：检测视野内最近的中性生物，主动围猎：多只合作者围住
    同一目标时，攻击伤害按 plugin_utils.cooperation_bonus 线性放大
    （上限 2.0 倍）；攻击把中性生物的能量"打散"回网格（守恒），
    死亡后 100~300 tick 在原位置或附近重生。

中性生物：独立实体（不占 grid 能量格），初始化能量从网格就地抽取
（只取存在、绝不创造，守恒）；随机游走；被围猎打散的能量回网格。

家族系统：同家族 Agent 不互相攻击（捕食者除外——本引擎当前无
prey-vs-prey 攻击，该规则为防御性设计，供未来攻击类插件通过
plugin_utils.filter_family 检查）；邻近家族成员数记录在
agent['near_family']，渲染时同家族用相近色相（±5°）显示。
"""
import colorsys
import sys
import os

import numpy as np

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
import plugin_utils  # noqa: E402  （纯函数工具库，重复导入无副作用）

DESCRIPTION = '社会生态：家族 / 利他让食 / 合作围猎 / 中性生物（优先于决心插件）'

# 生态类插件默认禁用：避免改变既有实验的默认行为（侧边栏可手动开启）
DEFAULT_ENABLED = False
# 优先级高于行为插件（focus 等）：先决策并接管移动
PRIORITY = -100

NEUTRAL_ENERGY = 20.0          # 中性生物初始能量（从网格就地抽取）
NEUTRAL_ATTACK_DAMAGE = 2.0    # 合作者单次攻击基础伤害
NEUTRAL_WANDER_EVERY = 3       # 每 N tick 游走一步
NEUTRAL_HUNT_RANGE = 2.0       # 判定"围住"目标的距离
RESPAWN_MIN, RESPAWN_MAX = 100, 300
TRAITS = ('selfish', 'altruist', 'cooperator')


def register(world):
    pass


# ==================== 中性生物 ====================

def _extract(world, x, y, amount):
    """从 (x,y) 周围 9 格就地采集能量（只取存在的量，绝不创造，守恒）。"""
    g = world.grid.data
    size = world.size
    got = 0.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if got >= amount - 1e-12:
                break
            wx, wy = (x + dx) % size, (y + dy) % size
            v = g[wy, wx]
            if v <= 0:
                continue
            take = min(v, amount - got)
            g[wy, wx] = v - take
            got += take
    return got


def _spawn_neutral(world, x=None, y=None):
    """生成一只中性生物：能量从网格就地抽取（贫瘠之地可能不足，公平）。"""
    if x is None:
        x = int(world.rng.integers(0, world.size))
    if y is None:
        y = int(world.rng.integers(0, world.size))
    return {'id': int(world.rng.integers(0, 2 ** 31)),
            'x': x, 'y': y, 'energy': _extract(world, x, y, NEUTRAL_ENERGY),
            'alive': True, 'age': 0}


def _init_world(world):
    """惰性初始化：挂载中性生物池（world.social）。"""
    if hasattr(world, 'social'):
        return
    world.social = {'neutrals': [], 'respawn': []}
    want = max(0, int(world.params.get('neutral_count', 15)))
    for _ in range(want):
        world.social['neutrals'].append(_spawn_neutral(world))


def _sync_neutral_count(world):
    """UI 调整中性生物数量后同步：不足则补、超出则回收（能量散落守恒）。"""
    s = world.social
    want = max(0, int(world.params.get('neutral_count', 15)))
    alive = [n for n in s['neutrals'] if n['alive']]
    have = len(alive) + len(s['respawn'])
    if have < want:
        for _ in range(want - have):
            s['neutrals'].append(_spawn_neutral(world))
    elif have > want:
        for n in alive:
            if have <= want:
                break
            n['alive'] = False
            if n['energy'] > 0:
                world.scatter(n['x'], n['y'], float(n['energy']), radius=2)
            n['energy'] = 0.0
            have -= 1


# ==================== 社会倾向分配 ====================

def _assign_trait(world, agent):
    """按 params['social_inherit'] 分配 family_id 与 social_trait。"""
    rng = world.rng
    mode = world.params.get('social_inherit', '随机')
    parent = agent.get('social_parent')
    if mode == '继承' and parent is not None \
            and parent.get('social_trait') is not None:
        agent['family_id'] = int(parent.get('family_id', rng.integers(0, 2 ** 31)))
        agent['social_trait'] = parent['social_trait']
    elif mode == '轮盘赌':
        fam = {}
        for a in world.agents:
            if a['alive'] and 'family_id' in a:
                fid = a['family_id']
                fam[fid] = fam.get(fid, 0) + 1
        if fam:
            ids = list(fam.keys())
            w = np.array([fam[f] for f in ids], dtype=float)
            agent['family_id'] = int(ids[int(rng.choice(len(ids), p=w / w.sum()))])
        else:
            agent['family_id'] = int(rng.integers(0, 2 ** 31))
        agent['social_trait'] = str(rng.choice(TRAITS))
    else:  # 随机
        agent['family_id'] = int(rng.integers(0, 2 ** 31))
        agent['social_trait'] = str(rng.choice(TRAITS))


# ==================== 人性决策 ====================

def _altruist_share(world, a, agents, view_range):
    """利他让食：视野内能量低于 20% 基准的同伴，让出脚下食物。
    转移量 = min(脚下能量, 缺口, 自己能负担的惩罚量)；
    惩罚消耗散落回网格（守恒）。"""
    p = a['params']
    base = float(p.get('initial_energy_max', 160)) * 0.2
    best = None
    for b in agents:
        if b is a or not b['alive'] or b['kind'] != 'prey':
            continue
        if b['energy'] < base and plugin_utils.is_visible(a, b, view_range):
            if best is None or b['energy'] < best['energy']:
                best = b
    if best is None:
        return
    cell = world.grid.data[a['y'], a['x']]
    if cell <= 0:
        return
    penalty = float(world.params.get('altruist_penalty', 0.35))
    afford = a['energy'] / max(penalty, 1e-9) if penalty > 0 else 1e9
    t = min(cell, base - best['energy'], afford)
    if t <= 0:
        return
    # 让食：网格 → 同伴（守恒）
    world.grid.data[a['y'], a['x']] = cell - t
    best['energy'] += t
    best['eaten'] += t
    # 利他代价：自己额外消耗，散落回网格（守恒）
    cost = t * penalty
    a['energy'] -= cost
    a['spent'] += cost
    world.scatter(a['x'], a['y'], cost, radius=3)


def _cooperator_hunt(world, a, view_range, rng):
    """合作围猎：接管移动走向最近中性生物；围住时协同攻击。
    攻击把中性生物能量打散回网格（守恒）；死亡进入重生队列。"""
    s = world.social
    nb, nd = None, 1e18
    for n in s['neutrals']:
        if not n['alive']:
            continue
        d = plugin_utils.distance(a, n)
        if d <= view_range and d < nd:
            nb, nd = n, d
    if nb is None:
        return
    if nd <= 1.0:
        # 围猎：统计目标附近（≤2 格）的其他合作者 → 协同伤害加成
        hunters = [b for b in world.agents
                   if b['alive'] and b['kind'] == 'prey'
                   and b['id'] != a['id']
                   and b.get('social_trait') == 'cooperator'
                   and plugin_utils.distance(b, nb) <= NEUTRAL_HUNT_RANGE]
        dmg = NEUTRAL_ATTACK_DAMAGE * plugin_utils.cooperation_bonus(
            [a] + hunters, nb)
        take = min(dmg, nb['energy'])
        nb['energy'] -= take
        world.scatter(nb['x'], nb['y'], take, radius=2)   # 打散回网格（守恒）
        a['eaten'] += take
        if nb['energy'] <= 0:
            nb['alive'] = False
            s['respawn'].append({'pos': (nb['x'], nb['y']),
                                 'left': int(rng.integers(RESPAWN_MIN,
                                                          RESPAWN_MAX + 1))})
        return
    # 接管移动（优先级高于决心插件）：切比雪夫短边走向目标
    size = world.size
    dx = 0
    if nb['x'] != a['x']:
        dx = 1 if (nb['x'] - a['x']) % size <= size // 2 else -1
    dy = 0
    if nb['y'] != a['y']:
        dy = 1 if (nb['y'] - a['y']) % size <= size // 2 else -1
    if dx != 0 and dy != 0 and rng.random() < 0.5:
        dy = 0
    if dx != 0 or dy != 0:
        world.move_agent(a, dx, dy)
    a['social_takeover'] = True


# ==================== 主循环 ====================

def on_tick(agents, grid):
    world = grid.world
    rng = world.rng
    _init_world(world)
    s = world.social
    _sync_neutral_count(world)

    # 1) 复位上 tick 的接管标志 + 惰性初始化社会字段
    for a in agents:
        a['social_takeover'] = False
        if a['alive'] and 'family_id' not in a:
            _assign_trait(world, a)

    # 2) 中性生物：重生计时 + 随机游走
    for entry in s['respawn'][:]:
        entry['left'] -= 1
        if entry['left'] <= 0:
            s['neutrals'].append(
                _spawn_neutral(world, entry['pos'][0], entry['pos'][1]))
            s['respawn'].remove(entry)
    for n in s['neutrals']:
        if not n['alive']:
            continue
        n['age'] += 1
        if n['age'] % NEUTRAL_WANDER_EVERY == 0:
            n['x'] = (n['x'] + int(rng.integers(-1, 2))) % world.size
            n['y'] = (n['y'] + int(rng.integers(-1, 2))) % world.size

    # 3) 人性决策（先于 focus；focus 尊重 social_takeover）
    for a in agents:
        if not a['alive'] or a['kind'] != 'prey':
            continue
        trait = a.get('social_trait', 'selfish')
        vr = int(a['params'].get('view_range', 10))
        if trait == 'altruist':
            _altruist_share(world, a, agents, vr)
        elif trait == 'cooperator':
            _cooperator_hunt(world, a, vr, rng)

    # 4) 家族邻近记录（渲染与未来攻击插件用）
    for a in agents:
        if not a['alive'] or a['kind'] != 'prey':
            continue
        a['near_family'] = sum(
            1 for b in plugin_utils.filter_family(a, agents)
            if plugin_utils.distance(a, b) <= 3.0)


# ==================== 渲染 ====================

def on_render(ax, agents, grid):
    world = grid.world
    if not hasattr(world, 'social'):
        return
    # 中性生物：琥珀色菱形
    for n in world.social['neutrals']:
        if n['alive']:
            ax.scatter([n['x']], [n['y']], s=42, marker='D', c='#d29922',
                       edgecolors='white', linewidths=0.3, zorder=3)
    # 家族颜色：同家族用相近色相（±5° 偏移本质=同色相），仅画 ≥2 成员的家族
    if world.params.get('family_colors', True):
        fam = {}
        for a in agents:
            if a['alive'] and a['kind'] == 'prey' and 'family_id' in a:
                fam.setdefault(a['family_id'], []).append(a)
        for fid, members in fam.items():
            if len(members) < 2:
                continue
            hue = (fid % 360) / 360.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.95)
            xs = [m['x'] for m in members]
            ys = [m['y'] for m in members]
            ax.scatter(xs, ys, s=13, c=[(r, g, b)] * len(xs), alpha=0.85,
                       linewidths=0, zorder=2)


def ui_controls(world):
    """参数由 app.py 统一渲染（PARAM_SPECS 归属 social_ecology）。"""
    pass
