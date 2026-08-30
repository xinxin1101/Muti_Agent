# DevFlow 借鉴 OpenHands 的重构蓝图

## 1. 目标与边界

### 目标

将 DevFlow 从“固定流程的多 Agent 代码交付应用”演进为“可扩展的受控 Agent 工程运行平台”：

- 继续以 PostgreSQL、Git、确定性验证和人工授权作为唯一事实来源；
- 支持本地、Docker 和远程三类执行工作区；
- 允许接入 SiliconFlow 内置 Agent、ACP Agent（如 Codex、Claude Code、Gemini）以及未来的 OpenHands Agent Server；
- 将每次运行表示为可恢复、可审计、可回放的事件流；
- 以工具权限、风险标签和确认策略约束 Agent 的外部动作；
- 在运行内核稳定后，再接入 GitHub/Webhook/定时自动化。

### 不做什么

- 不以聊天记录或模型自述替代 DevFlow 的完成判定。
- 不直接复制或嵌入 OpenHands 全部代码库。
- 不在第一阶段引入 Kubernetes、云多租户或复杂插件市场。
- 不允许 Agent 因切换后端而绕过任务范围、验证、Git 来源校验、租约或人工关卡。

### 架构原则

> Agent 可以提出动作；只有受控工具执行、持久化证据和策略检查可以改变 DevFlow 的运行事实。

1. **控制面与执行面分离**：UI/API 负责项目、运行、审批、观测；Agent Runtime 只负责受控执行。
2. **端口优先**：核心领域依赖接口（Port），不直接依赖 SiliconFlow、Docker、Dramatiq 或某个 Agent。
3. **事件追加，不可变覆盖**：状态由事件投影得出；终态仍由现有证据链裁定。
4. **工作区可替换**：同一任务契约能运行在本机、容器或远程 Agent Server。
5. **默认最小权限**：工具必须声明读写、破坏性、幂等性和外部网络风险。
6. **体验优先于架构纯度**：用户始终只需理解“注册项目 → 描述需求 → 查看过程 → 处理必要审批 → 获取结果”；内部 Backend、Workspace、MCP 等概念默认隐藏在高级设置中。
7. **可运行优先于可扩展**：每一个阶段都必须在 Windows 本地模式下完成启动、项目注册、Run 执行、失败提示和关闭流程验证，才允许进入下一阶段。

## 1.1 用户体验与可靠性红线

以下要求是所有重构任务的前置验收条件；不满足时不得合并或启用新能力。

| 用户场景 | 必须达到的体验 | 技术护栏 |
|---|---|---|
| 首次启动 | 一条命令启动；依赖缺失、Docker 未启动、密钥未配、网络不可用时给出中文可执行提示 | 启动前自检、健康检查、超时、错误分类 |
| 项目注册 | 不会无限显示“正在注册”；用户能看到地址、分支、工作区和失败原因 | 前端请求超时；后端持久化 `PROVISIONING/READY/FAILED` 状态；Git 超时与网络错误分类 |
| 发起 Run | 默认只有需求输入；Agent/模型/工作区选择放入“高级选项”且提供安全默认值 | 配置档案、能力校验、不可用项禁用并说明原因 |
| 运行中 | 清楚展示当前阶段、正在做什么、预计等待何种外部条件；可安全暂停、取消、恢复 | 事件时间线、心跳、租约、幂等命令、断线重连 |
| 高风险操作 | 用户明确知道动作影响并可批准或拒绝 | Policy Decision + Human Gate + 审计事件 |
| 失败 | 显示中文原因、建议操作和“重试/查看诊断”；不把内部堆栈直接当作产品文案 | 错误码→用户文案映射；保留脱敏技术详情供诊断 |
| 关闭与重启 | 一键关闭不误杀无关程序；重启后可继续查看和恢复安全任务 | PID 身份校验、持久化事件、恢复检查点 |

### 默认产品流程

```text
首页状态检查
  → 注册仓库（地址 + 默认分支）
  → 显示“工作区就绪”
  → 输入需求并启动运行
  → 查看中文实时进度、文件变更、测试与验证结果
  → 仅在需要时处理人工审批
  → 查看可下载/可追溯的最终结果，或安全重试失败步骤
```

用户不应被迫理解 DAG、ACP、MCP、租约、围栏令牌、事件投影或容器编排；这些信息只能作为“查看技术详情”出现。

## 2. 目标架构

```text
┌──────────────────────────────── Control Plane ────────────────────────────────┐
│ React Agent Canvas                                                            │
│ 项目 / 运行 / 会话 / 时间线 / DAG / Diff / 审批 / 自动化 / 设置               │
│          │ REST + SSE/WebSocket                                               │
│ FastAPI Control API                                                           │
│ 项目、Run、Conversation、策略、审批、后端与工作区选择、查询投影              │
└──────────┬───────────────────────────────────────────────────────────────────┘
           │ durable commands / events
┌──────────▼──────────────────────── Runtime Plane ────────────────────────────┐
│ Run Orchestrator                                                              │
│ DAG 调度、租约、围栏令牌、重试、暂停/恢复、证据终态裁定                      │
│          │                                                                    │
│ Agent Backend Port ── SiliconFlowBackend / ACPBackend / OpenHandsBackend     │
│ Workspace Port ───── LocalWorkspace / DockerWorkspace / RemoteWorkspace      │
│ Tool Registry ────── Git、文件、终端、测试、浏览器、MCP 工具                 │
│ Policy Engine ────── 范围、风险、授权、预算、网络、密钥策略                  │
└──────────┬───────────────────────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────── Truth Plane ──────────────────────────────┐
│ PostgreSQL：Run/DAG/事件/证据/审批/工具调用/租约/投影                         │
│ Git：受管工作区、提交来源、集成历史                                            │
│ Redis + Dramatiq：异步派发与后台执行                                           │
│ Docker：可信验证基镜像与隔离执行环境                                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 与当前代码的对应关系

| 当前能力 | 重构后归属 | 保留方式 |
|---|---|---|
| Planner / Developer / Reviewer / Repair | `runtime/backends/siliconflow` | 保留现有提示词、模型配置和业务规则，封装到 Backend Adapter |
| TaskDAG、租约、围栏、集成队列 | `runtime/orchestration` | 继续作为唯一调度与终态裁定内核 |
| PostgreSQL Evidence、Run、Human Gate | `truth/persistence` | 保持现有数据事实，新增事件与工具调用表 |
| Git 工作区、验证容器 | `runtime/workspaces`、`runtime/tools` | 先适配现有实现，再增加本地/远程实现 |
| FastAPI 产品 API | `control/api` | 保持原路由兼容，新增 v2 会话/后端/工具 API |
| React 页面、SSE 看板 | `frontend/src/features` | 逐步演进为 Agent Canvas，不重写既有 Run 页面 |

## 3. 建议目录调整

以渐进迁移为原则：新目录先承接新增能力，旧模块通过 Adapter 兼容，稳定后再移动实现。

```text
backend/app/
├── control/                         # 控制面：外部 API 与查询投影
│   ├── api/                         # FastAPI routers、请求/响应模型
│   ├── commands/                    # StartRun、Pause、Resume、ApproveAction
│   ├── queries/                     # Dashboard、Timeline、Conversation 投影
│   └── automations/                 # 后期：Webhook、定时触发器
├── runtime/                         # 执行面：不直接暴露 HTTP
│   ├── orchestration/               # DAG、lease、fencing、reconcile、state machine
│   ├── backends/                    # AgentBackend 实现
│   │   ├── siliconflow/
│   │   ├── acp/
│   │   └── openhands/               # 可选的远程 Agent Server Adapter
│   ├── workspaces/                  # Local/Docker/Remote 工作区
│   ├── tools/                       # 工具定义、执行器、MCP Bridge
│   ├── policy/                      # 风险、范围、审批、密钥、预算策略
│   └── context/                     # repo skill、上下文装载与压缩
├── truth/                           # 事实层：领域模型与持久化
│   ├── domain/                      # Run、Task、Event、Evidence、Approval
│   ├── persistence/                 # SQLAlchemy repositories + migrations
│   └── projections/                 # 只读投影与事件订阅
├── integrations/                    # GitHub、SiliconFlow、MCP、OpenTelemetry
├── legacy/                          # 临时兼容层，最终删除
└── workers/                         # Dramatiq Actor，只调用 runtime command handler

frontend/src/
├── app/                             # 路由、QueryClient、应用壳
├── features/
│   ├── projects/
│   ├── runs/
│   ├── conversations/               # 新：消息、动作、观察、暂停/恢复
│   ├── approvals/                   # 新：高风险动作/人工关卡
│   ├── backends/                    # 新：Agent Backend 与 Workspace 选择
│   ├── automations/                 # 后期：规则、历史、Webhook
│   └── observability/               # 新：时间线、指标、Trace
├── components/                      # 可复用 UI
├── api/                             # 类型安全的 REST/SSE/WS 客户端
└── i18n/                            # 中文主语言与后续多语言资源
```

## 4. 核心接口设计

### 4.1 Agent Backend Port

Agent 后端只负责“根据上下文提出下一步”，不能自行修改 Run 终态。

```python
class AgentBackend(Protocol):
    backend_id: str

    async def start(self, request: AgentStartRequest) -> AgentSession: ...
    async def step(self, session: AgentSession, context: AgentContext) -> AgentStep: ...
    async def resume(self, session: AgentSession, feedback: UserFeedback) -> None: ...
    async def cancel(self, session: AgentSession) -> None: ...
```

`AgentStep` 的输出仅包含文本、结构化工具调用、建议、token/费用/延迟等元数据；由 Orchestrator 交给 Policy Engine 和 Tool Registry 处理。

首批实现：

- `SiliconFlowBackend`：封装当前 Planner/Developer/Reviewer/Repair 调用。
- `AcpBackend`：通过 ACP 调用 Codex、Claude Code、Gemini 等本机 Agent。
- `OpenHandsBackend`：可选，通过 REST/WebSocket 连接 OpenHands Agent Server；不将其作为强制依赖。

### 4.2 Workspace Port

```python
class Workspace(Protocol):
    workspace_id: str
    capability: WorkspaceCapability

    async def prepare(self, spec: WorkspaceSpec) -> None: ...
    async def execute(self, command: CommandSpec) -> CommandResult: ...
    async def read_file(self, path: SafePath) -> FileContent: ...
    async def apply_patch(self, patch: UnifiedPatch) -> PatchResult: ...
    async def snapshot(self) -> WorkspaceSnapshot: ...
    async def dispose(self) -> None: ...
```

实现顺序：

1. `ManagedGitWorkspaceAdapter`：包装现有受管 Git 工作区；
2. `DockerWorkspace`：复用可信镜像、资源限制、只读验证策略；
3. `LocalWorkspace`：仅开发模式，显式显示“主机权限”风险；
4. `RemoteWorkspace`：调用远程 Agent Server 或企业执行节点。

### 4.3 Tool Port 与风险注解

```python
class ToolDefinition(BaseModel):
    name: str
    input_schema: dict[str, object]
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool                 # 是否访问网络或外部系统
    required_capabilities: set[str]
    confirmation_level: Literal["never", "policy", "always"]

class ToolExecutor(Protocol):
    async def execute(self, action: ToolAction, context: ToolContext) -> ToolObservation: ...
```

初始工具集：`git.read`、`git.apply_patch`、`file.read`、`file.write`、`terminal.run`、`test.run`、`verification.run`、`github.publish_draft`。每一次执行均产生持久化 `ToolActionRequested` 和 `ToolObservationRecorded` 事件。

### 4.4 事件与会话模型

保留当前 Evidence，但增加通用事件层：

```text
ConversationCreated
UserMessageAdded
AgentStepStarted
AgentMessageProduced
ToolActionRequested
ActionAwaitingApproval
ToolObservationRecorded
VerificationRecorded
TaskEvidenceAccepted | TaskEvidenceRejected
RunPaused | RunResumed | RunCancelled
```

规则：

- 事件只追加，不可更新；
- `Run.status`、DAG 状态和 UI 时间线均由投影生成；
- 仅 `VerificationRecorded + Git provenance + Review + policy` 能创建现有的 Accepted Evidence；
- 普通 Agent 文本、工具输出和错误不能直接宣布 Run 成功。

### 4.5 Policy Engine

```python
class PolicyEngine(Protocol):
    async def evaluate(self, request: ActionAuthorizationRequest) -> PolicyDecision: ...
```

决策结果：`ALLOW`、`REQUIRE_HUMAN_APPROVAL`、`DENY`。决策必须引用：任务范围、工作区类型、工具风险标记、允许路径、网络权限、密钥能力、预算、Run 状态及已有人工授权。

## 5. 运行流程（目标态）

```text
需求 → 创建 Run / Conversation → 持久化 DAG 与初始事件
     → Orchestrator 取得租约与围栏令牌
     → 选择 AgentBackend + Workspace + Tool Profile
     → Agent 产生 ToolActionRequested
     → Policy Engine: 允许 / 拒绝 / 等待人工批准
     → Tool 执行并写入 ToolObservationRecorded
     → Agent 下一步，或进入确定性验证
     → 验证、审查、Git 来源校验形成 Evidence
     → DAG 集成、下游任务解锁、必要时 Human Gate
     → Evidence-bound terminal Run
```

对前端而言，任何任务都是一个可订阅的 Event Timeline；对后端而言，任何状态变更必须对应可验证的命令和事件。

## 6. 分阶段实施清单

### Phase 0：基线与契约冻结

- [ ] 为现有 API、Run/DAG/Evidence、SSE 建立契约测试与回归样本。
- [ ] 绘制当前依赖图，明确 `api`、`service`、`workspace`、`worker`、`persistence` 的循环依赖。
- [ ] 为现有关键路径补齐 trace id、run id、task id、lease/fencing id 日志关联。
- [ ] 不改变现有 `/api/v1` 行为；所有新能力从 `/api/v2` 或 feature flag 进入。
- [ ] 建立 Windows 本地“黄金路径”冒烟测试：启动、健康检查、注册公开仓库、失败仓库提示、创建 Run、关闭服务。
- [ ] 建立前端体验契约测试：加载、空状态、超时、网络失败、审批、恢复均使用中文可操作文案。

**验收**：现有多 Agent Run、验证、人工门控和 Draft PR 流程回归通过；黄金路径无需手工编辑命令或查看日志即可完成。

### Phase 1：抽象边界，不改变行为

- [ ] 新建 `AgentBackend`、`Workspace`、`ToolDefinition`、`PolicyEngine` Port。
- [ ] 把现有 SiliconFlow 调用包进 `SiliconFlowBackend`。
- [ ] 把受管 Git 工作区包进 `ManagedGitWorkspaceAdapter`。
- [ ] 把现有验证、Git 发布操作注册为内部工具，并标注风险属性。
- [ ] Orchestrator 仍沿用 Dramatiq、PostgreSQL、租约和 DAG。
- [ ] 使用 feature flag 保持旧执行路径为默认；新 Adapter 仅用于内部测试项目。

**验收**：切换到 Adapter 后的执行证据、Git 提交和验证结果与当前版本等价，页面布局与普通用户操作路径不变。

### Phase 2：事件化会话与可恢复执行

- [ ] 新增 `conversations`、`agent_events`、`tool_actions`、`approvals` 表与 Alembic migration。
- [ ] 写入不可变事件；实现 Run/Task/Conversation 三类投影。
- [ ] 增加 Pause、Resume、Cancel、追加用户说明 API。
- [ ] 前端增加时间线，展示 Agent 文本、工具调用、观察结果和审批等待。
- [ ] 支持从最后一个已持久化安全点恢复，不重复已接受的工具动作。
- [ ] 为每种运行状态定义中文标题、用户解释、下一步动作和技术详情折叠面板。

**验收**：中断进程后可恢复；重复投递不会造成重复写文件、重复发布或重复集成；用户无需刷新页面也能理解恢复进度。

### Phase 3：工作区分级与安全策略

- [ ] 实现 `LocalWorkspace` 开发模式，明确主机权限警告。
- [ ] 将 Docker 验证能力扩展为 `DockerWorkspace`，限制 CPU、内存、网络、挂载路径和生命周期。
- [ ] 引入 `PolicyEngine`，以工具风险、任务作用域和项目策略裁决动作。
- [ ] 高风险动作接入现有 Human Gate；低风险只读操作自动执行。
- [ ] 所有工具输出结构化保存，敏感字段脱敏。
- [ ] 以“本地快速模式”和“隔离安全模式”呈现，说明差异但不要求用户理解底层容器实现。

**验收**：未经授权的越界路径、网络访问、Git 推送、删除操作均被拒绝或要求确认；模式切换不会使现有项目或运行不可读。

### Phase 4：可插拔 Agent 与 MCP

- [ ] 实现 `AcpBackend`，先支持一个本机 ACP Agent。
- [ ] 实现 MCP Client Bridge：发现工具、校验输入 Schema、套用 DevFlow Policy。
- [ ] 加入项目级 `skills/`：仓库规范、测试命令、架构知识、触发条件。
- [ ] 建立 Agent/工具/模型配置档案，允许项目选择但保持审批与证据约束。

**验收**：同一任务可在 SiliconFlow 与 ACP 后端运行；二者均无法绕过验证和证据终态。

### Phase 5：自动化与可观测性

- [ ] 建立 Automation：GitHub Issue、Webhook、定时巡检触发 Run。
- [ ] 引入 OpenTelemetry，贯通前端请求、API、Worker、模型、工具、验证与 Git 操作。
- [ ] 记录 token、费用、延迟、工具失败率、验证通过率和恢复次数。
- [ ] 完成审计导出、保留策略和运行成本上限。

**验收**：一次自动化 Run 可从触发源追到每个工具动作、证据和终态；预算超限能安全停止。

## 7. 数据迁移策略

1. 旧表不重写、不删除；先只增加事件和投影表。
2. 对既有 Run 生成最小的 `RunImported` 事件，仅用于浏览，不伪造历史工具细节。
3. 新 Run 默认启用事件模型；旧 Run 继续由旧读取路径服务。
4. 在连续多个版本验证新旧投影一致后，再逐步迁移查询入口。
5. 任意阶段出现证据一致性问题，允许关闭 feature flag 回退到当前 Runtime。

## 8. 优先级建议

| 优先级 | 工作 | 原因 |
|---|---|---|
| P0 | 修复项目注册的 GitHub 网络/代理可用性 | 没有稳定仓库获取，任何 Agent 平台能力都无法可靠运行 |
| P0 | Phase 0 + Phase 1 | 先建立抽象边界，避免后续功能继续堆入单体 Service |
| P1 | Phase 2 事件时间线、暂停/恢复 | 立刻提升调试体验、可解释性和失败恢复能力 |
| P1 | Phase 3 Workspace + Policy | 让本地调试简单，同时使真实执行保持隔离和受控 |
| P2 | Phase 4 ACP/MCP | 扩展模型与工具生态，但不应早于安全边界 |
| P3 | Phase 5 Automation | 在执行可靠、可观测、可审计后再自动触发 |

## 9. 成功标准

重构完成不以“接入更多模型”衡量，而以以下结果衡量：

- 新 AgentBackend 或 MCP 工具不需要修改 DAG、Evidence 或 Git 终态逻辑。
- 本地模式可快速调试；Docker/远程模式可安全运行不可信任务。
- 每一项外部动作都能回答：谁请求、谁批准、在哪个工作区执行、输入输出是什么、是否可重试。
- 任何模型后端都不能自行宣告任务成功。
- 页面可在中文环境下完整展示运行、会话、工具、审批与失败原因。
- 本地用户在网络、密钥或 Docker 不可用时，能在 75 秒内得到中文、可执行的失败提示，而不是无限等待。
- 任一新阶段上线后，现有的一键启动、一键关闭、项目列表、Run 看板和历史数据仍可正常使用。
- “高级能力”失败时不影响基础浏览、项目管理和历史运行查看。

## 10. 参考

- OpenHands Agent Canvas：<https://github.com/OpenHands/OpenHands>
- OpenHands SDK 总体架构：<https://docs.openhands.dev/sdk/arch/overview>
- Agent 推理—行动循环：<https://docs.openhands.dev/sdk/arch/agent>
- 事件架构：<https://docs.openhands.dev/sdk/arch/events>
- 工具与 MCP 架构：<https://docs.openhands.dev/sdk/arch/tool-system>
- 工作区架构：<https://docs.openhands.dev/sdk/arch/workspace>
