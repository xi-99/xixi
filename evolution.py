# -*- coding: utf-8 -*-
"""
WORLD3/evolution.py —— 数字基因核心（v2.0 基因纪元）。

Agent 的"大脑"不再是一行行 if-else，而是一组随机浮点数（基因组）。
本模块提供四个能力：

  1. 基因组表达：单层感知机 forward(genome, x) —— 纯矩阵运算，无任何规则。
     输入 4 个感知标量（见 gene_brain.py）：
         x0 最近食物距离（归一化 0~1）
         x1 食物方向 sin 分量（归一化 -1~1）
         x2 食物方向 cos 分量（归一化 -1~1）
         x3 当前能量（归一化 0~1）
     输出 5 个动作概率：上 / 下 / 左 / 右 / 驻留。
     （计划书为 4 节点；我们把"驻留"加入动作空间——否则"站在食物上
     细嚼慢咽"永远无法被学习，觅食行为只能学到乱跑。仍是纯感知机。）

  2. 自然选择：fitness(agent, tick) = 本世代存活时间 × 本世代进食数量。
     每次世代更替时精英会重置进食基数（eaten_base）——每代从同一起跑线
     竞争，淘汰的是"这一代的表现"，而不是资历。

  3. 遗传算子：crossover（单点交换基因片段）+ mutate（高斯噪声变异）。

  4. 世代更替：turnover(world) —— 每 GENERATION_TICKS 触发一次（全员换代）：
       适应度前 elite_fraction 的活体成为"父母"（不再保留身体）；
       全部个体退休，能量归还网格（守恒）；
       子代填满种群（agent_count 只），每只由两名随机父母的基因
       交叉 + 变异产生，出生能量从出生地（自身脚下及周围 8 格）实地
       采集——只取存在的能量、绝不创造，守恒严格成立。
       每代基因池完整轮换：活下来的基因，就是上一代最会生存的基因。
"""

import numpy as np

# ==================== 基因组结构 ====================
# 单层感知机：4 输入 → 5 输出。W(4×5=20) + b(5) = 25 个浮点数。
N_INPUTS = 4
N_OUTPUTS = 5          # 上/下/左/右/驻留
GENOME_LEN = N_INPUTS * N_OUTPUTS + N_OUTPUTS

# 默认遗传参数（UI 滑条可覆盖，存于 world.params）
DEFAULT_GENERATION_TICKS = 5000   # 世代间隔（tick）
DEFAULT_ELITE_FRACTION = 0.2      # 适应度前 20% 成为父母
DEFAULT_MUTATION_SIGMA = 0.15     # 高斯变异标准差

ENERGY_SCALE = 300.0              # 能量归一化分母（大致为"吃饱"水平）

# ==================== 感知机 ====================

def random_genome(rng):
    """出生随机基因：U(-1, 1) 均匀随机权重 —— "随机的本能"。"""
    return rng.uniform(-1.0, 1.0, GENOME_LEN)


def forward(genome, x):
    """
    感知机前向：x 为 4 维输入向量，返回 5 个 logits（未归一化）。
    logit = W·x + b —— 唯一"智能"来源，全在权重里。
    """
    w = genome[:N_INPUTS * N_OUTPUTS].reshape(N_INPUTS, N_OUTPUTS)
    b = genome[N_INPUTS * N_OUTPUTS:]
    return np.asarray(x, dtype=np.float64) @ w + b


def softmax(z):
    z = np.asarray(z, dtype=np.float64) - np.max(z)
    e = np.exp(z)
    return e / np.sum(e)


def action_of(idx):
    """动作索引 → (dx, dy)。0 上 / 1 下 / 2 左 / 3 右 / 4 驻留。"""
    return [(0, -1), (0, 1), (-1, 0), (1, 0), (0, 0)][idx]


def sample_move(genome, x, rng):
    """感知机 → softmax 概率 → 采样一个动作，返回 (dx, dy)。"""
    p = softmax(forward(genome, x))
    idx = int(rng.choice(N_OUTPUTS, p=p))
    return action_of(idx)


# ==================== 遗传算子 ====================

def crossover(g_a, g_b, rng):
    """单点交叉：随机切点，交换基因片段（计划书：权重交叉）。"""
    cut = int(rng.integers(1, GENOME_LEN))
    child = np.empty(GENOME_LEN, dtype=np.float64)
    child[:cut] = g_a[:cut]
    child[cut:] = g_b[cut:]
    return child


def mutate(genome, rng, sigma=DEFAULT_MUTATION_SIGMA):
    """随机变异：每个基因加微小高斯噪声（计划书：随机变异）。"""
    return genome + rng.normal(0.0, float(sigma), GENOME_LEN)


# ==================== 自然选择 ====================

def fitness(agent, tick):
    """
    适应度 = 本世代存活时间 × 本世代进食数量。
    存活时间：出生（或上次精英重置）到当前 tick（或死亡 tick）。
    本世代进食量 = eaten − eaten_base（精英每次更替时重置基数）。
    没吃到任何食物 → 适应度 0，基因直接出局——"饿死的个体没有后代"。
    """
    death = agent.get('death_tick')
    end = death if death is not None else tick
    survival = max(0, int(end) - int(agent.get('born_tick', 0)))
    eaten_this_gen = float(agent.get('eaten', 0.0)) - float(agent.get('eaten_base', 0.0))
    return float(survival) * max(0.0, eaten_this_gen)


def turnover(world):
    """
    世代更替（全员换代）：每 GENERATION_TICKS tick 由引擎调用一次。
      - 父母：活体 prey 中本世代适应度前 elite_fraction（计划书：前 20%）；
      - 全员退休：能量归还脚下网格（守恒）；
      - 子代：填满种群到 agent_count，每只由两名随机父母交叉 + 变异；
          出生在随机位置，出生能量从出生地周围 9 格实地采集。
    返回摘要 dict（供引擎记录 evolution_log / UI 展示）。
    """
    params = world.params
    agents = world.agents
    rng = world.rng
    tick = world.tick

    prey = [a for a in agents if a['alive'] and a['kind'] == 'prey']
    n_agent = int(params.get('agent_count', 100))
    elite_frac = float(params.get('elite_fraction', DEFAULT_ELITE_FRACTION))
    # v2.1：出生能量目标与 _new_agent 一致——initial_energy_min/max 间均匀取值
    emin = float(params.get('initial_energy_min', 0.0) or 0.0)
    emax = float(params.get('initial_energy_max', 0.0) or 0.0)
    init_energy = float(params.get('init_energy', 100.0))
    sigma = float(params.get('mutation_sigma', DEFAULT_MUTATION_SIGMA))

    summary = {'tick': tick, 'alive_prey_before': len(prey),
               'parents': 0, 'children': 0, 'max_gen': 0,
               'avg_fitness_all': 0.0, 'avg_fitness_parents': 0.0,
               'avg_eaten_parents': 0.0, 'extinct': False}
    if not prey:
        summary['extinct'] = True
        return summary

    # ---- 1. 本世代适应度排名 → 父母 ----
    scored = sorted(prey, key=lambda a: fitness(a, tick), reverse=True)
    n_parents = max(1, min(len(prey), int(round(n_agent * elite_frac))))
    parents = scored[:n_parents]
    summary['avg_fitness_all'] = float(np.mean([fitness(a, tick) for a in prey]))
    summary['avg_fitness_parents'] = float(np.mean([fitness(a, tick) for a in parents]))
    summary['avg_eaten_parents'] = float(np.mean(
        [max(0.0, a['eaten'] - a.get('eaten_base', 0.0)) for a in parents]))
    summary['parents'] = len(parents)

    # ---- 2. 全员退休：能量归还网格（守恒）----
    for a in prey:
        world.grid.data[a['y'], a['x']] += a['energy']
        a['energy'] = 0.0
        a['alive'] = False
        a['death_cause'] = '世代更替'

    # ---- 3. 子代填满种群 ----
    next_gen = max((a.get('gen', 0) for a in prey), default=0) + 1
    for _ in range(n_agent):
        g_a = parents[int(rng.integers(0, len(parents)))]['genome']
        g_b = parents[int(rng.integers(0, len(parents)))]['genome']
        genome = mutate(crossover(g_a, g_b, rng), rng, sigma)

        child = world._new_agent(len(agents), 'prey')
        child['genome'] = genome
        child['gen'] = next_gen
        # 出生能量：从出生地（随机位置）周围 9 格实地采集（守恒，绝不创造）
        target = float(rng.uniform(emin, emax)) if emax > emin else init_energy
        child['energy'] = _extract(world.grid.data, world.size,
                                   child['x'], child['y'], target)
        child['born_tick'] = tick
        child['last_pos'] = None
        child['walked_this_tick'] = False
        agents.append(child)

    summary['children'] = n_agent
    summary['max_gen'] = next_gen
    return summary


def _extract(grid, size, x, y, amount):
    """
    从 (x,y) 及其周围 8 格实地采集能量，最多 amount。
    只取格子里真实存在的能量（永不取负、永不创造），
    因此网格能量 + 活体能量 的总和严格守恒。
    返回实际采集到的量（贫瘠之地可能少于 amount——孩子生来贫困，公平）。
    """
    cells = [(x, y)]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx != 0 or dy != 0:
                cells.append((x + dx, y + dy))
    got = 0.0
    for cx, cy in cells:
        if got >= amount - 1e-12:
            break
        wx, wy = cx % size, cy % size
        v = grid[wy, wx]
        if v <= 0:
            continue
        take = min(v, amount - got)
        grid[wy, wx] = v - take
        got += take
    return got
