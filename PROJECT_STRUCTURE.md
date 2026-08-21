# WORLD3 项目结构

```
WORLD3/
├── app.py                  # Streamlit 主程序（Web 控制台，唯一调参入口）：
│                           #   实时地图 / 参数滑条 / 批次线程 / 历史弹窗 /
│                           #   自动诊断 / 参数扫描 v2.1 / 插件管理
│                           #   v2.0：🧬 Agent 模式切换（硬编码 vs 进化）、
│                           #   进化参数滑条、世代显示、进化趋势图
│                           #   v2.1：config.py 全部参数并入 PARAM_SPECS；
│                           #   自动调参扫描升级——自定义步长 / 多参数联合扫描
│                           #   （散点·热力图·表格）/ 🔇无渲染后台模式
├── world_engine.py         # 微内核世界引擎：
│                           #   2D 网格、Agent 坐标管理、tick 主循环线程、
│                           #   插件调度、能量守恒审计、终止条件与暂停语义
│                           #   v2.0：genome 初始化 + 世代更替钩子 + generation 统计
│                           #   v2.1：食物分布/初始能量等改从 params 读取（config 合并）
├── evolution.py            # v2.0 数字基因核心：
│                           #   感知机 forward/softmax/sample_move、
│                           #   基因组 random_genome/crossover/mutate、
│                           #   适应度 fitness / 世代更替 turnover（全员换代）
├── plugin_loader.py        # 插件扫描与加载：
│                           #   自动发现 plugins/ 下所有 .py，缺函数空操作兜底
├── main.py                 # ⚠️ 已废弃：旧命令行实验入口（使用经典引擎）
├── tune.py                 # ⚠️ 已废弃：旧参数窗口扫描器（使用经典引擎）
├── config.py               # ⚠️ 已废弃：旧命令行引擎集中参数（已全部合并进 PARAM_SPECS）
├── agent.py                # 旧命令行引擎的 Agent（随上述链路一并弃用，仅历史复现）
├── world.py                # ⚠️ 已废弃：旧命令行引擎的世界（使用经典引擎）
│
├── docs/                    # 文档（v2.0 新增）
│   ├── 基因纪元-v2.0.md      # 基因纪元设计与对照实验指南（含与计划书差异论证）
│   └── 如何开发一个新插件.md  # 社区插件开发 Walkthrough（社交记忆/信息素示例）
│
├── data/                   # 实验历史数据包
│   ├── init_db.py          # SQLite 初始化 / 保存 / 查询（含旧库自动兼容）
│   └── __init__.py
│
├── tools/                  # 辅助工具
│   ├── smoke_v2.py         # v2.0 无头回归测试（守恒/世代/存活/捕食者）
│   ├── test_scan_logic.py  # v2.1 扫描逻辑集成测试（自定义步长/联合扫描/无渲染）
│   └── archive/
│       └── token_stats.py  # DSH 会话 Token 用量统计工具
│
├── plugins/                # 插件目录（放入 .py 即被自动扫描加载）
│   ├── metabolism.py       # 基础代谢：走路耗能、吃能量点、能量死亡判定
│   ├── focus.py            # 决心机制（硬编码脑，对照组）：目标锁定、猎场补位、分心概率
│   ├── gene_brain.py       # v2.0 基因大脑（实验组）：感知机随机权重驱动移动
│   └── predator.py         # 红色捕食者：饥饿驱动猎杀绿色 Agent
│
├── .streamlit/
│   └── config.toml         # Streamlit 主题（深色）与行为配置
├── results/                # 命令行实验输出目录（.log / .csv / .report.txt，不入库）
├── screenshots/            # README 截图目录（待添加，.gitkeep 占位）
│
├── start.bat               # Windows 一键启动
├── start.sh                # Linux / macOS 启动脚本
├── requirements.txt        # Python 依赖
├── README.md               # 项目说明
├── LICENSE                 # MIT 许可证
├── PROJECT_STRUCTURE.md    # 本文件
└── .gitignore              # Git 忽略规则
```

## 引擎说明（v2.1 起：Web 控制台是唯一入口）

- **`world_engine.py`（微内核）**：Web 控制台（`app.py`）使用的引擎。行为逻辑全部在插件中，
  参数由界面滑条控制。v2.0 的基因纪元（`evolution.py` + `plugins/gene_brain.py`）挂载在此引擎上：
  Agent 出生携带随机基因组，主循环按世代间隔触发自然选择，行为随世代演化。
  v2.1 起：食物分布（绿洲数量/半径/密度、碎屑概率）、初始能量上下限、进食速率、基础代谢、
  散落半径、耐心系数/常数、游荡保持等参数，统一从 `params` 字典读取（即 `PARAM_SPECS` 的值）。
- **`config.py` / `world.py` / `main.py` / `tune.py`（旧命令行引擎，已废弃）**：
  仅供历史复现，不再维护。全部参数值已合并进 `app.py` 的 `PARAM_SPECS`；
  调参、扫描、对照实验请全部改用 Web 控制台。

## 插件调度顺序

按文件名字母序：`focus → gene_brain → metabolism → predator`。
两个大脑插件互斥：`focus` 启用时 `gene_brain` 自动让位（UI 的 Agent 模式开关同时管理二者）。
