"use client";

import {
  Bot,
  FileCode2,
  FileText,
  FlaskConical,
  GitCompareArrows,
  LayoutDashboard,
  ListTree,
  Network,
  Settings,
  Shapes,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { ProjectContext } from "@/components/project-context";
import { useToast } from "@/components/toast";
import { Button, ErrorNotice, Notice, Skeleton, usePageTitle } from "@/components/ui";
import { errorMessage, fetcher, postJson } from "@/lib/api";
import type { Project } from "@/lib/types";

const NAV_GROUPS = [
  {
    label: "Understand",
    items: [
      { href: "", label: "Dashboard", Icon: LayoutDashboard },
      { href: "/architecture", label: "Architecture", Icon: Shapes },
      { href: "/inventory", label: "Code inventory", Icon: ListTree },
      { href: "/graph", label: "Dependency graph", Icon: Network },
      { href: "/code", label: "Source code", Icon: FileCode2 },
    ],
  },
  {
    label: "Maintain",
    items: [
      { href: "/assistant", label: "AI assistant", Icon: Bot },
      { href: "/compare", label: "Compare commits", Icon: GitCompareArrows },
      { href: "/tests", label: "Regression tests", Icon: FlaskConical },
      { href: "/docs", label: "Documentation", Icon: FileText },
    ],
  },
  {
    label: "Project",
    items: [{ href: "/settings", label: "Settings", Icon: Settings }],
  },
];

function Progress({ project }: { project: Project }) {
  const percent = project.job?.progress ?? 0;
  return (
    <div className="mx-auto mt-[12vh] max-w-lg rounded-xl border border-line bg-surface p-6 text-center">
      <h1 className="font-semibold break-words">Analyzing {project.name}</h1>
      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Analysis progress"
        className="mt-4 h-2 overflow-hidden rounded"
        style={{ background: "var(--meter-track)" }}
      >
        <div className="h-full rounded transition-all" style={{ width: `${percent}%`, background: "var(--series-1)" }} />
      </div>
      <p className="mt-2 text-sm text-ink-2">{project.job?.message ?? "Waiting to start."}</p>
      <p className="mt-1 text-xs text-ink-3">You can leave this page. The analysis keeps running on the server.</p>
    </div>
  );
}

function FailedImport({ project, onRetried }: { project: Project; onRetried: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const canRetry = project.source_type === "github";
  async function retry() {
    setBusy(true);
    try {
      await postJson(`/api/projects/${project.id}/reanalyze`, {});
      onRetried();
    } catch (caught) {
      toast.error(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mx-auto mt-[10vh] max-w-lg space-y-3">
      <h1 className="text-lg font-semibold break-words">{project.name} could not be imported</h1>
      <ErrorNotice error={new Error(project.error ?? "The import failed.")} />
      <div className="flex flex-wrap gap-2">
        {canRetry && <Button variant="primary" busy={busy} onClick={retry}>Try the import again</Button>}
        <Link href={`/projects/${project.id}/settings`} className="inline-flex min-h-9 items-center rounded-lg border border-line-strong px-3 py-1.5 text-sm font-medium hover:bg-surface-2">
          Project settings
        </Link>
      </div>
      {!canRetry && <p className="text-sm text-ink-2">Check the archive and add it again as a new project, then delete this one in settings.</p>}
    </div>
  );
}

function FailedUpdate({ project, onRetried }: { project: Project; onRetried: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function retry() {
    setBusy(true);
    try {
      await postJson(`/api/projects/${project.id}/reanalyze`, {});
      onRetried();
    } catch (caught) {
      toast.error(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mb-4">
      <Notice tone="warning">
        <p className="font-medium">The last update failed. These results are from the previous successful analysis.</p>
        <p className="mt-0.5 break-words">{project.error}</p>
        <Button className="mt-2" busy={busy} onClick={retry}>Analyze again</Button>
      </Notice>
    </div>
  );
}

export default function ProjectLayout({ children }: { children: ReactNode }) {
  const { id } = useParams<{ id: string }>();
  const pathname = usePathname();
  const { data: project, error, mutate } = useSWR<Project>(`/api/projects/${id}`, fetcher, {
    // Also polls while the search index is still being built in the background.
    refreshInterval: (latest) =>
      latest && (!["ready", "failed"].includes(latest.status) || ["queued", "running"].includes(latest.job?.status ?? "")) ? 2000 : 0,
  });
  const context = useMemo(() => (project ? { project, refresh: () => void mutate() } : null), [project, mutate]);
  usePageTitle(project?.name ?? "Project");

  if (error) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6">
        <ErrorNotice error={error} onRetry={() => mutate()} />
        <Link href="/projects" className="mt-3 inline-block text-sm text-accent hover:underline">Back to projects</Link>
      </div>
    );
  }
  if (!project || !context) {
    return (
      <div className="flex flex-1">
        <div className="hidden w-56 border-r border-line bg-surface p-3 md:block"><Skeleton className="h-64" /></div>
        <div className="flex-1 space-y-3 p-6"><Skeleton className="h-8 w-56" /><Skeleton className="h-48" /></div>
      </div>
    );
  }

  const busy = !["ready", "failed"].includes(project.status);
  // A re-analysis keeps earlier results visible, but a first import has nothing to show yet.
  const nothingToShow = project.metrics === null;
  const onSettings = pathname.endsWith("/settings");

  return (
    <ProjectContext.Provider value={context}>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="shrink-0 border-b border-line bg-surface md:sticky md:top-13 md:h-[calc(100vh-3.25rem)] md:w-56 md:overflow-y-auto md:border-r md:border-b-0">
          <nav aria-label="Project" className="flex gap-0.5 overflow-x-auto p-2 md:flex-col md:gap-3">
            {NAV_GROUPS.map((group) => (
              <div key={group.label} className="flex gap-0.5 md:flex-col">
                <p className="hidden px-2.5 pt-1 pb-1 text-xs font-medium text-ink-3 md:block">{group.label}</p>
                {group.items.map(({ href, label, Icon }) => {
                  const target = `/projects/${id}${href}`;
                  const active = href === "" ? pathname === target : pathname.startsWith(target);
                  return (
                    <Link
                      key={href}
                      href={target}
                      aria-current={active ? "page" : undefined}
                      className={`flex shrink-0 items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm whitespace-nowrap ${active ? "bg-surface-3 font-medium" : "text-ink-2 hover:bg-surface-2"}`}
                    >
                      <Icon className="size-4 shrink-0" aria-hidden />
                      {label}
                    </Link>
                  );
                })}
              </div>
            ))}
          </nav>
          {project.local_ai_only && (
            <p className="mx-3 mb-3 hidden items-center gap-1.5 rounded-lg bg-good-soft px-2 py-1.5 text-xs md:flex">
              <ShieldCheck className="size-3.5 shrink-0" aria-hidden /> Local AI only
            </p>
          )}
        </aside>
        <main className="min-w-0 flex-1 p-4 sm:p-6">
          {busy && nothingToShow && <Progress project={project} />}
          {!busy && nothingToShow && !onSettings && <FailedImport project={project} onRetried={() => void mutate()} />}
          {busy && !nothingToShow && (
            <p role="status" className="mb-4 rounded-lg border border-line-strong bg-surface-2 p-2.5 text-sm">
              Updating analysis: {project.job?.message ?? "starting"} ({project.job?.progress ?? 0}%)
            </p>
          )}
          {project.status === "failed" && !nothingToShow && <FailedUpdate project={project} onRetried={() => void mutate()} />}
          {project.status === "ready" && project.search_index?.status === "building" && project.job?.status === "running" && (
            <p role="status" className="mb-4 flex flex-wrap items-center gap-x-2 rounded-lg border border-line bg-surface p-2.5 text-sm text-ink-2">
              <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden />
              Building the semantic search index ({project.job.progress}%). Everything else works now, and search finds exact names meanwhile.
            </p>
          )}
          {!nothingToShow || (onSettings && !busy) ? children : null}
        </main>
      </div>
    </ProjectContext.Provider>
  );
}
