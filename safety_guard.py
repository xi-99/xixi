"""
WORLD3 安全兜底机制 - 防止过载

功能：
1. 批次大小分级保护（硬上限 + 分级警告）
2. 运行时长预估与实时监控
3. 内存监控与熔断
4. 连续失败自动停止
5. 紧急停止机制
6. 参数边界合理性检查

设计原则：
- 安全机制默认开启，不可关闭
- 硬上限必须遵守，警告可由用户确认后继续
- 所有安全检查都有明确的用户反馈
"""

import time
import traceback
from dataclasses import dataclass, field
from typing import Optional, Callable, Any


# ==================== 安全常量 ====================

# 硬上限：超过直接拒绝运行
HARD_LIMIT_COMBOS = 400        # 扫描组合数上限
HARD_LIMIT_BATCH_TIME = 3600   # 批次总时长上限（秒）= 1 小时
HARD_LIMIT_SINGLE_TIME = 300   # 单组最长运行时间（秒）= 5 分钟
HARD_LIMIT_MEMORY_MB = 4096    # 内存使用上限（MB）

# 软限制：超过给出警告，用户确认后可继续
SOFT_LIMIT_WARN_COMBOS = 100   # 超过此数给出强警告
SOFT_LIMIT_NOTICE_COMBOS = 30  # 超过此数给出提示

# 熔断机制
CIRCUIT_BREAKER_CONSECUTIVE_FAILS = 5   # 连续失败次数触发熔断
CIRCUIT_BREAKER_MIN_SURVIVAL = 0.01     # 存活率低于此值视为"无意义"


# ==================== 安全状态 ====================

@dataclass
class SafetyReport:
    """安全检查报告"""
    safe: bool = True                    # 是否安全
    level: str = 'ok'                    # ok / warn / block
    message: str = ''                    # 提示信息
    suggestions: list = field(default_factory=list)  # 建议列表
    stats: dict = field(default_factory=dict)        # 统计数据


class ScanSafetyGuard:
    """扫描安全守卫"""
    
    def __init__(self):
        self.start_time: Optional[float] = None
        self.consecutive_fails: int = 0
        self.circuit_broken: bool = False
        self.last_memory_mb: float = 0
        self._emergency_stop: bool = False
    
    # ==================== 预运行检查 ====================
    
    def preflight_check(self, n_combos: int, repeats: int, 
                       max_ticks: int, tick_duration: float,
                       scan_set: dict) -> SafetyReport:
        """
        启动前安全检查。
        
        Args:
            n_combos: 扫描组合数
            repeats: 每组重复次数
            max_ticks: 最大步数
            tick_duration: 每步时长（秒）
            scan_set: 扫描设置
            
        Returns:
            SafetyReport: 安全检查报告
        """
        total_runs = n_combos * repeats
        estimated_time = total_runs * max_ticks * tick_duration
        
        report = SafetyReport(
            stats={
                'combos': n_combos,
                'repeats': repeats,
                'total_runs': total_runs,
                'max_ticks': max_ticks,
                'tick_duration': tick_duration,
                'estimated_time': estimated_time,
                'estimated_time_str': self._format_duration(estimated_time),
            }
        )
        
        # 1. 硬上限检查：组合数
        if n_combos > HARD_LIMIT_COMBOS:
            report.safe = False
            report.level = 'block'
            report.message = (
                f'扫描组合数 {n_combos} 超过硬上限 {HARD_LIMIT_COMBOS}！'
                f'请增大步长或减少扫描参数。'
            )
            report.suggestions = [
                '增大扫描步长（如从 0.01 改为 0.05）',
                '减少同时扫描的参数数量',
                '使用随机扰动模式替代全网格扫描',
                f'当前 {n_combos} 组 = {n_combos} × {repeats} 次 = {total_runs} 次运行',
            ]
            return report
        
        # 2. 硬上限检查：预估时长
        if estimated_time > HARD_LIMIT_BATCH_TIME:
            report.safe = False
            report.level = 'block'
            report.message = (
                f'预估运行时间 {self._format_duration(estimated_time)} '
                f'超过硬上限 {self._format_duration(HARD_LIMIT_BATCH_TIME)}！'
            )
            report.suggestions = [
                '减少扫描组合数',
                '降低最大步数（max_ticks）',
                '减少重复次数',
                '开启 🔇 无渲染模式',
            ]
            return report
        
        # 3. 单组运行时间检查
        single_time = max_ticks * tick_duration
        if single_time > HARD_LIMIT_SINGLE_TIME:
            report.level = 'warn'
            report.safe = True  # 仍允许运行，但给出警告
            report.message = (
                f'单组最长运行时间 {self._format_duration(single_time)} '
                f'超过建议上限 {self._format_duration(HARD_LIMIT_SINGLE_TIME)}'
            )
            report.suggestions.append(
                '建议降低最大步数，防止单组运行时间过长'
            )
        
        # 4. 软限制警告
        if total_runs > SOFT_LIMIT_WARN_COMBOS:
            report.level = 'warn'
            report.message = (
                f'⚠️ 本次扫描共 {total_runs} 次运行，预估 '
                f'{self._format_duration(estimated_time)}。'
                f'建议开启 🔇 无渲染模式。'
            )
            report.suggestions.extend([
                '开启 🔇 无渲染模式（扫描模式下自动关闭地图绘制）',
                '可随时点击"暂停"或"直接结束"中断',
                '大批量扫描建议先小范围测试参数敏感性',
            ])
        elif total_runs > SOFT_LIMIT_NOTICE_COMBOS:
            if report.level != 'warn':
                report.level = 'notice'
            report.message = (
                f'本次扫描共 {total_runs} 次运行，预估 '
                f'{self._format_duration(estimated_time)}。'
            )
        
        # 5. 内存预估（粗略）
        est_memory_per_run = 50  # MB per run (rough estimate)
        est_total_memory = total_runs * est_memory_per_run
        if est_total_memory > HARD_LIMIT_MEMORY_MB:
            report.level = 'warn'
            report.suggestions.append(
                f'⚠️ 预估内存使用 {est_total_memory}MB 可能较大，'
                f'建议减少扫描组合数'
            )
        
        return report
    
    # ==================== 运行时监控 ====================
    
    def start_monitoring(self):
        """开始运行时监控"""
        self.start_time = time.time()
        self.consecutive_fails = 0
        self.circuit_broken = False
        self._emergency_stop = False
    
    def check_runtime(self, current_step: int, total_steps: int,
                      survival_rate: float) -> SafetyReport:
        """
        运行时安全检查。
        
        Args:
            current_step: 当前进度
            total_steps: 总步数
            survival_rate: 当前存活率
            
        Returns:
            SafetyReport: 安全检查报告
        """
        report = SafetyReport()
        
        if self.start_time is None:
            return report
        
        elapsed = time.time() - self.start_time
        
        # 1. 检查批次总时长
        total_est_time = total_steps * HARD_LIMIT_SINGLE_TIME
        if elapsed > HARD_LIMIT_BATCH_TIME:
            report.safe = False
            report.level = 'block'
            report.message = (
                f'⚠️ 批次运行已超过 {self._format_duration(HARD_LIMIT_BATCH_TIME)}！'
                f'建议立即停止。'
            )
            report.suggestions = [
                '点击"直接结束"停止本次扫描',
                '已有结果会自动保存',
            ]
        
        # 2. 连续失败熔断
        if survival_rate <= CIRCUIT_BREAKER_MIN_SURVIVAL:
            self.consecutive_fails += 1
            if self.consecutive_fails >= CIRCUIT_BREAKER_CONSECUTIVE_FAILS:
                report.level = 'warn'
                report.message = (
                    f'连续 {self.consecutive_fails} 组存活率极低（<1%），'
                    f'可能参数范围设置不当。'
                )
                report.suggestions = [
                    '检查参数范围是否合理',
                    '考虑使用随机扰动模式',
                    '当前运行会继续，但建议关注结果',
                ]
        else:
            self.consecutive_fails = 0
        
        # 3. 存活率异常低警告
        if 0 < survival_rate < 0.05 and report.level != 'warn':
            report.level = 'notice'
            report.message = (
                f'存活率 {survival_rate:.1%} 偏低，请关注后续结果'
            )
        
        return report
    
    def emergency_stop(self):
        """触发紧急停止"""
        self._emergency_stop = True
        self.circuit_broken = True
    
    @property
    def is_stopped(self) -> bool:
        """是否已触发紧急停止"""
        return self._emergency_stop
    
    # ==================== 辅助方法 ====================
    
    @staticmethod
    def _format_duration(seconds: float) -> str:
        """格式化时长"""
        if seconds < 60:
            return f'{seconds:.0f}秒'
        elif seconds < 3600:
            minutes = seconds / 60
            return f'{minutes:.0f}分钟'
        else:
            hours = seconds / 3600
            minutes = (seconds % 3600) / 60
            return f'{hours:.0f}小时{minutes:.0f}分钟'


# ==================== 工具函数 ====================

def estimate_batch_time(n_combos: int, repeats: int, 
                        max_ticks: int, tick_duration: float = 0.05) -> float:
    """
    预估批次运行时间（秒）。
    
    Args:
        n_combos: 扫描组合数
        repeats: 每组重复次数
        max_ticks: 最大步数
        tick_duration: 每步时长（秒）
        
    Returns:
        float: 预估时间（秒）
    """
    return n_combos * repeats * max_ticks * tick_duration


def validate_scan_params(keys: list, ranges: dict) -> SafetyReport:
    """
    验证扫描参数设置合理性。
    
    Args:
        keys: 扫描参数列表
        ranges: 参数范围字典
        
    Returns:
        SafetyReport: 验证报告
    """
    from app import PARAM_SPECS, _scan_values_for
    
    report = SafetyReport()
    
    if not keys:
        report.safe = False
        report.level = 'block'
        report.message = '未选择任何扫描参数'
        return report
    
    # 检查每个参数的范围合理性
    for key in keys:
        r = ranges.get(key, {})
        spec = PARAM_SPECS.get(key)
        
        if spec is None:
            report.safe = False
            report.level = 'block'
            report.message = f'未知参数：{key}'
            return report
        
        lo = r.get('start')
        hi = r.get('end')
        step = r.get('step')
        
        # 获取参数规格（PARAM_SPECS 格式：label, lo, hi, default, step, dtype, help, plugin）
        spec = PARAM_SPECS.get(key)
        
        if spec is None:
            report.safe = False
            report.level = 'block'
            report.message = f'未知参数：{key}'
            return report
        
        label = spec[0]
        p_min = spec[1]
        p_max = spec[2]
        p_type = spec[5]
        
        # 检查范围
        if lo is not None and hi is not None:
            if float(lo) > float(hi):
                report.safe = False
                report.level = 'block'
                report.message = (
                    f'参数 "{label}" 的起始值 ({lo}) 大于结束值 ({hi})'
                )
                return report
            
            # 检查是否超出硬边界
            if float(hi) > float(p_max) * 1.5 or float(lo) < float(p_min) * 0.5:
                report.level = 'warn'
                report.message = (
                    f'参数 "{label}" 的范围 [{lo}, {hi}] '
                    f'可能超出合理区间 [{p_min}, {p_max}]'
                )
                report.suggestions.append(
                    f'建议将 "{label}" 范围限制在 [{p_min}, {p_max}] 内'
                )
        
        # 检查步长
        if step is not None and float(step) <= 0:
            report.safe = False
            report.level = 'block'
            report.message = (
                f'参数 "{label}" 的步长必须为正数，当前值：{step}'
            )
            return report
        
        # 检查点数
        try:
            values = _scan_values_for(key, lo, hi, step)
            if len(values) > HARD_LIMIT_COMBOS:
                report.level = 'warn'
                report.suggestions.append(
                    f'参数 "{label}" 的扫描点数 {len(values)} 过多，'
                    f'建议增大步长'
                )
        except ValueError as e:
            report.safe = False
            report.level = 'block'
            report.message = f'参数 "{label}" 扫描设置无效：{e}'
            return report
    
    return report


# ==================== 全局安全守卫实例 ====================

guard = ScanSafetyGuard()


# ==================== 集成辅助函数 ====================

def integrate_safety_to_app():
    """
    将安全机制集成到 WORLD3 主应用。
    
    此函数提供了 app.py 需要调用的钩子点：
    1. start_experiment() 前调用 preflight_check
    2. batch_worker() 中调用 check_runtime
    3. end_experiment() 中调用 emergency_stop
    """
    return {
        'guard': guard,
        'preflight': guard.preflight_check,
        'runtime_check': guard.check_runtime,
        'emergency_stop': guard.emergency_stop,
        'validate_params': validate_scan_params,
        'estimate_time': estimate_batch_time,
    }
