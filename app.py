# -*- coding: utf-8 -*-
"""
WORLD3/app.py —— 数字生命实验控制台（终极版）。

运行：start.bat 或  python -m streamlit run app.py

功能：
  - 实时 2D 地图（刷新间隔可调，可关闭动画省 CPU）
  - 全部参数 st.slider 化（滑条+数字输入），带悬浮解释（help= 悬停显示）
  - 终止条件：到达步数 / 全体死亡 / 两者任一，状态栏显示终止原因
  - 实验模式一：单次挂机跑（可重复 N 次，种子自动递增，多曲线叠加 + 均值±标准差）
  - 实验模式二：自动调参扫描（顺序/随机扰动，散点图+趋势线+最优参数高亮）
  - SQLite 历史记录自动刷新（含终止原因），绿色高亮当前最优参数
  - 四段智能诊断（追加终止原因）
"""
import json
import os
import sys
import threading
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from world_engine import World  # noqa: E402
from plugin_loader import scan_plugins  # noqa: E402
from data import init_db  # noqa: E402

PLUGIN_DIR = os.path.join(BASE_DIR, 'plugins')

# matplotlib 中文字体（Windows 常见字体）
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


@st.cache_resource
def get_canvas():
    """
    复用的 matplotlib 画布（进程级单例）。
    地图渲染循环外初始化 fig/ax，每帧 ax.clear() 后重绘并推送到占位符，
    避免每帧销毁重建 figure 造成的闪烁。
    """
    fig, ax = plt.subplots(figsize=(8.6, 8.6))
    fig.patch.set_facecolor('#0d1117')
    return fig, ax

# ==================== 参数硬编码表 ====================
# key -> (标签, 下限, 上限, 默认值, 步长, 类型, 悬浮解释, 归属插件)
# 归属插件：'focus'/'predator'，插件被禁用时该参数自动置灰；None=基础参数
PARAM_SPECS = {
    'map_size':        ('地图大小', 20, 120, 80, 10, int,
                        '地图边长（格）。越大世界越空旷，觅食与捕猎都更艰难。', None),
    'agent_count':     ('Agent 数量', 10, 300, 100, 5, int,
                        '开局绿色 Agent 的数量。越多越热闹，食物消耗也越快。', None),
    'predator_count':  ('捕食者初始数量', 0, 30, 5, 1, int,
                        '开局红色捕食者的数量。0 = 没有捕食者，纯素食世界。', 'predator'),
    'prey_hp':         ('绿色生命值', 1, 50, 10, 1, int,
                        '绿色 Agent 的生命值，被捕食者攻击扣减，归零死亡。', None),
    'move_cost':       ('能量消耗', 0.0, 5.0, 0.6, 0.05, float,
                        '每走一步消耗的能量。越高，乱跑越致命。', None),
    'food_energy':     ('食物能量', 1, 50, 10, 1, int,
                        '吃掉一个能量点获得的能量。', None),
    'view_range':      ('视野距离', 1, 50, 20, 1, int,
                        'Agent 能发现多远的食物/猎物。视野越大越容易做对决定。', None),
    'hesitation_prob': ('犹豫概率', 0.0, 1.0, 0.5, 0.01, float,
                        '0 = 死磕到底（决心型）；1 = 犹豫不决（走神型）。', 'focus'),
    'patch_radius':    ('猎场补位半径', 1, 10, 3, 1, int,
                        '目标被吃光时，此范围内换目标不算"重新决策"——同一片猎场。', 'focus'),
    'emergency_energy': ('危急阈值', 1, 50, 15, 1, int,
                         '能量低于此值时放弃远目标，优先就近求生。', 'focus'),
    'attack_power':    ('攻击力', 1, 10, 3, 1, int,
                        '每次攻击扣除绿色 Agent 的生命值。攻击力 3 时 4 次致死。', 'predator'),
    'hunger_threshold': ('饥饿触发阈值', 10, 100, 70, 5, int,
                         '捕食者饥饿值高于此值时主动猎杀；低于 30 则随机游走。', 'predator'),
}

SCAN_KEYS = ['hesitation_prob', 'move_cost', 'food_energy', 'view_range']

DEFAULT_PARAMS = {
    'seed': 42,
    'map_size': 80,
    'agent_count': 100,
    'move_cost': 0.6,
    'food_energy': 10,
    'view_range': 20,
    'hesitation_prob': 0.5,
    'predator_enabled': True,
    'predator_count': 5,
    'attack_power': 3,
    'hunger_threshold': 70,
    'prey_hp': 10,
    'patch_radius': 3,
    'emergency_energy': 15,
    'max_ticks': 20000,
    'tick_duration': 0.05,
    'init_energy': 100.0,
    'stop_condition': '任一',
    'repeats': 1,
}

REFRESH_OPTIONS = {'实时加载': 0.5, '关闭动画': None}

st.set_page_config(page_title='WORLD3 数字生命实验控制台', layout='wide',
                   page_icon='🧬')


# ==================== 会话状态 ====================

def init_state():
    ss = st.session_state
    if 'initialized' in ss:
        return
    ss.initialized = True
    ss.params = dict(DEFAULT_PARAMS)
    ss.plugins = scan_plugins(PLUGIN_DIR)
    ss.enabled = {p.name: True for p in ss.plugins}
    ss.param_mode = '换代生效'
    ss.refresh_interval = 0.5          # 画面刷新：实时加载（0.5秒/帧）或关闭动画
    ss.exp_mode = '单次挂机跑'          # 实验模式
    ss.scan_set = {'param': 'hesitation_prob', 'start': None, 'end': None,
                   'step': None, 'random': False, 'n_random': 10}
    ss.realtime = False                # 参数提交模式：False=点【确定】统一生效
    ss.dialog_rid = None               # 详情弹窗：当前打开的实验 id（None=关闭）
    # runtime：普通 dict，后台批次线程与渲染线程共享（st.session_state
    # 只能在脚本线程访问，后台线程一律走 runtime）
    ss.runtime = {
        'world': None,
        'progress': (0, 1, ''),
        'status': '待命',
        'batch_active': False,
        'batch_done': False,
        'batch_cancel': False,
        'batch_thread': None,
        'batch_results': [],           # [{'meta':..., 'result':...}]
        'history': init_db.load_experiments(),   # 启动即加载历史（含复现来源）
        'last_error': '',
        'pending_reset': False,        # 批次结束后由渲染线程执行自动就绪
        'last_message': None,          # (文本, 时间戳) 状态栏提示，3 秒消失
        'last_finish_reason': None,    # 最近一次结束原因
        'reproduced_from': None,       # 复现参数来源实验 id（下次开始写入 source）
    }
    # 建一个静态世界用于初始渲染
    w = World(params=ss.params, plugins=ss.plugins, enabled=ss.enabled)
    ss.runtime['world'] = w


init_state()
ss = st.session_state


# ==================== 世界构建 ====================

def build_idle_world():
    """用当前参数建一个不运行的静态世界（重置/初始渲染用）"""
    ss.runtime['world'] = World(params=ss.params, plugins=ss.plugins,
                                enabled=ss.enabled)
    ss.runtime['status'] = '待命'


# ==================== 批次（实验）线程 ====================

def build_tasks():
    """
    根据实验模式生成任务列表。
    每项: (params_dict, meta)
    meta: {mode, seed, source, param?, value?, rep?}
    source: 'manual'（手动开始）或 'reproduced_from_#X'（复制参数后运行）
    """
    base_seed = int(ss.params['seed'])
    repeats = max(1, int(ss.params.get('repeats', 1)))
    # 来源标记：复制参数后第一次运行记为复现，之后消费掉
    rep_from = ss.runtime.get('reproduced_from')
    source = f'reproduced_from_{rep_from}' if rep_from is not None else 'manual'
    ss.runtime['reproduced_from'] = None
    # UI 设置快照（详情弹窗参数列表展示用）
    ui_snap = {
        'ui_refresh_interval': '实时加载' if ss.refresh_interval is not None else '关闭动画',
        'ui_param_mode': ss.param_mode,
    }
    tasks = []
    idx = 0
    if ss.exp_mode == '单次挂机跑':
        for r in range(repeats):
            p = dict(ss.params)
            p.update(ui_snap)
            p['seed'] = base_seed + idx
            idx += 1
            tasks.append((p, {'mode': 'single', 'seed': p['seed'], 'rep': r,
                              'source': source}))
    else:  # 自动调参扫描
        key = ss.scan_set['param']
        spec = PARAM_SPECS[key]
        lo = float(ss.scan_set['start'] if ss.scan_set['start'] is not None
                   else spec[1])
        hi = float(ss.scan_set['end'] if ss.scan_set['end'] is not None else spec[2])
        step = float(ss.scan_set['step'] if ss.scan_set['step'] is not None
                     else spec[4])
        lo = max(float(spec[1]), min(float(spec[2]), lo))
        hi = max(float(spec[1]), min(float(spec[2]), hi))
        if hi < lo:
            lo, hi = hi, lo
        rng = np.random.default_rng(base_seed)
        if ss.scan_set['random']:
            n = int(ss.scan_set['n_random'])
            values = np.sort(rng.uniform(lo, hi, n))
        else:
            step = max(float(spec[4]), step)
            values = np.arange(lo, hi + step / 2.0, step)
        # 按参数类型取整
        if spec[5] is int:
            values = np.unique(np.round(values).astype(int))
        values = np.clip(values, float(spec[1]), float(spec[2]))
        for v in values:
            for r in range(repeats):
                p = dict(ss.params)
                p.update(ui_snap)
                p[key] = spec[5](v)
                p['seed'] = base_seed + idx
                p['tick_duration'] = 0.0   # 扫描模式纯算加速
                idx += 1
                tasks.append((p, {'mode': 'scan', 'param': key, 'value': spec[5](v),
                                  'rep': r, 'seed': p['seed'], 'source': source}))
    return tasks


def collect_result(w, meta):
    agent_count = max(int(w.params['agent_count']), 1)
    return {
        'end_tick': w.tick,
        'alive_final': w.alive_count('prey'),
        'peak_alive': max((n for _, n in w.series), default=0),
        'survival_rate': w.alive_count('prey') / agent_count,
        'finish_reason': w.finish_reason or '暂停',
        'source': meta.get('source', 'manual'),
        'deaths': w.deaths,               # 死亡明细（详情弹窗分段复盘用）
        'diagnosis': build_diagnosis(w),
        'series': w.series,
        'meta': meta,
    }


def batch_worker(tasks, runtime, plugins, enabled):
    """后台批次线程：依次执行任务，每组结束立即存库并刷新历史。
    注意：本线程只操作 runtime 字典（普通 dict），绝不触碰 st.session_state。
    - 自然结束 / 手动结束（finish_reason 非 None）→ 保存该组数据
    - 批次全部完成后设置 pending_reset，由渲染线程把世界置为就绪（自动重置）"""
    runtime['batch_active'] = True
    runtime['batch_cancel'] = False
    runtime['batch_done'] = False
    runtime['batch_results'] = []
    total = len(tasks)
    last_reason = None
    try:
        for i, (params_i, meta) in enumerate(tasks):
            if runtime['batch_cancel']:
                break
            desc = f'第 {i+1}/{total} 组'
            if meta['mode'] == 'scan':
                label = PARAM_SPECS[meta['param']][0]
                desc += f'｜{label} = {meta["value"]:g}（重复 {meta["rep"]+1}）'
            else:
                desc += f'｜种子 {meta["seed"]}'
            runtime['progress'] = (i, total, desc)
            runtime['status'] = '运行中'

            w = World(params=params_i, plugins=plugins, enabled=enabled)
            runtime['world'] = w
            th = threading.Thread(target=w.run_loop, daemon=True)
            th.start()
            th.join()
            # 被暂停（finish_reason=None）：等待用户"继续"或"直接结束"或取消
            while w.finish_reason is None and not runtime['batch_cancel']:
                time.sleep(0.2)
            # 自然结束 / 手动提前结束 → 保存当前组（含未达步数的部分数据）
            if w.finish_reason is not None:
                result = collect_result(w, meta)
                runtime['batch_results'].append({'meta': meta, 'result': result})
                last_reason = result['finish_reason']
                try:
                    result['plugin_enabled'] = dict(enabled)
                    init_db.save_experiment(w.params, result)
                    runtime['history'] = init_db.load_experiments()  # 自动刷新历史
                except Exception as e:
                    runtime['last_error'] = f'保存实验记录失败: {e}'
                runtime['progress'] = (i + 1, total, desc)
            if runtime['batch_cancel']:
                break
    finally:
        runtime['batch_active'] = False
        runtime['batch_done'] = True
        runtime['last_finish_reason'] = last_reason
        runtime['pending_reset'] = True   # 结束 → 渲染线程自动就绪（清空/重置步数）
        runtime['status'] = (f'已结束 [{last_reason}]' if last_reason else '已结束')


# ==================== 实验控制 ====================

def start_experiment():
    """开始新实验（空闲/已结束状态可用）。"""
    if ss.runtime['batch_active']:
        return   # 批次已在运行，忽略（暂停恢复请用"继续"）
    tasks = build_tasks()
    if not tasks:
        ss.runtime['last_error'] = '任务列表为空，请检查扫描参数'
        return
    ss.runtime['batch_results'] = []
    ss.runtime['batch_done'] = False
    ss.runtime['last_finish_reason'] = None
    ss.runtime['batch_thread'] = threading.Thread(
        target=batch_worker, args=(tasks, ss.runtime, ss.plugins, ss.enabled),
        daemon=True)
    ss.runtime['batch_thread'].start()


def pause_experiment():
    """暂停：停住主循环；之后可选择"继续"或"直接结束"。"""
    w = ss.runtime.get('world')
    if w is not None and w.running:
        w.running = False
        ss.runtime['status'] = '已暂停'


def resume_experiment():
    """继续：恢复被暂停的实验。"""
    w = ss.runtime.get('world')
    if w is not None and not w.running and not w.finished \
            and w.finish_reason is None:
        threading.Thread(target=w.run_loop, daemon=True).start()
        ss.runtime['status'] = '运行中'


def end_experiment():
    """
    直接结束：停止主循环 → 立即保存当前数据（含未达步数部分）→
    标记终止原因"手动提前结束" → 世界自动就绪（由渲染线程执行）。
    """
    w = ss.runtime.get('world')
    if w is not None:
        w.running = False
        if w.finish_reason is None:
            w.finish_reason = '手动提前结束'   # 触发批次线程保存该组
            w.finished = True
    ss.runtime['batch_cancel'] = True          # 保存当前组后批次停止
    ss.runtime['pending_reset'] = True         # 渲染线程立即执行就绪
    ss.runtime['last_message'] = ('✅ 实验已手动结束，数据已保存', time.time())
    try:
        st.toast('✅ 实验已手动结束，数据已保存')
    except Exception:
        pass


def _abort_experiment():
    """
    内部中止（"立即重置"参数模式用）：取消当前批次并重建就绪世界。
    注意：与"直接结束"不同，中止不保存当前实验数据。
    """
    ss.runtime['batch_cancel'] = True
    w = ss.runtime.get('world')
    if w is not None:
        w.running = False
    th = ss.runtime.get('batch_thread')
    if th is not None and th.is_alive():
        th.join(timeout=3.0)
    ss.runtime['batch_cancel'] = False
    ss.runtime['batch_active'] = False
    ss.runtime['batch_done'] = False
    ss.runtime['batch_results'] = []
    build_idle_world()


# ==================== 参数回调 ====================

def on_param(key):
    """
    参数变化回调。
    非实时模式（默认）：仅记录数值，等待【确定】统一推送给世界；
    实时模式：立即生效（与旧行为一致）。
    """
    ss.params[key] = st.session_state.get(f'p_{key}')
    if not ss.realtime:
        return
    if ss.param_mode == '立即重置':
        _abort_experiment()


def on_realtime():
    ss.realtime = bool(st.session_state.get('p_realtime', False))


def apply_params():
    """
    【确定】：将当前所有控件值一次性推送给世界，并刷新地图/模型状态。
    仅在非实时模式下可见可用；运行中禁用。
    """
    if ss.runtime['batch_active']:
        return
    # 从所有控件读取当前值（非实时模式下回调不写世界参数，统一在此生效）
    for key in PARAM_SPECS:
        wk = f'p_{key}'
        if wk in st.session_state:
            ss.params[key] = st.session_state[wk]
    if 'p_seed' in st.session_state:
        ss.params['seed'] = int(st.session_state['p_seed'])
    if 'p_max_ticks' in st.session_state:
        ss.params['max_ticks'] = int(st.session_state['p_max_ticks'])
    if 'p_repeats' in st.session_state:
        ss.params['repeats'] = int(st.session_state['p_repeats'])
    if 'p_stop_condition' in st.session_state:
        v = st.session_state['p_stop_condition']
        ss.params['stop_condition'] = {'到达步数': '步数', '全体死亡': '全灭',
                                       '两者任一': '任一'}[v]
    if 'p_param_mode' in st.session_state:
        ss.param_mode = st.session_state['p_param_mode']
    # 执行：立即重置 → 中止当前并重建；换代 → 重建静态世界使地图反映新参数
    if ss.param_mode == '立即重置':
        _abort_experiment()
    else:
        build_idle_world()
    try:
        st.toast('✅ 参数已生效')
    except Exception:
        pass


def on_mode():
    """参数生效模式：非实时模式下等待【确定】统一生效"""
    if not ss.realtime:
        return
    ss.param_mode = st.session_state['p_param_mode']


def on_stop_condition():
    if not ss.realtime:
        return
    v = st.session_state['p_stop_condition']
    ss.params['stop_condition'] = {'到达步数': '步数', '全体死亡': '全灭',
                                   '两者任一': '任一'}[v]


def on_refresh():
    v = st.session_state['p_refresh']
    ss.refresh_interval = REFRESH_OPTIONS[v]


def on_exp_mode():
    ss.exp_mode = st.session_state['p_exp_mode']


def on_repeats():
    """重复次数：非实时模式下等待【确定】统一生效"""
    if not ss.realtime:
        return
    ss.params['repeats'] = int(st.session_state['p_repeats'])


def on_scan_param():
    ss.scan_set['param'] = st.session_state['p_scan_param']
    # 切换扫描参数后重置范围输入（避免旧值越界）
    ss.scan_set['start'] = None
    ss.scan_set['end'] = None
    ss.scan_set['step'] = None


def on_scan_random():
    ss.scan_set['random'] = bool(st.session_state['p_scan_random'])


def on_scan_n_random():
    ss.scan_set['n_random'] = int(st.session_state['p_scan_n_random'])


def on_scan_range(key):
    ss.scan_set[key] = st.session_state[f'p_scan_{key}']


def on_toggle_plugin(name):
    ss.enabled[name] = bool(st.session_state.get(f'plug_{name}', True))
    w = ss.runtime.get('world')
    if w is not None:
        w.enabled = ss.enabled


def refresh_plugins():
    """重新扫描插件目录：新插件默认禁用；运行中不可刷新（按钮已置灰）。"""
    new_plugins = scan_plugins(PLUGIN_DIR)
    for p in new_plugins:
        if p.name not in ss.enabled:
            ss.enabled[p.name] = False   # 新发现的插件默认禁用
    ss.plugins = new_plugins
    w = ss.runtime.get('world')
    if w is not None and not w.running:
        w.plugins = new_plugins
        w.enabled = ss.enabled
    st.rerun()


# ==================== 诊断 ====================

def _alive_at(world, t):
    best = None
    for tick, n in world.series:
        if tick <= t:
            best = n
        else:
            break
    return best if best is not None else (world.series[0][1] if world.series else 0)


def _segment_phrase(idx, n_deaths, cause_str, alive, avg_sw, avg_wk):
    seg = f'死亡 {n_deaths} 只（{cause_str}），段末存活 {alive} 只'
    act = f'平均目标切换 {avg_sw:.0f} 次、行走 {avg_wk:.0f} 步'
    if n_deaths == 0:
        return f'{seg}。{act}，此阶段风平浪静。'
    if cause_str.startswith('被猎杀') and '能量耗尽' not in cause_str:
        return f'{seg}。{act}。红色捕食者主导了本阶段，绿色种群受到猎杀压力。'
    if '被猎杀' in cause_str and '能量耗尽' in cause_str:
        return f'{seg}。{act}。捕食与饥饿双线施压，种群腹背受敌。'
    return f'{seg}。{act}。能量短缺是主要死因，觅食效率跟不上消耗。'


def _macro_phrase(world):
    s = world.stats()
    causes = world.death_causes()
    n_prey = s['alive_prey']
    total_prey = int(world.params['agent_count'])
    hunted = causes.get('被猎杀', 0)
    starved = causes.get('能量耗尽', 0)
    pred_starved = causes.get('饿死', 0)
    if world.tick >= int(world.params['max_ticks']) - 1 and n_prey >= total_prey * 0.7:
        return (f'种群在参数下稳定存活（{n_prey}/{total_prey}），生态趋于平衡。'
                f'总死亡 {len(world.deaths)} 只，守恒偏差 {s["conservation_error"]:.1e}。')
    if hunted > starved and hunted > 0:
        return (f'捕食者压力过大，绿色种群被大量猎杀（被猎杀 {hunted} 只）。'
                f'可降低捕食者数量/攻击力，或提高绿色生命值。')
    if starved > hunted and starved > 0:
        return (f'能量产出不足导致大规模饿死（能量耗尽 {starved} 只）。'
                f'可提高食物能量/降低移动消耗，或调低 Agent 数量。')
    if pred_starved > 0 and n_prey == total_prey:
        return f'捕食者因食物短缺先行崩溃（饿死 {pred_starved} 只），绿色种群安然无恙。'
    if n_prey == 0:
        return f'参数失衡导致绿色种群全灭于 tick {world.tick}，需要重新调整。'
    return (f'种群经历了 {world.tick} 步的演化，最终存活 {n_prey}/{total_prey} 只。'
            f'主要死因：' + ('、'.join(f'{k} {v}只' for k, v in causes.items()) or '无') + '。')


def build_diagnosis(world):
    end = world.tick
    reason = world.finish_reason or '手动暂停'
    if end <= 0:
        return f'【终止原因】{reason}\n实验尚未运行，无诊断数据。'
    quarters = [end // 4, end // 2, 3 * end // 4, end]
    seg_texts = []
    for i, t1 in enumerate(quarters):
        t0 = 0 if i == 0 else quarters[i - 1]
        ds = [d for d in world.deaths if t0 <= d.get('tick', 0) < t1]
        causes = {}
        for d in ds:
            c = d.get('cause') or '未知'
            causes[c] = causes.get(c, 0) + 1
        cause_str = '、'.join(f'{k} {v}只' for k, v in causes.items()) or '无'
        alive = _alive_at(world, t1)
        prey = [a for a in world.agents if a['kind'] == 'prey']
        avg_sw = sum(a['switch_count'] for a in prey) / len(prey) if prey else 0.0
        avg_wk = sum(a['walked'] for a in prey) / len(prey) if prey else 0.0
        seg_texts.append(f'{t0}-{t1}步：{_segment_phrase(i, len(ds), cause_str, alive, avg_sw, avg_wk)}')
    return (f'【终止原因】{reason}（tick {end}）\n'
            f'【宏观结论】{_macro_phrase(world)}\n'
            f'【分段复盘】\n' + '\n'.join(seg_texts))


# ==================== 参数复现（历史一键加载）====================

def load_params_from(rid):
    """
    从数据库读取实验 #rid 的完整参数（含滑条值、种子、插件启用状态），
    实时更新右侧控制面板的所有控件。只填参数，不自动运行。
    """
    rec = init_db.get_experiment(rid)
    if rec is None:
        ss.runtime['last_error'] = f'实验 #{rid} 不存在'
        return
    params = rec.get('params_json') or {}
    if params:
        for k, v in params.items():
            if k == 'plugin_enabled':
                continue
            if k in ss.params:
                ss.params[k] = v
            # 同步控件状态（widget key）
            key = f'p_{k}'
            if key in st.session_state:
                st.session_state[key] = v
        plug = params.get('plugin_enabled')
        if isinstance(plug, dict):
            for name in ss.enabled:
                ss.enabled[name] = bool(plug.get(name, True))
            ss.params['predator_enabled'] = ss.enabled.get('predator', True)
    w = ss.runtime.get('world')
    if w is not None:
        w.enabled = ss.enabled
    ss.runtime['reproduced_from'] = int(rid)   # 下次开始记为 reproduced_from_#X
    try:
        st.toast(f'✅ 已加载 #{rid} 的参数，可修改后重新开始实验')
    except Exception:
        pass
    st.rerun()


# ==================== 结果图表 ====================

def render_batch_result():
    """批次完成后：单次模式叠加曲线；扫描模式散点+趋势线+最优高亮。"""
    results = [r['result'] for r in ss.runtime['batch_results']]
    metas = [r['meta'] for r in ss.runtime['batch_results']]
    if not results:
        return
    first = metas[0]['mode']

    if first == 'single':
        # ---- 多曲线叠加 ----
        frames = {}
        for m, r in zip(metas, results):
            frames[f'种子 {m["seed"]}'] = pd.Series(
                [n for _, n in r['series']],
                index=[t for t, _ in r['series']])
        df = pd.DataFrame(frames)
        st.line_chart(df, height=260)
        surv = np.array([r['survival_rate'] for r in results])
        mean_s = surv.mean()
        std_s = surv.std()
        if std_s < 0.05:
            stab = '稳定'
        elif std_s < 0.15:
            stab = '较稳定'
        else:
            stab = '波动较大'
        st.caption(f'重复 {len(results)} 次：平均存活率 {mean_s*100:.1f}% ± '
                   f'{std_s*100:.1f}% ｜稳定性判断：{stab}')
    else:
        # ---- 扫描散点 + 趋势线 ----
        key = metas[0]['param']
        label = PARAM_SPECS[key][0]
        xs, ys, yerrs = [], [], []
        by_value = {}
        for m, r in zip(metas, results):
            by_value.setdefault(m['value'], []).append(r['survival_rate'])
        for v in sorted(by_value):
            xs.append(v)
            ys.append(float(np.mean(by_value[v])))
            yerrs.append(float(np.std(by_value[v])))
        xs = np.array(xs)
        ys = np.array(ys)
        yerrs = np.array(yerrs)
        best_i = int(np.argmax(ys))

        fig, ax = plt.subplots(figsize=(8.4, 4.6))
        ax.set_facecolor('#0d1117')
        fig.patch.set_facecolor('#0d1117')
        ax.errorbar(xs, ys, yerr=yerrs, fmt='o', color='#58a6ff',
                    markersize=6, ecolor='#8b949e', capsize=3, zorder=3,
                    label='存活率（含重复标准差）')
        # 趋势线（点数足够时二次拟合，否则线性）
        if len(xs) >= 3:
            coeffs = np.polyfit(xs, ys, 2)
        else:
            coeffs = np.polyfit(xs, ys, 1)
        xs_line = np.linspace(xs.min(), xs.max(), 120)
        ax.plot(xs_line, np.polyval(coeffs, xs_line), '--', color='#e3b341',
                linewidth=1.6, label='趋势线', zorder=2)
        # 绿色高亮最优
        ax.scatter([xs[best_i]], [ys[best_i]], s=200, facecolors='none',
                   edgecolors='#3fb950', linewidths=2.6, zorder=5,
                   label='存活率最高')
        ax.set_xlabel(label, color='#c9d1d9')
        ax.set_ylabel('存活率', color='#c9d1d9')
        ax.set_title(f'{label} 扫描', color='#e6edf3')
        ax.tick_params(colors='#8b949e')
        ax.legend(facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')
        ax.grid(alpha=0.15)
        st.pyplot(fig, clear_figure=True)
        plt.close(fig)
        best_v = xs[best_i]
        st.markdown(f"**🏆 建议使用：{label} = {best_v:g}**"
                    f"（存活率 {ys[best_i]*100:.1f}%）")


# ==================== 历史记录（唯一入口：侧边栏）====================

def _open_detail(rid):
    """打开详情弹窗（fragment 内只设标志 + 全页重跑，由主脚本调用 st.dialog）"""
    ss.dialog_rid = int(rid)
    st.rerun()


def _close_detail():
    ss.dialog_rid = None
    st.rerun()


def _series_alive_at(series, t):
    """从存活率时序取最接近 t（且 <=t）的存活数"""
    best = None
    for tick, n in series:
        if tick <= t:
            best = n
        else:
            break
    return best if best is not None else (series[0][1] if series else 0)


def _detail_chart(rec, n_seg):
    """详情弹窗存活率曲线图：深色主题，按分段数在 tick 轴均分画分隔线。"""
    fig, ax = plt.subplots(figsize=(6.6, 2.4))
    ax.set_facecolor('#0d1117')
    fig.patch.set_facecolor('#0d1117')
    series = rec.get('series') or []
    agent_count = max(int(rec.get('agent_count') or 100), 1)
    end = int(rec.get('end_tick') or 0)
    if series:
        ticks = [t for t, _ in series]
        vals = [n / agent_count for _, n in series]
        ax.plot(ticks, vals, color='#58a6ff', linewidth=1.4,
                label='存活率')
        end = max(end, max(ticks))
    if end > 0 and n_seg > 1:
        for i in range(1, n_seg):
            ax.axvline(end * i / n_seg, color='#e3b341', alpha=0.35,
                       linestyle='--', linewidth=0.8)
        ytop = ax.get_ylim()[1]
        for i in range(n_seg):
            xc = end * (i + 0.5) / n_seg
            ax.text(xc, ytop * 0.96, f'S{i + 1}', color='#e3b341', fontsize=8,
                    ha='center', va='top')
    ax.set_xlabel('tick', color='#c9d1d9')
    ax.set_ylabel('存活率', color='#c9d1d9')
    ax.set_xlim(0, max(end, 1))
    ax.set_ylim(0, 1.02)
    ax.tick_params(colors='#8b949e', labelsize=8)
    ax.grid(alpha=0.15)
    return fig


def _detail_segments(rec, n_seg):
    """
    按指定分段数生成复盘文字（基于死亡明细 + 存活率时序）。
    数据不足时给出提示或合并显示。
    """
    deaths = rec.get('deaths') or []
    series = rec.get('series') or []
    end = int(rec.get('end_tick') or 0)
    if end <= 0:
        return '数据不足：该实验未产生有效运行数据。'
    if series and len(series) < n_seg * 2:
        hint = '（注：存活曲线采样点较少，分段为近似估算）\n'
    else:
        hint = ''
    lines = []
    for i in range(n_seg):
        t0 = end * i // n_seg
        t1 = end * (i + 1) // n_seg
        ds = [d for d in deaths if t0 <= d.get('tick', 0) < t1]
        causes = {}
        for d in ds:
            c = d.get('cause') or '未知'
            causes[c] = causes.get(c, 0) + 1
        cause_str = '、'.join(f'{k} {v}只' for k, v in causes.items()) or '无'
        alive0 = _series_alive_at(series, t0)
        alive1 = _series_alive_at(series, t1)
        delta = alive1 - alive0
        if len(ds) == 0:
            tail = '此段风平浪静。'
        elif '被猎杀' in causes and '能量耗尽' not in causes:
            tail = '捕食者主导本段，绿色种群受到猎杀压力。'
        elif '被猎杀' in causes and '能量耗尽' in causes:
            tail = '捕食与饥饿双线施压，种群腹背受敌。'
        else:
            tail = '能量短缺是主要死因，觅食效率跟不上消耗。'
        lines.append(
            f'段{i + 1}（{t0}-{t1}步）：死亡 {len(ds)} 只（{cause_str}），'
            f'存活 {alive0}→{alive1}（{delta:+d} 只）。{tail}')
    return hint + '\n'.join(lines)


def _detail_params(rec):
    """详情弹窗参数列表（参数名 + 数值）"""
    p = rec.get('params_json') or {}
    cond_map = {'任一': '两者任一', '步数': '到达步数', '全灭': '全体死亡'}
    stop = cond_map.get(p.get('stop_condition'), p.get('stop_condition') or '两者任一')
    rows = [
        ('地图大小', f"{p.get('map_size', rec.get('map_size', '—'))} × "
                    f"{p.get('map_size', rec.get('map_size', '—'))}"),
        ('Agent 数量', p.get('agent_count', rec.get('agent_count', '—'))),
        ('能量消耗', p.get('move_cost', '—')),
        ('食物能量', p.get('food_energy', '—')),
        ('视野距离', p.get('view_range', '—')),
        ('犹豫概率', p.get('hesitation_prob', p.get('distract_prob', '—'))),
        ('猎场补位半径', p.get('patch_radius', '—')),
        ('危急阈值', p.get('emergency_energy', '—')),
        ('捕食者初始数量', p.get('predator_count', '—')),
        ('攻击力', p.get('attack_power', '—')),
        ('饥饿触发阈值', p.get('hunger_threshold', '—')),
        ('随机种子', p.get('seed', rec.get('seed', '—'))),
        ('终止条件', stop),
        ('画面刷新间隔', p.get('ui_refresh_interval', '实时加载')),
        ('参数生效模式', p.get('ui_param_mode', '换代生效')),
    ]
    out = []
    for k, v in rows:
        out.append(f'**{k}**：{v}')
    return rows, out


@st.dialog('实验详情', width='large')
def show_detail(rid):
    """
    详情弹窗：存活率曲线（分段可调）→ 分段复盘 → 参数列表 → 复制参数。
    图表按需生成：仅弹出时从数据库读取并绘制；不点开不生成。
    弹窗高度/宽度由全局 CSS 控制，内容区单一滚动条（stDialogBody 原生滚动）。
    """
    rec = init_db.get_experiment(rid)
    if rec is None:
        st.error(f'实验 #{rid} 不存在或已被删除')
        return
    st.markdown(f'### 实验 #{rid} 详情')
    st.caption(f"时间 {rec['created_at']}｜存活率 "
               f"{rec['survival_rate']*100:.1f}%｜终止"
               f"[{rec.get('finish_reason') or '—'}]"
               f"｜来源 {rec.get('source') or 'manual'}")

    # ---- 4.2 分段数（先于图表，变化时图表与复盘同步更新）----
    n_seg = st.slider('分段数', 2, 10, 4, key=f'seg_{rid}',
                      help='分段复盘与图表分隔线的段数；调整后图表与文字同步更新。')

    # ---- 4.1 存活率曲线图（按分段均分）----
    fig = _detail_chart(rec, n_seg)
    st.pyplot(fig, clear_figure=True)
    plt.close(fig)

    # ---- 4.2 分段复盘文字 ----
    st.markdown(_detail_segments(rec, n_seg))

    # ---- 4.3 实验参数列表（简洁两列）----
    st.markdown('**实验参数**')
    rows, _ = _detail_params(rec)
    cols = st.columns(2)
    for i, (k, v) in enumerate(rows):
        cols[i % 2].markdown(f'**{k}**：{v}')
    # 插件状态
    p = rec.get('params_json') or {}
    plug = p.get('plugin_enabled') or {}
    plug_line = '　'.join(
        f"{'✅' if plug.get(n.name, True) else '❌'} {n.name}"
        for n in ss.plugins)
    st.caption(f'插件：{plug_line}')

    # ---- 4.4 复制参数 + 关闭（对话框保持打开）----
    c1, c2 = st.columns(2)
    c1.button('📋 复制参数到控制台', use_container_width=True,
              on_click=load_params_from, args=(rid,),
              help='加载该实验全部参数到控制台（不自动运行），对话框保持打开')
    c2.button('✖ 关闭', use_container_width=True, on_click=_close_detail)


# ==================== 历史记录（唯一入口：侧边栏）====================

def _sidebar_history_inner():
    """
    侧边栏历史实验面板（唯一入口）：固定高度 280px 编号列表，
    超过约 5 条自动滚动；点击编号打开详情弹窗（图表按需生成）。
    """
    with st.expander('📜 历史实验', expanded=False):
        hist = list(ss.runtime['history'])
        if not hist:
            st.caption('暂无记录——完成一次实验后自动保存')
            return
        with st.container(height=280):
            for rec in hist:          # 已按 id 倒序（最新在上）
                rid = rec['id']
                rep_mark = '🔁 ' if (rec.get('source') or '').startswith('reproduced') else ''
                st.button(f'{rep_mark}#{rid}', key=f'histbtn_{rid}',
                          use_container_width=True, on_click=_open_detail,
                          args=(rid,),
                          help=f'查看实验 #{rid} 详情')
        st.caption(f'共 {len(hist)} 条记录（点击编号查看详情）')


# ==================== 地图渲染 ====================
#
# 地图只有两种模式（回退简化版）：
#   实时加载：fragment(run_every=0.5) 每 0.5 秒向占位符推送一帧（实时动画）
#   关闭动画：停止推送，保留最后一帧静止可见（后台实验照常运行）
#
# 渲染原则：
#   1. 占位符 ph = st.empty() 在主脚本创建并存入 session_state；
#      创建后**立即写入默认内容**（Streamlit 要求容器在初始运行时就有内容，
#      否则后续 fragment 重跑无法为它保留稳定位置，会报
#      "片段试图写入碎片外部创建的容器"错误），随后**立即推送一帧**——
#      任何页面重绘后地图都有内容，绝无空白期。
#   2. 实时加载时每次 fragment 重跑都推送新帧（先更新画面、后等待下一帧），
#      等待期间容器始终有上一帧画面。
#   3. 关闭动画 / 扫描加速：定时不推送，占位符保留最后一帧。
#   4. _draw_map 内的 ax.clear() 是绘制新帧前的坐标系清空，与推送同一
#      次调用内瞬间完成，不影响容器显示。

def _should_draw_map(initial=False):
    """
    是否允许推送地图帧。
    initial=True：初始/补帧场景——即使"关闭动画"也画一帧（静止可见）；
    定时推送场景（initial=False）：关闭动画/扫描加速时返回 False。
    """
    world = ss.runtime.get('world')
    if world is None:
        return False
    if not initial and ss.refresh_interval is None:
        return False
    if ss.exp_mode == '自动调参扫描' and ss.runtime['batch_active']:
        return False
    return True


def _ensure_map_frame():
    """
    占位符（重建）后立即补一帧：保证地图容器始终保持有内容。
    初始/补帧场景允许在"关闭动画"下也画一帧，使地图静止但可见。
    """
    if not _should_draw_map(initial=True):
        return
    world = ss.runtime.get('world')
    _draw_map(ss.map_ph, world)


@st.fragment(run_every=0.5)
def render_left_panel():
    world = ss.runtime.get('world')

    # ---- 自动就绪：批次结束/手动结束后，世界清空并回到初始空白状态 ----
    if ss.runtime.get('pending_reset'):
        ss.runtime['pending_reset'] = False
        build_idle_world()
        world = ss.runtime.get('world')

    # ---- 状态行 ----
    if world is not None:
        s = world.stats()
        if ss.runtime['batch_active']:
            i, total, desc = ss.runtime.get('progress', (0, 1, ''))
            st.progress(min(i / max(total, 1), 1.0),
                        text=f'{desc}（tick {world.tick}）')
        status = ss.runtime.get('status', '待命')
        if ss.runtime['batch_done'] and ss.runtime['last_finish_reason']:
            status = f'已结束 [{ss.runtime["last_finish_reason"]}]'
        elif world.finished and world.finish_reason:
            status = f'已结束 [{world.finish_reason}]'
        st.caption(
            f"状态：{status}｜tick {world.tick}｜绿色 {s['alive_prey']} 只｜"
            f"捕食者 {s['alive_predator']} 只｜平均能量 {s['avg_energy']:.0f}｜"
            f"守恒偏差 {s['conservation_error']:.1e}")

    # ---- 手动结束提示（状态栏，3 秒后自动消失）----
    msg = ss.runtime.get('last_message')
    if msg is not None and time.time() - msg[1] < 3.0:
        st.success(msg[0])
    elif msg is not None:
        ss.runtime['last_message'] = None

    # ---- 地图：实时加载 → 每 0.5 秒推一帧；关闭动画 → 保留最后一帧 ----
    if _should_draw_map():
        _draw_map(ss.map_ph, world)

    # ---- 批次结果图表 ----
    if ss.runtime['batch_done'] and ss.runtime['batch_results']:
        render_batch_result()

    if ss.runtime['last_error']:
        st.warning(ss.runtime['last_error'])
        ss.runtime['last_error'] = ''


def _draw_map(placeholder, world):
    """绘制一帧并推送到占位符。
    ax.clear() 是绘制新帧前瞬间清空坐标系（与推送同一次调用内完成），
    容器始终保持有内容——先更新画面，后等待刷新间隔。"""
    fig, ax = get_canvas()
    ax.clear()
    ax.set_facecolor('#0d1117')
    g = world.grid.data
    nz = np.nonzero(g)
    if nz[0].size:
        ax.scatter(nz[1], nz[0], s=4, c='#3fb950', alpha=0.75, linewidths=0)
    prey_x, prey_y, prey_c = [], [], []
    pred_x, pred_y = [], []
    for a in world.agents:
        if not a['alive']:
            continue
        if a['kind'] == 'prey':
            prey_x.append(a['x'])
            prey_y.append(a['y'])
            prey_c.append('#ff9f43' if a['hp'] <= 3 else '#58a6ff')
        else:
            pred_x.append(a['x'])
            pred_y.append(a['y'])
    if prey_x:
        ax.scatter(prey_x, prey_y, s=30, c=prey_c, edgecolors='white',
                   linewidths=0.4, zorder=3)
    if pred_x:
        ax.scatter(pred_x, pred_y, s=46, marker='^', c='#f85149',
                   edgecolors='white', linewidths=0.5, zorder=4)
    for p in ss.plugins:
        if ss.enabled.get(p.name, True):
            try:
                p.on_render(ax, world.agents, world.grid)
            except Exception:
                pass
    size = world.size
    ax.set_xlim(-0.5, size - 0.5)
    ax.set_ylim(size - 0.5, -0.5)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    placeholder.pyplot(fig, clear_figure=False)


# ==================== 右侧控制面板 ====================

def _slider_row(key, col, running=False):
    """带悬浮提示的滑条渲染（help= 悬停显示）。
    锁定条件：实验运行中 或 归属插件被禁用。"""
    label, lo, hi, default, step, dtype, tip, plugin = PARAM_SPECS[key]
    disabled = running or (plugin is not None and not ss.enabled.get(plugin, True))
    col.slider(label, lo, hi, float(ss.params[key]) if dtype is float
               else int(ss.params[key]),
               step=step, key=f'p_{key}', on_change=on_param, args=(key,),
               help=tip, disabled=disabled)


def render_controls():
    """右侧控制面板：固定顶部（控制按钮+模式+状态）｜ 滚动参数区 ｜ 固定底部（提交）。"""
    w = ss.runtime.get('world')
    running = ss.runtime['batch_active'] and w is not None and w.running
    paused = (ss.runtime['batch_active'] and w is not None
              and not w.running and w.finish_reason is None)
    finished = ss.runtime['batch_done']

    # ==================== 3.1 固定顶部（不滚动）====================
    st.subheader('实验控制')
    if paused:
        # 暂停中：显示"继续"与"直接结束"，开始/暂停按钮隐藏
        c1, c2 = st.columns(2)
        c1.button('▶️ 继续', use_container_width=True, on_click=resume_experiment,
                  help='恢复实验运行（等同于取消暂停）')
        c2.button('⏹ 直接结束', use_container_width=True, on_click=end_experiment,
                  help='立即终止本次实验并保存当前数据，不再继续运行')
        st.caption('⏸ 已暂停——请选择继续运行或直接结束')
    else:
        b1, b2, b3 = st.columns(3)
        b1.button('开始', use_container_width=True, on_click=start_experiment,
                  disabled=running,
                  help='启动新实验（空闲/已结束状态可用）')
        b2.button('暂停', use_container_width=True, on_click=pause_experiment,
                  disabled=not running,
                  help='暂停实验，之后可选择继续或直接结束')
        if running:
            b3.markdown('🟢 运行中')
        elif finished:
            b3.markdown('🏁 已结束')
        else:
            b3.markdown('💤 空闲')
    if ss.runtime['batch_active']:
        st.caption('批次运行中……关掉浏览器标签页也会继续跑')

    st.radio('实验模式', ['单次挂机跑', '自动调参扫描'], index=0,
             key='p_exp_mode', on_change=on_exp_mode, disabled=running,
             help='实验模式：单次挂机跑 = 固定参数连续跑 N 次看稳定性；'
                  '自动调参扫描 = 扫描某个参数找最优值。')
    # 状态信息栏
    if w is not None:
        s = w.stats()
        status = ss.runtime.get('status', '待命')
        if finished and ss.runtime['last_finish_reason']:
            status = f'已结束 [{ss.runtime["last_finish_reason"]}]'
        elif w.finished and w.finish_reason:
            status = f'已结束 [{w.finish_reason}]'
        st.caption(f'状态：{status}｜tick {w.tick}｜绿色 {s["alive_prey"]} 只｜'
                   f'捕食者 {s["alive_predator"]} 只')

    # ==================== 3.2 滚动参数区（固定高度，内部滚动）====================
    with st.container(height=540):
        st.markdown('**参数设置**（运行中锁定）')
        st.selectbox('终止条件', ['两者任一', '到达步数', '全体死亡'], index=0,
                     key='p_stop_condition', on_change=on_stop_condition,
                     disabled=running,
                     help='实验何时自动结束。默认"两者任一"：先到先停。')
        st.selectbox('画面刷新间隔', list(REFRESH_OPTIONS.keys()), index=0,
                     key='p_refresh', on_change=on_refresh, disabled=running,
                     help='实时加载：每 0.5 秒更新一帧动画；'
                          '关闭动画：地图静止保留最后一帧（省 CPU），后台实验照常运行。')
        st.slider('重复次数', 1, 20, int(ss.params.get('repeats', 1)), step=1,
                  key='p_repeats', on_change=on_repeats, disabled=running,
                  help='重复次数：>1 时种子自动递增（42,43,44...），'
                       '跑完后叠加多条曲线并给出稳定性判断。')
        st.radio('参数生效模式', ['换代生效', '立即重置'], index=0,
                 key='p_param_mode', on_change=on_mode, disabled=running,
                 help='换代生效：当前 Agent 用出生时参数，新参数下次实验生效；'
                      '立即重置：参数提交后世界立即重启。')

        st.markdown('**世界参数**')
        pairs = [('map_size', 'agent_count'), ('predator_count', 'prey_hp'),
                 ('move_cost', 'food_energy'), ('view_range', 'hesitation_prob'),
                 ('patch_radius', 'emergency_energy'), ('attack_power', 'hunger_threshold')]
        for k1, k2 in pairs:
            c1, c2 = st.columns(2)
            _slider_row(k1, c1, running)
            _slider_row(k2, c2, running)

        c1, c2 = st.columns(2)
        c1.number_input('随机种子', 0, 999999, int(ss.params['seed']),
                        key='p_seed', on_change=on_param, args=('seed',),
                        disabled=running, help='世界随机性种子。重复跑时自动递增。')
        c2.number_input('运行步数', 100, 1000000, int(ss.params['max_ticks']),
                        step=100, key='p_max_ticks', on_change=on_param,
                        args=('max_ticks',), disabled=running,
                        help='实验最大步数（终止条件"到达步数/两者任一"时生效）。')

        # 扫描设置（仅扫描模式）
        if ss.exp_mode == '自动调参扫描':
            st.markdown('**扫描设置**')
            st.selectbox('扫描参数', SCAN_KEYS,
                         format_func=lambda k: PARAM_SPECS[k][0],
                         index=SCAN_KEYS.index(ss.scan_set['param']),
                         key='p_scan_param', on_change=on_scan_param,
                         disabled=running,
                         help='要扫描哪个参数？其余参数保持不变。')
            key = ss.scan_set['param']
            spec = PARAM_SPECS[key]
            c1, c2, c3 = st.columns(3)
            start_v = spec[1] if ss.scan_set['start'] is None else ss.scan_set['start']
            end_v = spec[2] if ss.scan_set['end'] is None else ss.scan_set['end']
            step_v = spec[4] if ss.scan_set['step'] is None else ss.scan_set['step']
            c1.number_input('起始值', float(spec[1]), float(spec[2]),
                            float(start_v), step=float(spec[4]),
                            key='p_scan_start', on_change=on_scan_range,
                            args=('start',), disabled=running,
                            help='扫描起始值（受该参数硬上限约束）。')
            c2.number_input('结束值', float(spec[1]), float(spec[2]),
                            float(end_v), step=float(spec[4]),
                            key='p_scan_end', on_change=on_scan_range,
                            args=('end',), disabled=running,
                            help='扫描结束值（受该参数硬上限约束）。')
            c3.number_input('步长', float(spec[4]), float(spec[2]) - float(spec[1]),
                            float(step_v), step=float(spec[4]),
                            key='p_scan_step', on_change=on_scan_range,
                            args=('step',), disabled=running,
                            help='步长：按 起始值→步长→结束值 顺序遍历。')
            st.checkbox('随机扰动', value=bool(ss.scan_set['random']),
                        key='p_scan_random', on_change=on_scan_random,
                        disabled=running,
                        help='开启后忽略步长，在范围内随机抽取 N 组进行测试。')
            if ss.scan_set['random']:
                st.slider('随机抽取组数', 5, 50, int(ss.scan_set['n_random']),
                          step=1, key='p_scan_n_random', on_change=on_scan_n_random,
                          disabled=running,
                          help='随机抽取的参数组数（5~50）。')
            # 预计组数
            lo, hi = float(start_v), float(end_v)
            if hi < lo:
                lo, hi = hi, lo
            if ss.scan_set['random']:
                n_est = int(ss.scan_set['n_random'])
            else:
                n_est = int((hi - lo) / max(float(step_v), float(spec[4])) + 1)
            n_total = n_est * int(ss.params.get('repeats', 1))
            st.caption(f'共 {n_est} 组 × 重复 {int(ss.params.get("repeats", 1))} 次 = '
                       f'{n_total} 次运行（扫描模式自动关闭动画、纯算加速）')

    # ==================== 3.3 固定底部（不滚动）====================
    st.divider()
    locked = running or paused   # 运行/暂停中均锁定参数提交
    st.checkbox('⚡ 实时更新', value=bool(ss.realtime), key='p_realtime',
                on_change=on_realtime, disabled=locked,
                help='关闭（默认）：参数调整只更新显示数值，不推送给世界，'
                     '点【确定】后统一生效；开启：每次调整立即生效。')
    if ss.realtime:
        st.caption('实时更新已开启：参数变化立即生效')
    else:
        st.button('确定', use_container_width=True, on_click=apply_params,
                  disabled=locked,
                  help='将当前所有参数一次性推送给世界，并刷新地图/模型状态。')


def _sidebar_plugins_inner():
    """侧边栏插件管理（从右侧控制面板整体迁移，功能完全保留）。"""
    running = (ss.runtime['batch_active']
               and ss.runtime.get('world') is not None
               and ss.runtime['world'].running)
    with st.expander('🧩 插件管理', expanded=False):
        if running:
            st.caption('🔒 实验运行中，开关已锁定——请先暂停或直接结束')
        else:
            st.caption('悬停查看插件描述；新插件默认禁用')
        for p in ss.plugins:
            enabled = ss.enabled.get(p.name, True)
            state = '🟢 启用' if enabled else '⚪ 禁用'
            c1, c2 = st.columns([3, 1])
            c1.markdown(f'**{p.name}**　{state}')
            c1.caption(p.description or '无描述')
            c2.toggle('启用', value=enabled, key=f'plug_{p.name}',
                      disabled=running, on_change=on_toggle_plugin,
                      args=(p.name,),
                      help=p.description or f'插件 {p.name}')
        if st.button('🔄 刷新插件列表', use_container_width=True,
                     disabled=running, on_click=refresh_plugins):
            pass
        st.caption('调度顺序：' + ' → '.join(p.name for p in ss.plugins))


@st.fragment(run_every=1.0)
def render_sidebar():
    """侧边栏（自上而下）：实验状态摘要 → 📜 历史实验 → 🧩 插件管理。"""
    # ---- 2.1 实验状态摘要 ----
    world = ss.runtime.get('world')
    if world is not None:
        s = world.stats()
        status = ss.runtime.get('status', '待命')
        if ss.runtime['batch_done'] and ss.runtime['last_finish_reason']:
            status = f'已结束 [{ss.runtime["last_finish_reason"]}]'
        elif world.finished and world.finish_reason:
            status = f'已结束 [{world.finish_reason}]'
        st.caption(f'📊 状态：{status}｜tick {world.tick}｜'
                   f'绿色 {s["alive_prey"]} 只｜捕食者 {s["alive_predator"]} 只')
    # ---- 2.2 历史实验（唯一入口）----
    _sidebar_history_inner()
    # ---- 2.3 插件管理 ----
    _sidebar_plugins_inner()


# ==================== 主布局 ====================

def _inject_dialog_css():
    """
    详情弹窗尺寸定制：
      高度 calc(100vh - 16px)（几乎占满视口），宽度 min(78vw, 1280px)；
      内容区单一滚动条（stDialogBody 原生滚动），弹窗内不再嵌套滚动容器。
    """
    st.markdown("""
<style>
[data-testid="stDialog"] [data-testid="stDialogBody"] {
    width: min(78vw, 1280px) !important;
    max-height: calc(100vh - 16px);
    overflow-y: auto;
}
</style>
""", unsafe_allow_html=True)


def main():
    _inject_dialog_css()
    with st.sidebar:
        st.markdown('**🧬 WORLD3 控制台**')
        render_sidebar()
    left, right = st.columns([7, 3], gap='medium')
    with left:
        st.title('🧬 WORLD3 数字生命实验控制台')
        st.caption('微内核 + 插件架构：代谢 / 决心 / 捕食者。能量守恒，只搬家不消失。')
        # 地图占位符：创建后**立即写入默认内容**（提供稳定锚点，避免
        # "片段试图写入碎片外部创建的容器"报错），随后补一帧真实地图。
        ss.map_ph = st.empty()
        if ss.exp_mode == '自动调参扫描' and ss.runtime['batch_active']:
            ss.map_ph.text('🔬 扫描运行中（纯算加速，不渲染地图）')
        else:
            ss.map_ph.text('地图加载中...')
        _ensure_map_frame()
        render_left_panel()
    with right:
        render_controls()
    # ---- 详情弹窗：由主脚本调用（侧边栏 fragment 只设标志 + rerun）----
    if ss.dialog_rid is not None:
        show_detail(ss.dialog_rid)


main()
