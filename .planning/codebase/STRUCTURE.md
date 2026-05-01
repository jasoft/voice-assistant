# 项目结构 (STRUCTURE.md)

## 目录索引
- **`press_to_talk/`**: Python 核心代码库
    - `agent/`: LLM 代理逻辑
    - `api/`: FastAPI 接口定义
    - `audio/`: 录音与播放处理
    - `execution/bt/`: 基于行为树的执行引擎 (Core Logic)
    - `storage/`: 存储抽象层 (FTS5, SQLite, PocketBase)
    - `models/`: 数据模型与 Pydantic 定义
- **`mac_gui/`**: Swift 实现的 macOS 状态栏应用
- **`web_gui/`**: 基于 Web 的用户界面
- **`assets/`**: 提示音等静态资源
- **`data/`**: 存放本地数据库和缓存
- **`scripts/`**: CI/CD、部署及维护脚本
- **`.planning/`**: GSD 规划与项目状态跟踪
- **`.agents/`**: 机器人技能与自定义指令

## 核心入口
1. **CLI (`press-to-talk`)**: 主程序入口，通常映射到 `cli.py`。
2. **Storage CLI (`ptt-storage`)**: 独立存储层管理工具。
3. **API (`ptt-api`)**: 后端服务，供 GUI 或远程调用。
4. **Mac GUI (`run-gui.sh`)**: 编译运行 Swift 应用。
