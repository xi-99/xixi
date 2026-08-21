# -*- coding: utf-8 -*-
"""
WORLD3/tools/test_scan_logic.py —— v2.1 扫描逻辑集成测试。

用 stub 替换 streamlit（app.py 的 UI 调用全部变成 no-op），
直接驱动 app.py 的纯函数与批次管线，验证：
  1. 自定义步长（可细于参数默认步长；int 参数取整去重；步长过小报错）
  2. 多参数联合扫描（笛卡尔积、meta['values']、随机联合采样）
  3. 🔇 无渲染模式（_should_draw_map 全场景关闭）
  4. 端到端：build_tasks → batch_worker → render_batch_result（1D/2D/3D+）
  5. 旧版 scan_set 格式迁移

运行：python tools/test_scan_logic.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# ---------- streamlit stub（仅保留 session_state 语义，其余 no-op） ----------
class _SS(dict):
    """同时支持属性访问与下标访问的 session_state 替身。"""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            return None

    def __setattr__(self, k, v):
        self[k] = v

    def __delattr__(self, k):
        if k in self:
            del self[k]


def _deco(f=None, **kw):
    """装饰器兼容：@st.xxx（无参）返回原函数；@st.xxx(...)（带参）返回恒等装饰器。"""
    if callable(f):
        return f
    return lambda g: g


class _St:
    session_state = _SS()

    def __getattr__(self, name):
        if name == 'session_state':
            return self.session_state
        if name in ('cache_resource', 'fragment', 'dialog'):
            return _deco
        return lambda *a, **k: None


sys.modules['streamlit'] = _St()

import app  # noqa: E402  （import 时 init_state 会用 stub session_state 正常执行）

ss = app.ss

# 测试批次不写入真实实验数据库（只读历史，不落库）
app.init_db.save_experiment = lambda params, result: 0


def reset_session():
    """就地清空 session_state 并重新 init_state（app.ss 引用同一对象）。"""
    ss.clear()
    app.init_state()
    app._migrate_scan_set()
    # 测试用最小运行规模
    ss.params['max_ticks'] = 200
    ss.params['map_size'] = 32
    ss.params['agent_count'] = 20
    ss.params['predator_enabled'] = False
    return ss


def check(name, cond, detail=''):
    status = 'PASS' if cond else 'FAIL'
    print(f'  [{status}] {name}' + (f'  ({detail})' if detail else ''))
    if not cond:
        raise SystemExit(f'测试失败：{name}')


def test_custom_step():
    print('== 1. 自定义步长 ==')
    # float：步长 0.25 细于默认 0.01 → 0,0.25,0.5,0.75,1.0
    v = app._scan_values_for('hesitation_prob', 0.0, 1.0, 0.25)
    check('float 自定义步长 0.25', v == [0.0, 0.25, 0.5, 0.75, 1.0], str(v))
    # float：粗步长 0.4 → 0,0.4,0.8（arange 上界含入）
    v = app._scan_values_for('hesitation_prob', 0.0, 1.0, 0.4)
    check('float 粗步长 0.4', v == [0.0, 0.4, 0.8], str(v))
    # int：步长 2 → 1,3,5,7,9（取整去重）
    v = app._scan_values_for('attack_power', 1, 9, 2)
    check('int 步长 2 取整去重', v == [1, 3, 5, 7, 9], str(v))
    # int：步长 0.5 → 提升为 1
    v = app._scan_values_for('attack_power', 1, 4, 0.5)
    check('int 步长 <1 提升为 1', v == [1, 2, 3, 4], str(v))
    # None 步长 → 用 spec 默认
    v = app._scan_values_for('hesitation_prob', None, None, None)
    check('None 步长用 spec 默认', len(v) == 101, f'{len(v)} 点')
    # 越界夹取 + 端点互换
    v = app._scan_values_for('hesitation_prob', 0.8, 0.2, 0.5)
    check('范围端点自动互换', v == [0.2, 0.7], str(v))
    # 步长过小 → ValueError
    try:
        app._scan_values_for('hesitation_prob', 0.0, 1.0, 1e-4)
        check('步长过小抛错', False)
    except ValueError:
        check('步长过小抛错', True)


def test_joint_scan():
    print('== 2. 多参数联合扫描 ==')
    keys = ['hesitation_prob', 'move_cost']
    ranges = {'hesitation_prob': {'start': 0.0, 'end': 1.0, 'step': 0.5},
              'move_cost': {'start': 0.5, 'end': 1.0, 'step': 0.25}}
    combos = app._scan_combos(keys, ranges)
    check('笛卡尔积 3×3=9 组', len(combos) == 9, f'{len(combos)} 组')
    check('组合取值正确',
          combos[0] == {'hesitation_prob': 0.0, 'move_cost': 0.5} and
          combos[-1] == {'hesitation_prob': 1.0, 'move_cost': 1.0}, str(combos))
    # 随机联合采样
    rand = app._scan_random_combos(keys, ranges, 10, 42)
    check('随机联合采样 10 组', len(rand) == 10)
    check('随机值在范围内',
          all(ranges[k]['start'] <= c[k] <= ranges[k]['end']
              for c in rand for k in keys))
    # 预估组数
    est = app._scan_estimate({'params': keys, 'ranges': ranges, 'random': False})
    check('预估组数 9', est == 9, str(est))
    # 三参数（小范围）
    keys3 = ['hesitation_prob', 'move_cost', 'eat_rate']
    ranges3 = {'hesitation_prob': {'start': 0.0, 'end': 1.0, 'step': 0.5},
               'move_cost': {'start': 0.0, 'end': 1.0, 'step': 0.5},
               'eat_rate': {'start': 1.0, 'end': 2.0, 'step': 0.5}}
    n3 = len(app._scan_combos(keys3, ranges3))
    check('三参数联合 3×3×3=27 组', n3 == 27, str(n3))
    # 旧格式 meta 兼容
    check('旧 meta 兼容', app._scan_meta_keys({'param': 'move_cost'}) == ['move_cost'])
    check('新 meta 读取',
          app._scan_meta_keys({'param': 'a', 'values': {'a': 1, 'b': 2}}) == ['a', 'b'])


def test_headless():
    print('== 3. 🔇 无渲染模式 ==')
    ss.exp_mode = '自动调参扫描'
    ss.scan_set['headless'] = True
    ss.runtime['batch_active'] = True
    check('无渲染：初始帧也不画', not app._should_draw_map(initial=True))
    check('无渲染：定时帧不画', not app._should_draw_map(initial=False))
    # 无渲染在空闲时同样完全关闭（含初始帧）
    ss.runtime['batch_active'] = False
    check('无渲染：空闲初始帧也不画', not app._should_draw_map(initial=True))
    # 关闭无渲染后恢复原有语义
    ss.scan_set['headless'] = False
    ss.runtime['batch_active'] = True
    check('非无渲染：扫描运行中不画（原有行为）', not app._should_draw_map(initial=True))
    check('非无渲染：扫描运行中不推帧', not app._should_draw_map(initial=False))
    ss.runtime['batch_active'] = False
    check('非无渲染：空闲初始帧恢复', app._should_draw_map(initial=True))
    old_refresh = ss.refresh_interval
    ss.refresh_interval = None
    check('非无渲染：关闭动画不推帧', not app._should_draw_map(initial=False))
    ss.refresh_interval = old_refresh
    check('非无渲染：实时加载推帧', app._should_draw_map(initial=False))
    ss.exp_mode = '单次挂机跑'


def test_e2e():
    print('== 4. 端到端批次管线（build_tasks → batch_worker → 渲染）==')
    # 4a. 单参数扫描（自定义步长）+ 无渲染
    ss.exp_mode = '自动调参扫描'
    ss.scan_set = {'params': ['hesitation_prob'],
                   'ranges': {'hesitation_prob': {'start': 0.0, 'end': 1.0,
                                                  'step': 0.5}},
                   'random': False, 'n_random': 10, 'headless': True}
    ss.params['repeats'] = 1
    tasks = app.build_tasks()
    check('单参数任务 3 组', len(tasks) == 3, f'{len(tasks)} 组')
    check('meta 带 values', all('values' in m for _, m in tasks))
    check('扫描任务纯算', all(p['tick_duration'] == 0.0 for p, _ in tasks))

    # 跑完第一批并渲染（1D 散点路径，3 点含趋势线）
    runtime = ss.runtime
    app.batch_worker(tasks, runtime, ss.plugins, ss.enabled)
    check('批次完成', runtime['batch_done'])
    check('结果 3 条', len(runtime['batch_results']) == 3)
    app.render_batch_result()          # 1D 渲染不抛异常

    # 单点扫描（手动提前结束/单值扫描）渲染不崩溃
    runtime['batch_results'] = []
    runtime['batch_done'] = False
    app.batch_worker([tasks[0]], runtime, ss.plugins, ss.enabled)
    app.render_batch_result()          # 1 点无趋势线路径

    # 4b. 双参数联合扫描（2D 热力图路径）
    ss.scan_set = {'params': ['hesitation_prob', 'move_cost'],
                   'ranges': {'hesitation_prob': {'start': 0.0, 'end': 1.0,
                                                  'step': 0.5},
                              'move_cost': {'start': 0.5, 'end': 1.0,
                                            'step': 0.5}},
                   'random': False, 'n_random': 10, 'headless': True}
    tasks = app.build_tasks()
    check('双参数任务 6 组', len(tasks) == 6, f'{len(tasks)} 组')
    check('任务参数与组合一致',
          set(tuple(sorted(t[1]['values'].items())) for t in tasks) ==
          set(tuple(sorted(c.items())) for c in app._scan_combos(
              ['hesitation_prob', 'move_cost'],
              {'hesitation_prob': {'start': 0.0, 'end': 1.0, 'step': 0.5},
               'move_cost': {'start': 0.5, 'end': 1.0, 'step': 0.5}})))
    runtime['batch_results'] = []
    runtime['batch_done'] = False
    app.batch_worker(tasks, runtime, ss.plugins, ss.enabled)
    check('双参数批次完成', runtime['batch_done'])
    check('双参数结果 6 条', len(runtime['batch_results']) == 6)
    app.render_batch_result()          # 2D 热力图渲染不抛异常

    # 4c. 三参数联合（表格路径，粗步长控制任务量）
    ss.scan_set = {'params': ['hesitation_prob', 'move_cost', 'eat_rate'],
                   'ranges': {'hesitation_prob': {'start': 0.0, 'end': 1.0,
                                                  'step': 1.0},
                              'move_cost': {'start': 0.5, 'end': 1.0,
                                            'step': 0.5},
                              'eat_rate': {'start': 1.0, 'end': 3.0,
                                           'step': 1.0}},
                   'random': False, 'n_random': 10, 'headless': True}
    tasks = app.build_tasks()
    check('三参数任务 2×2×3=12 组', len(tasks) == 12, f'{len(tasks)} 组')
    runtime['batch_results'] = []
    runtime['batch_done'] = False
    app.batch_worker(tasks, runtime, ss.plugins, ss.enabled)
    check('三参数批次完成', runtime['batch_done'])
    app.render_batch_result()          # 表格渲染不抛异常

    # 4d. 随机联合扫描
    ss.scan_set = {'params': ['hesitation_prob', 'move_cost'],
                   'ranges': {'hesitation_prob': {'start': 0.0, 'end': 1.0,
                                                  'step': 0.5},
                              'move_cost': {'start': 0.5, 'end': 1.0,
                                            'step': 0.5}},
                   'random': True, 'n_random': 5, 'headless': True}
    tasks = app.build_tasks()
    check('随机联合任务 5 组', len(tasks) == 5, f'{len(tasks)} 组')

    # 4e. 空参数与过密步长保护
    ss.scan_set = {'params': [], 'ranges': {}, 'random': False,
                   'n_random': 10, 'headless': True}
    tasks = app.build_tasks()
    check('空参数返回空并报错', tasks == [] and bool(ss.runtime['last_error']))
    ss.scan_set = {'params': ['hesitation_prob'],
                   'ranges': {'hesitation_prob': {'start': 0.0, 'end': 1.0,
                                                  'step': 1e-6}},
                   'random': False, 'n_random': 10, 'headless': True}
    tasks = app.build_tasks()
    check('过密步长被拦截',
          tasks == [] and '步长过小' in (app._get_error() or ''),
          str(app._get_error()))

    ss.exp_mode = '单次挂机跑'


def test_migration():
    print('== 5. 旧 scan_set 格式迁移 ==')
    ss.clear()
    ss.initialized = True              # 模拟旧会话：init_state 直接返回
    ss.scan_set = {'param': 'move_cost', 'start': 0.2, 'end': 1.8, 'step': 0.4,
                   'random': False, 'n_random': 7}
    app._migrate_scan_set()
    check('迁移出 params 列表', ss.scan_set['params'] == ['move_cost'])
    check('迁移保留范围', ss.scan_set['ranges']['move_cost'] ==
          {'start': 0.2, 'end': 1.8, 'step': 0.4}, str(ss.scan_set['ranges']))
    check('迁移 headless 默认关', ss.scan_set['headless'] is False)
    # 已迁移的会话不再重复迁移
    before = dict(ss.scan_set)
    app._migrate_scan_set()
    check('幂等（不重复迁移）', ss.scan_set == before)


if __name__ == '__main__':
    reset_session()
    test_custom_step()
    test_joint_scan()
    test_headless()
    test_e2e()
    reset_session()
    test_migration()
    print('\nALL PASS ✅  tools/test_scan_logic.py')
