# -*- coding: utf-8 -*-
"""
WORLD3/plugin_loader.py —— 插件加载与调度（支持热扫描）。

插件接口标准（每个插件必须暴露以下函数，缺失的自动用空操作兜底）：
    register(world) -> None              # 可选：注册插件信息
    on_tick(agents, grid) -> None        # 每 tick 行为逻辑（grid.world 可取内核工具）
    on_render(ax, agents, grid) -> None  # 在地图上叠加自定义图形
    ui_controls(world) -> None           # 专属控件（可选）

插件可选元信息（模块级常量）：
    DESCRIPTION       描述（无 docstring 时使用）
    PRIORITY          调度优先级，越小越先执行（默认 0，同优先级按文件名排序）
    DEFAULT_ENABLED   新会话是否默认启用（默认 True；生态类插件建议 False，
                      避免改变既有实验的默认行为）

插件放置于 plugins/ 目录，按 (PRIORITY, 文件名) 排序自动扫描加载。
描述来源优先级：文件头部三引号 docstring > DESCRIPTION 常量 > "无描述"。
"""
import importlib.util
import os
import sys


class Plugin:
    """插件包装：统一接口 + 空操作兜底，缺失函数不崩溃。"""

    def __init__(self, name, module, path):
        self.name = name
        self.module = module
        self.path = path
        self.enabled = True
        self.description = ''
        self.priority = 0
        self.default_enabled = True
        if module is not None:
            self.priority = int(getattr(module, 'PRIORITY', 0) or 0)
            self.default_enabled = bool(getattr(module, 'DEFAULT_ENABLED', True))

    def register(self, world):
        fn = getattr(self.module, 'register', None)
        if callable(fn):
            try:
                fn(world)
            except Exception as e:
                self.description = f'register 异常: {e}'
        else:
            self.description = self.description or '无描述'

    def on_tick(self, agents, grid):
        fn = getattr(self.module, 'on_tick', None)
        if callable(fn):
            return fn(agents, grid)

    def on_render(self, ax, agents, grid):
        fn = getattr(self.module, 'on_render', None)
        if callable(fn):
            return fn(ax, agents, grid)

    def ui_controls(self, world):
        fn = getattr(self.module, 'ui_controls', None)
        if callable(fn):
            return fn(world)


def scan_plugins(plugins_dir):
    """
    扫描 plugins/ 目录，加载所有 .py 文件（跳过 _ 开头与 __init__.py，
    __pycache__ 目录天然不参与）。
    返回 [Plugin, ...]，按 (PRIORITY, 文件名) 排序，保证 on_tick 调用顺序稳定
    （PRIORITY 越小的插件越先执行，如 social_ecology 用负优先级抢占行为决策）。
    可重复调用实现热刷新：新增文件会被发现，旧文件以新对象重新加载。
    """
    plugins = []
    if not os.path.isdir(plugins_dir):
        return plugins
    files = sorted(f for f in os.listdir(plugins_dir)
                   if f.endswith('.py') and not f.startswith('_'))
    for fname in files:
        name = fname[:-3]
        path = os.path.join(plugins_dir, fname)
        try:
            spec = importlib.util.spec_from_file_location(f'plugin_{name}', path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            p = Plugin(name, module, path)
            # 描述来源：docstring > DESCRIPTION 常量 > 无描述
            doc = (module.__doc__ or '').strip()
            if doc:
                p.description = doc.splitlines()[0].strip() if doc else '无描述'
            else:
                p.description = getattr(module, 'DESCRIPTION', '') or '无描述'
            plugins.append(p)
        except Exception as e:
            # 插件加载失败不阻塞内核：包装成空插件并记录错误
            plugins.append(Plugin(name, None, path))
            plugins[-1].description = f'加载失败: {e}'
    plugins.sort(key=lambda p: (p.priority, p.name))
    return plugins
