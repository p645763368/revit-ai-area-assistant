# Revit AI Area Assistant

这是一个面向 Autodesk Revit 2026 的AI辅助面积计算Demo。项目使用pyRevit提供Revit内嵌界面，本地Python Agent负责AI对话与任务编排，rvt-mcp负责读取和操作Revit模型。

## 从这里开始

- [非专业开发人员双人Codex协同开发指南](docs/非专业开发人员双人Codex协同开发指南.md)
- [产品与技术规格 Issue #1](https://github.com/p645763368/revit-ai-area-assistant/issues/1)
- [全部开发任务](https://github.com/p645763368/revit-ai-area-assistant/issues)

Issue #2工程基线已经完成。后续任务按照各Issue的`Blocked by`关系在独立Worktree中开发。

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

Agent就绪检查应输出`status: ready`和`contract_version: 1.0`。当前入口不连接模型API，也不写入Revit。

若Windows中的`python`命中了Microsoft Store占位程序，请使用已安装Python解释器的完整路径执行相同命令。

## pyRevit最小入口

1. 在pyRevit中把`pyrevit/AI Area Assistant.extension`配置为扩展目录。
2. 重新加载pyRevit。
3. 打开`AI Area Assistant`选项卡，点击`AI Area Assistant`按钮。
4. 应显示Revit实例、文档标题、完整路径、活动视图和`IsModified`。

此人工检查不修改或保存RVT。正式Dockable Pane和Agent自动启动由Issue #3实现。

## 文档安全绑定

Issue #4新增两层只读安全检查：

- pyRevit入口读取当前进程、文档完整路径、活动视图、修改状态和文档指纹。
- 本地Agent将pyRevit快照与rvt-mcp独立读取的Revit进程、文档标题、完整路径、项目身份、活动视图和修改状态交叉验证，并把任务绑定到一个实例和一个文档。任何证据冲突都会暂停任务并撤销写入许可。

指定开发测试副本的完整路径只通过用户级环境变量提供，不写入仓库：

```powershell
[Environment]::SetEnvironmentVariable(
  "AI_AREA_ASSISTANT_TEST_DOCUMENT",
  "<开发测试副本的绝对路径>",
  "User"
)
```

设置后需完全退出并重新启动Revit。路径匹配只是候选授权；只有Agent确认pyRevit与rvt-mcp读取的实例、文档、活动视图和修改状态全部一致后，`write_allowed`才会为`true`。未保存文档、其他模型、原模型、切换后的文档以及任何rvt-mcp证据不一致时始终拒绝写入。

文档切换触发的暂停锁在当前Revit进程内不会自动恢复，切回授权副本或执行pyRevit `Reload`也仍保持拒绝写入。Issue #4尚未提供“开始新任务”交互；人工测试若需重新绑定，必须完全退出并重新启动Revit。后续面板Ticket可以在明确的用户操作下提供新任务/重新绑定入口。

pyRevit通过独立CPython运行Agent，需要配置解释器路径。扩展仍位于本仓库时会自动推导仓库根目录；复制到其他位置时还需显式配置仓库根目录：

```powershell
[Environment]::SetEnvironmentVariable(
  "AI_AREA_ASSISTANT_AGENT_PYTHON",
  "<现代CPython的python.exe绝对路径>",
  "User"
)
[Environment]::SetEnvironmentVariable(
  "AI_AREA_ASSISTANT_REPOSITORY_ROOT",
  "<本仓库根目录绝对路径>",
  "User"
)
```

Agent会优先使用`AI_AREA_ASSISTANT_RVT_MCP_COMMAND`指定的rvt-mcp服务命令；未设置时，从`%LOCALAPPDATA%\RvtMcp\rvt\server\`自动选择已安装服务。按钮在后台运行Agent和rvt-mcp验证，避免占用Revit UI线程：第一次点击启动验证并显示`pending`，关闭提示、等待数秒后再次点击查看结果。rvt-mcp请求10秒超时，Agent子进程15秒超时；无响应时终止检查并保持拒绝写入。

Revit 2026人工验收步骤见[`docs/issue-4-revit-manual-test.md`](docs/issue-4-revit-manual-test.md)。

## 共享契约

公共契约说明见[`contracts/README.md`](contracts/README.md)。后续并行任务必须复用`contracts/v1`信封；任何不兼容变化都需要新主版本并在PR中说明影响。

## 本地数据和凭据

本仓库只保存代码、匿名示例和文档。`.gitignore`阻止常见RVT、凭据、日志、截图和`AI_Area_Assistant_Data`进入Git，但提交前仍必须人工检查暂存文件。真实API密钥只能通过用户级环境变量或安全凭据提供。

