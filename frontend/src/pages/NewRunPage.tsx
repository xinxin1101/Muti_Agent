import { useSearchParams } from "react-router";

import { TaskComposer } from "../components/TaskComposer";

export function NewRunPage() {
  const [searchParams] = useSearchParams();

  return (
    <section className="mx-auto max-w-3xl space-y-7 pt-10">
      <div>
        <h2 className="sr-only">新建运行</h2>
        <p className="text-sm text-stone-500">新建开发会话</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-stone-900">把需求交给 DevFlow</h1>
        <p className="mt-3 text-[15px] leading-7 text-stone-500">服务端会确认 Git 基线、规划并验证任务 DAG，再从已满足依赖的任务开始执行。</p>
      </div>
      <TaskComposer initialProjectId={searchParams.get("projectId") ?? ""} />
    </section>
  );
}
