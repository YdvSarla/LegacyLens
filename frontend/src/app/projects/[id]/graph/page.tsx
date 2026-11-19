"use client";

import type { Core, ElementDefinition, EventObject } from "cytoscape";
import { ExternalLink, Focus, Maximize, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
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
  Skeleton,
  TableWrap,
  inputClass,
  tdClass,
  thClass,
  usePageTitle,
} from "@/components/ui";
import { fetcher } from "@/lib/api";
import { languageColor, languageLabel } from "@/lib/format";
import type { FileDetail, GraphData, GraphNode } from "@/lib/types";

type Level = "module" | "file" | "symbol";

/** Canvas drawing cannot use CSS variables, so they are resolved to real colors first. */
function resolveColor(value: string): string {
  const match = value.match(/var\((--[\w-]+)\)/);
  if (!match) return value;
  return getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || "#888888";
}

function applySelection(cy: Core, selected: string | null) {
  cy.elements().removeClass("faded selected highlighted");
  if (!selected) return;
  const node = cy.getElementById(selected);
  if (node.empty()) return;
  cy.elements().not(node.closedNeighborhood()).addClass("faded");
  node.addClass("selected");
  node.connectedEdges().addClass("highlighted");
}

function GraphCanvas({ data, selected, onSelect, layoutName, onReady }: {
  data: GraphData;
  selected: string | null;
  onSelect: (id: string | null) => void;
  layoutName: "fcose" | "breadthfirst";
  onReady: (cy: Core | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const selectedRef = useRef(selected);

  useEffect(() => {
    let cancelled = false;
    let instance: Core | null = null;
    (async () => {
      const [{ default: cytoscape }, { default: fcose }] = await Promise.all([import("cytoscape"), import("cytoscape-fcose")]);
      if (cancelled || !container.current) return;
      try {
        cytoscape.use(fcose);
      } catch {
        // Already registered by an earlier mount.
      }
      const maxLoc = Math.max(...data.nodes.map((node) => node.loc), 1);
      const elements: ElementDefinition[] = [
        ...data.nodes.map((node) => ({
          data: {
            id: node.id,
            label: node.label,
            color: resolveColor(languageColor(node.language)),
            size: 18 + Math.sqrt(node.loc / maxLoc) * 34,
          },
        })),
        ...data.edges.map((edge) => ({
          data: { id: edge.id, source: edge.source, target: edge.target, width: Math.min(1 + Math.log2(edge.weight + 1) * 0.5, 4) },
        })),
      ];
      const text = resolveColor("var(--text)");
      const surface = resolveColor("var(--surface)");
      const line = resolveColor("var(--border-strong)");
      const accent = resolveColor("var(--accent)");
      instance = cytoscape({
        container: container.current,
        elements,
        minZoom: 0.08,
        maxZoom: 3,
        style: [
          {
            selector: "node",
            style: {
              "background-color": "data(color)",
              width: "data(size)",
              height: "data(size)",
              label: "data(label)",
              color: text,
              "font-size": 10,
              "text-valign": "bottom",
              "text-margin-y": 3,
              "text-outline-color": surface,
              "text-outline-width": 2,
              "min-zoomed-font-size": 7,
              "border-width": 2,
              "border-color": surface,
            },
          },
          {
            selector: "edge",
            style: {
              width: "data(width)",
              "line-color": line,
              "target-arrow-color": line,
              "target-arrow-shape": "triangle",
              "arrow-scale": 0.8,
              "curve-style": "bezier",
              opacity: 0.8,
            },
          },
          { selector: ".faded", style: { opacity: 0.12 } },
          { selector: "node.selected", style: { "border-color": accent, "border-width": 4 } },
          { selector: "edge.highlighted", style: { "line-color": accent, "target-arrow-color": accent, opacity: 1 } },
        ],
        layout:
          layoutName === "fcose"
            ? ({ name: "fcose", animate: false, quality: data.nodes.length > 400 ? "draft" : "default", nodeRepulsion: 9000, idealEdgeLength: 90, randomize: true } as never)
            : { name: "breadthfirst", directed: true, spacingFactor: 1.15, animate: false },
      });
      instance.on("tap", "node", (event: EventObject) => onSelect(event.target.id()));
      instance.on("tap", (event: EventObject) => {
        if (event.target === instance) onSelect(null);
      });
      cyRef.current = instance;
      onReady(instance);
      applySelection(instance, selectedRef.current);
    })();
    return () => {
      cancelled = true;
      instance?.destroy();
      cyRef.current = null;
      onReady(null);
    };
    // A new data set or layout rebuilds the canvas. Selection is applied in the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, layoutName]);

  useEffect(() => {
    selectedRef.current = selected;
    if (cyRef.current) applySelection(cyRef.current, selected);
  }, [selected]);

  return (
    <div
      ref={container}
      role="img"
      aria-label={`Dependency graph with ${data.nodes.length} nodes and ${data.edges.length} links. The table below lists the same nodes.`}
      className="h-[60vh] min-h-96 w-full"
    />
  );
}

function NodePanel({ node, level, onFocus }: { node: GraphNode; level: Level; onFocus: (path: string) => void }) {
  const { project } = useProject();
  const { data, error } = useSWR<FileDetail>(
    level === "module" ? null : `/api/projects/${project.id}/file-detail?path=${encodeURIComponent(node.path)}`,
    fetcher,
  );
  return (
    <div className="space-y-3 text-sm">
      <div>
        <p className="font-mono text-xs font-medium break-all">{node.qualified_name ?? node.path}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <Badge>{languageLabel(node.language)}</Badge>
          <Badge>{node.kind ?? node.role}</Badge>
        </div>
      </div>
      {level !== "module" && (
        <div className="flex flex-wrap gap-2">
          <CodeLink projectId={project.id} path={node.path} line={node.line}>
            <span className="inline-flex items-center gap-1 font-sans text-sm"><ExternalLink className="size-3.5" aria-hidden /> Open code</span>
          </CodeLink>
          {level === "file" && (
            <button type="button" onClick={() => onFocus(node.path)} className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
              <Focus className="size-3.5" aria-hidden /> Focus here
            </button>
          )}
        </div>
      )}
      {level === "module" && <p className="text-ink-2">{node.files} files, {node.loc.toLocaleString("en-US")} lines of code.</p>}
      {error && <ErrorNotice error={error} />}
      {data && (
        <>
          <dl className="grid grid-cols-2 gap-2">
            <div><dt className="text-xs text-ink-3">Lines of code</dt><dd className="font-semibold">{data.loc}</dd></div>
            <div><dt className="text-xs text-ink-3">Complexity</dt><dd className="font-semibold">{data.complexity}</dd></div>
          </dl>
          <section>
            <h3 className="mb-1 text-xs font-medium text-ink-2">Used by {data.dependents.length} file(s)</h3>
            <ul className="max-h-40 space-y-0.5 overflow-y-auto">
              {data.dependents.map((item) => <li key={item.path}><CodeLink projectId={project.id} path={item.path} /></li>)}
            </ul>
          </section>
          <section>
            <h3 className="mb-1 text-xs font-medium text-ink-2">Depends on {data.dependencies.length} file(s)</h3>
            <ul className="max-h-40 space-y-0.5 overflow-y-auto">
              {data.dependencies.map((item) => <li key={item.path}><CodeLink projectId={project.id} path={item.path} /></li>)}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

export default function GraphPage() {
  usePageTitle("Dependency graph");
  const { project } = useProject();
  const [level, setLevel] = useState<Level>("file");
  const [language, setLanguage] = useState("");
  const [includeTests, setIncludeTests] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [depth, setDepth] = useState(2);
  const [layout, setLayout] = useState<"fcose" | "breadthfirst">("fcose");
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [cy, setCy] = useState<Core | null>(null);

  const params = new URLSearchParams({ level, limit: "500", depth: String(depth) });
  if (language) params.set("language", language);
  if (includeTests) params.set("include_tests", "true");
  if (focus && level !== "module") params.set("focus", focus);
  const { data, error, isLoading, mutate } = useSWR<GraphData>(`/api/projects/${project.id}/graph?${params}`, fetcher, { keepPreviousData: true });

  const languages = useMemo(() => [...new Set((data?.nodes ?? []).map((node) => node.language).filter(Boolean))] as string[], [data]);
  const selectedNode = data?.nodes.find((node) => node.id === selected) ?? null;
  const matches = useMemo(() => {
    const query = search.trim().toLowerCase();
    return query ? (data?.nodes ?? []).filter((node) => node.path.toLowerCase().includes(query) || node.label.toLowerCase().includes(query)).slice(0, 8) : [];
  }, [data, search]);
  const ranked = useMemo(() => [...(data?.nodes ?? [])].sort((a, b) => b.fan_in + b.fan_out - (a.fan_in + a.fan_out)).slice(0, 30), [data]);

  const handleSelect = useCallback((id: string | null) => setSelected(id), []);
  const selectAndCenter = useCallback((id: string) => {
    setSelected(id);
    setSearch("");
    const node = cy?.getElementById(id);
    if (node && !node.empty()) cy?.animate({ center: { eles: node }, zoom: 1.2, duration: 200 });
  }, [cy]);

  return (
    <div className="space-y-3">
      <PageHeader title="Dependency graph" description="Who uses what. Select a node to see its dependents, or focus on one file to cut through the noise." />
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs">
          <span className="mb-1 block text-ink-2">Detail</span>
          <select value={level} onChange={(event) => { setLevel(event.target.value as Level); setSelected(null); }} className={`${inputClass} w-auto`}>
            <option value="module">Modules</option>
            <option value="file">Files</option>
            <option value="symbol">Functions and classes</option>
          </select>
        </label>
        {level === "file" && (
          <label className="text-xs">
            <span className="mb-1 block text-ink-2">Language</span>
            <select value={language} onChange={(event) => setLanguage(event.target.value)} className={`${inputClass} w-auto`}>
              <option value="">All</option>
              {project.languages.map((item) => <option key={item.language} value={item.language}>{languageLabel(item.language)}</option>)}
            </select>
          </label>
        )}
        <label className="text-xs">
          <span className="mb-1 block text-ink-2">Layout</span>
          <select value={layout} onChange={(event) => setLayout(event.target.value as "fcose" | "breadthfirst")} className={`${inputClass} w-auto`}>
            <option value="fcose">Clustered</option>
            <option value="breadthfirst">Hierarchy</option>
          </select>
        </label>
        <div className="relative text-xs">
          <label htmlFor="graph-search" className="mb-1 block text-ink-2">Find a node</label>
          <input id="graph-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name or path" className={`${inputClass} w-56`} />
          {matches.length > 0 && (
            <ul className="absolute z-20 mt-1 w-72 rounded-lg border border-line-strong bg-surface p-1 shadow-lg">
              {matches.map((node) => (
                <li key={node.id}>
                  <button type="button" onClick={() => selectAndCenter(node.id)} className="w-full rounded px-2 py-1 text-left font-mono text-xs break-all hover:bg-surface-2">
                    {node.qualified_name ?? node.path}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {level !== "module" && (
          <label className="flex min-h-9 items-center gap-1.5 text-sm">
            <input type="checkbox" checked={includeTests} onChange={(event) => setIncludeTests(event.target.checked)} /> Include tests
          </label>
        )}
        <Button onClick={() => cy?.fit(undefined, 30)} aria-label="Fit graph to view">
          <Maximize className="size-4" aria-hidden /> Fit
        </Button>
      </div>

      {focus && level !== "module" && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line-strong bg-surface-2 p-2 text-sm">
          <span>Showing files within</span>
          <select aria-label="Focus depth" value={depth} onChange={(event) => setDepth(Number(event.target.value))} className="rounded border border-line-strong bg-surface px-1 py-0.5">
            {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <span>step(s) of <span className="font-mono text-xs break-all">{focus}</span></span>
          <Button variant="ghost" onClick={() => setFocus(null)}><RotateCcw className="size-3.5" aria-hidden /> Show all</Button>
        </div>
      )}
      {error && <ErrorNotice error={error} onRetry={() => mutate()} />}
      {data?.truncated && (
        <Notice tone="info">
          Showing the {data.nodes.length} most connected of {data.total_nodes} nodes. Filter by language or focus on a file to see the rest.
        </Notice>
      )}
      {isLoading && !data && <Skeleton className="h-[60vh]" />}
      {data && data.nodes.length === 0 && (
        <EmptyState title="No dependencies to show">
          {level === "symbol" ? "No calls between functions could be resolved." : "Try including tests or removing filters."}
        </EmptyState>
      )}
      {data && data.nodes.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-[1fr_20rem]">
          <div className="overflow-hidden rounded-xl border border-line bg-surface">
            <GraphCanvas data={data} selected={selected} onSelect={handleSelect} layoutName={layout} onReady={setCy} />
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-3 py-2 text-xs text-ink-2">
              {languages.map((name) => (
                <span key={name} className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-full" style={{ background: languageColor(name) }} aria-hidden />
                  {languageLabel(name)}
                </span>
              ))}
              <span className="ml-auto text-ink-3">{data.nodes.length} nodes, {data.edges.length} links. Size shows lines of code.</span>
            </div>
          </div>
          <Card title={selectedNode ? selectedNode.label : "Details"}>
            {selectedNode ? (
              <NodePanel node={selectedNode} level={level} onFocus={(path) => { setFocus(path); setLevel("file"); }} />
            ) : (
              <p className="text-sm text-ink-3">Select a node to see what it depends on and what depends on it.</p>
            )}
          </Card>
        </div>
      )}
      {data && data.nodes.length > 0 && (
        <details className="rounded-xl border border-line bg-surface p-3">
          <summary className="cursor-pointer text-sm font-medium">Most connected nodes as a table</summary>
          <TableWrap>
            <table className="mt-2 w-full min-w-[30rem]">
              <thead>
                <tr>
                  <th className={thClass}>Node</th>
                  <th className={`${thClass} text-right`}>Used by</th>
                  <th className={`${thClass} text-right`}>Depends on</th>
                  <th className={`${thClass} text-right`}>Lines</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((node) => (
                  <tr key={node.id}>
                    <td className={tdClass}>
                      <button type="button" onClick={() => selectAndCenter(node.id)} className="text-left font-mono text-xs break-all text-accent hover:underline">
                        {node.qualified_name ?? node.path}
                      </button>
                    </td>
                    <td className={`${tdClass} text-right tabular-nums`}>{node.fan_in}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{node.fan_out}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{node.loc || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </details>
      )}
    </div>
  );
}
