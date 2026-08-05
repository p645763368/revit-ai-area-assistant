# Revit AI Area Assistant

这是一个面向 Autodesk Revit 2026 的AI辅助面积计算Demo。项目使用pyRevit提供Revit内嵌界面，本地Python Agent负责AI对话与任务编排，rvt-mcp负责读取和操作Revit模型。

## 从这里开始

- [非专业开发人员双人Codex协同开发指南](docs/非专业开发人员双人Codex协同开发指南.md)
- [产品与技术规格 Issue #1](https://github.com/p645763368/revit-ai-area-assistant/issues/1)
- [全部开发任务](https://github.com/p645763368/revit-ai-area-assistant/issues)

当前第一个可开始的开发任务是 [Issue #2：建立可并行开发的工程骨架](https://github.com/p645763368/revit-ai-area-assistant/issues/2)。

> 安全提醒：禁止向GitHub提交RVT文件、API密钥、项目截图、运行日志或真实项目数据。

## 工程结构

- `pyrevit/AI Area Assistant.extension/`：pyRevit端最小只读入口。Dockable Pane属于后续Issue。
- `area_assistant_agent/`：独立CPython Agent最小入口。
- `contracts/v1/`：pyRevit、Agent与后续rvt-mcp集成共享的版本化JSON契约。
- `knowledge/`：经批准、匿名化且可跨项目复用的规则与案例边界。
- `tests/`：不依赖Revit的用户可见入口、契约和安全边界测试。
- `.github/workflows/ci.yml`：在Windows上执行编译检查与自动测试。

## 本地运行

需要Python 3.9或更高版本。在仓库根目录执行：

```powershell
python -m pip install -e ".[test]"
python -m area_assistant_agent --check
python -m unittest discover -s tests -v
```

Agent就绪检查应输出`status: ready`和`contract_version: 1.0`。当前入口不连接模型API、不调用rvt-mcp，也不写入Revit。

若Windows中的`python`命中了Microsoft Store占位程序，请使用已安装Python解释器的完整路径执行相同命令。

## pyRevit最小入口

1. 在pyRevit中把`pyrevit/AI Area Assistant.extension`配置为扩展目录。
2. 重新加载pyRevit。
3. 打开`AI Area Assistant`选项卡，点击`AI Area Assistant`按钮。
4. 应出现“Engineering baseline is ready”提示。

此人工检查不修改或保存RVT。正式Dockable Pane、Agent自动启动和模型交互由后续Issue实现。

## 共享契约

公共契约说明见[`contracts/README.md`](contracts/README.md)。后续并行任务必须复用`contracts/v1`信封；任何不兼容变化都需要新主版本并在PR中说明影响。

## 本地数据和凭据

本仓库只保存代码、匿名示例和文档。`.gitignore`阻止常见RVT、凭据、日志、截图和`AI_Area_Assistant_Data`进入Git，但提交前仍必须人工检查暂存文件。真实API密钥只能通过用户级环境变量或安全凭据提供。

Issue #5提供了外部会话持久化接口。数据根目录固定为项目目录下的`AI_Area_Assistant_Data`，并在运行时解析为绝对路径。可在仓库根目录查看当前项目解析后的路径：

```powershell
python -m area_assistant_agent --show-data-root .
```

输出示例为`{"data_root": "D:\\path\\to\\project\\AI_Area_Assistant_Data"}`。运行数据按文档指纹的SHA-256目录键隔离，结构如下：

```text
AI_Area_Assistant_Data/
└── documents/<document-key>/sessions/<session-id>/
    ├── state.json
    ├── conversation.jsonl
    ├── operations.jsonl
    ├── agent.log.jsonl
    └── session.md
```

`SessionRepository.recovery_prompt()`只列出当前文档可恢复的会话。面板集成方必须先向用户显示“继续上次会话”或“新建会话”的选择；不得在打开面板时调用`resume_session()`。用户明确选择继续后，恢复状态为`awaiting_user_action`，不会重放旧的模型操作。对话内容以及工具输入、输出和错误写入记录前会递归遮蔽Authorization、API密钥、Token、Secret和Password字段。

本Issue只提供本地Agent侧的公开持久化边界；Dockable Pane对这些接口的调用由对应面板Issue集成。本功能不读取、修改或保存RVT，也没有修改`contracts/v1`共享通信契约。

