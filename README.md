# Revit AI Area Assistant

面向 Autodesk Revit 2026 的 AI 面积助手。

## 最高原则

- **简单且有效。**
- 只实现真实工作流需要的最短可靠路径。
- 不用测试数量、复杂状态机或兼容层制造“已经可用”的假象。
- 没有完成 Revit 2026 实机验证的功能必须明确标记为未完成。

## 当前架构

```text
Revit 2026 C#/.NET 8 WPF Add-in
        │ localhost HTTP
        ▼
Python Agent
        ├─ DeepSeek API
        └─ rvt-mcp 只读证据
```

旧的 Python 脚本化 Revit 前端已经被彻底抛弃，不再是项目依赖，也不再接受修复或扩展。

C# Add-in 负责 Ribbon、Dockable Pane、当前文档与选择、ExternalEvent、直接 Revit API，以及后续 Issue 中的 Transaction/TransactionGroup。

Python Agent 负责 DeepSeek、规划、知识、会话与持久 Job、rvt-mcp 只读证据、结果验证和安全诊断。

## 当前真实状态

已经完成并真实验证：

- Python Agent loopback HTTP 服务；
- DeepSeek 严格工具调用；
- DeepSeek 官方 JSON Output：`response_format={"type":"json_object"}`；
- 工具阶段与最终 JSON 阶段分离；
- 持久规划 Job；
- 一次真实只读 Job 到达 `completed / finished` 并返回 3 个方案；
- 该次测试中 Revit 保持 `IsModified: False`。

尚未完成：

- Revit 2026 C# Add-in、Dockable Pane、自动启动 Agent；
- C# 元素选择、Job 查询和方案展示；
- 任何 Revit 写入功能。

不要把 Python Agent 已完成描述成整个 Revit 插件已经可用。

## 目录

- `area_assistant_agent/`：本地 Python Agent。
- `contracts/v1/`：C# Add-in、Python Agent 与只读证据接口共享的 JSON 契约。
- `knowledge/`：版本化面积规则和案例知识。
- `revit_addin/`：Revit 2026 C# Add-in，按实施计划创建。
- `docs/superpowers/specs/2026-08-26-csharp-revit-frontend-design.md`：权威架构规格。
- `docs/superpowers/plans/2026-08-26-csharp-revit-frontend.md`：当前实施计划。
- `tests/`：Python Agent 与契约测试。

## Python Agent

需要 Python 3.10 或更高版本。

用户级环境变量：

```text
AI_AREA_ASSISTANT_BASE_URL=https://api.deepseek.com
AI_AREA_ASSISTANT_MODEL=deepseek-v4-flash
AI_AREA_ASSISTANT_API_KEY=<local secret>
AI_AREA_ASSISTANT_PORT=8765
AI_AREA_ASSISTANT_TIMEOUT_SECONDS=30
```

API Key 只能通过本机安全配置注入，不得写入源码、文档、日志、测试数据、RVT 或 DLL。

启动与健康检查：

```powershell
python -m area_assistant_agent --serve
Invoke-RestMethod http://127.0.0.1:8765/health
```

## DeepSeek JSON 规则

- 最终请求使用 `{"type":"json_object"}`；
- 提示词明确要求 JSON 并给出结构示例；
- Agent 本地验证 `summary`、`question` 和 2–4 个 `options`；
- 必须恰好一个 `recommended`；
- HTTP、协议或验证失败不得自动重新发送模型请求。

## 开发验证

```powershell
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
python -m compileall -q area_assistant_agent scripts tests
python scripts/check_repository_safety.py
```

C# 只针对 Revit 2026、.NET 8 和 x64。具体步骤见当前 C# 实施计划。

## 后续 Issue 的强制边界

- Issue #8–#13 使用 C# 执行 Revit API 交互和事务。
- Python Agent 只输出计划、判断、状态和安全诊断。
- 不恢复旧前端，不迁移旧面板代码。
- 不做跨 Revit 版本兼容。
- 不自动重试可能付费的模型请求。
- 没有用户明确授权时，不执行 Revit 写入或真实模型请求。

