# Issue #4 Revit 2026人工测试

本测试只读取状态，不创建事务、不修改模型、不保存RVT。测试截图、真实路径、日志和项目数据不得提交到Git。

## 前置条件

1. 安装Revit 2026、pyRevit和rvt-mcp。
2. 使用开发测试副本，不使用原模型进行授权测试。
3. 将环境变量`AI_AREA_ASSISTANT_TEST_DOCUMENT`设置为该测试副本的完整绝对路径，然后完全重启Revit。
4. 确认同一时间只有一个Revit实例参与本测试。

## 测试A：显示当前状态

1. 打开开发测试副本并记录当前`IsModified`。
2. 在pyRevit的`AI Area Assistant`选项卡点击同名按钮。
3. 核对提示框显示Revit实例、文档标题、完整路径、活动视图和`IsModified`。
4. 核对`Authorized path match`为`yes`。
5. 核对入口仍显示`Agent/rvt-mcp binding: pending`及`Write permission: denied`；单凭路径不得授权。

## 测试B：Agent与rvt-mcp交叉验证

1. 通过rvt-mcp列出目标，使用返回的四位年份连接并验证。
2. 读取当前活动视图，核对Revit进程和视图与pyRevit快照一致。
3. 将两份快照交给Agent文档状态动作。
4. 预期绑定状态为`bound`、rvt-mcp状态为`verified`；只有完整路径也匹配时`write_allowed`为`true`。

## 测试C：切换保护

1. 绑定测试副本后，切换到另一文档；不要执行任何写入。
2. 预期任务立即变为`paused`，原因是`document_changed`，`write_allowed`为`false`。
3. 如果可以安全地启动第二个Revit实例，改为观察另一个实例；预期原因是`revit_instance_changed`。
4. 切回原文档不得静默恢复旧任务；应由用户显式建立新绑定。

## 测试D：原模型与未保存文档

1. 打开非授权模型或新建未保存文档。
2. 预期`Authorized path match`为`no`，最终`write_allowed`始终为`false`。

## 完成记录

- 记录测试日期、Revit/pyRevit/rvt-mcp版本和通过/失败结论。
- 记录测试前后`IsModified`；本功能不应改变该值。
- 确认没有保存RVT。
- 证据保存在本机受控目录，不提交GitHub。
