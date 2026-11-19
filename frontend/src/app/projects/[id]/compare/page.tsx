"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import useSWR from "swr";
import { StatTile } from "@/components/charts";
import { Markdown } from "@/components/markdown";
import { useProject } from "@/components/project-context";
import {
  Badge,
  Button,
  Card,
  CodeLink,
  EmptyState,
  ErrorNotice,
  Notice,
  PageHeader,
  SeverityBadge,
  TableSkeleton,
  TableWrap,
  usePageTitle,
  inputClass,
  tdClass,
  thClass,
} from "@/components/ui";
import { errorMessage, fetcher, postJson } from "@/lib/api";
import { formatDate, shortSha } from "@/lib/format";
import type { Commit, Comparison, ComparisonResult, Severity } from "@/lib/types";

interface HistoryItem {
  id: string;
  base_commit: string;
  head_commit: string;
  risk: { score: number; level: Severity };
  stats: { files_changed: number };
  created_at: string;
}

const STATUS_TONE: Record<string, "good" | "critical" | "accent" | "neutral"> = {
  added: "good",
  removed: "critical",
  deleted: "critical",
  modified: "accent",
  renamed: "neutral",
};

function Results({ comparison, projectId }: { comparison: Comparison; projectId: string }) {
  const result: ComparisonResult = comparison.result;
  const { stats, risk } = result;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(14rem,18rem)_1fr]">
        <Card title="Change risk">
          <div className="flex items-baseline gap-2">
            <span className="text-5xl font-semibold">{risk.score}</span>
            <span className="text-sm text-ink-2">of 100</span>
          </div>
          <div className="mt-2"><SeverityBadge severity={risk.level} /></div>
          <p className="mt-2 text-xs text-ink-3">{shortSha(result.base)} to {shortSha(result.head)}</p>
        </Card>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Files changed" value={stats.files_changed} />
          <StatTile label="Lines added" value={stats.additions} />
          <StatTile label="Lines removed" value={stats.deletions} />
          <StatTile label="Files affected" value={stats.impacted_files} sub="Depend on changed code" />
          <StatTile label="Symbols added" value={stats.symbols_added} />
          <StatTile label="Symbols changed" value={stats.symbols_modified} />
          <StatTile label="Symbols removed" value={stats.symbols_removed} />
          <StatTile label="Endpoints affected" value={result.endpoints.length} />
        </div>
      </div>

      {comparison.ai_summary && (
        <Card title={`AI review (${comparison.provider})`}><Markdown>{comparison.ai_summary}</Markdown></Card>
      )}
      {comparison.ai_error && <Notice tone="warning">The AI review was skipped. {comparison.ai_error}</Notice>}
      {result.graph_note && <Notice tone="info">{result.graph_note}</Notice>}

      <Card title={`Potential risks (${result.risks.length})`}>
        {result.risks.length === 0 ? (
          <p className="text-sm text-ink-3">No known risk patterns were found in this change.</p>
        ) : (
          <ul className="divide-y divide-line">
            {result.risks.map((item, index) => (
              <li key={index} className="py-2.5 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={item.severity} />
                  <span className="text-sm font-medium">{item.title}</span>
                </div>
                <p className="mt-0.5 text-sm text-ink-2 break-words">{item.detail}</p>
                {item.evidence.length > 0 && (
                  <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    {item.evidence.map((evidence, position) => (
                      <li key={position}><CodeLink projectId={projectId} path={evidence.path} line={evidence.line} /></li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Affected modules">
          <TableWrap>
            <table className="w-full min-w-[20rem]">
              <thead>
                <tr>
                  <th className={thClass}>Module</th>
                  <th className={`${thClass} text-right`}>Changed files</th>
                  <th className={`${thClass} text-right`}>Affected files</th>
                </tr>
              </thead>
              <tbody>
                {result.modules.map((module) => (
                  <tr key={module.module}>
                    <td className={`${tdClass} font-mono text-xs break-all`}>{module.module}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{module.changed_files}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{module.impacted_files}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
        <Card title="Files that depend on this change">
          {result.impacted.length === 0 ? (
            <p className="text-sm text-ink-3">No other files depend on the changed code.</p>
          ) : (
            <ul className="max-h-72 space-y-1.5 overflow-y-auto">
              {result.impacted.map((item) => (
                <li key={item.path} className="text-xs">
                  <CodeLink projectId={projectId} path={item.path} />
                  <span className="block text-ink-3 break-all">{item.distance} step(s) away, through {item.via}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Changed code">
        <TableWrap>
          <table className="w-full min-w-[36rem]">
            <thead>
              <tr>
                <th className={thClass}>Change</th>
                <th className={thClass}>Symbol</th>
                <th className={thClass}>Kind</th>
                <th className={thClass}>Location</th>
              </tr>
            </thead>
            <tbody>
              {result.symbols.map((symbol, index) => (
                <tr key={index}>
                  <td className={tdClass}>
                    <Badge tone={STATUS_TONE[symbol.status] ?? "neutral"}>{symbol.status}</Badge>
                    {symbol.signature_changed && <span className="ml-1 text-xs text-ink-3">signature</span>}
                  </td>
                  <td className={`${tdClass} font-mono text-xs break-all`}>{symbol.qualified_name}</td>
                  <td className={tdClass}>{symbol.kind}</td>
                  <td className={tdClass}>
                    {symbol.status === "removed"
                      ? <span className="font-mono text-xs break-all text-ink-3">{symbol.path}:{symbol.line}</span>
                      : <CodeLink projectId={projectId} path={symbol.path} line={symbol.line} />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
        {result.symbols.length === 0 && <p className="mt-2 text-sm text-ink-3">No functions or classes changed.</p>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Changed files">
          <ul className="max-h-72 space-y-1 overflow-y-auto">
            {result.files.map((file) => (
              <li key={file.path} className="flex flex-wrap items-center gap-2 text-xs">
                <Badge tone={STATUS_TONE[file.status] ?? "neutral"}>{file.status}</Badge>
                {file.status === "deleted"
                  ? <span className="font-mono break-all text-ink-3">{file.path}</span>
                  : <CodeLink projectId={projectId} path={file.path} />}
                <span className="tabular-nums text-ink-3">+{file.additions} -{file.deletions}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Affected endpoints">
          {result.endpoints.length === 0 ? (
            <p className="text-sm text-ink-3">No HTTP endpoints are affected.</p>
          ) : (
            <ul className="max-h-72 space-y-1.5 overflow-y-auto">
              {result.endpoints.map((endpoint, index) => (
                <li key={index} className="text-xs">
                  <Badge tone="accent">{endpoint.method}</Badge> <span className="font-mono break-all">{endpoint.path}</span>
                  <span className="block text-ink-3">{endpoint.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function ComparePage() {
  usePageTitle("Compare commits");
  const { project } = useProject();
  const base = `/api/projects/${project.id}`;
  const { data: commits, error: commitsError, isLoading } = useSWR<Commit[]>([`${base}/commits`, project.head_commit], ([path]: [string, unknown]) => fetcher<Commit[]>(path));
  const { data: history, mutate: refreshHistory } = useSWR<HistoryItem[]>(`${base}/comparisons`, fetcher);
  const [baseRef, setBaseRef] = useState("");
  const [headRef, setHeadRef] = useState("");
  const [withAi, setWithAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);

  useEffect(() => {
    if (commits && commits.length >= 2 && !headRef) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- Sensible defaults once commits have loaded.
      setHeadRef(commits[0].sha);
      setBaseRef(commits[1].sha);
    }
  }, [commits, headRef]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const data = await postJson<Comparison>(`${base}/compare`, { base: baseRef, head: headRef, with_ai_summary: withAi });
      setComparison(data);
      await refreshHistory();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function openHistory(id: string) {
    setError(null);
    try {
      setComparison(await fetcher<Comparison>(`${base}/comparisons/${id}`));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  if (isLoading) return <TableSkeleton />;
  if (commitsError) return <ErrorNotice error={commitsError} />;

  const commitOption = (commit: Commit) => (
    <option key={commit.sha} value={commit.sha}>
      {commit.short_sha} {commit.subject.slice(0, 60)}
    </option>
  );

  return (
    <div className="space-y-4">
      <PageHeader title="Compare commits" description="See what changed between two versions, what depends on it, and what could go wrong." />
      {commits && commits.length < 2 ? (
        <EmptyState title="There is only one version so far">
          {project.source_type === "zip" ? (
            <>Upload a new ZIP version in <Link className="text-accent underline" href={`/projects/${project.id}/settings`}>project settings</Link> to compare the two.</>
          ) : (
            <>Sync the repository in <Link className="text-accent underline" href={`/projects/${project.id}/settings`}>project settings</Link> after new commits are pushed.</>
          )}
        </EmptyState>
      ) : (
        <form onSubmit={submit} className="grid gap-3 rounded-xl border border-line bg-surface p-4 md:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block font-medium">Older commit</span>
            <select value={baseRef} onChange={(event) => setBaseRef(event.target.value)} className={inputClass}>{commits?.map(commitOption)}</select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block font-medium">Newer commit</span>
            <select value={headRef} onChange={(event) => setHeadRef(event.target.value)} className={inputClass}>{commits?.map(commitOption)}</select>
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <input type="checkbox" checked={withAi} onChange={(event) => setWithAi(event.target.checked)} />
            Add an AI review (uses one free AI request)
          </label>
          <div className="md:col-span-2">
            <Button type="submit" variant="primary" busy={busy} disabled={baseRef === headRef}>Compare</Button>
            {baseRef === headRef && <span className="ml-2 text-xs text-ink-3">Choose two different commits.</span>}
          </div>
        </form>
      )}
      {error && <ErrorNotice error={new Error(error)} />}
      {comparison && <Results comparison={comparison} projectId={project.id} />}
      {history && history.length > 0 && (
        <Card title="Earlier comparisons">
          <ul className="divide-y divide-line">
            {history.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-3 py-1.5 text-sm">
                <button type="button" onClick={() => openHistory(item.id)} className="font-mono text-xs text-accent hover:underline">
                  {shortSha(item.base_commit)} to {shortSha(item.head_commit)}
                </button>
                <SeverityBadge severity={item.risk.level} />
                <span className="text-xs text-ink-3">{item.stats.files_changed} {item.stats.files_changed === 1 ? "file" : "files"}, {formatDate(item.created_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
