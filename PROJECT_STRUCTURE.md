# WORLD3 项目结构

```
WORLD3/
├── app.py                  # Streamlit 主程序（Web 控制台）：
│                           #   实时地图 / 参数滑条 / 批次线程 / 历史弹窗 /
│                           #   自动诊断 / 参数扫描 / 插件管理
├── world_engine.py         # 微内核世界引擎：
│                           #   2D 网格、Agent 坐标管理、tick 主循环线程、
│                           #   插件调度、能量守恒审计、终止条件与暂停语义
├── plugin_loader.py        # 插件扫描与加载：
│                           #   自动发现 plugins/ 下所有 .py，缺函数空操作兜底
├── main.py                 # 命令行实验入口（可选）：
│                           #   python main.py --mode compare/sweep/single/smoke
├── tune.py                 # 参数窗口扫描器（可选）：
│                           #   批量对比犹豫型 vs 决心型的存活率差距
├── config.py               # 命令行引擎的集中参数（main.py / tune.py / world.py 使用）
├── agent.py                # 命令行引擎的 Agent：感知 → 打分 → 决心决策 → 执行
├── world.py                # 命令行引擎的世界：网格、能量守恒审计、主循环
│
├── data/                   # 实验历史数据包
│   ├── init_db.py          # SQLite 初始化 / 保存 / 查询（含旧库自动兼容）
│   └── __init__.py
│
├── tools/                  # 辅助工具（备用，app 不调用）
│   └── archive/
│       └── token_stats.py  # DSH 会话 Token 用量统计工具
│
├── plugins/                # 插件目录（放入 .py 即被自动扫描加载）
│   ├── metabolism.py       # 基础代谢：走路耗能、吃能量点、能量死亡判定
│   ├── focus.py            # 决心机制：目标锁定、猎场补位、分心概率
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

## 两套引擎说明

- **`world_engine.py`（微内核）**：Web 控制台（`app.py`）使用的引擎。行为逻辑全部在插件中，参数由界面滑条控制。
- **`world.py` / `agent.py`（命令行引擎）**：`main.py` 与 `tune.py` 使用的经典引擎，参数集中在 `config.py`，用于无界面批跑与对照实验。
