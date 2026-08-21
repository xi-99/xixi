# -*- coding: utf-8 -*-
"""
WORLD3/data/init_db.py —— 实验历史数据库初始化。

存储路径：data/experiments.db（相对项目根目录）。
表 experiments：每次实验结束后写入参数、种子、存活率时序、智能诊断。
"""
import json
import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'data', 'experiments.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    -- 参数
    seed           INTEGER NOT NULL,
    map_size       INTEGER NOT NULL,
    agent_count    INTEGER NOT NULL,
    move_cost      REAL NOT NULL,
    food_energy    REAL NOT NULL,
    view_range     INTEGER NOT NULL,
    hesitation_prob REAL NOT NULL,
    predator_count INTEGER NOT NULL,
    attack_power   INTEGER NOT NULL,
    hunger_threshold REAL NOT NULL,
    max_ticks      INTEGER NOT NULL,
    -- 结果
    end_tick       INTEGER NOT NULL,
    alive_final    INTEGER NOT NULL,
    survival_rate  REAL NOT NULL,
    peak_alive     INTEGER NOT NULL,
    finish_reason  TEXT,
    source         TEXT,
    params_json    TEXT,
    deaths_json    TEXT,
    diagnosis      TEXT,
    series         TEXT NOT NULL
);
"""


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    # 兼容旧库：缺列则 ALTER 补上
    cols = [r[1] for r in conn.execute('PRAGMA table_info(experiments)')]
    if 'finish_reason' not in cols:
        conn.execute('ALTER TABLE experiments ADD COLUMN finish_reason TEXT')
    if 'source' not in cols:
        conn.execute('ALTER TABLE experiments ADD COLUMN source TEXT')
    if 'hesitation_prob' not in cols:
        conn.execute('ALTER TABLE experiments ADD COLUMN hesitation_prob REAL')
    if 'params_json' not in cols:
        conn.execute('ALTER TABLE experiments ADD COLUMN params_json TEXT')
    if 'deaths_json' not in cols:
        conn.execute('ALTER TABLE experiments ADD COLUMN deaths_json TEXT')
    conn.commit()
    return conn


def save_experiment(params, result):
    """
    params: 实验参数 dict（含 seed/map_size/...）
    result: {end_tick, alive_final, peak_alive, survival_rate,
             finish_reason, source, plugin_enabled, deaths, diagnosis, series}
    """
    conn = get_connection()
    try:
        # 完整参数快照（JSON）：供"复制参数"一键复现
        params_json = json.dumps(
            {**params,
             'plugin_enabled': result.get('plugin_enabled', {})},
            ensure_ascii=False)
        deaths_json = json.dumps(result.get('deaths', []), ensure_ascii=False)
        conn.execute(
            """INSERT INTO experiments
               (created_at, seed, map_size, agent_count, move_cost, food_energy,
                view_range, hesitation_prob, predator_count, attack_power,
                hunger_threshold, max_ticks, end_tick, alive_final, survival_rate,
                peak_alive, finish_reason, source, params_json, deaths_json,
                diagnosis, series)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.strftime('%Y-%m-%d %H:%M:%S'),
                int(params.get('seed', 0)),
                int(params.get('map_size', 50)),
                int(params.get('agent_count', 100)),
                float(params.get('move_cost', 1.0)),
                float(params.get('food_energy', 5.0)),
                int(params.get('view_range', 10)),
                float(params.get('hesitation_prob', params.get('distract_prob', 0.0))),
                int(params.get('predator_count', 0)),
                int(params.get('attack_power', 3)),
                float(params.get('hunger_threshold', 70.0)),
                int(params.get('max_ticks', 20000)),
                int(result['end_tick']),
                int(result['alive_final']),
                float(result['survival_rate']),
                int(result['peak_alive']),
                result.get('finish_reason', ''),
                result.get('source', 'manual'),
                params_json,
                deaths_json,
                result['diagnosis'],
                json.dumps(result['series'], ensure_ascii=False),
            ),
        )
        conn.commit()
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    finally:
        conn.close()


def _rec_from_row(conn, row):
    """行 → 记录 dict（解析 series/params_json/deaths_json）"""
    cols = [d[0] for d in conn.execute(
        'SELECT * FROM experiments LIMIT 1').description]
    rec = dict(zip(cols, row))
    try:
        rec['series'] = json.loads(rec['series'])
    except (ValueError, TypeError):
        rec['series'] = []
    try:
        rec['params_json'] = json.loads(rec['params_json']) \
            if rec['params_json'] else {}
    except (ValueError, TypeError):
        rec['params_json'] = {}
    try:
        rec['deaths'] = json.loads(rec['deaths_json']) \
            if rec['deaths_json'] else []
    except (ValueError, TypeError):
        rec['deaths'] = []
    return rec


def load_experiments():
    """按时间倒序返回全部实验记录（含解析后的 series/params_json/deaths）"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, created_at, seed, map_size, agent_count, move_cost, "
            "food_energy, view_range, hesitation_prob, predator_count, attack_power, "
            "hunger_threshold, max_ticks, end_tick, alive_final, survival_rate, "
            "peak_alive, finish_reason, source, params_json, deaths_json, "
            "diagnosis, series "
            "FROM experiments ORDER BY id DESC").fetchall()
        return [_rec_from_row(conn, r) for r in rows]
    finally:
        conn.close()


def get_experiment(rid):
    """按 id 返回单条实验记录（含解析后的 series/params_json/deaths）"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, created_at, seed, map_size, agent_count, move_cost, "
            "food_energy, view_range, hesitation_prob, predator_count, attack_power, "
            "hunger_threshold, max_ticks, end_tick, alive_final, survival_rate, "
            "peak_alive, finish_reason, source, params_json, deaths_json, "
            "diagnosis, series "
            "FROM experiments WHERE id = ?", (int(rid),)).fetchone()
        return _rec_from_row(conn, row) if row else None
    finally:
        conn.close()


def best_experiment():
    """存活率最高的实验（用于绿色高亮"当前最优参数"）"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM experiments ORDER BY survival_rate DESC, id ASC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


if __name__ == '__main__':
    conn = get_connection()
    n = conn.execute('SELECT COUNT(*) FROM experiments').fetchone()[0]
    print(f'数据库就绪: {DB_PATH}，已有 {n} 条实验记录')
    conn.close()
