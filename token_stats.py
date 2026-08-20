# -*- coding: utf-8 -*-
"""
WORLD3/token_stats.py —— DSH Token 用量统计。

数据源：~/.dsh/sessions/**/session.jsonl[.zstd]（DSH 会话转录）。
  - 优先解析每条记录中的 usage 字段（input/prompt 与 output/completion tokens）
  - 无 usage 字段的记录按字符数估算（1 token ≈ 4 字符，输入/输出 7:3）
  - zstandard 未安装时按压缩比估算（标注 estimated=True）
费用按单价折算（UI 可调，默认输入 ¥0.5/M、输出 ¥2/M）。
"""
import glob
import json
import os
import time


def _iter_session_lines(path):
    """逐行产出会话文本；zstd 解压失败或缺失时返回 None 由调用方降级。"""
    try:
        if path.endswith('.zstd'):
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            with open(path, 'rb') as f:
                # 帧头不带内容大小时 stream_reader 也能流式解压
                reader = dctx.stream_reader(f)
                data = reader.read()
            text = data.decode('utf-8', errors='replace')
        else:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        return text.splitlines()
    except Exception:
        return None


def _pick(d, *keys):
    """按多个候选键取值（显式 None 判断，避免 0 值被 or 吞掉）"""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _extract_usage(obj):
    """
    从 JSON 对象中提取 (input_tokens, output_tokens)；无则返回 None。
    usage 可能在顶层，也可能在 data 字段内（DSH 会话格式：
    {type:'assistant/message', data:{..., usage:{inputTokens, outputTokens,
    cacheReadTokens, reasoningTokens}}}）。
    """
    if not isinstance(obj, dict):
        return None
    usage = obj.get('usage')
    if not isinstance(usage, dict):
        data = obj.get('data')
        usage = data.get('usage') if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        usage = obj.get('tokens')
    if not isinstance(usage, dict):
        return None
    inp = _pick(usage, 'input_tokens', 'prompt_tokens', 'inputTokens')
    out = _pick(usage, 'output_tokens', 'completion_tokens', 'outputTokens')
    if inp is None and out is None:
        return None
    cache = _pick(usage, 'cacheReadTokens', 'cache_read_tokens', 'cached_tokens') or 0
    reasoning = _pick(usage, 'reasoningTokens', 'reasoning_tokens') or 0
    return (float(inp or 0) + float(cache), float(out or 0) + float(reasoning))


def _parse_file(path):
    """解析单个会话文件 -> (input_tokens, output_tokens, estimated, lines_ok)"""
    lines = _iter_session_lines(path)
    if lines is None:
        # zstd 不可用/损坏：按压缩大小 × 压缩比 8 估算
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        est = size * 8 / 4
        return est * 0.7, est * 0.3, True, False

    inp = out = 0.0
    n_usage = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        u = _extract_usage(obj)
        if u is not None:
            inp += u[0]
            out += u[1]
            n_usage += 1

    if n_usage == 0:
        # 整个会话无 usage 字段：按解压后字符数估算（1 token ≈ 4 字符）
        est = sum(len(l) for l in lines) / 4.0
        return est * 0.7, est * 0.3, True, True
    return inp, out, False, True


def get_usage(price_in_per_m=0.5, price_out_per_m=2.0):
    """
    汇总今日/本周 token 消耗与预估费用。
    返回: {
      'today_tokens', 'week_tokens', 'today_cost', 'week_cost',
      'estimated', 'session_count', 'available'
    }
    """
    home = os.path.expanduser('~')
    pattern = os.path.join(home, '.dsh', 'sessions', '**', 'session.jsonl*')
    files = glob.glob(pattern, recursive=True)

    today = time.strftime('%Y-%m-%d')
    week_ago = time.time() - 7 * 86400

    today_in = today_out = 0.0
    week_in = week_out = 0.0
    estimated = False
    n_sessions = 0

    for path in files:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        inp, out, est, _ = _parse_file(path)
        if est:
            estimated = True
        n_sessions += 1
        week_in += inp
        week_out += out
        if time.strftime('%Y-%m-%d', time.localtime(mtime)) == today:
            today_in += inp
            today_out += out

    available = n_sessions > 0
    cost = lambda i, o: i / 1e6 * price_in_per_m + o / 1e6 * price_out_per_m
    return {
        'today_tokens': int(today_in + today_out),
        'today_in': int(today_in),
        'today_out': int(today_out),
        'week_tokens': int(week_in + week_out),
        'week_in': int(week_in),
        'week_out': int(week_out),
        'today_cost': cost(today_in, today_out),
        'week_cost': cost(week_in, week_out),
        'estimated': estimated,
        'session_count': n_sessions,
        'available': available,
        'price_in': price_in_per_m,
        'price_out': price_out_per_m,
    }
