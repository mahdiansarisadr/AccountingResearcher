"use client";

import type { ExperimentRun, Progress } from "@/lib/types";

import { ProgressCurve } from "./ProgressCurve";

const METRIC_LABEL: Record<string, string> = {
  r2: "r²",
  mae: "MAE",
  rmse: "RMSE",
  accuracy: "Acc",
  f1_weighted: "F1",
  roc_auc: "AUC",
};

// Scores on rows the agent never sees. They played no part in choosing between
// versions, which is what makes them worth reporting.
const LOCKED_PREFIX = "locked_";

type VersionGroup = {
  version: number;
  parent: number | null;
  depth: number;
  model: string | null;
  note: string | null;
  runs: ExperimentRun[];
  best: ExperimentRun | null;
  failed: number;
  error: string | null;
  spread: number | null;
  lastAt: string | null;
};

function humanModel(model: string | null): string {
  const raw = (model || "").trim().toLowerCase();
  if (!raw || raw === "code" || raw === "auto") return "Custom";
  if (raw === "hist_gb") return "Hist. GB";
  if (raw === "random_forest") return "Rand. forest";
  if (raw === "xgboost") return "XGBoost";
  if (raw === "lightgbm") return "LightGBM";
  if (raw === "logistic") return "Logistic";
  if (raw === "ridge") return "Ridge";
  return model || "Custom";
}

function shortText(text: string | null, limit = 72): string {
  if (!text) return "";
  const clipped = text.replace(/\s+/g, " ").trim();
  if (clipped.length <= limit) return clipped;
  return `${clipped.slice(0, limit - 3).replace(/\s+\S*$/, "")}…`;
}

function formatMetricValue(metric: string, value: number): string {
  if (metric === "r2" || metric === "accuracy" || metric === "f1_weighted" || metric === "roc_auc") {
    return value.toFixed(3);
  }
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(0)}k`;
  return value.toFixed(1);
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const delta = Date.now() - then.getTime();
  if (delta < 60_000) return "just now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86_400_000) {
    return then.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  return then.toLocaleDateString([], { month: "short", day: "numeric" });
}

function scored(runs: ExperimentRun[]): ExperimentRun[] {
  return runs.filter(
    (run) =>
      run.status === "finished" &&
      run.primary_metric &&
      run.primary_value != null &&
      Number.isFinite(run.primary_value),
  );
}

function pickBest(runs: ExperimentRun[]): ExperimentRun | null {
  const usable = scored(runs);
  if (!usable.length) return null;
  return usable.reduce((lead, run) =>
    (run.primary_value ?? Number.NEGATIVE_INFINITY) >
    (lead.primary_value ?? Number.NEGATIVE_INFINITY)
      ? run
      : lead,
  );
}

/** Collapse runs onto the recipe version each one executed, ordered as a tree. */
function groupByVersion(runs: ExperimentRun[]): VersionGroup[] {
  const byVersion = new Map<number, ExperimentRun[]>();
  for (const run of runs) {
    if (run.recipe_version == null) continue;
    const bucket = byVersion.get(run.recipe_version);
    if (bucket) bucket.push(run);
    else byVersion.set(run.recipe_version, [run]);
  }

  const groups = new Map<number, VersionGroup>();
  for (const [version, members] of byVersion) {
    const values = scored(members).map((run) => run.primary_value as number);
    const failures = members.filter((run) => run.status === "failed");
    groups.set(version, {
      version,
      parent: members.find((run) => run.recipe_parent != null)?.recipe_parent ?? null,
      depth: 0,
      model: members.find((run) => run.model)?.model ?? null,
      note: members.find((run) => run.hypothesis)?.hypothesis ?? null,
      runs: members,
      best: pickBest(members),
      failed: failures.length,
      error: failures.find((run) => run.error)?.error ?? null,
      spread: values.length > 1 ? Math.max(...values) - Math.min(...values) : null,
      lastAt: members[0]?.started_at ?? null,
    });
  }

  const children = new Map<number | null, number[]>();
  for (const group of groups.values()) {
    const parent = group.parent != null && groups.has(group.parent) ? group.parent : null;
    const bucket = children.get(parent);
    if (bucket) bucket.push(group.version);
    else children.set(parent, [group.version]);
  }

  const ordered: VersionGroup[] = [];
  const seen = new Set<number>();
  const walk = (version: number, depth: number) => {
    if (seen.has(version)) return;
    seen.add(version);
    const group = groups.get(version);
    if (!group) return;
    ordered.push({ ...group, depth });
    for (const child of (children.get(version) ?? []).sort((a, b) => a - b)) {
      walk(child, depth + 1);
    }
  };
  for (const root of (children.get(null) ?? []).sort((a, b) => a - b)) walk(root, 0);
  for (const version of [...groups.keys()].sort((a, b) => a - b)) walk(version, 0);
  return ordered;
}

function VersionCard({
  group,
  bestVersion,
  ceiling,
}: {
  group: VersionGroup;
  bestVersion: number | null;
  ceiling: number | null;
}) {
  const isBest = group.version === bestVersion;
  const value = group.best?.primary_value ?? null;
  const metricKey = group.best?.primary_metric || "";
  const locked = group.best?.metrics?.[`${LOCKED_PREFIX}${metricKey}`] ?? null;
  const broken = value == null && group.failed > 0;
  const share =
    value != null && ceiling && ceiling !== 0
      ? Math.max(0, Math.min(1, value / ceiling))
      : null;

  return (
    <li
      style={{ marginLeft: `${Math.min(group.depth, 4) * 10}px` }}
      className={`rounded-xl px-3 py-3 ${
        broken
          ? "bg-red-50/80 ring-1 ring-red-200/80"
          : isBest
            ? "bg-white ring-1 ring-copper/30 shadow-sm"
            : "bg-white/70 ring-1 ring-black/5"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold tabular-nums tracking-wide ${
              broken
                ? "bg-red-100 text-red-800"
                : isBest
                  ? "bg-copper/15 text-copper-dark"
                  : "bg-ink/5 text-ink-muted"
            }`}
          >
            v{group.version}
          </span>
          {group.parent != null ? (
            <span className="text-[10px] tabular-nums text-ink-faint">from v{group.parent}</span>
          ) : null}
        </div>
        <span className="text-[11px] text-ink-faint">{formatWhen(group.lastAt)}</span>
      </div>

      {value != null ? (
        <p className="mt-2 flex items-baseline gap-1.5">
          <span className="font-serif text-2xl leading-none tracking-tight text-ink">
            {formatMetricValue(metricKey, value)}
          </span>
          <span className="text-xs text-ink-muted">
            {METRIC_LABEL[metricKey] || metricKey} holdout
          </span>
        </p>
      ) : broken ? (
        <p className="mt-2 font-serif text-lg leading-6 text-red-900/80">Didn’t finish</p>
      ) : (
        <p className="mt-2 text-sm text-ink-muted">No holdout score</p>
      )}

      {share != null ? (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-black/5">
          <div
            className="h-full rounded-full bg-copper"
            style={{ width: `${Math.round(share * 100)}%` }}
          />
        </div>
      ) : null}

      {locked != null ? (
        <p
          className="mt-1 text-[11px] tabular-nums text-ink-faint"
          title="Scored on held-back rows the agent never sees, so it could not tune against them"
        >
          {formatMetricValue(metricKey, locked)} on held-back rows
        </p>
      ) : null}

      {group.note ? (
        <p className="mt-2 text-[13px] leading-5 text-ink-muted">{shortText(group.note)}</p>
      ) : null}

      {group.error ? (
        <p className="mt-1.5 text-[11px] leading-4 text-red-800/80">
          {shortText(group.error, 110)}
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-ink-faint">
        <span>{humanModel(group.model)}</span>
        <span>
          {group.runs.length} run{group.runs.length === 1 ? "" : "s"}
          {group.failed ? `, ${group.failed} failed` : ""}
        </span>
        {group.spread != null ? (
          <span title="Range across runs of this version — the noise floor">
            ±{formatMetricValue(metricKey, group.spread)}
          </span>
        ) : null}
      </div>
    </li>
  );
}

export function ExperimentSidebar({
  runs,
  progress,
  visible,
}: {
  runs: ExperimentRun[];
  progress?: Progress | null;
  visible: boolean;
}) {
  const groups = groupByVersion(runs);
  const best = pickBest(runs);
  const bestVersion = best?.recipe_version ?? null;
  const ceiling = best?.primary_value ?? null;
  const legacy = runs.filter((run) => run.recipe_version == null);

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-black/10 bg-[#f3eee4]">
      <div className="flex items-baseline justify-between px-4 py-3">
        <h2 className="text-xs font-medium uppercase tracking-widest text-ink-faint">
          Experiments
        </h2>
        {visible && groups.length ? (
          <span className="text-[11px] tabular-nums text-ink-faint">
            {groups.length} version{groups.length === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>

      {!visible ? (
        <p className="px-4 text-sm text-ink-faint">Open a conversation to see runs.</p>
      ) : runs.length === 0 ? (
        <p className="px-4 py-8 text-sm leading-6 text-ink-faint">
          Train a model and each version lands here — what changed, what it scored, and which
          version it was built on.
        </p>
      ) : (
        <>
        {progress ? <ProgressCurve progress={progress} /> : null}
        <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 pb-4">
          {groups.map((group) => (
            <VersionCard
              key={group.version}
              group={group}
              bestVersion={bestVersion}
              ceiling={ceiling}
            />
          ))}
          {legacy.length ? (
            <li className="px-1 pt-2 text-[11px] uppercase tracking-widest text-ink-faint">
              Before versioning
            </li>
          ) : null}
          {legacy.map((run) => (
            <li key={run.run_id} className="rounded-xl bg-white/50 px-3 py-2 ring-1 ring-black/5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[12px] text-ink-muted">{humanModel(run.model)}</span>
                <span className="text-[11px] text-ink-faint">{formatWhen(run.started_at)}</span>
              </div>
              {run.primary_metric && run.primary_value != null ? (
                <p className="mt-1 text-[13px] tabular-nums text-ink">
                  {METRIC_LABEL[run.primary_metric] || run.primary_metric}{" "}
                  {formatMetricValue(run.primary_metric, run.primary_value)}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
        </>
      )}
    </aside>
  );
}
