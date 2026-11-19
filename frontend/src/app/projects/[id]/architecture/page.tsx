"use client";

import useSWR from "swr";
import { BarList } from "@/components/charts";
import { useProject } from "@/components/project-context";
import { Badge, Card, CodeLink, ErrorNotice, Notice, PageHeader, TableSkeleton, TableWrap, tdClass, thClass } from "@/components/ui";
import { fetcher } from "@/lib/api";
import { languageColor, languageLabel } from "@/lib/format";
import type { Architecture } from "@/lib/types";

export default function ArchitecturePage() {
  const { project } = useProject();
  const { data, error, isLoading, mutate } = useSWR<Architecture>(`/api/projects/${project.id}/architecture`, fetcher);
  if (isLoading) return <TableSkeleton rows={10} />;
  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return null;
  const runtimes = Object.entries(data.runtime ?? {}).filter(([, value]) => value);
  const links = data.module_graph?.links ?? [];
  const modules = data.module_graph?.modules ?? [];

  return (
    <div className="space-y-4">
      <PageHeader title="Architecture" description="How this codebase is organized, based on its files, imports and frameworks." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Technology">
          <dl className="space-y-2.5 text-sm">
            <div>
              <dt className="mb-1 text-xs text-ink-3">Frameworks</dt>
              <dd className="flex flex-wrap gap-1">
                {data.frameworks.length ? data.frameworks.map((name) => <Badge key={name} tone="accent">{name}</Badge>) : <span className="text-ink-3">None detected</span>}
              </dd>
            </div>
            <div>
              <dt className="mb-1 text-xs text-ink-3">Languages</dt>
              <dd className="flex flex-wrap gap-x-3 gap-y-1">
                {data.languages.map((item) => (
                  <span key={item.language} className="flex items-center gap-1">
                    <span className="size-2 rounded-sm" style={{ background: languageColor(item.language) }} aria-hidden />
                    {languageLabel(item.language)}
                  </span>
                ))}
              </dd>
            </div>
            {runtimes.length > 0 && (
              <div>
                <dt className="mb-1 text-xs text-ink-3">Declared runtimes</dt>
                <dd>{runtimes.map(([name, value]) => `${languageLabel(name)} ${value}`).join(", ")}</dd>
              </div>
            )}
          </dl>
        </Card>
        <Card title="Entry points">
          {data.entry_points.length === 0 ? (
            <p className="text-sm text-ink-3">No conventional entry points were found.</p>
          ) : (
            <ul className="space-y-1">
              {data.entry_points.map((path) => <li key={path}><CodeLink projectId={project.id} path={path} /></li>)}
            </ul>
          )}
        </Card>
      </div>
      <Card title="Layers">
        <BarList
          ariaLabel="Files per layer"
          items={data.layers.map((layer) => ({ key: layer.role, label: layer.label, value: layer.files, detail: `files in ${layer.label}` }))}
        />
      </Card>
      {data.cycles.length > 0 && (
        <Notice tone="warning">
          <p className="font-medium">{data.cycles.length} group(s) of files import each other in a cycle.</p>
          <ul className="mt-1.5 space-y-1">
            {data.cycles.slice(0, 5).map((cycle) => (
              <li key={cycle.join()} className="font-mono text-xs break-all">{cycle.join(" → ")}</li>
            ))}
          </ul>
        </Notice>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Modules">
          <TableWrap>
            <table className="w-full min-w-[20rem]">
              <thead>
                <tr>
                  <th className={thClass}>Module</th>
                  <th className={thClass}>Language</th>
                  <th className={`${thClass} text-right`}>Files</th>
                  <th className={`${thClass} text-right`}>Lines</th>
                </tr>
              </thead>
              <tbody>
                {modules.slice(0, 50).map((module) => (
                  <tr key={module.name}>
                    <td className={`${tdClass} font-mono text-xs break-all`}>{module.name}</td>
                    <td className={tdClass}>{languageLabel(module.language)}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{module.files}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{module.loc.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
        <Card title="Module dependencies">
          {links.length === 0 ? (
            <p className="text-sm text-ink-3">No dependencies between modules were found.</p>
          ) : (
            <TableWrap>
              <table className="w-full min-w-[20rem]">
                <thead>
                  <tr><th className={thClass}>From</th><th className={thClass}>To</th><th className={`${thClass} text-right`}>References</th></tr>
                </thead>
                <tbody>
                  {links.slice(0, 50).map((link) => (
                    <tr key={`${link.source}>${link.target}`}>
                      <td className={`${tdClass} font-mono text-xs break-all`}>{link.source}</td>
                      <td className={`${tdClass} font-mono text-xs break-all`}>{link.target}</td>
                      <td className={`${tdClass} text-right tabular-nums`}>{link.weight}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      </div>
      {data.drupal_hooks?.length > 0 && (
        <Card title="Drupal hooks">
          <TableWrap>
            <table className="w-full min-w-[30rem]">
              <thead>
                <tr><th className={thClass}>Hook</th><th className={thClass}>Module</th><th className={thClass}>Implementation</th></tr>
              </thead>
              <tbody>
                {data.drupal_hooks.map((hook) => (
                  <tr key={`${hook.path}:${hook.function}`}>
                    <td className={`${tdClass} font-mono text-xs`}>hook_{hook.hook}</td>
                    <td className={tdClass}>{hook.module}</td>
                    <td className={tdClass}><CodeLink projectId={project.id} path={hook.path} line={hook.line}>{hook.function}</CodeLink></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      )}
    </div>
  );
}
