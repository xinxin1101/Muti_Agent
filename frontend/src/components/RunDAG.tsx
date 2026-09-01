import type {
  ProductDAGNode,
  ProductDAGNodeState,
  ProductRunDAG,
} from "../types/product";
import { labelFor, translateTaskObjective } from "../i18n";

type RunDAGProps = Readonly<{
  runId: string;
  dag: ProductRunDAG;
}>;

type Point = Readonly<{ x: number; y: number }>;

const NODE_WIDTH = 220;
const NODE_HEIGHT = 92;
const COLUMN_GAP = 96;
const ROW_GAP = 42;
const PADDING = 28;

export function RunDAG({ runId, dag }: RunDAGProps) {
  const layers = groupByLayer(dag.nodes);
  const positions = nodePositions(layers);
  const layerCount = Math.max(1, layers.length);
  const maxRows = Math.max(1, ...layers.map((layer) => layer.length));
  const width =
    PADDING * 2 + layerCount * NODE_WIDTH + (layerCount - 1) * COLUMN_GAP;
  const height = PADDING * 2 + maxRows * NODE_HEIGHT + (maxRows - 1) * ROW_GAP;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-stone-900">任务 DAG</h2>
          <p className="mt-1 text-sm text-stone-600">
            来自后端已验证 DAG 的只读拓扑。节点状态是由证据支撑的展示投影，而非浏览器调度状态。
          </p>
        </div>
        <div className="text-right text-xs text-stone-500">
          <p>{dag.nodes.length} 个节点 · {dag.edges.length} 条边</p>
          <p className="mt-1 font-mono">DAG {dag.dag_sha256.slice(0, 12)}</p>
        </div>
      </div>

      <div className="df-technical-panel overflow-x-auto p-2">
        <svg
          role="img"
          aria-label="已验证的任务依赖 DAG"
          viewBox={`0 0 ${width} ${height}`}
          className="min-h-52"
          style={{ minWidth: width }}
        >
          <defs>
            <marker
              id="dag-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-slate-500" />
            </marker>
          </defs>

          {dag.edges.map((edge) => {
            const source = positions.get(edge.source_task_id);
            const target = positions.get(edge.target_task_id);
            if (!source || !target) {
              return null;
            }
            const startX = source.x + NODE_WIDTH;
            const startY = source.y + NODE_HEIGHT / 2;
            const endX = target.x;
            const endY = target.y + NODE_HEIGHT / 2;
            const bend = Math.max(32, (endX - startX) / 2);
            return (
              <path
                key={`${edge.source_task_id}->${edge.target_task_id}`}
                d={`M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`}
                fill="none"
                className="stroke-slate-600"
                strokeWidth="2"
                markerEnd="url(#dag-arrow)"
              />
            );
          })}

          {dag.nodes.map((node) => {
            const point = positions.get(node.task_id);
            if (!point) {
              return null;
            }
            return (
              <a
                key={node.task_id}
                href={`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(node.task_id)}`}
                aria-label={`打开任务 ${node.task_id}`}
              >
                <g transform={`translate(${point.x} ${point.y})`} data-state={node.presentation_state}>
                  <title>{translateTaskObjective(node.objective)}</title>
                  <rect
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    rx="12"
                    className={`${nodeFillClass(node.presentation_state)} stroke-slate-700 transition hover:stroke-cyan-400`}
                    strokeWidth="1.5"
                  />
                  <text x="16" y="25" className="fill-cyan-200 font-mono text-xs">
                    {truncate(node.task_id, 28)}
                  </text>
                  <text x="16" y="50" className={stateTextClass(node.presentation_state)}>
                    {labelFor(node.presentation_state)}
                  </text>
                  <text x="16" y="72" className="fill-slate-500 text-[10px]">
                    层级 {node.layer} · {labelFor(node.state_basis)}
                  </text>
                </g>
              </a>
            );
          })}
        </svg>
      </div>

      <div className="flex flex-wrap gap-2 text-xs text-stone-600">
        {(["READY", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED"] as const).map((state) => (
          <span key={state} className="rounded-full border border-stone-200 bg-stone-50 px-2 py-1">
            {labelFor(state)}
          </span>
        ))}
        <span className="ml-auto">拓扑来源：{labelFor(dag.topology_source)}</span>
      </div>
    </div>
  );
}

function groupByLayer(nodes: readonly ProductDAGNode[]): readonly (readonly ProductDAGNode[])[] {
  const maxLayer = Math.max(0, ...nodes.map((node) => node.layer));
  return Array.from({ length: maxLayer + 1 }, (_, layer) =>
    nodes
      .filter((node) => node.layer === layer)
      .toSorted((left, right) => left.topological_index - right.topological_index),
  );
}

function nodePositions(
  layers: readonly (readonly ProductDAGNode[])[],
): ReadonlyMap<string, Point> {
  const positions = new Map<string, Point>();
  for (const [layerIndex, nodes] of layers.entries()) {
    for (const [rowIndex, node] of nodes.entries()) {
      positions.set(node.task_id, {
        x: PADDING + layerIndex * (NODE_WIDTH + COLUMN_GAP),
        y: PADDING + rowIndex * (NODE_HEIGHT + ROW_GAP),
      });
    }
  }
  return positions;
}

function truncate(value: string, maximum: number): string {
  return value.length <= maximum ? value : `${value.slice(0, maximum - 1)}…`;
}

function nodeFillClass(state: ProductDAGNodeState): string {
  if (state === "FAILED" || state === "BLOCKED") {
    return "fill-rose-950/70";
  }
  if (state === "SUCCEEDED") {
    return "fill-emerald-950/70";
  }
  if (["RUNNING", "VERIFYING", "REVIEWING", "REPAIRING"].includes(state)) {
    return "fill-cyan-950/70";
  }
  if (state === "READY") {
    return "fill-amber-950/60";
  }
  return "fill-slate-900";
}

function stateTextClass(state: ProductDAGNodeState): string {
  if (state === "FAILED" || state === "BLOCKED") {
    return "fill-rose-300 text-xs font-semibold";
  }
  if (state === "SUCCEEDED") {
    return "fill-emerald-300 text-xs font-semibold";
  }
  if (["RUNNING", "VERIFYING", "REVIEWING", "REPAIRING"].includes(state)) {
    return "fill-cyan-300 text-xs font-semibold";
  }
  if (state === "READY") {
    return "fill-amber-300 text-xs font-semibold";
  }
  return "fill-slate-400 text-xs font-semibold";
}
