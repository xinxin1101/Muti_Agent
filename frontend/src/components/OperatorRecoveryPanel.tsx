import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  executeOperatorAction,
  getOperatorRecovery,
  type OperatorAction,
} from "../api/product";
import { labelFor, translateOperatorRecoveryReason } from "../i18n";

export function OperatorRecoveryPanel({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const recovery = useQuery({
    queryKey: ["operator-recovery", runId],
    queryFn: () => getOperatorRecovery(runId),
    enabled: Boolean(runId),
  });
  const action = useMutation({
    mutationFn: (selected: OperatorAction) => executeOperatorAction(runId, selected.action_id),
    onSuccess: async (result) => {
      queryClient.setQueryData(["operator-recovery", runId], result.refreshed_plan);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["operator-recovery", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run-dag", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run-metrics", runId] }),
        queryClient.invalidateQueries({ queryKey: ["human-gates", runId] }),
      ]);
    },
  });

  if (recovery.isLoading) {
    return (
      <p className="df-surface-card p-5 text-sm text-stone-600">
        正在重建持久化恢复状态…
      </p>
    );
  }
  if (recovery.error || !recovery.data) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-5">
        <h2 className="font-semibold text-amber-900">运维恢复不可用</h2>
        <p className="mt-2 text-sm text-amber-800">
          {recovery.error?.message ?? "无法重建持久化恢复状态。"}
        </p>
      </div>
    );
  }

  const plan = recovery.data;
  return (
    <section aria-label="运维恢复" className="space-y-4">
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-blue-900">运维恢复</h2>
            <p className="mt-2 max-w-3xl text-sm text-blue-800">
              这是对持久化运行时事实的诊断投影。因果追踪可解释运行，但不能授权重试、恢复、合并或发布。
            </p>
          </div>
          <span className="rounded-full border border-blue-200 bg-white px-3 py-1 font-mono text-xs text-blue-800">
            必须重新验证
          </span>
        </div>

        <div className="mt-5 space-y-2">
          {plan.reconciliation.tasks.map((task) => (
            <div
              key={task.task_id}
              className="grid gap-2 rounded-lg border border-blue-100 bg-white p-3 md:grid-cols-[8rem_12rem_1fr]"
            >
              <p className="font-mono text-xs text-cyan-200">{task.task_id}</p>
              <div>
                <p className="font-mono text-xs text-stone-800">
                  {labelFor(task.frontier_state)}
                </p>
                <p className="mt-1 text-[11px] text-stone-500">
                  {labelFor(task.lease_state)} · 代次 {task.lease_generation}
                </p>
              </div>
              <p className="text-xs leading-5 text-stone-600">
                {translateOperatorRecoveryReason(task.reason)}
              </p>
            </div>
          ))}
        </div>

        {action.error ? (
          <p className="mt-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
            {action.error.message}
          </p>
        ) : null}

        <div className="mt-5 flex flex-wrap gap-3">
          {plan.actions.map((candidate) => (
            <button
              key={candidate.action_id}
              type="button"
              disabled={action.isPending}
              title={candidate.description}
              onClick={() => action.mutate(candidate)}
              className="df-button df-button-primary"
            >
              {action.isPending ? "正在重新验证持久化事实…" : candidate.label}
            </button>
          ))}
          {plan.actions.length === 0 ? (
            <p className="text-sm text-stone-600">
              服务端当前未公布可执行的运维变更。浏览器不会根据追踪或任务状态虚构操作。
            </p>
          ) : null}
        </div>

        <p className="mt-4 text-[11px] text-stone-500">
          操作标识是绑定当前 DAG、租约、Worker 证据、执行基线和分派账本事实的不透明服务端值；点击仅发送该操作 ID。
        </p>
      </div>
    </section>
  );
}
