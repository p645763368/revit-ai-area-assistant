# Issue #7 — Revit 2026 人工测试手册

## 安全边界

- 只使用指定开发测试副本；不要打开原项目执行本手册。
- 测试前记录 Revit `IsModified` 和当前撤销菜单；测试结束不得保存 RVT。
- 本 Issue 只允许读取和截图，不应出现 Transaction、模型元素变化或新的 Revit 撤销项。
- 不把截图、运行日志、真实元素数据或 API 密钥复制进仓库。
- live provider conformance probe 与 live Revit“扫描与方案”是两个顺序门禁，必须分别取得用户授权。每个授权只允许一次提交；结果不确定时禁止 Retry、Send 或再次“扫描与方案”。

## 前置配置

1. 使用用户级环境变量配置 `AI_AREA_ASSISTANT_PYTHON`、`AI_AREA_ASSISTANT_TEST_DOCUMENT`，并显式设置 `AI_AREA_ASSISTANT_BASE_URL=https://api.deepseek.com`、`AI_AREA_ASSISTANT_MODEL=deepseek-v4-flash`。`AI_AREA_ASSISTANT_API_KEY` 的真实值只通过本机安全渠道注入，不读取、不打印、不复制进命令记录或本手册。
2. Agent 只在进程启动时读取上述配置。变量有任何变更时，先完全停止旧 Agent；进入 provider 门禁前启动新 Agent 并检查 `/health`、`/v1/health`。进入 Revit 门禁前还要完全退出并重启 Revit/pyRevit，不能只 Reload 面板。
3. 确认 rvt-mcp 引用的 `RevitAPIUI` 等程序集版本与当前加载的 Revit 2026 build 一致。若 Revit journal 出现类似 `26.1.0.0` 与已加载 `26.0.4.0` 不匹配，先停止截图测试并修复 rvt-mcp/Revit 安装匹配；即使环境不匹配，面板仍必须显示可重试错误且 Revit 不得退出。
4. 完全退出并重启 Revit 2026，打开指定开发测试副本。
5. 加载本 worktree 的 pyRevit extension，打开“AI Area Assistant”面板。
6. 等待文档状态显示 `bound`、rvt-mcp 显示 `verified`；选择“新建会话”。

## 自动化与 live 验证门禁状态（2026-08-26）

- [x] 完整离线验证：`unittest discover` 为 223 tests / 39.116s / OK；`compileall` 退出 0；repository safety 输出 `Repository safety check passed.`；`git diff --check` 退出 0（仅提示现有工作树的 LF/CRLF 转换）。精确命令与边界见本次 Task 5 离线报告。
- [ ] Provider gate：尚未执行，也未授权；在离线验证与独立代码审查完成前不得执行。
- [ ] Revit gate：尚未执行，也未授权；只有 provider gate 通过并再次取得独立授权后才可执行。
- [ ] 本手册以下场景均尚未在本轮执行，不能把离线测试结果称为 provider 或 Revit 验收通过。

## 严格 JSON 与安全诊断预期

- 每个模型可见工具必须带 strict、禁止额外字段的参数 schema，且只允许 `inspect_revit_model` 与条件可用的 `capture_revit_view`。最终结果必须由 `response_format.type = json_schema` 约束为非空 `summary`、非空 `question` 和 2–4 个完整选项；本地校验仍须确认恰好一个 `recommended: true`。
- 未知响应结构、reasoning-only、畸形工具调用、非对象参数或不符合最终方案 schema 的内容必须 fail closed；不得把 reasoning 或未知字段当作方案继续执行。
- `model_protocol_error` 的结构诊断只允许写入当前会话的 `AI_Area_Assistant_Data/documents/<document-key>/sessions/<session-id>/model_diagnostics/protocol.jsonl`。记录只含时间、任务/诊断标识、受控键名、类型和计数；不得含 API key/Authorization、prompt/对话、模型 content/reasoning、工具名称或参数值、Revit 元素/几何/截图、文档路径/指纹或 provider 原始响应体。
- HTTP、连接、超时、协议或结果校验失败后，Agent 与面板都不得自动重发模型请求。轮询失败只允许继续查询原 `job_id`；终态后的任何新请求必须由用户重新授权并明确操作。

## Provider gate checklist（尚未执行）

- [ ] 离线完整验证与独立 Standards/Spec 审查均通过；若未通过，停在此处。
- [ ] 单独向用户说明这是一次可能计费、完全不含 Revit 证据的最小探针，并取得明确授权。
- [ ] 重启 Agent，确认进程加载精确 DeepSeek 配置并先通过 `/health`、`/v1/health`；不得显示或记录 API key。
- [ ] 只发送一次最小请求，同时携带 strict 只读工具定义和预期 `json_schema` response format。
- [ ] 验证标准化工具调用可被接受，并在后续模型轮次得到 schema-valid 最终 JSON；记录安全 shape、调用次数和 pass/fail，不记录模型正文。
- [ ] 若 provider 拒绝 tools + `json_schema` 组合或返回任何不确定失败，停止且绝不自动重试；不得进入 Revit gate。后续实现应按规格改为独立 finalization turn，并重新完成离线审查。

## Revit gate checklist（尚未执行）

- [ ] Provider gate 已通过；另行取得一次 live Revit“扫描与方案”的明确授权。
- [ ] 完全重启 Agent 与 Revit/pyRevit，确认指定开发测试副本的完整路径、活动视图、文档绑定和 rvt-mcp 连接，记录开始前 `IsModified: False` 与撤销菜单。
- [ ] 新建会话，只选择一个有效 Floor、Roof 或 Wall 边界来源，点击一次“扫描与方案”。
- [ ] 记录单一 `job_id`，只轮询该任务直到一个终态；等待期间不得点击 Retry、Send 或再次“扫描与方案”。
- [ ] 记录时间戳、state/stage 变化、可获得的模型调用次数、最终 schema 校验、结果/安全错误和 `diagnostic_id`；不得复制模型正文或诊断禁存值进仓库。
- [ ] 确认 Agent 没有重复 provider 请求、规划只调用允许的只读工具，结束后 `IsModified` 仍为 `False`、撤销菜单无新增项，并关闭 RVT 且选择“不保存”。

## 场景 1：只读扫描、工具调用和截图

1. 点击“扫描与方案”。
2. 确认 Revit 在分析期间保持可操作，面板显示“分析中”。
3. 在当前会话的 `AI_Area_Assistant_Data` 下检查 `operations.jsonl`：应只出现 `inspect_revit_model`、`capture_revit_view`；不得出现写入工具或 Authorization/API key。
4. 若 Agent 判断需要视觉证据，确认会话 `screenshots` 目录生成 PNG；该文件必须留在忽略的外部数据目录。
5. 确认结果卡片包含 2–4 个选项，恰好一个带 `★` 推荐标记，并为每项显示“依据”和“影响”。
6. 对照模型检查截图结论是否仅作为辅助，方案依据仍引用结构化模型/几何证据。
7. 若 `capture_view_image` 超时，确认本轮不再实际重复调用截图；Agent 应记录视觉证据不可用，并基于已取得的结构化几何返回 2–4 个选项或要求用户补充证据，而不是让整个规划请求返回 HTTP 503。
8. 若当前 rvt-mcp 的 `tools/list` 不包含 `capture_view_image`，确认 Agent 不实际调用截图工具；已有 `boundary_candidates` 证据时继续返回明确披露截图缺失的方案，没有该证据时面板应显示具体能力/证据错误，禁止生成推荐方案或只显示笼统 `HTTP 503 Service Unavailable`。
9. 若 `AI_AREA_ASSISTANT_TIMEOUT_SECONDS=120`，确认规划运行超过50秒时面板仍保持等待并最终接收结果；只有超过规划专用上限（135秒）才显示“任务可能仍在Agent运行”的明确提示，此时“重试”“发送”“扫描与方案”不得再次提交同一付费规划。health、文档验证和普通会话请求仍应使用原短超时。

## 场景 2：点击选项与自由文本继续同一会话

1. 点击一个非推荐选项，确认面板将选择作为下一轮用户输入，并返回更新后的结构化方案。
2. 在自由文本框说明一个项目意图并发送，确认 Agent 结合前两轮上下文继续规划，而不是开始无历史的新对话。
3. 检查同一 `conversation.jsonl` 依次包含扫描请求、结构化结果、点击选择、更新结果和自由文本；敏感字段应被遮蔽。

## 场景 3：安全暂停和失败恢复

1. 发起规划后切换到另一个文档；确认旧会话立即暂停，旧结果不能回写面板或会话。
2. 切回测试副本时按既有安全流程重新验证并明确选择会话，不应静默继续工具调用。
3. 临时令 rvt-mcp 或模型 API 不可用，确认面板显示可重试错误且 Revit 不被阻塞。
4. 恢复服务后重试，确认规划可重新完成。

## 最终无写入检查

1. 确认 Revit `IsModified` 与测试开始前一致（若开始前已有人工修改，不应因本功能进一步改变）。
2. 确认撤销菜单没有新增 AI 面积助手相关模型操作。
3. 关闭开发测试副本并选择“不保存”。

## 尚需人工记录

- 实际使用的 Revit 2026、pyRevit、rvt-mcp 与模型服务版本。
- 是否观察到模型服务正确接收工具调用和视觉输入。
- 每轮响应耗时、截图是否成功、结构化选项是否符合模型证据。
- `IsModified`、撤销菜单和关闭时“不保存”的最终确认。
