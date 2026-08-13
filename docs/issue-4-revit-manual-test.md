# Issue #4 Revit 2026 人工测试说明

这项测试只读取状态，不修改模型，也不会自动保存。只使用开发测试副本；不要使用原模型。截图、真实路径和日志只保存在本机，不提交到GitHub。

## 测试前准备

1. 完全关闭Revit。
2. 打开PowerShell，设置用户级环境变量：

```powershell
[Environment]::SetEnvironmentVariable(
  "AI_AREA_ASSISTANT_PYTHON",
  "<python.exe的完整路径>",
  "User"
)
[Environment]::SetEnvironmentVariable(
  "AI_AREA_ASSISTANT_TEST_DOCUMENT",
  "<开发测试副本.rvt的完整路径>",
  "User"
)
```

3. 在pyRevit扩展设置中，只启用本Issue worktree里的`AI Area Assistant.extension`，不要同时加载旧副本。
4. 重新启动Revit 2026，只打开刚才指定的开发测试副本。
5. 如果Codex正在占用rvt-mcp连接，暂时关闭Codex，再在Revit中把MCP切换为OFF、然后切回ON。

## 测试一：授权副本可以安全绑定

1. 在Revit功能区打开`AI Area Assistant`选项卡。
2. 点击`AI Area Assistant`按钮，右侧应出现同名面板。
3. 找到面板中的“文档安全状态”卡片。它会自动开始验证；如未开始，点击“验证文档”。
4. 卡片显示“验证中”时等待，不要切换文档、修改模型或重复点击。通常几十秒内完成，最多等待50秒。
5. 最终逐项核对：

| 显示项 | 必须看到的结果 |
|---|---|
| 实例 | 以`revit-`开头 |
| 路径 | 当前开发测试副本的完整路径 |
| 视图 | 与当前活动视图一致 |
| 授权路径 | `yes` |
| 状态 | `bound` |
| rvt-mcp | `verified` |
| 写入许可 | `allowed` |
| 暂停原因 | `none` |

`IsModified`可以是`True`或`False`，但请记住这个值。测试过程不应令它发生变化。

如果显示`unavailable`或超时，请确认没有其他Codex/MCP客户端占用连接，再执行MCP的OFF→ON并点击“验证文档”。如果仍失败，停止测试并把状态文字发给开发者。

## 测试二：切换文档后立即暂停

1. 保持开发测试副本打开。
2. 在Revit中新建一个空白项目，不要保存。
3. 切换到这个未保存项目。面板应自动显示“验证中”，不需要重新点击功能区按钮。
4. 等待验证完成，必须看到：

- 路径：`<unsaved>`
- 授权路径：`no`
- 状态：`paused`
- 写入许可：`denied`
- 暂停原因：`document_changed`

如果未保存项目出现`allowed`，立即停止测试，不要执行任何其他操作。

## 测试三：切回副本也不能静默恢复

1. 切回开发测试副本。
2. 等待面板自动验证完成。
3. 必须仍看到`paused`、`denied`和`document_changed`。这是安全锁，不是故障。
4. pyRevit的`Reload`不会清除该锁。若要开始新的绑定会话，必须完全退出Revit，再重新启动并打开开发测试副本。

## 结束测试

1. 关闭未保存的空白项目，选择“不保存”。
2. 不要为了本测试保存开发测试副本。
3. 确认开发测试副本的`IsModified`与测试前一致。
4. 完全关闭Revit，再重新打开Codex。

把以下结果发给开发者即可，不要粘贴真实文件路径：

```text
Issue #4人工测试结果
Revit版本：
pyRevit版本：
授权副本：bound / verified / allowed / none（通过/失败）
切换空白项目：paused / denied / document_changed（通过/失败）
切回授权副本：仍为paused / denied（通过/失败）
测试前后IsModified一致：是/否
是否保存任何RVT：否/是
其他错误文字：
```
