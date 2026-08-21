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

import evolution


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
        self.evolution_log = []          # 世代更替摘要（v2.0 基因纪元）
        self.plugins = plugins or []
        self.enabled = enabled or {name: True for name, _ in (plugins or [])}
        self._init_energy()
        self._init_agents()
        self.initial_total = self._total_energy()

    # ===== 初始化 =====

    # 食物分布默认值（config.py 合并后由 params 提供：
    # oasis_count / oasis_radius / oasis_density / base_crumb_prob；
    # 以下常量仅作旧参数记录（无这些 key）的兜底，保留旧 Web 行为）
    CLUSTER_COUNT = 3        # 高密度食物斑块（绿洲）数量
    CLUSTER_RADIUS = 6       # 斑块半径（格）
    CLUSTER_BOOST = 5.0      # 斑块内能量倍数（旧语义：food_energy × 倍数）
    BASE_DENSITY = 0.2       # 基底碎屑密度

    def _init_energy(self):
        """铺能量点：稀疏碎屑 + 高密度斑块（绿洲）。
        斑块是"固定的绿洲"，碎屑是"沿途随机刷新的食物"。
        v2.1：斑块数量/半径/密度与碎屑概率来自 params（config.py 合并）；
        碎屑格能量 = food_energy；斑块每格能量 = oasis_density（config 语义）。"""
        size = self.size
        rng = self.rng
        f = float(self.params['food_energy'])
        n_oasis = int(self.params.get('oasis_count', self.CLUSTER_COUNT))
        r = int(self.params.get('oasis_radius', self.CLUSTER_RADIUS))
        crumb_prob = float(self.params.get('base_crumb_prob', self.BASE_DENSITY))
        if 'oasis_density' in self.params:
            density = float(self.params['oasis_density'])
        else:
            density = f * self.CLUSTER_BOOST   # 旧记录兜底：food_energy × 倍数
        mask = rng.random((size, size)) < crumb_prob
        self.grid.data[mask] = f
        for _ in range(n_oasis):
            cx = int(rng.integers(0, size))
            cy = int(rng.integers(0, size))
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) <= r:
                        self.grid.data[(cy + dy) % size, (cx + dx) % size] += density

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
        # v2.1：初始能量在 initial_energy_min/max（config 合并）间均匀取值；
        # 旧参数记录无这两个 key 时回退 init_energy（旧行为）
        emin = float(self.params.get('initial_energy_min', 0.0) or 0.0)
        emax = float(self.params.get('initial_energy_max', 0.0) or 0.0)
        if emax > emin:
            init_e = float(self.rng.uniform(emin, emax))
        else:
            init_e = float(self.params['init_energy'])
        return {
            'id': aid,
            'kind': kind,
            'params': dict(self.params),   # 出生时参数快照——"换代生效"的基础
            'x': int(self.rng.integers(0, self.size)),
            'y': int(self.rng.integers(0, self.size)),
            'last_pos': None,
            'energy': float(init_e),
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
            'eaten_base': 0.0,        # 本世代进食基数（进化适应度用）
            'spent': 0,
            'walked_this_tick': False,
            'wander_dx': 0,
            'wander_dy': 0,
            'wander_ticks': 0,
            'born_tick': 0,
            'death_tick': None,
            'death_cause': None,
            'gen': 0,                    # 世代（v2.0 基因纪元；0 = 初代）
            'genome': None,              # 数字基因：None = 无基因脑（硬编码模式）
        }
        # v2.0：进化模式下，初代 prey 出生即携带随机本能（随机权重）
        if kind == 'prey' and self.params.get('evolution_mode'):
            agent['genome'] = evolution.random_genome(self.rng)

    # ===== 内核工具（插件调用）=====

    def move_agent(self, agent, dx, dy):
        """坐标管理：环形边界移动一步，记录离开的格。
        若 grid 上存在 terrain（地理隔离插件 geography）：
          山脉（1）不可通行 → 拦截移动并返回 False；
          河流（2）通行额外消耗 2 × move_cost，散落回起点格（守恒）。
        返回 True=已移动 / False=被地形拦截。"""
        nx = (agent['x'] + dx) % self.size
        ny = (agent['y'] + dy) % self.size
        terrain = getattr(self.grid, 'terrain', None)
        if terrain is not None and (dx != 0 or dy != 0):
            t = int(terrain[ny, nx])
            if t == 1:
                return False                      # 山脉不可通行
            if t == 2:
                extra = float(agent['params'].get('move_cost', 0.6)) * 2.0
                agent['energy'] -= extra
                agent['spent'] += extra
                self.grid.data[agent['y'], agent['x']] += extra   # 守恒：回起点格
        agent['last_pos'] = (agent['x'], agent['y'])
        agent['x'] = nx
        agent['y'] = ny
        agent['walked_this_tick'] = True
        agent['walked'] += 1
        return True

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
        # v2.0 基因纪元：进化模式下按世代间隔触发自然选择
        if self.params.get('evolution_mode'):
            gen_ticks = int(self.params.get('generation_ticks',
                                            evolution.DEFAULT_GENERATION_TICKS))
            if self.tick > 0 and self.tick % gen_ticks == 0:
                try:
                    self.evolution_log.append(evolution.turnover(self))
                except Exception as e:
                    # 进化引擎异常不允许打死世界：记录并降级为硬编码模式
                    self.deaths.append({'tick': self.tick, 'kind': 'evolution',
                                        'cause': f'进化引擎异常: {e}'})
                    self.params['evolution_mode'] = False
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
            'generation': (max((a.get('gen', 0) for a in prey), default=0)
                           if prey else 0),
        }

    def death_causes(self):
        """死亡原因分布: {原因: 数量}"""
        out = {}
        for d in self.deaths:
            cause = d.get('cause') or 'unknown'
            out[cause] = out.get(cause, 0) + 1
        return out
