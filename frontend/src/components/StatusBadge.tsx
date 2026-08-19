type StatusBadgeProps = {
  status: string;
};

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs font-semibold tracking-wide text-slate-200">
      {status}
    </span>
  );
}
