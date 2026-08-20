# -*- coding: utf-8 -*-
"""
WORLD3/agent.py —— 数字生命：感知 → 打分 → 决心决策 → 执行。
只有几条 if-else 的傻瓜。智慧应该从 100 个傻瓜的互动里涌现。

决心机制：
  - 锁定的不是"一个格子"，而是"一片猎场"：目标被吃光时，在旧目标
    附近（PATCH_RADIUS）补位不算重新决策——同一片猎场，死磕继续。
  - 只有四种情况才真正重新思考：猎场枯竭 / 耐心耗尽 / 能量危急 / 走神（p）。
"""
import numpy as np
from config import (GRID, PERCEPTION_RANGE, EAT_RATE, METABOLISM, SCATTER_RADIUS,
                    MOVE_COST, EMERGENCY_ENERGY, PATIENCE_FACTOR, PATIENCE_BASE,
                    PATCH_RADIUS, WANDER_PERSIST)


class Agent:
    __slots__ = ('id', 'x', 'y', 'energy', 'alive', 'target', 'init_dist', 'steps',
                 'last_dist', 'switch_count', 'walked', 'eaten', 'spent',
                 'wander_dx', 'wander_dy', 'wander_ticks',
                 'death_tick', 'death_cause')

    def __init__(self, aid, x, y, energy):
        self.id = aid
        self.x = x
        self.y = y
        self.energy = energy
        self.alive = True
        self.target = None          # 锁定的目标格 (x, y)
        self.init_dist = 0          # 选定目标时的距离
        self.steps = 0              # 朝当前目标已走步数
        self.last_dist = 0          # 上次与目标的距离（判断是否原地踏步）
        self.switch_count = 0       # 真正重新决策的次数 —— 犹豫的量化指标
        self.walked = 0             # 累计行走步数
        self.eaten = 0              # 累计进食量
        self.spent = 0              # 累计消耗量（代谢+移动）
        self.wander_dx = 0          # 游荡方向
        self.wander_dy = 0
        self.wander_ticks = 0
        self.death_tick = None
        self.death_cause = None

    # ===== 感知 =====

    def dist_to(self, tx, ty):
        """环形网格上的切比雪夫距离"""
        dx = abs(tx - self.x)
        dy = abs(ty - self.y)
        dx = min(dx, GRID - dx)
        dy = min(dy, GRID - dy)
        return max(dx, dy)

    def _scan_box(self, cx, cy, r):
        """以 (cx,cy) 为中心取 (2r+1)² 的能量补丁（环形）"""
        ys = (cy + np.arange(-r, r + 1)) % GRID
        xs = (cx + np.arange(-r, r + 1)) % GRID
        return np.ix_(ys, xs)

    def perceive(self, world):
        """视野半径内的能量格列表: [(x, y, 能量, 距离)]"""
        r = PERCEPTION_RANGE
        patch = world.grid[self._scan_box(self.x, self.y, r)]
        nz = np.nonzero(patch)
        if nz[0].size == 0:
            return []
        vals = patch[nz]
        out = []
        for (ry, rx), v in zip(zip(nz[0], nz[1]), vals):
            dx = int(rx) - r
            dy = int(ry) - r
            d = max(abs(dx), abs(dy))
            if d == 0:
                continue  # 脚下由进食处理
            out.append(((self.x + dx) % GRID, (self.y + dy) % GRID, float(v), d))
        return out

    def pick_target(self, world, emergency=False):
        """真正重新决策：紧急时最近优先，平时价值/距离打分。"""
        cells = self.perceive(world)
        if not cells:
            self.target = None
            return False
        if emergency:
            best = min(cells, key=lambda c: c[3])            # 求生：最近
        else:
            best = max(cells, key=lambda c: c[2] / (1.0 + c[3]))  # 价值/距离
        self.target = (best[0], best[1])
        self.init_dist = best[3]
        self.steps = 0
        self.last_dist = best[3]
        return True

    def patch_retarget(self, world):
        """
        猎场补位：旧目标附近 (PATCH_RADIUS) 找替代目标。
        同一片猎场换一个格子吃，不算重新决策——死磕继续。
        """
        tx, ty = self.target
        r = PATCH_RADIUS
        patch = world.grid[self._scan_box(tx, ty, r)]
        nz = np.nonzero(patch)
        if nz[0].size == 0:
            return False
        vals = patch[nz]
        best = None
        best_score = -1.0
        for (ry, rx), v in zip(zip(nz[0], nz[1]), vals):
            wx = (tx + int(rx) - r) % GRID
            wy = (ty + int(ry) - r) % GRID
            d = self.dist_to(wx, wy)
            score = float(v) / (1.0 + d)
            if score > best_score:
                best_score = score
                best = (wx, wy, d)
        if best is None:
            return False
        self.target = (best[0], best[1])
        self.init_dist = best[2]
        self.steps = 0
        self.last_dist = best[2]
        return True

    # ===== 决心决策 =====

    def decide(self, world, rng):
        """
        决心：选定猎场后死磕到底，忽略中途诱惑。
        目标失效时先在旧目标附近补位（不算重新决策）；
        只有猎场枯竭 / 耐心耗尽 / 能量危急 / 走神（p）才真正重新思考。
        """
        reason = None
        if self.target is not None:
            tx, ty = self.target
            if world.grid[ty, tx] <= 0:
                reason = 'eaten'                       # 目标被吃光/消失
            elif self.x == tx and self.y == ty:
                reason = 'arrived'                     # 已到达（脚下已吃空）
            elif self.steps > self.init_dist * PATIENCE_FACTOR + PATIENCE_BASE:
                reason = 'patience'                    # 耐心耗尽
            elif self.energy < EMERGENCY_ENERGY:
                reason = 'emergency'                   # 危急：就近求生
            elif rng.random() < world.distract_prob:
                reason = 'distracted'                  # 走神（犹豫旋钮）

        if reason is not None:
            # 目标被吃光/到达后吃空：先在猎场内补位，不打断决心
            if reason in ('eaten', 'arrived') and self.energy >= EMERGENCY_ENERGY:
                if self.patch_retarget(world):
                    return 'seek'
            # 其余情况：真正重新思考
            self.target = None
            emergency = self.energy < EMERGENCY_ENERGY
            self.pick_target(world, emergency=emergency)
            self.switch_count += 1
        elif self.target is None:
            # 首次决策
            emergency = self.energy < EMERGENCY_ENERGY
            self.pick_target(world, emergency=emergency)
            self.switch_count += 1

        return 'seek' if self.target is not None else 'wander'

    # ===== 执行 =====

    def step(self, world, rng):
        # 1. 代谢（散落到 1~3 格外——站着等死才是常态，必须觅食）
        dx = dy = 0
        while dx == 0 and dy == 0:
            dx = int(rng.integers(-SCATTER_RADIUS, SCATTER_RADIUS + 1))
            dy = int(rng.integers(-SCATTER_RADIUS, SCATTER_RADIUS + 1))
        self.energy -= METABOLISM
        world.grid[(self.y + dy) % GRID, (self.x + dx) % GRID] += METABOLISM
        self.spent += METABOLISM

        # 2. 进食（最多 EAT_RATE）
        cell = world.grid[self.y, self.x]
        if cell > 0:
            take = min(EAT_RATE, cell)
            world.grid[self.y, self.x] = cell - take
            self.energy += take
            self.eaten += take

        # 3. 死亡检查
        if self.energy <= 0:
            world.grid[self.y, self.x] += self.energy  # 负余额归还网格（守恒）
            self.energy = 0.0
            self.alive = False
            self.death_cause = 'starved'
            return

        # 4. 决策
        action = self.decide(world, rng)

        # 5. 执行
        if action == 'seek':
            self._seek_step(world, rng)
        else:
            self._wander_step(world, rng)

        # 6. 移动后死亡检查
        if self.energy <= 0:
            world.grid[self.y, self.x] += self.energy
            self.energy = 0.0
            self.alive = False
            self.death_cause = 'starved'

    def _seek_step(self, world, rng):
        """朝锁定目标走一步（切比雪夫方向，走短边）"""
        tx, ty = self.target
        if self.x == tx and self.y == ty:
            return  # 已到达，站在上面吃
        dx = 0
        if tx != self.x:
            dx = 1 if (tx - self.x) % GRID <= GRID // 2 else -1
        dy = 0
        if ty != self.y:
            dy = 1 if (ty - self.y) % GRID <= GRID // 2 else -1
        if dx != 0 and dy != 0 and rng.random() < 0.5:
            dy = 0  # 对角线时一半概率只走横轴，路径更自然
        ox, oy = self.x, self.y
        self.x = (self.x + dx) % GRID
        self.y = (self.y + dy) % GRID
        self.energy -= MOVE_COST
        world.grid[oy, ox] += MOVE_COST  # 移动耗能散落回离开的格
        self.spent += MOVE_COST
        self.walked += 1
        self.steps += 1
        self.last_dist = self.dist_to(tx, ty)

    def _wander_step(self, world, rng):
        """无目标游荡：保持方向 WANDER_PERSIST tick，避免原地打转"""
        if self.wander_ticks <= 0:
            dirs = [(1, 0), (-1, 0), (0, 1), (0, -1),
                    (1, 1), (1, -1), (-1, 1), (-1, -1)]
            self.wander_dx, self.wander_dy = dirs[int(rng.integers(0, 8))]
            self.wander_ticks = WANDER_PERSIST
        self.wander_ticks -= 1
        ox, oy = self.x, self.y
        self.x = (self.x + self.wander_dx) % GRID
        self.y = (self.y + self.wander_dy) % GRID
        self.energy -= MOVE_COST
        world.grid[oy, ox] += MOVE_COST
        self.spent += MOVE_COST
        self.walked += 1
