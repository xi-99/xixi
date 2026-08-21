# -*- coding: utf-8 -*-
"""v2.0 冒烟测试（保持脚本供后续回归）：
   1 进化模式  2 硬编码对照  3 纯基因脑不进化  4 进化+捕食者  5 app 默认规模。"""
import os, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from plugin_loader import scan_plugins
from world_engine import World

PLUGINS = scan_plugins(os.path.join(BASE, 'plugins'))

def base_params(**kw):
    p = dict(seed=42, map_size=40, agent_count=40, move_cost=0.5, food_energy=10,
             view_range=12, predator_enabled=False, predator_count=0, prey_hp=10,
             attack_power=3, hunger_threshold=70,
             init_energy=100.0, max_ticks=6500, tick_duration=0.0,
             stop_condition='任一', evolution_mode=False,
             generation_ticks=2000, elite_fraction=0.2, mutation_sigma=0.15)
    p.update(kw)
    return p

def enabled_map(focus=True, gene_brain=False):
    # v2.2：尊重插件声明的默认启用状态（生态类插件默认禁用），
    # 确保回归场景与既有行为一致
    e = {p.name: p.default_enabled for p in PLUGINS}
    e['focus'] = focus
    e['gene_brain'] = gene_brain
    return e

def run(name, params, enabled):
    w = World(params, PLUGINS, enabled)
    w.run_loop()
    s = w.stats()
    print(f"--- {name} ---", flush=True)
    print(f"  tick={w.tick} 存活prey={s['alive_prey']} 世代={s['generation']} "
          f"avgE={s['avg_energy']:.1f} 守恒偏差={s['conservation_error']:.2e} "
          f"审计失败={s['audit_fails']}", flush=True)
    print(f"  死亡原因: {w.death_causes()}", flush=True)
    for ent in w.evolution_log:
        print(f"  更替@{ent['tick']}: 父母={ent['parents']} 子代={ent['children']} "
              f"世代={ent['max_gen']} 父母适应度={ent['avg_fitness_parents']:.0f} "
              f"父母本代进食={ent['avg_eaten_parents']:.0f}", flush=True)
    return w

run('场景1 进化模式', base_params(evolution_mode=True),
    enabled_map(focus=False, gene_brain=True))
run('场景2 硬编码对照', base_params(evolution_mode=False),
    enabled_map(focus=True, gene_brain=False))
run('场景3 纯基因脑不进化', base_params(evolution_mode=False),
    enabled_map(focus=False, gene_brain=True))
run('场景4 进化+捕食者', base_params(evolution_mode=True, predator_enabled=True,
                                    predator_count=3, max_ticks=4000),
    enabled_map(focus=False, gene_brain=True))
run('场景5 app默认规模', base_params(map_size=80, agent_count=100, view_range=20,
                                    move_cost=0.6, max_ticks=15000,
                                    generation_ticks=5000, evolution_mode=True),
    enabled_map(focus=False, gene_brain=True))
print('OK', flush=True)
