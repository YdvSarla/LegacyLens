"use client";

import { ChevronDown, ChevronRight, FileCode2, FileText, Folder } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import useSWR from "swr";
import { CodeView } from "@/components/code-view";
import { CopyButton } from "@/components/copy-button";
import { useProject } from "@/components/project-context";
import { Badge, Card, CodeLink, EmptyState, ErrorNotice, SearchInput, Skeleton, TableSkeleton, usePageTitle } from "@/components/ui";
import { fetcher } from "@/lib/api";
import { codeHref } from "@/lib/citations";
import { languageLabel } from "@/lib/format";
import type { FileDetail, FileItem, Page } from "@/lib/types";

interface TreeLevel {
  folders: { name: string; path: string; files: number }[];
  files: { name: string; path: string; language: string; loc: number; role: string }[];
}

const CODE_LANGUAGES = new Set(["php", "python", "javascript", "typescript"]);

function FileRow({ projectId, path, name, language, loc, active, depth }: {
  projectId: string;
  path: string;
  name: string;
  language: string;
  loc: number;
  active: boolean;
  depth: number;
}) {
  const Icon = CODE_LANGUAGES.has(language) ? FileCode2 : FileText;
  return (
    <Link
      href={codeHref(projectId, path)}
      aria-current={active ? "page" : undefined}
      style={{ paddingLeft: `${depth * 0.75 + 0.375}rem` }}
      className={`flex items-center gap-1.5 rounded py-1 pr-1.5 text-xs ${active ? "bg-surface-3 font-medium" : "hover:bg-surface-2"}`}
    >
      <Icon className="size-3.5 shrink-0 text-ink-3" aria-hidden />
      <span className="min-w-0 flex-1 truncate font-mono" title={path}>{name}</span>
      <span className="shrink-0 text-ink-3 tabular-nums">{loc}</span>
    </Link>
  );
}

/** Loads one folder at a time, so a project with thousands of files opens instantly. */
function TreeFolder({ projectId, directory, depth, activePath }: { projectId: string; directory: string; depth: number; activePath: string | null }) {
  const { data, error } = useSWR<TreeLevel>(`/api/projects/${projectId}/tree?directory=${encodeURIComponent(directory)}`, fetcher, { revalidateOnFocus: false });
  const [openFolders, setOpenFolders] = useState<Record<string, boolean>>({});
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <div className="py-1" style={{ paddingLeft: `${depth * 0.75 + 0.375}rem` }}><Skeleton className="h-4 w-32" /></div>;
  return (
    <ul>
      {data.folders.map((folder) => {
        // A folder on the way to the current file opens by itself.
        const open = openFolders[folder.path] ?? Boolean(activePath?.startsWith(`${folder.path}/`));
        const Chevron = open ? ChevronDown : ChevronRight;
        return (
          <li key={folder.path}>
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpenFolders((current) => ({ ...current, [folder.path]: !open }))}
              style={{ paddingLeft: `${depth * 0.75 + 0.125}rem` }}
              className="flex w-full items-center gap-1 rounded py-1 pr-1.5 text-left text-xs hover:bg-surface-2"
            >
              <Chevron className="size-3.5 shrink-0 text-ink-3" aria-hidden />
              <Folder className="size-3.5 shrink-0 text-ink-3" aria-hidden />
              <span className="min-w-0 flex-1 truncate font-mono">{folder.name}</span>
              <span className="shrink-0 text-ink-3 tabular-nums">{folder.files}</span>
            </button>
            {open && <TreeFolder projectId={projectId} directory={folder.path} depth={depth + 1} activePath={activePath} />}
          </li>
        );
      })}
      {data.files.map((file) => (
        <li key={file.path}>
          <FileRow projectId={projectId} path={file.path} name={file.name} language={file.language} loc={file.loc} active={file.path === activePath} depth={depth} />
        </li>
      ))}
    </ul>
  );
}

function FileBrowser({ activePath }: { activePath: string | null }) {
  const { project } = useProject();
  const [query, setQuery] = useState("");
  const { data, error } = useSWR<Page<FileItem>>(
    query ? `/api/projects/${project.id}/files?limit=100&q=${encodeURIComponent(query)}` : null,
    fetcher,
    { keepPreviousData: true },
  );
  return (
    <Card title="Files">
      <SearchInput label="Find a file" value={query} onChange={setQuery} className="mb-2" />
      <div className="scroll-thin max-h-[65vh] overflow-y-auto">
        {!query && <TreeFolder projectId={project.id} directory="" depth={0} activePath={activePath} />}
        {query && error && <ErrorNotice error={error} />}
        {query && data?.items.length === 0 && <p className="py-2 text-sm text-ink-3">No matching files.</p>}
        {query && data?.items.map((file) => (
          <FileRow key={file.path} projectId={project.id} path={file.path} name={file.path} language={file.language} loc={file.loc} active={file.path === activePath} depth={0} />
        ))}
        {query && data && data.total > data.items.length && <p className="mt-2 text-xs text-ink-3">Showing {data.items.length} of {data.total}. Type more to narrow the list.</p>}
      </div>
    </Card>
  );
}

function CodeBrowser() {
  usePageTitle("Source code");
  const { project } = useProject();
  const search = useSearchParams();
  const path = search.get("path");
  const start = Number(search.get("start")) || undefined;
  const end = Number(search.get("end")) || undefined;
  const { data, error, isLoading } = useSWR<FileDetail>(
    path ? `/api/projects/${project.id}/source?path=${encodeURIComponent(path)}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  const reference = path ? (start ? `${path}:${start}${end && end !== start ? `-${end}` : ""}` : path) : "";

  return (
    <div className="grid gap-4 xl:grid-cols-[20rem_minmax(0,1fr)]">
      <div className="min-w-0"><FileBrowser activePath={path} /></div>
      <div className="min-w-0 space-y-3">
        {!path && <EmptyState title="Choose a file">Pick a file from the list, or follow a reference from anywhere in LegacyLens.</EmptyState>}
        {path && (
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-sm font-medium break-all">{path}</h1>
            <CopyButton value={reference} label="Copy reference" />
            {data && <Badge>{languageLabel(data.language)}</Badge>}
            {data && <Badge>{data.role}</Badge>}
            {start && <span className="text-xs text-ink-3">Line {start}{end && end !== start ? ` to ${end}` : ""}</span>}
          </div>
        )}
        {error && <ErrorNotice error={error} />}
        {isLoading && <TableSkeleton rows={12} />}
        {data?.content !== undefined && <CodeView content={data.content} language={data.language} start={start} end={end} />}
        {data && (
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title={`Symbols (${data.symbols.length})`}>
              <ul className="scroll-thin max-h-60 space-y-0.5 overflow-y-auto">
                {data.symbols.map((symbol) => (
                  <li key={symbol.id}>
                    <Link href={codeHref(project.id, data.path, symbol.start_line, symbol.end_line)} className="block rounded px-1 py-0.5 font-mono text-xs break-all hover:bg-surface-2">
                      <span className="text-ink-3">{symbol.kind}</span> {symbol.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
            <Card title={`Used by (${data.dependents.length})`}>
              <ul className="scroll-thin max-h-60 space-y-1 overflow-y-auto">
                {data.dependents.map((item) => <li key={item.path}><CodeLink projectId={project.id} path={item.path} /></li>)}
              </ul>
            </Card>
            <Card title={`Depends on (${data.dependencies.length})`}>
              <ul className="scroll-thin max-h-60 space-y-1 overflow-y-auto">
                {data.dependencies.map((item) => <li key={item.path}><CodeLink projectId={project.id} path={item.path} /></li>)}
              </ul>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

export default function CodePage() {
  return <Suspense fallback={<TableSkeleton />}><CodeBrowser /></Suspense>;
}
