"use client";

import { Bot, FileText, GitCompareArrows, type LucideIcon } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { BarList, OrderedColumns, ScoreMeter, StatTile } from "@/components/charts";
import { useProject } from "@/components/project-context";
import {
  Badge,
  Card,
  CodeLink,
  ErrorNotice,
  Notice,
  PageHeader,
  SeverityBadge,
  Skeleton,
  TableWrap,
  tdClass,
  thClass,
} from "@/components/ui";
import { fetcher } from "@/lib/api";
import { formatDate, languageColor, languageLabel, shortSha } from "@/lib/format";
import type { Dashboard, Severity } from "@/lib/types";

const RAMP = ["var(--ramp-1)", "var(--ramp-2)", "var(--ramp-3)", "var(--ramp-4)", "var(--ramp-5)"];
const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low"];
const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "var(--critical)",
  high: "var(--serious)",
  medium: "var(--warning)",
  low: "var(--border-strong)",
};

function QuickLink({ href, Icon, label }: { href: string; Icon: LucideIcon; label: string }) {
  return (
    <Link href={href} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium hover:bg-surface-2">
      <Icon className="size-4 text-ink-2" aria-hidden /> {label}
    </Link>
  );
}

function DashboardSkeleton() {
  return (
    <div role="status" aria-label="Loading dashboard" className="space-y-4">
      <Skeleton className="h-9 w-64" />
      <div className="grid gap-4 lg:grid-cols-[minmax(16rem,20rem)_1fr]">
        <Skeleton className="h-72" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="h-24" />)}
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-52" />)}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { project } = useProject();
  const { data, error, isLoading, mutate } = useSWR<Dashboard>(
    // Refetches when a re-analysis finishes.
    [`/api/projects/${project.id}/dashboard`, project.analyzed_at],
    ([path]: [string, unknown]) => fetcher<Dashboard>(path),
  );
  if (isLoading) return <DashboardSkeleton />;
  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return null;
  const { metrics, quality, hotspots } = data;
  const base = `/projects/${project.id}`;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Dashboard"
        description={
          <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
            {project.head_commit && <>Commit {shortSha(project.head_commit)} · </>}
            Analyzed {formatDate(project.analyzed_at)}
            {data.frameworks.map((framework) => <Badge key={framework} tone="accent">{framework}</Badge>)}
          </span>
        }
        actions={
          <>
            <QuickLink href={`${base}/assistant`} Icon={Bot} label="Ask the assistant" />
            <QuickLink href={`${base}/compare`} Icon={GitCompareArrows} label="Compare commits" />
            <QuickLink href={`${base}/docs`} Icon={FileText} label="Generate docs" />
          </>
        }
      />

      {data.search_index?.status === "failed" && <Notice tone="warning">{data.search_index.error}</Notice>}
      {data.search_index?.embedded !== undefined &&
        data.search_index.chunks !== undefined &&
        data.search_index.embedded < data.search_index.chunks && (
          <Notice tone="info">
            This is a large project, so semantic search covers the most important {data.search_index.embedded.toLocaleString("en-US")} of{" "}
            {data.search_index.chunks.toLocaleString("en-US")} code sections. The rest, mostly tests, can still be found by exact name.
          </Notice>
        )}
      {metrics.files_with_syntax_errors > 0 && (
        <Notice tone="info">
          The parser recovered from syntax problems in {metrics.files_with_syntax_errors} of {metrics.parsed_files} files, so
          results for those files may be incomplete.
        </Notice>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(16rem,20rem)_1fr]">
        <Card title="Code quality score">
          <div className="flex items-baseline gap-2">
            <span className="text-5xl font-semibold">{quality.scores.overall}</span>
            <span className="text-sm text-ink-2">of 100, grade {quality.scores.grade}</span>
          </div>
          <div className="mt-4 space-y-3">
            <ScoreMeter label="Security" value={quality.scores.categories.security} />
            <ScoreMeter label="Maintainability" value={quality.scores.categories.maintainability} />
            <ScoreMeter label="Reliability" value={quality.scores.categories.reliability} />
          </div>
          <p className="mt-3 text-xs text-ink-3">Based on weighted findings per 1,000 lines of code.</p>
        </Card>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Lines of code" value={metrics.loc} sub={`${metrics.source_files} source files`} />
          <StatTile label="Functions and methods" value={metrics.functions + metrics.methods} sub={`Average complexity ${metrics.avg_complexity}`} />
          <StatTile label="Classes" value={metrics.classes} sub={`${metrics.interfaces + metrics.traits} interfaces and traits`} />
          <StatTile label="HTTP endpoints" value={metrics.endpoints} />
          <StatTile label="Data models" value={metrics.data_models} />
          <StatTile label="Packages" value={metrics.packages} />
          <StatTile label="Circular imports" value={metrics.cycles} sub="Groups of files" />
          <StatTile label="Test files" value={metrics.test_files} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Lines of code by language">
          <BarList
            ariaLabel="Lines of code by language"
            items={data.languages.map((language) => ({
              key: language.language,
              label: languageLabel(language.language),
              value: language.loc,
              color: languageColor(language.language),
              detail: `lines in ${language.files} ${languageLabel(language.language)} files`,
            }))}
          />
        </Card>
        <Card title="Issues by severity">
          <BarList
            ariaLabel="Issues by severity"
            items={SEVERITY_ORDER.map((severity) => ({
              key: severity,
              label: severity.charAt(0).toUpperCase() + severity.slice(1),
              value: quality.by_severity[severity] ?? 0,
              color: SEVERITY_COLOR[severity],
              detail: `${severity} issues`,
            }))}
          />
          <p className="mt-2 text-xs text-ink-3">{quality.total_issues} issues in total.</p>
        </Card>
        <Card title="Function complexity">
          <OrderedColumns
            ariaLabel="Number of functions by cyclomatic complexity"
            xLabel="Cyclomatic complexity"
            items={data.complexity_distribution.map((bucket, index) => ({
              key: bucket.bucket,
              label: bucket.bucket,
              value: bucket.count,
              color: RAMP[index],
            }))}
          />
        </Card>
      </div>

      <Card title="Most serious issues">
        {data.top_issues.length === 0 ? (
          <p className="text-sm text-ink-3">No critical or high severity issues were found.</p>
        ) : (
          <TableWrap>
            <table className="w-full min-w-[36rem]">
              <thead>
                <tr><th className={thClass}>Severity</th><th className={thClass}>Issue</th><th className={thClass}>Location</th></tr>
              </thead>
              <tbody>
                {data.top_issues.map((issue, index) => (
                  <tr key={index}>
                    <td className={tdClass}><SeverityBadge severity={issue.severity} /></td>
                    <td className={tdClass}>{issue.message}</td>
                    <td className={tdClass}>{issue.path && <CodeLink projectId={project.id} path={issue.path} line={issue.line} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Most complex functions">
          <TableWrap>
            <table className="w-full min-w-[24rem]">
              <thead>
                <tr><th className={thClass}>Function</th><th className={`${thClass} text-right`}>Complexity</th><th className={`${thClass} text-right`}>Lines</th></tr>
              </thead>
              <tbody>
                {hotspots.complex_functions.map((item) => (
                  <tr key={`${item.path}:${item.line}`}>
                    <td className={tdClass}>
                      <CodeLink projectId={project.id} path={item.path} line={item.line}>{item.name}</CodeLink>
                      <div className="text-xs break-all text-ink-3">{item.path}</div>
                    </td>
                    <td className={`${tdClass} text-right tabular-nums`}>{item.complexity}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{item.loc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
        <Card title="Files most other files depend on">
          {hotspots.most_depended_on.length === 0 ? (
            <p className="text-sm text-ink-3">No dependencies between files were found.</p>
          ) : (
            <TableWrap>
              <table className="w-full min-w-[20rem]">
                <thead>
                  <tr><th className={thClass}>File</th><th className={`${thClass} text-right`}>Dependents</th></tr>
                </thead>
                <tbody>
                  {hotspots.most_depended_on.map((item) => (
                    <tr key={item.path}>
                      <td className={tdClass}><CodeLink projectId={project.id} path={item.path} /></td>
                      <td className={`${tdClass} text-right tabular-nums`}>{item.dependents}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      </div>
    </div>
  );
}
