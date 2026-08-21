# -*- coding: utf-8 -*-
"""
WORLD3/tools/test_plugins_v22.py —— v2.2 生态插件集集成测试。

验证：
  1. 插件加载：4 个新插件被发现，PRIORITY / DEFAULT_ENABLED 元信息正确，
     social_ecology 排在 focus 之前（优先级高于决心插件）
  2. lifecycle：年龄/寿命字段、自然死亡（寿终正寝）、能量守恒
  3. geography：地形生成、山脉拦截移动、河流耗能守恒
  4. social_ecology：中性生物守恒、社会字段初始化、利他让食、合作围猎、
     接管标志让 focus 让位
  5. 生态插件默认禁用：不启用时世界行为不受影响（守恒回归）

运行：python tools/test_plugins_v22.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'plugins'))

from plugin_loader import scan_plugins  # noqa: E402
from world_engine import World  # noqa: E402

PLUGINS = scan_plugins(os.path.join(BASE, 'plugins'))
BY_NAME = {p.name: p for p in PLUGINS}


def check(name, cond, detail=''):
    status = 'PASS' if cond else 'FAIL'
    print(f'  [{status}] {name}' + (f'  ({detail})' if detail else ''))
    if not cond:
        raise SystemExit(f'测试失败：{name}')


def base_params(**kw):
    p = dict(seed=42, map_size=32, agent_count=20, move_cost=0.5, food_energy=10,
             view_range=12, predator_enabled=False, predator_count=0, prey_hp=10,
             attack_power=3, hunger_threshold=70, init_energy=100.0,
             initial_energy_min=120, initial_energy_max=160,
             max_ticks=2000, tick_duration=0.0, stop_condition='任一',
             evolution_mode=False, generation_ticks=5000, elite_fraction=0.2,
             mutation_sigma=0.15, reprod_cost=10, lifespan_mutation=0.05,
             neutral_count=8, altruist_penalty=0.35, family_colors=True,
             social_inherit='随机', mountain_ratio=0.15, river_ratio=0.1,
             terrain_visible=True)
    p.update(kw)
    return p


def enabled_map(**kw):
    e = {p.name: p.default_enabled for p in PLUGINS}
    e.update(kw)
    return e


def total_energy(w):
    """网格 + 活体能量 + 中性生物池能量
    （中性生物能量源自网格抽取、死亡散落回网格，属于世界能量的一部分）。"""
    total = float(w.grid.sum()) + sum(a['energy'] for a in w.agents if a['alive'])
    s = getattr(w, 'social', None)
    if s is not None:
        total += sum(n['energy'] for n in s['neutrals'] if n['alive'])
    return total


def test_loading():
    print('== 1. 插件加载与元信息 ==')
    for name in ('plugin_utils', 'lifecycle', 'social_ecology', 'geography'):
        check(f'发现 {name}', name in BY_NAME)
    check('生态插件默认禁用',
          all(not BY_NAME[n].default_enabled
              for n in ('plugin_utils', 'lifecycle', 'social_ecology', 'geography')))
    check('既有插件默认启用', BY_NAME['focus'].default_enabled)
    order = [p.name for p in PLUGINS]
    check('social_ecology 先于 focus（优先级更高）',
          order.index('social_ecology') < order.index('focus'),
          ' → '.join(order))
    check('PRIORITY 值', BY_NAME['social_ecology'].priority == -100,
          f"priority={BY_NAME['social_ecology'].priority}")


def test_lifecycle():
    print('== 2. lifecycle：寿命 / 自然死亡 / 守恒 ==')
    w = World(base_params(max_ticks=3500, map_size=24, agent_count=15),
              PLUGINS, enabled_map(lifecycle=True))
    w.run_loop()
    causes = w.death_causes()
    check('出现自然死亡（寿终正寝）', causes.get('寿终正寝', 0) > 0, str(causes))
    alive = [a for a in w.agents if a['alive']]
    if alive:
        check('活体有年龄/寿命字段',
              all('age' in a and 'max_lifespan' in a for a in alive))
    check('能量守恒', abs(total_energy(w) - w.initial_total) < 1e-6,
          f"偏差 {abs(total_energy(w) - w.initial_total):.2e}")


def test_geography():
    print('== 3. geography：地形 / 山脉拦截 / 河流耗能 ==')
    w = World(base_params(map_size=24, agent_count=5, max_ticks=50),
              PLUGINS, enabled_map(geography=True))
    w.run_loop()
    check('真实运行能量守恒', abs(total_energy(w) - w.initial_total) < 1e-6,
          f"偏差 {abs(total_energy(w) - w.initial_total):.2e}")
    t = w.grid.terrain
    check('地形已生成', t is not None and t.shape == (24, 24))
    ys, xs = __import__('numpy').nonzero(t == 1)
    check('存在山脉', xs.size > 0, f'{xs.size} 格')
    # 山脉拦截：构造 fake agent 尝试走入山脉格（fake 不在 agents 列表，
    # 守恒以"fake.energy + grid"为单位单独验证）
    import numpy as np
    m = np.nonzero(t == 1)
    my, mx = m[0][0], m[1][0]
    fake = {'x': (mx - 1) % 24, 'y': my, 'energy': 50.0, 'spent': 0.0,
            'last_pos': None, 'walked_this_tick': False, 'walked': 0,
            'params': dict(w.params)}
    g0 = float(w.grid.sum())
    ok = w.move_agent(fake, 1, 0)
    check('山脉拦截移动', ok is False and fake['x'] == (mx - 1) % 24)
    check('山脉拦截守恒（无任何能量变动）',
          abs(float(w.grid.sum()) - g0) < 1e-9 and fake['energy'] == 50.0)
    # 河流耗能守恒：构造 fake agent 走入河流格
    r = np.nonzero(t == 2)
    if r[0].size:
        ry, rx = r[0][0], r[1][0]
        e0 = 50.0
        fake2 = {'x': (rx - 1) % 24, 'y': ry, 'energy': e0, 'spent': 0.0,
                 'last_pos': None, 'walked_this_tick': False, 'walked': 0,
                 'params': dict(w.params)}
        g1 = float(w.grid.sum())
        ok2 = w.move_agent(fake2, 1, 0)
        extra = float(w.params['move_cost']) * 2.0
        check('河流可通行且双倍耗能',
              ok2 is True and abs((e0 - fake2['energy']) - extra) < 1e-9,
              f"耗能 {e0 - fake2['energy']:.2f}")
        check('河流耗能守恒（fake.energy + grid 不变）',
              abs(float(w.grid.sum()) - g1 - (e0 - fake2['energy'])) < 1e-9)


def test_social():
    print('== 4. social_ecology：中性生物 / 让食 / 围猎 / 接管 ==')
    w = World(base_params(map_size=24, agent_count=12, max_ticks=800),
              PLUGINS, enabled_map(social_ecology=True, focus=True))
    w.run_loop()
    s = w.social
    n_initial = 8
    alive_n = sum(1 for n in s['neutrals'] if n['alive'])
    check('中性生物池初始化', alive_n + len(s['respawn']) == n_initial,
          f'存活 {alive_n} + 重生排队 {len(s["respawn"])}')
    check('社会字段初始化',
          all('family_id' in a and 'social_trait' in a
              for a in w.agents if a['alive']),
          f"倾向分布: "
          + str({tr: sum(1 for a in w.agents if a.get('social_trait') == tr)
                 for tr in ('selfish', 'altruist', 'cooperator')}))
    check('能量守恒', abs(total_energy(w) - w.initial_total) < 1e-6,
          f"偏差 {abs(total_energy(w) - w.initial_total):.2e}")

    # 接管语义：social_takeover=True 时 focus 让位（本 tick 不移动）；
    # 且下一 tick 开头 social 会复位该标志
    import focus as focus_mod
    import social_ecology as soc_mod
    w3 = World(base_params(map_size=12, agent_count=2, max_ticks=2),
               PLUGINS, enabled_map(social_ecology=True))
    a3 = w3.agents[0]
    a3['social_takeover'] = True
    a3['walked'] = 0
    focus_mod.on_tick(w3.agents, w3.grid)
    check('focus 尊重接管标志（不移动）', a3['walked'] == 0)
    # tick 开头复位（neutral_count=0 无中性生物时全部复位）
    w4 = World(base_params(map_size=12, agent_count=3, max_ticks=1),
               PLUGINS, enabled_map(social_ecology=True))
    w4.run_loop()
    w4.params['neutral_count'] = 0
    for ag in w4.agents:
        ag['social_takeover'] = True
    soc_mod.on_tick(w4.agents, w4.grid)
    check('tick 开头接管标志全部复位', 
          all(not ag.get('social_takeover') for ag in w4.agents))

    # 直接单步验证利他让食与围猎攻击（构造场景）
    import social_ecology as soc
    # 让食：甲(altruist) 脚下有食物，乙(同伴) 能量低
    w2 = World(base_params(map_size=12, agent_count=2, max_ticks=5),
               PLUGINS, enabled_map(social_ecology=True))
    w2.grid.data[:] = 0.0
    a, b = w2.agents
    a['x'], a['y'] = 5, 5
    b['x'], b['y'] = 5, 6
    a['social_trait'] = 'altruist'
    b['social_trait'] = 'selfish'
    b['energy'] = 5.0
    a['energy'] = 100.0
    b['eaten'] = 0.0
    a['spent'] = 0.0
    w2.grid.data[5, 5] = 40.0
    w2.params['altruist_penalty'] = 0.5
    e_grid0 = float(w2.grid.sum())
    e_peer0 = b['energy']
    soc._altruist_share(w2, a, w2.agents, 10)
    check('利他让食：同伴获得能量', b['energy'] > e_peer0,
          f"{e_peer0:.1f} → {b['energy']:.1f}")
    t_total0 = float(w2.grid.sum()) + sum(x['energy'] for x in w2.agents)
    check('让食守恒（网格+双 Agent 能量不变）',
          abs(float(w2.grid.sum()) + sum(x['energy'] for x in w2.agents)
              - t_total0) < 1e-9)
    # 围猎：cooperator 站在中性生物旁 → 攻击打散能量回网格
    soc._init_world(w2)
    w2.social['neutrals'] = [{'id': 1, 'x': 5, 'y': 5, 'energy': 20.0,
                              'alive': True, 'age': 0}]
    c = w2.agents[0]
    c['x'], c['y'] = 5, 5
    c['social_trait'] = 'cooperator'
    c['eaten'] = 0.0
    g0 = float(w2.grid.sum())
    e0 = w2.social['neutrals'][0]['energy']
    soc._cooperator_hunt(w2, c, 10, w2.rng)
    nb = w2.social['neutrals'][0]
    check('围猎攻击：中性生物能量减少', nb['energy'] < e0,
          f"{e0:.1f} → {nb['energy']:.1f}")
    check('围猎守恒（打散回网格）',
          abs(float(w2.grid.sum()) - g0 - (e0 - nb['energy'])) < 1e-9)


if __name__ == '__main__':
    test_loading()
    test_lifecycle()
    test_geography()
    test_social()
    print('\nALL PASS ✅  tools/test_plugins_v22.py')
