# -*- coding: utf-8 -*-
"""
WORLD3/plugins/plugin_utils.py —— 生态插件通用工具库（纯函数，无副作用）。

提供供其他插件调用的纯函数：
  distance(a, b)            欧几里得距离（a/b 为含 x/y 的对象）
  is_visible(agent, target, radius)   目标是否在视野内
  filter_family(agent, agents)        同家族 Agent 列表（排除自身）
  cooperation_bonus(agents, target)   围猎效率加成（线性增长，上限 2.0 倍）

本模块本身不产生任何行为（register/on_tick/on_render 均为空操作），
可安全启用/禁用；被 social_ecology 等生态插件依赖。
"""
import math

DESCRIPTION = '生态插件通用工具库：距离/视野/家族/协同加成（纯函数）'

# 生态类插件默认禁用：避免改变既有实验的默认行为（侧边栏可手动开启）
DEFAULT_ENABLED = False
PRIORITY = 0


def distance(a, b):
    """欧几里得距离（a/b 为含 x/y 的对象）。"""
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def is_visible(agent, target, radius):
    """目标是否在视野内（欧几里得距离 ≤ radius）。"""
    return distance(agent, target) <= radius


def filter_family(agent, agents):
    """返回与 agent 同家族（family_id 相同）且非自身的 Agent 列表。
    无 family_id（未初始化）时返回空列表。"""
    fid = agent.get('family_id')
    if fid is None:
        return []
    return [a for a in agents
            if a is not agent
            and a.get('family_id') == fid
            and a.get('alive', True)]


def cooperation_bonus(agents, target):
    """围猎效率加成：统计 target 附近（切比雪夫 ≤2 格）的围猎者数量，
    加成线性增长、上限 2.0 倍：1 只 = 1.0，2 只 = 1.5，3 只及以上 = 2.0。"""
    n = sum(1 for a in agents
            if a.get('alive', True) and distance(a, target) <= 2.0)
    return min(2.0, 1.0 + 0.5 * max(0, n - 1))


def register(world):
    pass


def on_tick(agents, grid):
    pass


def on_render(ax, agents, grid):
    pass


def ui_controls(world):
    pass
