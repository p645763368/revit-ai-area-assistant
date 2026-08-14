# Revit AI Area Assistant

这是一个面向 Autodesk Revit 2026 的AI辅助面积计算Demo。项目使用pyRevit提供Revit内嵌界面，本地Python Agent负责AI对话与任务编排，rvt-mcp负责读取和操作Revit模型。

## 从这里开始

- [非专业开发人员双人Codex协同开发指南](docs/非专业开发人员双人Codex协同开发指南.md)
- [Issue #5 外部状态、日志与会话恢复人工测试手册](docs/Issue-5-外部状态日志与会话恢复-人工测试手册.md)
- [PR #17 / Issue #5 剩余人工验收操作手册](docs/Issue-5-PR17-剩余人工验收操作手册.md)
- [PR #17 / Issue #5 人工验收记录（2026-08-14）](docs/Issue-5-PR17-人工验收记录-2026-08-14.md)
- [Issue #5 人工测试记录（2026-08-13）](docs/Issue-5-人工测试记录-2026-08-13.md)
- [Issue #7 Revit 2026 人工测试手册](docs/issue-7-revit-manual-test.md)
- [产品与技术规格 Issue #1](https://github.com/p645763368/revit-ai-area-assistant/issues/1)
- [全部开发任务](https://github.com/p645763368/revit-ai-area-assistant/issues)

工程骨架 [Issue #2](https://github.com/p645763368/revit-ai-area-assistant/issues/2) 以及 Issue #7 的阻塞项 [#3](https://github.com/p645763368/revit-ai-area-assistant/issues/3)、[#4](https://github.com/p645763368/revit-ai-area-assistant/issues/4)、[#5](https://github.com/p645763368/revit-ai-area-assistant/issues/5) 已合并到 `main`。

> 安全提醒：禁止向GitHub提交RVT文件、API密钥、项目截图、运行日志或真实项目数据。

## 工程结构

- `pyrevit/AI Area Assistant.extension/`：注册并打开Revit内的Dockable Pane。
- `area_assistant_pyrevit/`：面板、回环客户端和Agent进程启动器。
- `area_assistant_agent/`：独立CPython Agent和OpenAI兼容模型API适配器。
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

Agent就绪检查应输出`status: ready`和`contract_version: 1.0`；`--check`本身不会连接模型API。`--serve`只提供本机AI对话服务，不调用rvt-mcp，也不读写Revit。

启动Agent服务前，在用户级环境变量中配置：

```powershell
$env:AI_AREA_ASSISTANT_API_KEY = Read-Host "请输入通过安全渠道取得的密钥"
$env:AI_AREA_ASSISTANT_MODEL = "<兼容服务提供的模型名称>"
$env:AI_AREA_ASSISTANT_BASE_URL = "https://api.fe8.cn/v1"
python -m area_assistant_agent --serve
```

`AI_AREA_ASSISTANT_BASE_URL`默认使用上面的Demo中转地址，`AI_AREA_ASSISTANT_PORT`默认是`8765`，模型请求超时默认30秒并可通过`AI_AREA_ASSISTANT_TIMEOUT_SECONDS`调整。面板自动启动Agent时会查找当前CPython、`py`或`python`；若未找到，请把Python 3.9或更高版本解释器的完整路径写入用户级`AI_AREA_ASSISTANT_PYTHON`环境变量。真实API密钥不要写入PowerShell脚本、`.env`、README或仓库文件。

pyRevit面板使用6.5.3默认的IronPython Forms后端；模型API请求始终由独立的现代CPython Agent执行。不要给扩展的`startup.py`或按钮脚本添加`#! python3`，因为当前pyRevit CPython Forms后端不提供Dockable Pane API。

若Windows中的`python`命中了Microsoft Store占位程序，请使用已安装Python解释器的完整路径执行相同命令。

## pyRevit最小入口

1. 在pyRevit中把`pyrevit/AI Area Assistant.extension`配置为扩展目录。
2. 重新加载pyRevit。
3. 打开`AI Area Assistant`选项卡，点击`AI Area Assistant`按钮。
4. 应在Revit右侧打开“AI Area Assistant”面板，先显示“连接中”，然后显示“已连接”。
5. 面板应显示当前Revit实例、完整文档路径、活动视图、`IsModified`和安全绑定状态。
6. 输入一条消息并点击“发送”，回复应逐段显示在面板中。
7. 暂时断开模型服务或配置无效模型后再次发送，Revit应保持可操作，面板应显示错误；可重试错误会启用“重试”。

此人工检查不修改或保存RVT。

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

pyRevit通过独立CPython运行Agent，需要配置解释器路径：

```powershell
[Environment]::SetEnvironmentVariable(
  "AI_AREA_ASSISTANT_PYTHON",
  "<现代CPython的python.exe绝对路径>",
  "User"
)
```

Agent会优先使用`AI_AREA_ASSISTANT_RVT_MCP_COMMAND`指定的rvt-mcp服务命令；未设置时，从`%LOCALAPPDATA%\RvtMcp\rvt\server\`自动选择已安装服务。打开面板后，“文档安全状态”卡片会自动在后台运行rvt-mcp交叉验证；也可点击“验证文档”重新检查。验证期间Revit界面保持可操作，卡片先显示“验证中”，最长约50秒后显示最终结果；超时或失败时始终保持拒绝写入。切换活动文档会立即触发重新验证和暂停锁，不需要再次点击功能区按钮。

Revit 2026人工验收步骤见[`docs/issue-4-revit-manual-test.md`](docs/issue-4-revit-manual-test.md)。

## 共享契约

公共契约说明见[`contracts/README.md`](contracts/README.md)。后续并行任务必须复用`contracts/v1`信封；任何不兼容变化都需要新主版本并在PR中说明影响。

Issue #7 在不改变 v1 信封的前提下新增兼容 action `analysis.plan`。面板的“扫描与方案”按钮会在当前已验证文档和已激活会话内启动只读规划：Agent 加载 `knowledge/rules` 与 `knowledge/cases` 中带版本、来源和适用范围的快照，自主选择固定只读模型查询，并在需要时调用 rvt-mcp `capture_view_image`。截图通过 rvt-mcp 允许的临时目录中转并在 `finally` 中清理，持久副本只保存在忽略的当前会话数据目录；截图只是视觉辅助，边界判断仍须由 Revit 曲线环、墙定位曲线和现有 Area Boundary 曲线复核。

规划结果固定返回 2 至 4 个可点击选项，恰好一个推荐项，每项显示依据和影响。用户既可点击选项，也可在原输入框自由说明；两者都会通过同一文档会话的历史继续规划。该功能不创建 Transaction、不修改模型、不自动保存 RVT。真实模型 API 与 Revit 2026 验收步骤见 [`docs/issue-7-revit-manual-test.md`](docs/issue-7-revit-manual-test.md)。

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

`SessionRepository.recovery_prompt()`只列出当前文档可恢复且状态文件完整的会话；单个损坏的`state.json`会被隔离，不会阻断其他候选。正式Dockable Pane在文档验证后显示“继续上次会话”或“新建会话”，选择前不创建、恢复或写入任何会话。用户明确选择继续后，恢复状态为`awaiting_user_action`，不会重放旧的模型操作。对话内容以及工具输入、输出和错误写入记录前会递归遮蔽Authorization、API密钥、Token、Secret和Password字段。

切换活动文档时，面板会立即撤销旧会话的发送和写入资格，重新读取当前文档指纹并要求用户为当前文档重新选择会话。面板和Agent通过`contracts/v1`版本化请求共同校验会话上下文；旧文档的迟到回复不能写入旧目录。本功能不读取、修改或保存RVT。

