# -*- coding: utf-8 -*-
"""
WORLD3/world_engine.py —— 微内核世界引擎。

内核只负责四件事：
  1. 2D 网格管理（能量点存放）
  2. 主循环定时器（tick 循环线程）
  3. Agent 坐标管理（移动、距离、散落工具）
  4. 插件调度（按启用状态依次调用 on_tick）

不包含任何具体行为逻辑——代谢、觅食、决心、捕食全部在 plugins/ 中。
"""
import threading
import time

import numpy as np


class WorldGrid:
    """网格包装：行为上等同 numpy 数组，另挂 .world 引用供插件取用内核工具。"""

    def __init__(self, world):
        self.world = world
        self.data = np.zeros((world.params['map_size'], world.params['map_size']),
                             dtype=np.float64)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __array__(self, dtype=None):
        return self.data if dtype is None else self.data.astype(dtype)

    def sum(self):
        return float(self.data.sum())


class World:
    """微内核世界。plugins: [(插件名, 模块), ...]，enabled: {插件名: bool}"""

    TICK_DURATION = 0.05   # 每 tick 间隔（秒），UI 可调
    AUDIT_EVERY = 500      # 能量审计间隔（tick）

    def __init__(self, params, plugins=None, enabled=None):
        # params 保持引用（UI 线程与世界线程共享）；Agent 出生时快照实现"换代生效"
        self.params = params
        self.rng = np.random.default_rng(int(self.params['seed']))
        size = int(self.params['map_size'])
        self.size = size
        self.grid = WorldGrid(self)
        self.agents = []
        self.tick = 0
        self.running = False
        self.finished = False
        self.finish_reason = None   # 终止原因：'到达步数' / '全体死亡' / None(被暂停)
        self.lock = threading.Lock()
        self.deaths = []            # 死亡记录 dict 列表
        self.series = []            # 存活率时序 [(tick, alive), ...]
        self.max_conservation_error = 0.0
        self.audit_fails = 0
        self.last_audit_total = 0.0
        self.plugins = plugins or []
        self.enabled = enabled or {name: True for name, _ in (plugins or [])}
        self._init_energy()
        self._init_agents()
        self.initial_total = self._total_energy()

    # ===== 初始化 =====

    # 食物分布常量（UI 无对应滑条，改这里即可）
    CLUSTER_COUNT = 3        # 高密度食物斑块（绿洲）数量
    CLUSTER_RADIUS = 6       # 斑块半径（格）
    CLUSTER_BOOST = 5.0      # 斑块内能量倍数
    BASE_DENSITY = 0.2       # 基底碎屑密度

    def _init_energy(self):
        """铺能量点：稀疏碎屑 + 高密度斑块（绿洲）。
        斑块是"固定的绿洲"，碎屑是"沿途随机刷新的食物"。"""
        size = self.size
        rng = self.rng
        f = float(self.params['food_energy'])
        mask = rng.random((size, size)) < self.BASE_DENSITY
        self.grid.data[mask] = f
        r = self.CLUSTER_RADIUS
        for _ in range(self.CLUSTER_COUNT):
            cx = int(rng.integers(0, size))
            cy = int(rng.integers(0, size))
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) <= r:
                        self.grid.data[(cy + dy) % size, (cx + dx) % size] += f * self.CLUSTER_BOOST

    def _init_agents(self):
        n_prey = int(self.params['agent_count'])
        for i in range(n_prey):
            self.agents.append(self._new_agent(i, 'prey'))
        if self.params.get('predator_enabled', False):
            n_pred = int(self.params['predator_count'])
            for i in range(n_pred):
                self.agents.append(self._new_agent(n_prey + i, 'predator'))

    def _new_agent(self, aid, kind, hunger=None):
        if hunger is None:
            hunger = float(self.rng.uniform(30.0, 60.0)) if kind == 'predator' else 0.0
        return {
            'id': aid,
            'kind': kind,
            'params': dict(self.params),   # 出生时参数快照——"换代生效"的基础
            'x': int(self.rng.integers(0, self.size)),
            'y': int(self.rng.integers(0, self.size)),
            'last_pos': None,
            'energy': float(self.params['init_energy']),
            'hp': float(self.params.get('prey_hp', 10.0)),
            'hunger': float(hunger),
            'attack_cooldown': 0,
            'alive': True,
            'target': None,
            'init_dist': 0,
            'steps': 0,
            'last_dist': 0,
            'switch_count': 0,
            'walked': 0,
            'eaten': 0,
            'spent': 0,
            'walked_this_tick': False,
            'wander_dx': 0,
            'wander_dy': 0,
            'wander_ticks': 0,
            'born_tick': 0,
            'death_tick': None,
            'death_cause': None,
        }

    # ===== 内核工具（插件调用）=====

    def move_agent(self, agent, dx, dy):
        """坐标管理：环形边界移动一步，记录离开的格。"""
        agent['last_pos'] = (agent['x'], agent['y'])
        agent['x'] = (agent['x'] + dx) % self.size
        agent['y'] = (agent['y'] + dy) % self.size
        agent['walked_this_tick'] = True
        agent['walked'] += 1

    def dist_xy(self, x1, y1, x2, y2):
        """环形网格切比雪夫距离"""
        dx = abs(x1 - x2)
        dy = abs(y1 - y2)
        dx = min(dx, self.size - dx)
        dy = min(dy, self.size - dy)
        return max(dx, dy)

    def dist(self, a, b):
        return self.dist_xy(a['x'], a['y'], b['x'], b['y'])

    def scatter(self, x, y, amount, radius=3):
        """把能量散落到 (x,y) 1~radius 格外的随机格（能量守恒：只搬家）"""
        if amount <= 0:
            return
        dx = dy = 0
        while dx == 0 and dy == 0:
            dx = int(self.rng.integers(-radius, radius + 1))
            dy = int(self.rng.integers(-radius, radius + 1))
        self.grid.data[(y + dy) % self.size, (x + dx) % self.size] += amount

    def alive_prey(self):
        return [a for a in self.agents if a['alive'] and a['kind'] == 'prey']

    def alive_predators(self):
        return [a for a in self.agents if a['alive'] and a['kind'] == 'predator']

    # ===== 主循环 =====

    def run_loop(self):
        """
        后台线程主循环：按插件顺序调度 on_tick。
        终止条件（params['stop_condition']）：
          '步数'：到达运行步数；'全灭'：全体死亡（绿色+捕食者均为 0）；
          '任一'：两者任一先到（默认）。
        被外部暂停（running=False）时 finished 保持 False，finish_reason 为 None。
        """
        self.running = True
        self.finished = False
        self.finish_reason = None
        max_ticks = int(self.params.get('max_ticks', 20000))
        duration = float(self.params.get('tick_duration', self.TICK_DURATION))
        cond = self.params.get('stop_condition', '任一')

        while self.running:
            reached = self.tick >= max_ticks
            all_dead = self.alive_count() == 0
            stop = False
            if cond == '步数':
                stop = reached
            elif cond == '全灭':
                stop = all_dead
            else:  # '任一'
                stop = reached or all_dead
            if stop:
                break
            with self.lock:
                self._tick_once()
            if duration > 0:
                time.sleep(duration)

        self.running = False
        # 区分自然终止与用户暂停
        if cond in ('步数', '任一') and self.tick >= max_ticks:
            self.finish_reason = '到达步数'
        elif cond in ('全灭', '任一') and self.alive_count() == 0:
            self.finish_reason = '全体死亡'
        if self.finish_reason is not None:
            self.finished = True

    def _tick_once(self):
        for plug in self.plugins:
            name = plug.name
            if self.enabled.get(name, True):
                try:
                    plug.on_tick(self.agents, self.grid)
                except Exception as e:  # 插件异常不允许打死世界
                    self.deaths.append({'tick': self.tick, 'kind': 'plugin',
                                        'cause': f'插件[{name}]异常: {e}'})
                    self.enabled[name] = False
        # 收集死亡
        for a in self.agents:
            if not a['alive'] and a['death_tick'] is None:
                a['death_tick'] = self.tick
                self.deaths.append({'tick': self.tick, 'id': a['id'],
                                    'kind': a['kind'], 'cause': a['death_cause'],
                                    'x': a['x'], 'y': a['y']})
        self.tick += 1
        if self.tick % 50 == 0:
            self.series.append((self.tick, len(self.alive_prey())))
        if self.tick % self.AUDIT_EVERY == 0:
            self._audit()

    def _total_energy(self):
        return float(self.grid.sum()) + sum(a['energy'] for a in self.agents
                                            if a['alive'])

    def _audit(self):
        total = self._total_energy()
        err = abs(total - self.initial_total)
        self.max_conservation_error = max(self.max_conservation_error, err)
        if err > 1e-6:
            self.audit_fails += 1
        self.last_audit_total = total

    # ===== 查询 =====

    def alive_count(self, kind=None):
        if kind is None:
            return sum(1 for a in self.agents if a['alive'])
        return sum(1 for a in self.agents if a['alive'] and a['kind'] == kind)

    def stats(self):
        """供 UI/诊断使用的汇总统计"""
        alive = [a for a in self.agents if a['alive']]
        prey = [a for a in alive if a['kind'] == 'prey']
        pred = [a for a in alive if a['kind'] == 'predator']
        return {
            'tick': self.tick,
            'alive_prey': len(prey),
            'alive_predator': len(pred),
            'avg_energy': sum(a['energy'] for a in prey) / len(prey) if prey else 0.0,
            'avg_hunger': sum(a['hunger'] for a in pred) / len(pred) if pred else 0.0,
            'avg_switch': sum(a['switch_count'] for a in prey) / len(prey) if prey else 0.0,
            'avg_walked': sum(a['walked'] for a in alive) / len(alive) if alive else 0.0,
            'grid_energy': float(self.grid.sum()),
            'conservation_error': self.max_conservation_error,
            'audit_fails': self.audit_fails,
        }

    def death_causes(self):
        """死亡原因分布: {原因: 数量}"""
        out = {}
        for d in self.deaths:
            cause = d.get('cause') or 'unknown'
            out[cause] = out.get(cause, 0) + 1
        return out
