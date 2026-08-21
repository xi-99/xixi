# -*- coding: utf-8 -*-
"""
⚠️⚠️⚠️  本文件已废弃（DEPRECATED）  ⚠️⚠️⚠️

WORLD3/world.py —— 【已废弃】旧命令行引擎的世界：网格、能量守恒、审计、主循环。
守恒法则：能量只搬家，不消失也不凭空出现。每 tick 审计。

自 v2.1 起：
  - 全部参数已合并进 app.py 的 PARAM_SPECS；
  - 新引擎为 world_engine.py（微内核 + 插件），由 Web 控制台（app.py）驱动；
  - 本文件仅被 main.py / tune.py 组成的旧命令行链路引用，仅供历史复现。

请使用 Web 控制台（python -m streamlit run app.py）进行实验。
"""
import warnings

import numpy as np
from config import (GRID, INITIAL_AGENTS, TICKS, SEED, OASIS_COUNT, OASIS_RADIUS,
                    OASIS_DENSITY, BASE_CRUMB_PROB, INITIAL_ENERGY_MIN,
                    INITIAL_ENERGY_MAX, DISTRACT_PROB, LOG_EVERY, SNAPSHOT_EVERY)
from agent import Agent

warnings.warn(
    'world.py 已废弃：请改用 world_engine.py（微内核 + 插件）与 Web 控制台 app.py。',
    DeprecationWarning, stacklevel=2)


class World:
    def __init__(self, seed=SEED, distract_prob=DISTRACT_PROB, ticks=TICKS):
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.distract_prob = distract_prob
        self.ticks = ticks
        self.tick = 0
        self.grid = np.zeros((GRID, GRID), dtype=np.float64)
        self.oases = []                 # [(cx, cy), ...]
        self._init_energy()
        self.agents = [Agent(i, int(self.rng.integers(0, GRID)),
                             int(self.rng.integers(0, GRID)),
                             float(self.rng.uniform(INITIAL_ENERGY_MIN,
                                                    INITIAL_ENERGY_MAX)))
                       for i in range(INITIAL_AGENTS)]
        self.initial_total = float(self.grid.sum()) + sum(a.energy for a in self.agents)
        self.deaths = []
        self.audit_fails = 0
        self.max_conservation_error = 0.0
        self.log_lines = []
        self.history = []               # (tick, alive, avgE, sw_delta, wk_delta, eff)
        self.snapshots = []             # (tick, [绿洲占用率...])
        self._prev_sw = 0
        self._prev_wk = 0

    # ===== 初始化 =====

    def _init_energy(self):
        rng = self.rng
        # 沿途碎屑：伯努利 1 点能量
        crumb = rng.random((GRID, GRID)) < BASE_CRUMB_PROB
        self.grid[crumb] += 1.0
        # 固定绿洲：切比雪夫方形斑块
        for _ in range(OASIS_COUNT):
            cx = int(rng.integers(0, GRID))
            cy = int(rng.integers(0, GRID))
            self.oases.append((cx, cy))
            r = OASIS_RADIUS
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) <= r:
                        self.grid[(cy + dy) % GRID, (cx + dx) % GRID] += OASIS_DENSITY

    # ===== 主循环 =====

    def run(self, verbose=False):
        while self.tick < self.ticks and self.alive_count() > 0:
            self._tick(verbose)
        return self.final_report()

    def alive_count(self):
        return sum(1 for a in self.agents if a.alive)

    def _tick(self, verbose):
        for a in self.agents:
            if a.alive:
                a.step(self, self.rng)
        # 记录死亡
        for a in self.agents:
            if not a.alive and a.death_tick is None:
                a.death_tick = self.tick
                self.deaths.append(a)
        self.tick += 1
        if self.tick % LOG_EVERY == 0:
            line = self._log_status()
            self.log_lines.append(line)
            if verbose:
                print(line)
        if self.tick % SNAPSHOT_EVERY == 0:
            self._snapshot()
        self._audit()

    # ===== 守恒审计（无补偿，只有检查）=====

    def _audit(self):
        total = float(self.grid.sum()) + sum(a.energy for a in self.agents if a.alive)
        err = abs(total - self.initial_total)
        if err > self.max_conservation_error:
            self.max_conservation_error = err
        if err > 1e-6:
            self.audit_fails += 1

    # ===== 统计 =====

    def _log_status(self):
        alive = [a for a in self.agents if a.alive]
        n = len(alive)
        avgE = sum(a.energy for a in alive) / n if n else 0.0
        sw = sum(a.switch_count for a in alive)
        wk = sum(a.walked for a in alive)
        sw_delta = sw - self._prev_sw
        wk_delta = wk - self._prev_wk
        self._prev_sw = sw
        self._prev_wk = wk
        eaten = sum(a.eaten for a in self.agents)
        spent = sum(a.spent for a in self.agents)
        eff = eaten / spent if spent > 0 else 0.0
        gridE = float(self.grid.sum())
        self.history.append((self.tick, n, avgE, sw_delta, wk_delta, eff))
        return (f"T {self.tick:>6} alive {n:>3} avgE {avgE:7.1f} "
                f"gridE {gridE:9.1f} 切换Δ {sw_delta:6.0f} 步数Δ {wk_delta:6.0f} "
                f"觅食效率 {eff:.3f} 守恒偏差 {self.max_conservation_error:.2e}")

    def _snapshot(self):
        alive = [a for a in self.agents if a.alive]
        n = len(alive)
        occ = []
        for (cx, cy) in self.oases:
            k = sum(1 for a in alive if a.dist_to(cx, cy) <= OASIS_RADIUS + 2)
            occ.append(k / n if n else 0.0)
        self.snapshots.append((self.tick, occ))

    def final_report(self):
        alive = [a for a in self.agents if a.alive]
        n = len(alive)
        total_sw = sum(a.switch_count for a in self.agents)
        total_wk = sum(a.walked for a in self.agents)
        total_eaten = sum(a.eaten for a in self.agents)
        total_spent = sum(a.spent for a in self.agents)
        causes = {}
        for a in self.deaths:
            causes[a.death_cause] = causes.get(a.death_cause, 0) + 1
        ktick = max(self.tick / 1000.0, 1e-9)
        return {
            'p': self.distract_prob,
            'seed': self.seed,
            'tick': self.tick,
            'alive': n,
            'survival': n / INITIAL_AGENTS,
            'avg_energy': sum(a.energy for a in alive) / n if n else 0.0,
            'deaths': len(self.deaths),
            'death_causes': causes,
            'switches_per_agent_per_ktick': total_sw / INITIAL_AGENTS / ktick,
            'walked_per_agent_per_ktick': total_wk / INITIAL_AGENTS / ktick,
            'forage_efficiency': total_eaten / total_spent if total_spent > 0 else 0.0,
            'final_grid': float(self.grid.sum()),
            'max_conservation_error': self.max_conservation_error,
            'audit_fails': self.audit_fails,
            'oasis_occupancy_last': self.snapshots[-1][1] if self.snapshots else [],
            'history': self.history,
        }
