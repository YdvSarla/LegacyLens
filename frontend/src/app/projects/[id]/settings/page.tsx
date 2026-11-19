"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { mutate } from "swr";
import { useProject } from "@/components/project-context";
import { useToast } from "@/components/toast";
import { Button, Card, ErrorNotice, Field, Notice, PageHeader, inputClass, usePageTitle } from "@/components/ui";
import { deleteRequest, errorMessage, postJson, uploadForm } from "@/lib/api";
import { formatDate, shortSha } from "@/lib/format";
import type { Project } from "@/lib/types";

export default function ProjectSettingsPage() {
  usePageTitle("Project settings");
  const toast = useToast();
  const { project, refresh } = useProject();
  const router = useRouter();
  const base = `/api/projects/${project.id}`;
  const [name, setName] = useState(project.name);
  const [localOnly, setLocalOnly] = useState(project.local_ai_only);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState("");
  const working = !["ready", "failed"].includes(project.status);

  async function changePrivacy(next: boolean) {
    // Shown at once, and put back if the server refuses.
    setLocalOnly(next);
    try {
      await postJson(base, { local_ai_only: next }, "PATCH");
      refresh();
      toast.success(next ? "This project now uses local AI only." : "Cloud AI providers are allowed again.");
    } catch (caught) {
      setLocalOnly(!next);
      setError(errorMessage(caught));
    }
  }

  async function run(key: string, action: () => Promise<unknown>, done?: string) {
    setBusy(key);
    setError(null);
    try {
      await action();
      refresh();
      if (done) toast.success(done);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="max-w-2xl space-y-4">
      <PageHeader title="Project settings" />
      {error && <ErrorNotice error={new Error(error)} />}

      <Card title="AI privacy">
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={localOnly}
            onChange={(event) => void changePrivacy(event.target.checked)}
          />
          <span>
            <span className="font-medium">Use local AI only for this project</span>
            <span className="mt-0.5 block text-ink-2">
              Code is then only sent to your own Ollama server, never to Gemini or Groq. Turn this on for private or client code, because
              Google may use content sent through Gemini&apos;s free tier to improve its products.
            </span>
          </span>
        </label>
      </Card>

      <Card title="Name">
        <form
          className="flex gap-2"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            void run("name", async () => {
              await postJson(base, { name }, "PATCH");
              await mutate("/api/projects");
            }, "Name saved.");
          }}
        >
          <input aria-label="Project name" value={name} maxLength={200} required onChange={(event) => setName(event.target.value)} className={inputClass} />
          <Button type="submit" busy={busy === "name"} disabled={name === project.name}>Save</Button>
        </form>
      </Card>

      <Card title="Update the code">
        <p className="mb-3 text-sm text-ink-2">
          Current version: {project.head_commit ? shortSha(project.head_commit) : "none"}, analyzed {formatDate(project.analyzed_at) || "never"}.
        </p>
        {project.source_type === "zip" ? (
          <form
            className="space-y-2"
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const target = event.currentTarget;
              void run("version", async () => {
                await uploadForm(`${base}/versions`, form);
                target.reset();
              }, "Upload received. Analyzing the new version.");
            }}
          >
            <Field label="New ZIP version" hint="Each upload becomes a version you can compare with earlier ones.">
              <input name="file" type="file" accept=".zip,application/zip" required className={inputClass} />
            </Field>
            <Field label="Version label">
              <input name="label" maxLength={200} placeholder="For example: Release 2.4" className={inputClass} />
            </Field>
            <Button type="submit" variant="primary" busy={busy === "version"} disabled={working}>Upload version</Button>
          </form>
        ) : (
          <form
            className="space-y-2"
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void run("sync", () => postJson(`${base}/sync`, { token: form.get("token") || null }), "Pulling the latest commits.");
            }}
          >
            <Field label="Access token" hint="Only for private repositories. It is not stored.">
              <input name="token" type="password" autoComplete="off" className={inputClass} />
            </Field>
            <Button type="submit" variant="primary" busy={busy === "sync"} disabled={working}>Pull latest commits</Button>
          </form>
        )}
        <div className="mt-3 border-t border-line pt-3">
          <Button busy={busy === "reanalyze"} disabled={working} onClick={() => run("reanalyze", () => postJson(`${base}/reanalyze`), "Analysis started.")}>
            Analyze again without changes
          </Button>
        </div>
      </Card>

      <Card title="Delete project">
        <Notice tone="warning">This permanently deletes the code copy, analysis, chats, tests and documentation of this project.</Notice>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <div className="min-w-56 flex-1">
            <Field label={`Type "${project.name}" to confirm`}>
              <input value={confirmDelete} onChange={(event) => setConfirmDelete(event.target.value)} className={inputClass} />
            </Field>
          </div>
          <Button
            variant="danger"
            busy={busy === "delete"}
            disabled={confirmDelete !== project.name || working}
            onClick={async () => {
              setBusy("delete");
              setError(null);
              try {
                await deleteRequest(base);
                // The project list is cached for the header switcher too, so drop it there at once.
                await mutate<Project[]>("/api/projects", (current) => current?.filter((item) => item.id !== project.id), { revalidate: true });
                toast.success(`${project.name} was deleted.`);
                router.replace("/projects");
              } catch (caught) {
                setError(errorMessage(caught));
                setBusy(null);
              }
            }}
          >
            Delete project
          </Button>
        </div>
      </Card>
    </div>
  );
}
