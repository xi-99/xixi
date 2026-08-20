# -*- coding: utf-8 -*-
"""
WORLD3/config.py —— 全部参数集中于此，调参只改这里。
实验哲学：能量严格守恒，世界不创造也不消灭能量，只搬家。
"""

# ===== 空间 =====
GRID = 128                 # 128×128 二维网格（环形边界，上下左右无缝）
INITIAL_AGENTS = 100       # 初始 Agent 数量
TICKS = 20000              # 单次运行时长（tick）
SEED = 42                  # 默认随机种子（对照实验必须同种子）

# ===== 能量供给（严格守恒，无外源注入）=====
# 固定的绿洲：高密度能量斑块
OASIS_COUNT = 5
OASIS_RADIUS = 12          # 绿洲半径（格，切比雪夫方形绿洲）
OASIS_DENSITY = 12.0       # 绿洲内每格能量
# 沿途碎屑：伯努利随机散布的 1 点能量（"沿途随机刷新的食物"）
# 1 点能量只够走 1 步——看起来诱人，但不养人；真正养人的是绿洲。
BASE_CRUMB_PROB = 0.3      # 每格有 1 点能量的概率
# 消耗散落（实时生成）：代谢→散落到 1~3 格外（不能原地自循环）；
# 移动→回到离开的格。总量恒定。

# ===== 个体 =====
INITIAL_ENERGY_MIN = 120   # 初始能量下限
INITIAL_ENERGY_MAX = 160   # 初始能量上限
PERCEPTION_RANGE = 10      # 视野半径（格）——"约 10 步"
EAT_RATE = 2.0             # 每 tick 最多进食量
METABOLISM = 0.1           # 基础代谢 / tick（散落到 1~3 格外）
SCATTER_RADIUS = 3         # 代谢散落的最大距离（格）
MOVE_COST = 1.0            # 每步消耗（散落回离开的格）

# ===== 决心机制 =====
DISTRACT_PROB = 0.0        # 犹豫旋钮 p：每 tick 重新决策的概率
                           #   p=0.0 死磕到底；p=1.0 每步都重新思考（复刻旧版行为）
EMERGENCY_ENERGY = 15.0    # 能量危急阈值：放弃远目标，就近求生（重新思考的合法理由）
PATIENCE_FACTOR = 1.5      # 耐心预算 = 初始距离 × 系数 + 常数
PATIENCE_BASE = 20
PATCH_RADIUS = 3           # 猎场半径：目标被吃光时，在旧目标附近补位（不算重新决策）
WANDER_PERSIST = 5         # 无目标游荡时，方向保持的 tick 数

# ===== 日志 =====
LOG_EVERY = 100            # 世界状态日志间隔（tick）
SNAPSHOT_EVERY = 1000      # 绿洲占用率快照间隔（tick）
