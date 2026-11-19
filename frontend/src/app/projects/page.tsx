"use client";

import { FileArchive, FolderSearch, GitBranch, Plus, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type DragEvent, type FormEvent } from "react";
import useSWR, { mutate } from "swr";
import { useToast } from "@/components/toast";
import { Badge, Button, CardsSkeleton, EmptyState, ErrorNotice, Field, Modal, PageHeader, SearchInput, inputClass, usePageTitle } from "@/components/ui";
import { errorMessage, fetcher, postJson, uploadForm } from "@/lib/api";
import { compact, formatDate, languageColor, languageLabel } from "@/lib/format";
import type { Project } from "@/lib/types";

const EXAMPLES = [
  { label: "Laravel skeleton", url: "https://github.com/laravel/laravel" },
  { label: "Express", url: "https://github.com/expressjs/express" },
  { label: "Flask", url: "https://github.com/pallets/flask" },
];

function StatusBadge({ project }: { project: Project }) {
  if (project.status === "ready") return <Badge tone="good">Ready</Badge>;
  if (project.status === "failed") return <Badge tone="critical">Failed</Badge>;
  return <Badge tone="accent">Analyzing {project.job?.progress ?? 0}%</Badge>;
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const [mode, setMode] = useState<"github" | "zip">("github");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function chooseFile(next: File | null | undefined) {
    if (!next) return;
    if (!next.name.toLowerCase().endsWith(".zip")) {
      setError("Please choose a .zip file.");
      return;
    }
    setError(null);
    setFile(next);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      let project: Project;
      if (mode === "github") {
        project = await postJson<Project>("/api/projects/github", {
          url,
          branch: form.get("branch") || null,
          token: form.get("token") || null,
        });
      } else {
        if (!file) throw new Error("Please choose a .zip file.");
        const body = new FormData();
        body.set("file", file);
        project = await uploadForm<Project>("/api/projects/upload", body, setUploadProgress);
      }
      toast.success(`Importing ${project.name}.`);
      await mutate("/api/projects");
      router.push(`/projects/${project.id}`);
    } catch (caught) {
      setError(errorMessage(caught));
      setUploadProgress(null);
      setBusy(false);
    }
  }

  const tabClass = (active: boolean) =>
    `flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm ${active ? "bg-surface font-medium shadow-sm" : "text-ink-2 hover:text-ink"}`;

  return (
    <Modal title="Add a project" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3.5">
        <div role="tablist" className="flex gap-1 rounded-lg bg-surface-2 p-1">
          <button type="button" role="tab" aria-selected={mode === "github"} className={tabClass(mode === "github")} onClick={() => setMode("github")}>
            <GitBranch className="size-4" aria-hidden /> GitHub
          </button>
          <button type="button" role="tab" aria-selected={mode === "zip"} className={tabClass(mode === "zip")} onClick={() => setMode("zip")}>
            <FileArchive className="size-4" aria-hidden /> ZIP upload
          </button>
        </div>
        {error && <ErrorNotice error={new Error(error)} />}
        {mode === "github" ? (
          <>
            <Field label="Repository URL">
              <input value={url} onChange={(event) => setUrl(event.target.value)} required placeholder="https://github.com/owner/repository" className={inputClass} />
            </Field>
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
              <span>Try:</span>
              {EXAMPLES.map((example) => (
                <button key={example.url} type="button" onClick={() => setUrl(example.url)} className="rounded-md border border-line-strong px-2 py-0.5 hover:bg-surface-2">
                  {example.label}
                </button>
              ))}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Branch" hint="Empty means the default branch.">
                <input name="branch" placeholder="main" className={inputClass} />
              </Field>
              <Field label="Access token" hint="For private repositories. Never stored.">
                <input name="token" type="password" autoComplete="off" className={inputClass} />
              </Field>
            </div>
          </>
        ) : (
          <label
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={`flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed p-6 text-center text-sm ${dragging ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-surface-2"}`}
          >
            <UploadCloud className="size-6 text-ink-3" aria-hidden />
            <span className="font-medium">{file ? file.name : "Drop a ZIP here, or click to choose"}</span>
            <span className="text-xs text-ink-3">
              {file ? `${(file.size / (1024 * 1024)).toFixed(1)} MB` : "Up to 200 MB. Dependencies like vendor and node_modules are skipped."}
            </span>
            <input aria-label="ZIP archive" type="file" accept=".zip,application/zip" className="sr-only" onChange={(event) => chooseFile(event.target.files?.[0])} />
          </label>
        )}
        {uploadProgress !== null && (
          <div role="progressbar" aria-label="Upload progress" aria-valuenow={uploadProgress} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded" style={{ background: "var(--meter-track)" }}>
            <div className="h-full rounded transition-all" style={{ width: `${uploadProgress}%`, background: "var(--series-1)" }} />
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={busy} disabled={mode === "zip" ? !file : !url.trim()}>
            {mode === "github" ? "Import repository" : "Upload and analyze"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ProjectCard({ project }: { project: Project }) {
  const busy = !["ready", "failed"].includes(project.status);
  const totalLoc = project.languages.reduce((sum, language) => sum + language.loc, 0);
  return (
    <Link href={`/projects/${project.id}`} className="flex h-full flex-col rounded-xl border border-line bg-surface p-4 shadow-[0_1px_2px_rgb(0_0_0/0.04)] transition-colors hover:border-line-strong">
      <div className="flex items-start justify-between gap-2">
        <h2 className="min-w-0 font-medium break-words">{project.name}</h2>
        <StatusBadge project={project} />
      </div>
      {project.status === "failed" && <p className="mt-2 line-clamp-2 text-sm text-critical-text">{project.error}</p>}
      {busy && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded" style={{ background: "var(--meter-track)" }}>
            <div className="h-full rounded transition-all" style={{ width: `${project.job?.progress ?? 0}%`, background: "var(--series-1)" }} />
          </div>
          <p className="mt-1 truncate text-xs text-ink-3">{project.job?.message ?? "Waiting to start."}</p>
        </div>
      )}
      {project.score && project.metrics && (
        <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
          <div>
            <dt className="text-xs text-ink-3">Quality</dt>
            <dd className="font-semibold">{project.score.overall} <span className="font-normal text-ink-3">{project.score.grade}</span></dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Lines</dt>
            <dd className="font-semibold">{compact(project.metrics.loc)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Endpoints</dt>
            <dd className="font-semibold">{compact(project.metrics.endpoints)}</dd>
          </div>
        </dl>
      )}
      {totalLoc > 0 && (
        <div className="mt-3 flex h-1.5 gap-0.5 overflow-hidden rounded" aria-hidden>
          {project.languages.map((language) => (
            <span key={language.language} style={{ width: `${(language.loc / totalLoc) * 100}%`, background: languageColor(language.language) }} />
          ))}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-2">
        {project.languages.slice(0, 3).map((language) => (
          <span key={language.language} className="flex items-center gap-1">
            <span className="size-2 rounded-sm" style={{ background: languageColor(language.language) }} aria-hidden />
            {languageLabel(language.language)}
          </span>
        ))}
        {project.frameworks.slice(0, 3).map((framework) => <Badge key={framework}>{framework}</Badge>)}
      </div>
      <p className="mt-auto pt-3 text-xs text-ink-3">
        {project.source_type === "github" ? "GitHub" : "ZIP upload"} · Added {formatDate(project.created_at)}
      </p>
    </Link>
  );
}

export default function ProjectsPage() {
  usePageTitle("Projects");
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const { data, error, isLoading, mutate } = useSWR<Project[]>("/api/projects", fetcher, {
    refreshInterval: (latest) => (latest?.some((p) => !["ready", "failed"].includes(p.status)) ? 2000 : 0),
  });
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? data?.filter((project) => project.name.toLowerCase().includes(needle)) : data;
  }, [data, query]);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 space-y-5 p-4 sm:p-6">
      <PageHeader
        title="Projects"
        description="Each project is one codebase, analyzed and ready to explore."
        actions={
          <>
            {Boolean(data?.length) && <SearchInput value={query} onChange={setQuery} label="Filter projects" className="w-48 sm:w-60" />}
            <Button variant="primary" onClick={() => setAdding(true)}>
              <Plus className="size-4" aria-hidden /> Add project
            </Button>
          </>
        }
      />
      {adding && <ImportDialog onClose={() => setAdding(false)} />}
      {isLoading && <CardsSkeleton count={3} className="h-44" />}
      {error && <ErrorNotice error={error} onRetry={() => mutate()} />}
      {data?.length === 0 && (
        <EmptyState title="Add your first project">
          <p>Import a GitHub repository or upload a ZIP. LegacyLens supports PHP, Laravel, Drupal, Python, JavaScript, TypeScript and Node.js.</p>
          <Button variant="primary" className="mt-3" onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden /> Add project
          </Button>
        </EmptyState>
      )}
      {Boolean(data?.length) && visible?.length === 0 && (
        <div className="flex flex-col items-center py-10 text-sm text-ink-2">
          <FolderSearch className="mb-2 size-6 text-ink-3" aria-hidden />
          No project matches that name.
        </div>
      )}
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {visible?.map((project) => (
          <li key={project.id}><ProjectCard project={project} /></li>
        ))}
      </ul>
    </main>
  );
}
