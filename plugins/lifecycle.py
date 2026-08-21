# -*- coding: utf-8 -*-
"""
WORLD3/plugins/lifecycle.py —— 生命周期插件（独立，不依赖其他插件）。

为 Agent 增加年龄 / 寿命 / 自然死亡 / 繁殖成本：
  - 每 tick 递增 age，达到 max_lifespan 时自然死亡：
    能量归零，尸体分解为能量点散落 1~3 格外（能量守恒：只搬家）。
  - on_agent_birth(world, agent, parent)：出生钩子（约定接口）——
    父代存在时继承 max_lifespan（± 变异幅度，UI 可调），子代年龄 0。
  - reproduce(world, parent)：繁殖工具（约定接口，供繁殖类插件调用）——
    检查父代能量 ≥ 有效繁殖成本（基础成本 × 寿命折扣，寿命越长成本越低），
    是则扣除并创建子代（扣除能量 50% 给子代、50% 散落回网格，守恒），
    否则返回 None（繁殖被拦截）。

惰性初始化：对已有 Agent 自动补齐 age/max_lifespan/reprod_cost 字段。
"""
import numpy as np

DESCRIPTION = '生命周期：年龄 / 寿命 / 自然死亡 / 繁殖成本（独立模块）'

# 生态类插件默认禁用：避免改变既有实验的默认行为（侧边栏可手动开启）
DEFAULT_ENABLED = False
PRIORITY = 0

LIFESPAN_MIN = 500       # 初始寿命下限（tick）
LIFESPAN_MAX = 2000      # 初始寿命上限（tick）
REPROD_COST_DEFAULT = 10.0
MUTATION_DEFAULT = 0.05


def register(world):
    pass


def on_tick(agents, grid):
    world = grid.world
    rng = world.rng
    reprod_cost = float(world.params.get('reprod_cost', REPROD_COST_DEFAULT))
    for a in agents:
        if not a['alive']:
            continue
        # 惰性初始化（对已有 Agent 同样生效）
        if 'age' not in a:
            a['age'] = 0
            a['max_lifespan'] = int(rng.integers(LIFESPAN_MIN, LIFESPAN_MAX + 1))
            a['reprod_cost'] = reprod_cost
        a['age'] += 1
        # 自然死亡：尸体分解为能量散落 1~3 格（守恒）
        if a['age'] >= a['max_lifespan']:
            if a['energy'] > 0:
                world.scatter(a['x'], a['y'], float(a['energy']), radius=3)
            a['energy'] = 0.0
            a['alive'] = False
            a['death_cause'] = '寿终正寝'


def on_agent_birth(world, agent, parent=None):
    """出生钩子（约定接口，供繁殖/进化类插件调用）：
    初始化年龄；有父代时继承 max_lifespan（± 变异幅度）与 reprod_cost。"""
    rng = world.rng
    agent['age'] = 0
    if parent is not None and parent.get('max_lifespan'):
        mutation = float(world.params.get('lifespan_mutation', MUTATION_DEFAULT))
        base = float(parent['max_lifespan'])
        span = int(base * rng.uniform(1.0 - mutation, 1.0 + mutation))
        agent['max_lifespan'] = max(100, span)
        agent['reprod_cost'] = float(parent.get(
            'reprod_cost', world.params.get('reprod_cost', REPROD_COST_DEFAULT)))
    else:
        agent['max_lifespan'] = int(rng.integers(LIFESPAN_MIN, LIFESPAN_MAX + 1))
        agent['reprod_cost'] = float(
            world.params.get('reprod_cost', REPROD_COST_DEFAULT))
    return agent


def reproduce(world, parent):
    """繁殖工具（约定接口，供繁殖类插件调用）。
    繁殖拦截：父代能量 < 有效繁殖成本 → 返回 None；
    否则扣除成本，50% 作为子代出生能量、50% 散落回网格（严格守恒）。
    子代出生在父代相邻格，继承父代寿命（± 变异）与家族信息。"""
    if not parent['alive']:
        return None
    base = float(parent.get('reprod_cost',
                            world.params.get('reprod_cost', REPROD_COST_DEFAULT)))
    # 寿命联动：寿命越长繁殖成本越低（× 1 - max_lifespan/2000 × 0.5）
    discount = 1.0 - (float(parent.get('max_lifespan', 1000)) / 2000.0) * 0.5
    cost = max(1.0, base * discount)
    if parent['energy'] < cost:
        return None                      # 繁殖拦截：能量不足
    rng = world.rng
    parent['energy'] -= cost
    child_share = cost * 0.5
    world.scatter(parent['x'], parent['y'], cost - child_share, radius=2)
    child = world._new_agent(len(world.agents), 'prey')
    child['x'] = (parent['x'] + int(rng.integers(-1, 2))) % world.size
    child['y'] = (parent['y'] + int(rng.integers(-1, 2))) % world.size
    child['last_pos'] = None
    child['born_tick'] = world.tick
    child['energy'] = child_share
    child['social_parent'] = parent          # 供 social_ecology 继承家族/倾向
    on_agent_birth(world, child, parent)
    world.agents.append(child)
    return child


def on_render(ax, agents, grid):
    pass


def ui_controls(world):
    """参数由 app.py 统一渲染（PARAM_SPECS 归属 lifecycle）。"""
    pass
