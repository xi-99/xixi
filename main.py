# -*- coding: utf-8 -*-
"""
WORLD3/main.py —— 实验入口：单跑 / 对照 / 扫描。

用法（在 WORLD3 目录下）：
    python main.py --mode smoke                # 快速冒烟（500 tick，验证守恒）
    python main.py --mode single --p 0.0       # 单次运行（决心型）
    python main.py --mode compare              # 对照：犹豫型 vs 决心型（同种子）
    python main.py --mode sweep                # 犹豫旋钮扫描 p∈{0,0.02,...,1.0}
    python main.py --mode sweep --ticks 5000   # 短扫描（快速调参用）
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import INITIAL_AGENTS, TICKS, SEED, DISTRACT_PROB  # noqa: E402
from world import World  # noqa: E402

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
os.makedirs(RESULTS, exist_ok=True)


def format_report(r):
    causes = '、'.join(f"{k} {v}只" for k, v in r['death_causes'].items()) or '无'
    occ = ' '.join(f"{o*100:.0f}%" for o in r['oasis_occupancy_last']) or '无'
    lines = [
        "=" * 62,
        f" WORLD3 实验报告   犹豫旋钮 p = {r['p']:g}",
        "=" * 62,
        f" 种子 {r['seed']} | 运行 {r['tick']} tick | 128×128 环形 | 3 绿洲 | 100 只起步",
        "",
        f" 存活: {r['alive']} / {INITIAL_AGENTS} ({r['survival']*100:.1f}%)",
        f" 死亡: {r['deaths']} 只（原因: {causes}）",
        f" 平均能量: {r['avg_energy']:.1f}",
        "",
        f" 目标切换/千tick: {r['switches_per_agent_per_ktick']:8.1f}   ← 犹豫指标",
        f" 行走距离/千tick: {r['walked_per_agent_per_ktick']:8.1f}",
        f" 觅食效率(吃/耗): {r['forage_efficiency']:.3f}   ← >1 才收支平衡",
        f" 绿洲占用率(末): {occ}",
        "",
        f" 守恒最大偏差: {r['max_conservation_error']:.2e}  "
        f"审计失败: {r['audit_fails']} 次",
        "=" * 62,
    ]
    return '\n'.join(lines)


def run_once(p, seed, ticks, tag, verbose=False):
    t0 = time.time()
    world = World(seed=seed, distract_prob=p, ticks=ticks)
    r = world.run(verbose=verbose)
    elapsed = time.time() - t0
    r['elapsed'] = elapsed
    base = f"run_{tag}_p{p:g}_seed{seed}"
    with open(os.path.join(RESULTS, base + '.log'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(world.log_lines) + '\n')
    with open(os.path.join(RESULTS, base + '.csv'), 'w', encoding='utf-8') as f:
        f.write('tick,alive,avg_energy,switches_per_1000tick,walked_per_1000tick,forage_efficiency\n')
        for t, n, avgE, sw, wk, eff in r['history']:
            f.write(f"{t},{n},{avgE:.2f},{sw*10:.1f},{wk*10:.1f},{eff:.3f}\n")
    with open(os.path.join(RESULTS, base + '.report.txt'), 'w', encoding='utf-8') as f:
        f.write(format_report(r) + f"\n 耗时: {elapsed:.1f}s\n")
    if verbose:
        print(format_report(r))
        print(f" 耗时: {elapsed:.1f}s")
    return r


def verdict(committed, hesitant):
    c_s = committed['survival']
    h_s = hesitant['survival']
    print()
    print("=" * 62)
    if c_s >= 0.7 and c_s - h_s >= 0.15:
        print(" [通过] 实验结论：决心机制有效。")
        print(f"    决心型存活 {c_s*100:.0f}%（达标 ≥70%），犹豫型仅 {h_s*100:.0f}%。")
        print("    犹豫是死亡主因；'决心'让觅食收支越过临界点。")
    elif c_s >= 0.7:
        print(" [注意] 决心型达标，但对照差异不明显。")
        print("    世界可能太富（犹豫也能活），调低 OASIS_DENSITY 或 BASE_CRUMB_PROB 再试。")
    else:
        print(" [未达标] 决心型未达标。")
        print("    世界太贫或初期死亡率过高，调参方向见 README.md 调参指南。")
    print("=" * 62)


def mode_smoke(args):
    print("=== 冒烟测试: 500 tick，验证守恒与稳定性 ===")
    run_once(DISTRACT_PROB, args.seed, 500, 'smoke', verbose=True)


def mode_single(args):
    p = args.p if args.p is not None else DISTRACT_PROB
    run_once(p, args.seed, args.ticks, 'single', verbose=True)


def mode_compare(args):
    print(f"=== 对照实验: 犹豫型(p=1.0) vs 决心型(p=0.0)  种子 {args.seed}  时长 {args.ticks} tick ===")
    hesitant = run_once(1.0, args.seed, args.ticks, 'hesitant', verbose=False)
    committed = run_once(0.0, args.seed, args.ticks, 'committed', verbose=False)
    rows = [
        ("", "犹豫型 p=1.0", "决心型 p=0.0"),
        ("存活", f"{hesitant['alive']} ({hesitant['survival']*100:.0f}%)",
         f"{committed['alive']} ({committed['survival']*100:.0f}%)"),
        ("平均能量", f"{hesitant['avg_energy']:.1f}", f"{committed['avg_energy']:.1f}"),
        ("目标切换/千tick", f"{hesitant['switches_per_agent_per_ktick']:.0f}",
         f"{committed['switches_per_agent_per_ktick']:.0f}"),
        ("行走/千tick", f"{hesitant['walked_per_agent_per_ktick']:.0f}",
         f"{committed['walked_per_agent_per_ktick']:.0f}"),
        ("觅食效率", f"{hesitant['forage_efficiency']:.3f}",
         f"{committed['forage_efficiency']:.3f}"),
        ("绿洲占用(末)", ' '.join(f"{o*100:.0f}%" for o in hesitant['oasis_occupancy_last']),
         ' '.join(f"{o*100:.0f}%" for o in committed['oasis_occupancy_last'])),
    ]
    w1 = max(len(x[1]) for x in rows) + 2
    w2 = max(len(x[2]) for x in rows) + 2
    print()
    print("=" * (w1 + w2 + 12))
    for a, b, c in rows:
        print(f" {a:<16}{b:<{w1}}{c:<{w2}}")
    print("=" * (w1 + w2 + 12))
    verdict(committed, hesitant)


def mode_sweep(args):
    ps = [0.0, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0]
    print(f"=== 犹豫旋钮扫描  种子 {args.seed}  时长 {args.ticks} tick ===")
    print(f" {'p':>6} | {'存活':>9} | {'平均能量':>8} | {'切换/千tick':>10} | {'觅食效率':>7}")
    print("-" * 58)
    rows = []
    for p in ps:
        r = run_once(p, args.seed, args.ticks, 'sweep', verbose=False)
        rows.append((p, r))
        print(f" {p:6g} | {r['survival']*100:7.0f}% ({r['alive']:3d}) | "
              f"{r['avg_energy']:8.1f} | {r['switches_per_agent_per_ktick']:10.1f} | "
              f"{r['forage_efficiency']:7.3f}")
    print("-" * 58)
    # 找相变点：存活率从高到低跳变的位置
    for i in range(1, len(rows)):
        if rows[i][1]['survival'] <= 0.5 < rows[i-1][1]['survival']:
            print(f" [相变] p 在 {rows[i-1][0]:g} ~ {rows[i][0]:g} 之间，存活率从 "
                  f"{rows[i-1][1]['survival']*100:.0f}% 跌到 {rows[i][1]['survival']*100:.0f}%")
            break
    else:
        print(" [观察] 未观察到明显相变带（世界太富或太贫，见 README 调参指南）")
    print("（完整报告见 results/run_sweep_*.report.txt）")


def main():
    # Windows GBK 控制台兜底：避免 emoji 打印崩溃
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='WORLD3 决心实验')
    ap.add_argument('--mode', default='compare',
                    choices=['smoke', 'single', 'compare', 'sweep'])
    ap.add_argument('--p', type=float, default=None, help='犹豫旋钮（single 模式用）')
    ap.add_argument('--seed', type=int, default=SEED)
    ap.add_argument('--ticks', type=int, default=TICKS)
    args = ap.parse_args()

    t0 = time.time()
    if args.mode == 'smoke':
        mode_smoke(args)
    elif args.mode == 'single':
        mode_single(args)
    elif args.mode == 'compare':
        mode_compare(args)
    else:
        mode_sweep(args)
    print(f"\n总耗时 {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
