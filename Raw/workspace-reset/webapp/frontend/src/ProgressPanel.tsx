import type { JobState } from "./useJob";

const CLASS_BY_STATUS: Record<string, string> = {
  running: "running",
  succeeded: "ok",
  failed: "error",
};

// Renders the live progress of a job: a phase progress bar, the ordered phase
// checklist, and the streamed output log.
export function ProgressPanel({ job }: { job: JobState }) {
  if (job.status === "idle") {
    return <pre className="output muted">Output will appear here.</pre>;
  }

  const cls = CLASS_BY_STATUS[job.status] ?? "running";
  const total = Math.max(1, job.phases.length - 1);
  const pct = job.status === "succeeded" ? 100 : Math.round((job.phaseIndex / total) * 100);
  const label =
    job.status === "running"
      ? `${job.phase || "Working"}…`
      : job.status === "succeeded"
      ? "Succeeded"
      : "Failed";

  return (
    <div className="progress">
      <div className="progress-head">
        <span className={`status ${cls}`}>{label}</span>
        <span className="pct">{pct}%</span>
      </div>
      <div className="bar">
        <div className={`fill ${cls}`} style={{ width: `${pct}%` }} />
      </div>
      {job.phases.length > 0 && (
        <ol className="phases">
          {job.phases.map((p, i) => {
            const state =
              job.status === "succeeded" || i < job.phaseIndex
                ? "done"
                : i === job.phaseIndex && job.status === "running"
                ? "active"
                : "";
            return (
              <li key={p} className={state}>
                {p}
              </li>
            );
          })}
        </ol>
      )}
      <pre className={`output ${cls}`}>
        {job.lines.join("\n") || "…"}
        {job.error ? `\n[error] ${job.error}` : ""}
        {job.status !== "running" && job.returncode !== null
          ? `\n\nexit code: ${job.returncode}`
          : ""}
      </pre>
    </div>
  );
}
