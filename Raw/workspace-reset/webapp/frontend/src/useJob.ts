import { useCallback, useRef, useState } from "react";

export type JobStatus = "idle" | "running" | "succeeded" | "failed";

export interface JobState {
  status: JobStatus;
  phases: string[];
  phaseIndex: number;
  phase: string;
  lines: string[];
  returncode: number | null;
  error?: string;
}

const INITIAL: JobState = {
  status: "idle",
  phases: [],
  phaseIndex: 0,
  phase: "",
  lines: [],
  returncode: null,
};

const POLL_MS = 1000;

// Starts a job (POST) then polls /api/jobs/<id> until done, accumulating the
// streamed output lines and tracking the reported phase for a progress bar.
export function useJob() {
  const [state, setState] = useState<JobState>(INITIAL);
  const timer = useRef<number | null>(null);

  const start = useCallback(async (url: string, body: unknown) => {
    if (timer.current) window.clearTimeout(timer.current);
    setState({ ...INITIAL, status: "running" });

    let res: Response;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      setState((s) => ({ ...s, status: "failed", error: String(e) }));
      return;
    }
    const started = (await res.json().catch(() => ({}))) as { jobId?: string; error?: string };
    if (!res.ok || !started.jobId) {
      setState((s) => ({ ...s, status: "failed", error: started.error ?? `HTTP ${res.status}` }));
      return;
    }

    const jobId = started.jobId;
    let since = 0;

    const poll = async () => {
      let r: Response;
      try {
        r = await fetch(`/api/jobs/${jobId}?since=${since}`);
      } catch (e) {
        setState((s) => ({ ...s, status: "failed", error: String(e) }));
        return;
      }
      const j = (await r.json().catch(() => ({}))) as Partial<JobState> & {
        done?: boolean;
        nextSince?: number;
        error?: string;
      };
      if (!r.ok) {
        setState((s) => ({ ...s, status: "failed", error: j.error ?? `HTTP ${r.status}` }));
        return;
      }
      since = j.nextSince ?? since;
      setState((s) => ({
        status: (j.status as JobStatus) ?? s.status,
        phases: j.phases ?? s.phases,
        phaseIndex: j.phaseIndex ?? s.phaseIndex,
        phase: j.phase ?? s.phase,
        lines: [...s.lines, ...(j.lines ?? [])],
        returncode: j.returncode ?? null,
      }));
      if (!j.done) timer.current = window.setTimeout(poll, POLL_MS);
    };

    poll();
  }, []);

  return { state, start };
}
