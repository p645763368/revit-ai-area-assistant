# Issue #4 Revit 2026人工测试（非开发人员版）

这份测试的目的只有三个：

1. 确认按钮能显示当前Revit文件和视图的信息。
2. 确认只有你指定的开发测试副本可能显示`Write permission: allowed`。
3. 确认切换到另一个文件后，状态立即变为`paused / denied`。

本功能只读取状态，不修改模型，也不会保存RVT。测试期间不要点击Revit的保存按钮。测试截图、真实文件路径和日志只保存在本机，不上传GitHub。

## 你最终要完成的最短流程

```text
关闭Revit
→ 设置3个环境变量
→ 确认pyRevit加载的是Issue #4的新扩展
→ 启动Revit并打开开发测试副本
→ 暂时关闭Codex，重新开启Revit中的MCP
→ 点击AI Area Assistant并核对bound / verified / allowed
→ 新建一个不保存的空白项目并切换过去
→ 再点击按钮并核对paused / denied
→ 切回测试副本，再次确认仍是paused / denied
→ 关闭空白项目且不保存
```

## 第1步：关闭Revit

先完全退出所有Revit窗口。环境变量只会在Revit重新启动时读取；如果Revit没有完全退出，后面的配置可能看起来正确但实际不会生效。

如果Revit询问是否保存，请根据你原来的工作状态自行决定。这个Issue不会要求保存任何模型。

## 第2步：打开正确的PowerShell目录

1. 在文件资源管理器中打开本Issue的Worktree目录。目录名称应以`revit-ai-area-assistant-issue-4`结尾。
2. 点击文件资源管理器顶部的地址栏。
3. 输入`powershell`并按Enter。
4. 新窗口提示符左边显示的目录应以`revit-ai-area-assistant-issue-4`结尾。

不要在原始仓库目录或Issue #2的旧Worktree中执行下面的命令。

## 第3步：设置三个环境变量

把下面整段命令复制到刚才打开的PowerShell，然后按Enter：

```powershell
$repoRoot = (Get-Location).Path
$pythonExe = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$testRvt = (Read-Host "请把开发测试副本RVT拖到这里，然后按Enter").Trim('"')

if (-not (Test-Path -LiteralPath $repoRoot -PathType Container)) {
    throw "找不到Issue #4仓库目录：$repoRoot"
}
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "找不到独立Python：$pythonExe"
}
if (-not (Test-Path -LiteralPath $testRvt -PathType Leaf)) {
    throw "找不到测试RVT：请重新执行并拖入正确文件"
}
if ([IO.Path]::GetExtension($testRvt) -ne '.rvt') {
    throw "你选择的不是RVT文件"
}

[Environment]::SetEnvironmentVariable(
    "AI_AREA_ASSISTANT_REPOSITORY_ROOT", $repoRoot, "User"
)
[Environment]::SetEnvironmentVariable(
    "AI_AREA_ASSISTANT_AGENT_PYTHON", $pythonExe, "User"
)
[Environment]::SetEnvironmentVariable(
    "AI_AREA_ASSISTANT_TEST_DOCUMENT", $testRvt, "User"
)

Write-Host "配置完成，请核对：" -ForegroundColor Green
Write-Host "Issue #4仓库：$repoRoot"
Write-Host "独立Python存在：$(Test-Path -LiteralPath $pythonExe)"
Write-Host "测试RVT存在：$(Test-Path -LiteralPath $testRvt)"
```

出现`请把开发测试副本RVT拖到这里`后：

1. 从文件资源管理器把开发测试副本拖进PowerShell窗口。
2. 不要拖入原模型。
3. 按Enter。
4. 最后必须看到两行`True`，并且没有红色错误。

如果看到`找不到独立Python`或`找不到测试RVT`，先停止，不要继续测试，把错误文字发给Codex。

## 第4步：确认pyRevit加载的是Issue #4扩展

Issue #2和Issue #4的扩展名称相同，不能同时加载两个副本。

1. 打开pyRevit的扩展目录设置。
2. 删除或停用原来指向Issue #2 Worktree的`AI Area Assistant.extension`路径。
3. 添加Issue #4 Worktree中的以下目录：

```text
revit-ai-area-assistant-issue-4
└─ pyrevit
   └─ AI Area Assistant.extension
```

4. 确认只有一个`AI Area Assistant.extension`处于启用状态。

如果不确定当前加载的是哪个版本，可以先完成设置，然后完全退出并重新启动Revit。

## 第5步：启动Revit并打开测试副本

1. 启动Revit 2026。
2. 只打开刚才配置的开发测试副本。
3. 暂时不要打开原模型。
4. 确认Revit功能区出现`AI Area Assistant`选项卡。
5. 确认rvt-mcp面板或按钮显示MCP已开启。

## 第6步：释放rvt-mcp连接

rvt-mcp v0.5的Revit插件一次只能让一个服务连接。Codex正在连接Revit时，AI Area Assistant自己的Agent可能显示`unavailable`。

请按以下顺序操作：

1. 记住你已经读到本步骤。
2. 暂时完全关闭Codex桌面应用。
3. 回到Revit，把rvt-mcp的`MCP: ON`切换为OFF。
4. 再把它切回ON。
5. 等待约3秒。

完成全部测试后再重新打开Codex，把结果发回来。

## 第7步：测试正确文档能否绑定

在开发测试副本处于当前活动文档时：

1. 点击Revit功能区的`AI Area Assistant`选项卡。
2. 点击其中的`AI Area Assistant`按钮。
3. 等待状态弹窗出现。

逐行核对弹窗：

| 弹窗字段 | 正确结果 | 含义 |
|---|---|---|
| `Revit instance` | 以`revit-`开头 | 已识别当前Revit进程 |
| `Document title` | 当前测试副本标题 | 没有读到其他文件 |
| `Document path` | 当前测试副本的完整路径 | 不是空白，也不是原模型 |
| `Active view` | 与当前Revit视图名称一致 | pyRevit与rvt-mcp观察的是同一视图 |
| `IsModified` | `True`或`False`均可 | 记录下来，后面要再次比较 |
| `Authorized path match` | `yes` | 当前文件与环境变量中的测试副本完全一致 |
| `Agent/rvt-mcp binding` | `bound` | Agent已经绑定当前实例和文档 |
| `rvt-mcp status` | `verified` | rvt-mcp进程和活动视图验证通过 |
| `Write permission` | `allowed` | 安全条件全部通过；本Issue仍不会真的写入 |
| `Pause reason` | `none` | 当前没有触发暂停 |

只有以上关键结果同时为`yes / bound / verified / allowed / none`，本步骤才算通过。

### 如果结果不正确

| 看到的结果 | 先做什么 |
|---|---|
| `pending` | Revit没有读取到Agent Python配置；完全退出Revit后重新执行第3步 |
| `unavailable` | 关闭其他Codex/MCP客户端，再执行第6步的OFF→ON |
| `Authorized path match: no` | 第3步选择的RVT和当前打开的RVT不是同一个完整路径 |
| `rvt-mcp status: mismatch` | 确认只有一个Revit实例，并重新执行MCP OFF→ON |
| 正确测试副本却显示`denied` | 不要继续，记录整段弹窗文字并发给Codex |

## 第8步：测试切换文档后是否立即暂停

不要使用原模型做这个测试。使用一个临时、未保存的空白项目即可。

1. 保持开发测试副本打开。
2. 在Revit中新建一个空白项目。
3. 不保存这个空白项目。
4. 确认空白项目成为当前活动文档。
5. 点击`AI Area Assistant`按钮。

正确结果：

- `Document path: <unsaved>`；
- `Authorized path match: no`；
- `Agent/rvt-mcp binding: paused`；
- `Write permission: denied`；
- `Pause reason: document_changed`。

如果空白项目出现`Write permission: allowed`，这是严重失败：立即停止测试，不要执行任何其他操作，把弹窗文字发给Codex。

## 第9步：确认切回原测试副本也不会静默恢复

1. 切回开发测试副本。
2. 再次点击`AI Area Assistant`按钮。

正确结果：

- 状态仍然是`paused`；
- `Write permission`仍然是`denied`；
- 系统不会因为切回原文件而自动恢复旧任务。

这是安全设计，不是故障。重新绑定功能会由后续面板交互提供。

## 第10步：结束测试

1. 关闭未保存的空白项目。
2. Revit询问是否保存空白项目时选择不保存。
3. 不要为了本测试保存开发测试副本。
4. 再看一次开发测试副本的`IsModified`；它应与第7步记录一致。
5. 关闭Revit。
6. 重新打开Codex。

## 把下面结果复制给Codex

不要粘贴真实路径；只填写通过或失败以及状态词。

```text
Issue #4人工测试结果

测试日期：
Revit版本：2026
pyRevit版本：

A. 测试副本状态显示：通过 / 失败
B. 测试副本绑定结果：bound / 其他
C. rvt-mcp结果：verified / 其他
D. 测试副本Write permission：allowed / denied
E. 切换到空白项目：paused / 其他
F. 空白项目Write permission：denied / allowed
G. 切回测试副本后：仍paused / 自动恢复 / 其他
H. 测试前后IsModified是否一致：是 / 否
I. 是否保存过RVT：否 / 是

失败时看到的状态词或错误文字：
```
