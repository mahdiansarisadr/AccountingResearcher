"use client";

import type { Progress } from "@/lib/types";

const METRIC_LABEL: Record<string, string> = {
  r2: "r²",
  mae: "MAE",
  rmse: "RMSE",
  accuracy: "Acc",
  f1_weighted: "F1",
  roc_auc: "AUC",
};

const WIDTH = 240;
const HEIGHT = 44;

function format(value: number | null): string {
  if (value == null) return "—";
  if (Math.abs(value) >= 1000) return value.toFixed(0);
  return Number(value.toFixed(4)).toString();
}

function formatDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

/** Best-so-far at each scored run, as a monotonic staircase. */
function path(values: number[]): { line: string; dots: { x: number; y: number }[] } {
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const step = values.length > 1 ? WIDTH / (values.length - 1) : 0;
  const points = values.map((value, index) => ({
    x: values.length > 1 ? index * step : WIDTH / 2,
    // Higher is better for every metric we rank on, so invert for screen space.
    y: HEIGHT - ((value - low) / span) * (HEIGHT - 6) - 3,
  }));
  return {
    line: points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
    dots: points,
  };
}

export function ProgressCurve({ progress }: { progress: Progress }) {
  const values = progress.steps
    .map((step) => step.best_so_far)
    .filter((value): value is number => value != null);

  if (values.length < 2 || progress.best == null) return null;

  const { line, dots } = path(values);
  const metric = METRIC_LABEL[progress.metric || ""] || progress.metric || "score";
  const improvedAt = new Set(
    progress.steps.filter((step) => step.improved).map((step) => step.order),
  );
  const scored = progress.steps.filter((step) => step.best_so_far != null);

  return (
    <div className="mx-3 mb-2 rounded-xl bg-white/70 px-3 py-3 ring-1 ring-black/5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] font-medium uppercase tracking-widest text-ink-faint">
          Progress
        </span>
        <span className="text-[11px] tabular-nums text-ink-faint">
          {progress.runs} runs · {formatDuration(progress.seconds)}
        </span>
      </div>

      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="mt-2 h-11 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Best ${metric} over ${values.length} runs`}
      >
        <path d={line} fill="none" stroke="currentColor" className="text-copper" strokeWidth="1.5" />
        {dots.map((dot, index) =>
          improvedAt.has(scored[index]?.order) ? (
            <circle
              key={index}
              cx={dot.x}
              cy={dot.y}
              r="2.5"
              className="fill-copper"
              vectorEffect="non-scaling-stroke"
            />
          ) : null,
        )}
      </svg>

      <p className="mt-1 flex items-baseline gap-1.5 tabular-nums">
        <span className="text-[13px] text-ink-muted">{format(progress.first)}</span>
        <span className="text-[11px] text-ink-faint">→</span>
        <span className="font-serif text-lg leading-none text-ink">{format(progress.best)}</span>
        <span className="text-[11px] text-ink-muted">
          {metric}
          {progress.best_version != null ? ` · v${progress.best_version}` : ""}
        </span>
      </p>

      <p className="mt-1.5 text-[11px] leading-4 text-ink-faint">
        {progress.noise == null ? (
          <>No version was run twice, so the noise floor is unknown — treat gains with care.</>
        ) : progress.improved ? (
          <>
            Gained {format(progress.total_gain)}, above the {format(progress.noise)} seen between
            repeats of one version.
          </>
        ) : (
          <span className="text-red-800/80">
            Gained {format(progress.total_gain)}, within the {format(progress.noise)} seen between
            repeats of one version — not a real improvement.
          </span>
        )}
      </p>
    </div>
  );
}
