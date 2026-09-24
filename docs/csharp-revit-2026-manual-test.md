# Revit 2026 C# 前端人工验收

本检查只验证 PR #19 的只读规划链路，不保存或修改 RVT。

## 前置条件

- 只使用 Autodesk Revit 2026。
- 只加载 `AreaAssistant.Revit2026.addin`，不加载任何旧 pyRevit 面板。
- `AI_AREA_ASSISTANT_PYTHON` 指向 Python 3.10。
- `AI_AREA_ASSISTANT_REPO_ROOT` 指向本仓库。
- DeepSeek 环境变量已在用户环境中配置，Key 不写入项目文件。

## 一次验收

1. 打开指定 detached RVT，记录 `IsModified`。
2. 点击 Ribbon 的 `Area Assistant`，确认右侧出现 C# Dockable Pane。
3. 确认 Agent 状态为“Agent 已连接”，文档标题、完整路径、视图和 `IsModified` 正确。
4. 在 Revit 中选择一个 Wall，点击“读取当前选择”。
5. 确认面板只显示当前选择，且“扫描与方案”可用。
6. 点击“扫描与方案”一次。不要重复点击，也不要执行第二次模型请求。
7. 确认运行中按钮禁用；前端只查询第一次返回的 Job ID。
8. 等待终态：成功时显示摘要、问题和 2–4 个方案且仅一个标为推荐；失败时显示安全错误。
9. 再次记录 `IsModified`，必须与步骤 1 相同。
10. 关闭文档，不保存。

## 尚未执行的边界

在实际完成上述 Revit 2026 检查前，只能声称自动测试和静态编译通过，不能声称插件已经实机可用。
