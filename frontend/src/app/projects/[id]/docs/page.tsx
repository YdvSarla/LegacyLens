"use client";

import { Download, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Markdown } from "@/components/markdown";
import { useProject } from "@/components/project-context";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorNotice, PageHeader, Spinner, TableSkeleton, usePageTitle } from "@/components/ui";
import { deleteRequest, errorMessage, fetcher, postJson } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { extractHeadings } from "@/lib/tree";
import type { DocumentItem } from "@/lib/types";

export default function DocsPage() {
  usePageTitle("Documentation");
  const { project } = useProject();
  const base = `/api/projects/${project.id}/docs`;
  const { data: documents, error: listError, isLoading, mutate } = useSWR<DocumentItem[]>(base, fetcher, {
    refreshInterval: (latest) => (latest?.some((d) => ["queued", "running"].includes(d.status)) ? 1500 : 0),
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [useAi, setUseAi] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const selected = documents?.find((item) => item.id === selectedId) ?? documents?.[0] ?? null;
  const { data: document, error: documentError } = useSWR<DocumentItem>(
    selected ? [`${base}/${selected.id}`, selected.status] : null,
    ([path]: [string, unknown]) => fetcher<DocumentItem>(path),
  );

  const headings = useMemo(() => extractHeadings(document?.content ?? ""), [document?.content]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const created = await postJson<DocumentItem>(base, { use_ai: useAi });
      await mutate();
      setSelectedId(created.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteRequest(`${base}/${pendingDelete}`);
      setSelectedId(null);
      await mutate();
      setPendingDelete(null);
    } catch (caught) {
      setError(errorMessage(caught));
      setPendingDelete(null);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-4">
      {pendingDelete && (
        <ConfirmDialog title="Delete this version?" confirmLabel="Delete version" busy={deleting} onConfirm={remove} onCancel={() => setPendingDelete(null)}>
          This version of the documentation will be removed permanently.
        </ConfirmDialog>
      )}
      <PageHeader
        title="Documentation"
        description="Written from the analysis, so it describes what the code does today."
        actions={
          <>
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={useAi} onChange={(event) => setUseAi(event.target.checked)} />
              Add an AI introduction
            </label>
            <Button variant="primary" onClick={generate} busy={busy}>Generate documentation</Button>
          </>
        }
      />
      {error && <ErrorNotice error={new Error(error)} />}
      {listError && <ErrorNotice error={listError} onRetry={() => mutate()} />}
      {isLoading && <TableSkeleton rows={4} />}
      {documents?.length === 0 && (
        <EmptyState title="No documentation yet">
          LegacyLens writes it from the analysis: architecture, API reference, data models, components and dependencies.
        </EmptyState>
      )}
      {selected && (
        <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
          <div className="min-w-0 space-y-4 lg:sticky lg:top-17 lg:self-start">
          <Card title="Versions">
            <ul className="-mx-2 space-y-0.5">
              {documents?.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    aria-current={item.id === selected.id ? "true" : undefined}
                    className={`w-full rounded-lg px-2 py-1.5 text-left text-sm ${item.id === selected.id ? "bg-surface-3" : "hover:bg-surface-2"}`}
                  >
                    {formatDate(item.created_at)}
                    <span className="mt-0.5 flex gap-1">
                      {item.status !== "ready" && <Badge tone={item.status === "failed" ? "critical" : "accent"}>{item.status}</Badge>}
                      {item.status === "ready" && <Badge tone={item.ai_enhanced ? "accent" : "neutral"}>{item.ai_enhanced ? `AI: ${item.provider}` : "No AI"}</Badge>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {headings.length > 0 && (
            <Card title="Contents">
              <ul className="space-y-1 text-sm">
                {headings.map((heading) => (
                  <li key={heading.id}><a href={`#${heading.id}`} className="text-ink-2 hover:text-ink hover:underline">{heading.text}</a></li>
                ))}
              </ul>
            </Card>
          )}
          </div>
          <Card
            action={
              selected.status === "ready" ? (
                <div className="flex items-center gap-1">
                  <a href={`${base}/${selected.id}/download`} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm hover:bg-surface-2">
                    <Download className="size-4" aria-hidden /> Download Markdown
                  </a>
                  <button type="button" onClick={() => setPendingDelete(selected.id)} aria-label="Delete this version" className="rounded p-2 text-ink-3 hover:bg-surface-2 hover:text-critical-text">
                    <Trash2 className="size-4" aria-hidden />
                  </button>
                </div>
              ) : undefined
            }
          >
            {selected.status === "failed" && <ErrorNotice error={new Error(selected.error ?? "Generation failed.")} />}
            {["queued", "running"].includes(selected.status) && <Spinner label="Writing documentation" />}
            {documentError && <ErrorNotice error={documentError} />}
            {selected.status === "ready" && document?.content && <Markdown>{document.content}</Markdown>}
          </Card>
        </div>
      )}
    </div>
  );
}
