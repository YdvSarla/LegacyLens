"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import useSWR from "swr";
import { useProject } from "@/components/project-context";
import {
  Badge,
  CodeLink,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Pager,
  SearchInput,
  SeverityBadge,
  TableSkeleton,
  TableWrap,
  inputClass,
  tdClass,
  thClass,
} from "@/components/ui";
import { fetcher } from "@/lib/api";
import type { EndpointItem, Issue, ModelItem, PackageItem, Page, SymbolItem } from "@/lib/types";

const TABS = [
  { key: "symbols", label: "Functions and classes" },
  { key: "endpoints", label: "APIs" },
  { key: "models", label: "Models" },
  { key: "dependencies", label: "Dependencies" },
  { key: "issues", label: "Issues" },
];
const PAGE_SIZE = 50;

const METHOD_TONE: Record<string, "good" | "accent" | "warning" | "critical" | "neutral"> = {
  GET: "good",
  POST: "accent",
  PUT: "warning",
  PATCH: "warning",
  DELETE: "critical",
};

function pageUrl(base: string, offset: number, filters: Record<string, string>) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return `${base}?${params}`;
}

function Symbols() {
  const { project } = useProject();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading } = useSWR<Page<SymbolItem>>(pageUrl(`/api/projects/${project.id}/symbols`, offset, { q: query, kind }), fetcher, { keepPreviousData: true });

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <SearchInput label="Search by name" value={query} onChange={(value) => { setQuery(value); setOffset(0); }} className="w-full max-w-xs" />
        <select aria-label="Filter by kind" value={kind} onChange={(event) => { setKind(event.target.value); setOffset(0); }} className={`${inputClass} w-auto`}>
          <option value="">All kinds</option>
          {["class", "function", "method", "interface", "trait", "enum"].map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </div>
      {error && <ErrorNotice error={error} />}
      {isLoading && !data && <TableSkeleton />}
      {data?.total === 0 && <EmptyState title="Nothing matches this search" />}
      {data && data.total > 0 && (
        <>
          <TableWrap>
            <table className="w-full min-w-[40rem]">
              <thead>
                <tr>
                  <th className={thClass}>Name</th>
                  <th className={thClass}>Kind</th>
                  <th className={`${thClass} text-right`}>Complexity</th>
                  <th className={`${thClass} text-right`}>Lines</th>
                  <th className={thClass}>Location</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((symbol) => (
                  <tr key={symbol.id}>
                    <td className={tdClass}>
                      <span className="font-mono text-xs break-all">{symbol.qualified_name}</span>
                      {symbol.docstring && <p className="mt-0.5 line-clamp-2 max-w-xl text-xs text-ink-3">{symbol.docstring}</p>}
                    </td>
                    <td className={tdClass}><Badge>{symbol.kind}</Badge></td>
                    <td className={`${tdClass} text-right tabular-nums`}>{symbol.kind === "function" || symbol.kind === "method" ? symbol.complexity : ""}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{symbol.loc}</td>
                    <td className={tdClass}><CodeLink projectId={project.id} path={symbol.path} line={symbol.start_line} endLine={symbol.end_line} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <Pager total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function Endpoints() {
  const { project } = useProject();
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading } = useSWR<Page<EndpointItem>>(pageUrl(`/api/projects/${project.id}/endpoints`, offset, { q: query }), fetcher, { keepPreviousData: true });
  return (
    <div>
      <SearchInput label="Search by path or handler" value={query} onChange={(value) => { setQuery(value); setOffset(0); }} className="mb-3 w-full max-w-sm" />
      {error && <ErrorNotice error={error} />}
      {isLoading && !data && <TableSkeleton />}
      {data?.total === 0 && (
        <EmptyState title={query ? "Nothing matches this search" : "No HTTP endpoints were found"}>
          {!query && "LegacyLens recognizes routes in Laravel, Drupal, Express, NestJS, Flask, FastAPI and Django, and plain PHP scripts that read request data."}
        </EmptyState>
      )}
      {data && data.total > 0 && (
        <>
          <TableWrap>
            <table className="w-full min-w-[44rem]">
              <thead>
                <tr>
                  <th className={thClass}>Method</th>
                  <th className={thClass}>Path</th>
                  <th className={thClass}>Handler</th>
                  <th className={thClass}>Guards</th>
                  <th className={thClass}>Location</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((endpoint) => (
                  <tr key={endpoint.id}>
                    <td className={tdClass}><Badge tone={METHOD_TONE[endpoint.method] ?? "neutral"}>{endpoint.method}</Badge></td>
                    <td className={`${tdClass} font-mono text-xs break-all`}>{endpoint.path}</td>
                    <td className={`${tdClass} font-mono text-xs break-all`}>{endpoint.handler}</td>
                    <td className={`${tdClass} text-xs break-words`}>{endpoint.middleware.join(", ")}</td>
                    <td className={tdClass}><CodeLink projectId={project.id} path={endpoint.file} line={endpoint.line} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <Pager total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function Models() {
  const { project } = useProject();
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading } = useSWR<Page<ModelItem>>(pageUrl(`/api/projects/${project.id}/models`, offset, { q: query }), fetcher, { keepPreviousData: true });
  return (
    <div>
      <SearchInput label="Search by model or table" value={query} onChange={(value) => { setQuery(value); setOffset(0); }} className="mb-3 w-full max-w-sm" />
      {error && <ErrorNotice error={error} />}
      {isLoading && !data && <TableSkeleton />}
      {data?.total === 0 && <EmptyState title={query ? "Nothing matches this search" : "No data models were found"} />}
      {data && data.total > 0 && (
        <>
          <ul className="grid gap-3 lg:grid-cols-2">
            {data.items.map((model) => (
              <li key={model.id} className="min-w-0 rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm font-medium break-all">{model.name}</span>
                  <Badge>{model.framework}</Badge>
                  {model.table_name && model.table_name !== model.name && <span className="text-xs text-ink-3">Table {model.table_name}</span>}
                </div>
                <div className="mt-1"><CodeLink projectId={project.id} path={model.file} line={model.line} /></div>
                {model.fields.length > 0 && (
                  <ul className="mt-2 flex flex-wrap gap-1">
                    {model.fields.map((field) => (
                      <li key={field.name} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs break-all">
                        {field.name} <span className="text-ink-3">{field.type}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {model.relations.length > 0 && (
                  <ul className="mt-2 space-y-0.5 text-xs text-ink-2">
                    {model.relations.map((relation) => (
                      <li key={relation.name} className="break-words"><span className="font-mono">{relation.name}</span>: {relation.type} of <span className="font-mono break-all">{relation.target}</span></li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          <Pager total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function Dependencies() {
  const { project } = useProject();
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading } = useSWR<Page<PackageItem>>(pageUrl(`/api/projects/${project.id}/dependencies`, offset, { q: query }), fetcher, { keepPreviousData: true });
  return (
    <div>
      <SearchInput label="Search packages" value={query} onChange={(value) => { setQuery(value); setOffset(0); }} className="mb-3 w-full max-w-sm" />
      {error && <ErrorNotice error={error} />}
      {isLoading && !data && <TableSkeleton />}
      {data?.total === 0 && (
        <EmptyState title={query ? "Nothing matches this search" : "No package manifests were found"}>
          {!query && "LegacyLens reads composer.json, package.json, requirements.txt and pyproject.toml."}
        </EmptyState>
      )}
      {data && data.total > 0 && (
        <>
          <TableWrap>
            <table className="w-full min-w-[34rem]">
              <thead>
                <tr>
                  <th className={thClass}>Package</th>
                  <th className={thClass}>Version</th>
                  <th className={thClass}>Ecosystem</th>
                  <th className={thClass}>Scope</th>
                  <th className={thClass}>Declared in</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td className={`${tdClass} font-mono text-xs break-all`}>{item.name}</td>
                    <td className={`${tdClass} font-mono text-xs`}>{item.version ?? "any"}</td>
                    <td className={tdClass}>{item.ecosystem}</td>
                    <td className={tdClass}>{item.is_dev ? <Badge>dev</Badge> : <Badge tone="accent">runtime</Badge>}</td>
                    <td className={tdClass}><CodeLink projectId={project.id} path={item.manifest_path} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <Pager total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function Issues() {
  const { project } = useProject();
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading } = useSWR<Page<Issue>>(pageUrl(`/api/projects/${project.id}/issues`, offset, { severity, category }), fetcher, { keepPreviousData: true });
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <select aria-label="Filter by severity" value={severity} onChange={(event) => { setSeverity(event.target.value); setOffset(0); }} className={`${inputClass} w-auto`}>
          <option value="">All severities</option>
          {["critical", "high", "medium", "low"].map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <select aria-label="Filter by category" value={category} onChange={(event) => { setCategory(event.target.value); setOffset(0); }} className={`${inputClass} w-auto`}>
          <option value="">All categories</option>
          {["security", "maintainability", "reliability"].map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </div>
      {error && <ErrorNotice error={error} />}
      {isLoading && !data && <TableSkeleton />}
      {data?.total === 0 && <EmptyState title="No issues match these filters" />}
      {data && data.total > 0 && (
        <>
          <TableWrap>
            <table className="w-full min-w-[40rem]">
              <thead>
                <tr>
                  <th className={thClass}>Severity</th>
                  <th className={thClass}>Issue</th>
                  <th className={thClass}>Rule</th>
                  <th className={thClass}>Location</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((issue) => (
                  <tr key={issue.id}>
                    <td className={tdClass}><SeverityBadge severity={issue.severity} /></td>
                    <td className={`${tdClass} break-words`}>{issue.message}</td>
                    <td className={`${tdClass} font-mono text-xs`}>{issue.rule}</td>
                    <td className={tdClass}>{issue.path && <CodeLink projectId={project.id} path={issue.path} line={issue.line} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <Pager total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

function Inventory() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = TABS.some((item) => item.key === params.get("tab")) ? params.get("tab")! : "symbols";
  return (
    <div className="space-y-4">
      <PageHeader title="Code inventory" description="Everything LegacyLens found in the code. Each row links to the exact lines." />
      <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => router.replace(`${pathname}?tab=${item.key}`)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm whitespace-nowrap ${tab === item.key ? "border-accent font-medium" : "border-transparent text-ink-2 hover:text-ink"}`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="rounded-xl border border-line bg-surface p-4">
        {tab === "symbols" && <Symbols />}
        {tab === "endpoints" && <Endpoints />}
        {tab === "models" && <Models />}
        {tab === "dependencies" && <Dependencies />}
        {tab === "issues" && <Issues />}
      </div>
    </div>
  );
}

export default function InventoryPage() {
  return <Suspense fallback={<TableSkeleton />}><Inventory /></Suspense>;
}
