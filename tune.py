# -*- coding: utf-8 -*-
"""
WORLD3/tune.py —— 调参扫描器：快速寻找"犹豫会死、决心能活"的参数窗口。
每组候选在 5000 tick 上跑 犹豫型(p=1.0) 和 决心型(p=0.0)，对比存活率。

用法：
    python tune.py                 # 跑内置候选
    python tune.py --ticks 5000    # 自定义时长
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

SEED = 42
TICKS = 5000

# 候选参数组：在基线（富世界）基础上逐级调贫
# 键名对应 config.py 中的变量
CANDIDATES = [
    ("A 基线(富)",  dict(OASIS_COUNT=5, OASIS_DENSITY=12.0, BASE_CRUMB_PROB=0.3,
                        EAT_RATE=2.0, INITIAL_ENERGY_MIN=120, INITIAL_ENERGY_MAX=160)),
    ("B 中贫",      dict(OASIS_COUNT=3, OASIS_DENSITY=8.0, BASE_CRUMB_PROB=0.2,
                        EAT_RATE=1.5, INITIAL_ENERGY_MIN=100, INITIAL_ENERGY_MAX=140)),
    ("C 贫",        dict(OASIS_COUNT=3, OASIS_DENSITY=5.0, BASE_CRUMB_PROB=0.12,
                        EAT_RATE=1.2, INITIAL_ENERGY_MIN=100, INITIAL_ENERGY_MAX=140)),
    ("D 贫+远行贵", dict(OASIS_COUNT=3, OASIS_DENSITY=6.0, BASE_CRUMB_PROB=0.12,
                        EAT_RATE=1.2, MOVE_COST=1.2,
                        INITIAL_ENERGY_MIN=100, INITIAL_ENERGY_MAX=140)),
    ("E 富碎片",    dict(OASIS_COUNT=4, OASIS_DENSITY=6.0, BASE_CRUMB_PROB=0.25,
                        EAT_RATE=1.2, INITIAL_ENERGY_MIN=100, INITIAL_ENERGY_MAX=140)),
    ("F 很贫",      dict(OASIS_COUNT=2, OASIS_DENSITY=6.0, BASE_CRUMB_PROB=0.08,
                        EAT_RATE=1.0, INITIAL_ENERGY_MIN=120, INITIAL_ENERGY_MAX=160)),
]


def run_candidate(name, overrides, ticks):
    import config
    for k, v in overrides.items():
        setattr(config, k, v)
    # 重新导入（读取已打补丁的 config）
    for mod in list(sys.modules):
        if mod.startswith(('world', 'agent', 'config')) and mod != 'config':
            del sys.modules[mod]
    from world import World

    def run(p):
        w = World(seed=SEED, distract_prob=p, ticks=ticks)
        w.run(verbose=False)
        return w.final_report()

    t0 = time.time()
    h = run(1.0)
    c = run(0.0)
    elapsed = time.time() - t0
    print(f"\n[{name}] 总能量≈{w.initial_total:.0f}  耗时{elapsed:.0f}s")
    print(f"  {'':<6}{'犹豫型':>12}{'决心型':>12}")
    print(f"  {'存活':<6}{h['survival']*100:>10.0f}%{c['survival']*100:>12.0f}%")
    print(f"  {'平均能量':<6}{h['avg_energy']:>12.1f}{c['avg_energy']:>12.1f}")
    print(f"  {'切换/千tick':<6}{h['switches_per_agent_per_ktick']:>12.1f}"
          f"{c['switches_per_agent_per_ktick']:>12.1f}")
    print(f"  {'行走/千tick':<6}{h['walked_per_agent_per_ktick']:>12.1f}"
          f"{c['walked_per_agent_per_ktick']:>12.1f}")
    print(f"  {'觅食效率':<6}{h['forage_efficiency']:>12.3f}{c['forage_efficiency']:>12.3f}")
    return h, c


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--ticks', type=int, default=TICKS)
    ap.add_argument('--only', type=str, default='', help='只跑名字含此字母的候选')
    args = ap.parse_args()
    print(f"=== WORLD3 参数窗口扫描  种子 {SEED}  时长 {args.ticks} tick ===")
    best = None
    for name, ov in CANDIDATES:
        if args.only and args.only not in name:
            continue
        h, c = run_candidate(name, ov, args.ticks)
        gap = c['survival'] - h['survival']
        if c['survival'] >= 0.7 and gap >= 0.15:
            print(f"  >> {name} 进入相变窗口: 决心型达标且明显优于犹豫型 (差距 {gap*100:.0f}%)")
            if best is None or gap > best[0]:
                best = (gap, name, ov)
    if best is None:
        print("\n未找到理想窗口：所有候选要么都活（太富），要么都死（太贫）。")
        print("参考上面的表格，在 config.py 中微调后重跑 tune.py。")
    else:
        print(f"\n最佳候选: {best[1]}（差距 {best[0]*100:.0f}%）→ 将参数写入 config.py 后跑完整实验")
        print("参数:", best[2])


if __name__ == '__main__':
    main()
