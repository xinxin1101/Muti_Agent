const labels: Readonly<Record<string, string>> = {
  RUNNING: "运行中",
  SUCCEEDED: "已成功",
  FAILED: "失败",
  RECOVERY_REQUIRED: "需要恢复",
  WAITING_EXTERNAL: "等待新进展",
  PENDING: "等待中",
  PROVISIONING: "准备中",
  READY: "就绪",
  PUBLISHING: "发布中",
  PUBLISHED: "已发布",
  ARCHIVED: "已归档",
  VERIFYING: "验证中",
  REVIEWING: "审查中",
  REPAIRING: "修复中",
  BLOCKED: "已阻塞",
  QUEUED: "已排队",
  BROKER_UNAVAILABLE: "消息队列不可用",
  CONNECTING: "正在连接",
  LIVE: "实时连接",
  RECONNECTING: "正在重连",
  UNSUPPORTED: "浏览器不支持",
  "STREAM ERROR": "事件流错误",
  INFO: "信息",
  WARNING: "警告",
  ERROR: "错误",
  EVIDENCE: "证据",
  DERIVED_DAG: "由 DAG 推导",
  PERSISTED: "已持久化",
  IMPLICIT_SINGLE_TASK: "隐式单任务",
  TASK: "任务变更",
  INTEGRATION: "集成变更",
  SINGLE_TASK: "单任务",
  ADDED: "新增",
  MODIFIED: "修改",
  DELETED: "删除",
  TYPE_CHANGED: "类型变更",
  PERSISTENCE: "持久化服务",
  LEASE: "任务租约",
  DISPATCH: "任务分派",
  WORKER: "执行工作进程",
  RUNTIME: "运行时",
  AGENT: "智能体",
  VERIFICATION: "确定性验证",
  REVIEW: "代码审查",
  REPAIR: "修复流程",
  RUN_STARTED: "运行已启动",
  RUN_FINALIZED: "运行已结束",
  LEASE_ACQUIRED: "已取得执行租约",
  LEASE_TAKEN_OVER: "已接管执行租约",
  LEASE_HEARTBEAT: "执行租约续期",
  LEASE_RELEASED: "已释放执行租约",
  EVIDENCE_RECORDED: "已记录运行证据",
  DISPATCH_EVENT: "分派事件",
  STATE_TRANSITION: "状态变更",
  DEVELOPER_RUN: "开发执行记录",
  FAILURE_REPORT: "失败报告",
  WORKER_EXECUTION: "工作进程执行记录",
  TRACE_BATCH: "运行追踪记录",
  RUN_TERMINAL: "运行已结束",
  BLOCKED_UPSTREAM_FAILURE: "被上游失败阻塞",
  WAIT_DEPENDENCIES: "等待依赖任务",
  WAIT_ACTIVE_OWNER: "等待当前执行者",
  BLOCKED_RECOVERY_GAP: "恢复信息不足",
  WAIT_INTEGRATION_BASE: "等待集成基线",
  RECONCILE_CANDIDATE: "可重新核对",
  UNOWNED: "未分配",
  ACTIVE: "执行中",
  EXPIRED: "已过期",
  RELEASED: "已释放",
};

export function labelFor(value: string): string {
  return labels[value] ?? value;
}

export function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN");
}

const taskObjectiveTranslations: Readonly<Record<string, string>> = {
  "Implement the core Gomoku game board module with a 15x15 grid, stone placement, move validation, and five-in-a-row win detection in all four directions (horizontal, vertical, two diagonals).":
    "实现五子棋棋盘核心模块：15×15 棋盘、落子、合法性校验，以及横向、纵向和两条对角线的五连判定。",
  "Implement a Gomoku AI opponent using heuristic evaluation and/or minimax search with alpha-beta pruning. The AI must evaluate board positions, detect threats (open fours, open threes), and select moves that maximize its position while blocking opponent threats.":
    "实现五子棋 AI 对手：使用启发式评估或带 Alpha-Beta 剪枝的极大极小搜索，识别活四、活三等威胁，并兼顾进攻与防守。",
  "Implement the main game loop and CLI interface for human-vs-AI Gomoku. Display the board, accept human input as row/col coordinates, invoke the AI for its move, and announce the winner or draw.":
    "实现人机五子棋的主循环和命令行界面：显示棋盘、接收行列坐标、调用 AI 落子，并宣布胜负或平局。",
  "Write pytest test suites for the board logic, AI engine, and game loop covering win detection in all directions, move validation, AI blocking behavior, AI winning behavior, and invalid input handling.":
    "编写 pytest 测试：覆盖四方向五连、落子校验、AI 防守与获胜策略，以及非法输入处理。",
};

export function translateTaskObjective(value: string): string {
  return taskObjectiveTranslations[value] ?? value;
}

export function translateRuntimeEventMessage(
  kind: string,
  message: string,
): string {
  if (message === "Persisted run started.") {
    return "运行记录已创建，正在等待任务调度。";
  }
  if (message === "Accepted evidence.") {
    return "运行证据已通过校验并保存。";
  }
  if (message === "Task lease generation acquired.") {
    return "工作进程已取得当前任务的执行租约。";
  }
  if (message === "Persisted Run is terminal; DAG reconciliation cannot reopen it.") {
    return "运行已结束，调度器不会重新打开此运行。";
  }
  if (message.includes("Queued worker execution failed")) {
    return "任务工作进程未能完成执行，请查看任务详情中的失败证据。";
  }
  if (kind === "RUN_STARTED") {
    return "运行已启动。";
  }
  if (kind === "RUN_FINALIZED") {
    return "运行已结束。";
  }
  if (kind === "EVIDENCE_RECORDED") {
    return "已接收并保存运行证据。";
  }
  return "已记录运行时事件。";
}

export function translateOperatorRecoveryReason(value: string): string {
  if (value === "Persisted Run is terminal; DAG reconciliation cannot reopen it.") {
    return "该运行已经结束，系统不会自动重新打开或重新执行它。";
  }
  if (value === "Fresh Step 5.3 revalidation is required before publication.") {
    return "发布前必须重新核对当前持久化事实。";
  }
  return "服务端已记录该任务的恢复状态。";
}
