# Revit AI Area Assistant

这是一个面向 Autodesk Revit 2026 的AI辅助面积计算Demo。项目使用pyRevit提供Revit内嵌界面，本地Python Agent负责AI对话与任务编排，rvt-mcp负责读取和操作Revit模型。

## 从这里开始

- [非专业开发人员双人Codex协同开发指南](docs/非专业开发人员双人Codex协同开发指南.md)
- [产品与技术规格 Issue #1](https://github.com/p645763368/revit-ai-area-assistant/issues/1)
- [全部开发任务](https://github.com/p645763368/revit-ai-area-assistant/issues)

工程骨架 [Issue #2](https://github.com/p645763368/revit-ai-area-assistant/issues/2) 已完成。当前波次的 [Issue #3](https://github.com/p645763368/revit-ai-area-assistant/issues/3)、[#4](https://github.com/p645763368/revit-ai-area-assistant/issues/4) 和 [#5](https://github.com/p645763368/revit-ai-area-assistant/issues/5) 可以在独立worktree中并行开发。

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
5. 输入一条消息并点击“发送”，回复应逐段显示在面板中。
6. 暂时断开模型服务或配置无效模型后再次发送，Revit应保持可操作，面板应显示错误；可重试错误会启用“重试”。

此人工检查不读取、修改或保存RVT。当前Issue只实现AI对话；Revit文档绑定、rvt-mcp、项目状态持久化和面积任务由后续Issue实现。

## 共享契约

公共契约说明见[`contracts/README.md`](contracts/README.md)。后续并行任务必须复用`contracts/v1`信封；任何不兼容变化都需要新主版本并在PR中说明影响。

## 本地数据和凭据

本仓库只保存代码、匿名示例和文档。`.gitignore`阻止常见RVT、凭据、日志、截图和`AI_Area_Assistant_Data`进入Git，但提交前仍必须人工检查暂存文件。真实API密钥只能通过用户级环境变量或安全凭据提供。

