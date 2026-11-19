"use client";

import { CheckCircle2, CircleX, Clock, Lock, Play, Save, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import useSWR from "swr";
import { useProject } from "@/components/project-context";
import {
  Badge,
  Button,
  Card,
  CodeLink,
  EmptyState,
  ConfirmDialog,
  ErrorNotice,
  Field,
  Notice,
  PageHeader,
  TableSkeleton,
  TableWrap,
  inputClass,
  tdClass,
  thClass,
  usePageTitle,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import { deleteRequest, errorMessage, fetcher, postJson } from "@/lib/api";
import type { FileItem, GeneratedTest, Page, TestRun } from "@/lib/types";

const RUNNABLE = new Set(["python", "javascript", "typescript", "php"]);

function RunStatus({ run }: { run: TestRun }) {
  const views = {
    passed: { Icon: CheckCircle2, color: "text-good", label: "Passed" },
    failed: { Icon: CircleX, color: "text-critical", label: "Failed" },
    error: { Icon: CircleX, color: "text-serious", label: "Could not run" },
    timeout: { Icon: Clock, color: "text-serious", label: "Timed out" },
    running: { Icon: Clock, color: "text-accent", label: "Running" },
    queued: { Icon: Clock, color: "text-ink-3", label: "Queued" },
  } as const;
  const { Icon, color, label } = views[run.status];
  return (
    <span className="inline-flex items-center gap-1 text-sm font-medium">
      <Icon className={`size-4 ${color}`} aria-hidden /> {label}
    </span>
  );
}

function RunResult({ run }: { run: TestRun }) {
  const results = run.results;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <RunStatus run={run} />
        {results && (
          <span className="text-xs text-ink-2">
            {results.passed} passed, {results.failed + results.error} failed, {results.skipped} skipped
          </span>
        )}
        {run.duration_seconds !== null && <span className="text-xs text-ink-3">{run.duration_seconds.toFixed(1)} seconds</span>}
      </div>
      {results && results.cases.length > 0 && (
        <TableWrap>
          <table className="w-full min-w-[24rem]">
            <thead><tr><th className={thClass}>Test</th><th className={thClass}>Result</th></tr></thead>
            <tbody>
              {results.cases.map((item, index) => (
                <tr key={index}>
                  <td className={tdClass}>
                    <span className="font-mono text-xs break-all">{item.name}</span>
                    {item.message && <pre className="mt-1 max-h-40 overflow-auto rounded bg-surface-2 p-2 text-xs whitespace-pre-wrap">{item.message}</pre>}
                  </td>
                  <td className={tdClass}><Badge tone={item.status === "passed" ? "good" : item.status === "skipped" ? "neutral" : "critical"}>{item.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {run.output && (
        <details open={run.status !== "passed"}>
          <summary className="cursor-pointer text-xs font-medium">Sandbox output</summary>
          <pre className="mt-1 max-h-96 overflow-auto rounded-lg border border-line bg-surface-2 p-3 font-mono text-xs whitespace-pre-wrap">{run.output}</pre>
        </details>
      )}
    </div>
  );
}

function TestDetail({ test, onChanged, sandboxReady }: { test: GeneratedTest; onChanged: () => void; sandboxReady: boolean }) {
  const { project } = useProject();
  const base = `/api/projects/${project.id}`;
  const [code, setCode] = useState(test.code);
  const [install, setInstall] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(test.latest_run?.id ?? null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const toast = useToast();
  const { data: run } = useSWR<TestRun>(runId ? `${base}/test-runs/${runId}` : null, fetcher, {
    refreshInterval: (latest) => (latest && ["queued", "running"].includes(latest.status) ? 1500 : 0),
    onSuccess: (latest) => {
      if (!["queued", "running"].includes(latest.status)) onChanged();
    },
  });
  const dirty = code !== test.code;

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Reset the editor when another test is selected.
    setCode(test.code);
    setRunId(test.latest_run?.id ?? null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [test.id]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await postJson(`${base}/tests/${test.id}`, { code }, "PUT");
      onChanged();
      toast.success("Test saved.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  async function start() {
    setStarting(true);
    setError(null);
    try {
      if (dirty) await postJson(`${base}/tests/${test.id}`, { code }, "PUT");
      const created = await postJson<TestRun>(`${base}/tests/${test.id}/run`, { install_dependencies: install });
      setRunId(created.id);
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setStarting(false);
    }
  }

  async function remove() {
    setDeleting(true);
    try {
      await deleteRequest(`${base}/tests/${test.id}`);
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
      setDeleting(false);
      setConfirming(false);
    }
  }

  const running = run ? ["queued", "running"].includes(run.status) : false;

  return (
    <div className="space-y-3">
      {confirming && (
        <ConfirmDialog title="Delete this test?" confirmLabel="Delete test" busy={deleting} onConfirm={remove} onCancel={() => setConfirming(false)}>
          The test for {test.target_path} and its run history will be removed permanently.
        </ConfirmDialog>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs break-all">{test.test_path}</span>
        <Badge>{test.framework}</Badge>
        <Badge tone={test.source === "ai" ? "accent" : "neutral"}>
          {test.source === "ai" ? `AI: ${test.provider}` : test.source === "edited" ? "Edited" : "Baseline, no AI"}
        </Badge>
        <button type="button" onClick={() => setConfirming(true)} aria-label="Delete this test" className="ml-auto rounded p-1.5 text-ink-3 hover:bg-surface-2 hover:text-critical-text">
          <Trash2 className="size-4" aria-hidden />
        </button>
      </div>
      <p className="text-xs text-ink-2">Target: <CodeLink projectId={project.id} path={test.target_path} />{test.target_symbol && <> ({test.target_symbol})</>}</p>
      {test.notice && <Notice tone="warning">{test.notice}</Notice>}
      <textarea
        aria-label="Test code"
        value={code}
        spellCheck={false}
        onChange={(event) => setCode(event.target.value)}
        rows={Math.min(Math.max(code.split("\n").length + 1, 10), 26)}
        className={`${inputClass} font-mono text-xs leading-5 whitespace-pre`}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={start} busy={starting || running} disabled={!sandboxReady}>
          <Play className="size-4" aria-hidden /> Run in sandbox
        </Button>
        <Button onClick={save} busy={saving} disabled={!dirty}><Save className="size-4" aria-hidden /> Save</Button>
        <label className="flex items-center gap-1.5 text-sm">
          <input type="checkbox" checked={install} onChange={(event) => setInstall(event.target.checked)} />
          Install dependencies first
        </label>
      </div>
      {install && (
        <Notice tone="warning">
          Installing gives one separate step network access so packages can download. Install scripts stay disabled, and the tests themselves still run with no network.
        </Notice>
      )}
      {error && <ErrorNotice error={new Error(error)} />}
      {run && test.source === "ai" && run.status === "failed" && (
        <Notice tone="info">
          A regression test describes how the code behaves today, so a failure here often means the AI guessed the expected result
          wrongly. Compare the test with the code before treating this as a bug, and correct the test above if needed.
        </Notice>
      )}
      {run && <RunResult run={run} />}
    </div>
  );
}

export default function TestsPage() {
  usePageTitle("Regression tests");
  const { project } = useProject();
  const base = `/api/projects/${project.id}`;
  const { data: tests, error: testsError, isLoading, mutate } = useSWR<GeneratedTest[]>(`${base}/tests`, fetcher);
  const { data: sandbox } = useSWR<{ available: boolean; reason: string | null }>(`${base}/sandbox-status`, fetcher);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [symbol, setSymbol] = useState("");
  const [useAi, setUseAi] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notices, setNotices] = useState<Record<string, string>>({});

  // Asks the server while typing, because large projects have thousands of files.
  const { data: files } = useSWR<Page<FileItem>>(
    `${base}/files?parsed_only=true&limit=50${target ? `&q=${encodeURIComponent(target)}` : ""}`,
    fetcher,
    { keepPreviousData: true, revalidateOnFocus: false },
  );
  const candidates = (files?.items ?? []).filter((file) => RUNNABLE.has(file.language) && file.role !== "test");
  const selected = tests?.find((test) => test.id === selectedId) ?? tests?.[0] ?? null;

  async function generate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const test = await postJson<GeneratedTest>(`${base}/tests/generate`, {
        target_path: target,
        symbol: symbol.trim() || null,
        use_ai: useAi,
      });
      if (test.notice) setNotices((current) => ({ ...current, [test.id]: test.notice! }));
      await mutate();
      setSelectedId(test.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Regression tests" description="Tests that describe how the code behaves today, so you notice when a change breaks it." />
      {sandbox && !sandbox.available && <Notice tone="warning">Tests cannot run right now. {sandbox.reason}</Notice>}
      {sandbox?.available && (
        <p className="flex items-center gap-1.5 text-xs text-ink-2">
          <Lock className="size-3.5 text-good" aria-hidden />
          Tests run in throwaway Docker containers with no network, a read-only system, no privileges, and memory, process and time limits.
        </p>
      )}
      <form onSubmit={generate} className="grid gap-3 rounded-xl border border-line bg-surface p-4 md:grid-cols-[1fr_16rem]">
        <Field label="File to test">
          <input list="test-targets" required value={target} onChange={(event) => setTarget(event.target.value)} placeholder="Start typing a path" className={`${inputClass} font-mono text-xs`} />
          <datalist id="test-targets">
            {candidates.map((file) => <option key={file.path} value={file.path} />)}
          </datalist>
        </Field>
        <Field label="Function or class" hint="Optional.">
          <input value={symbol} onChange={(event) => setSymbol(event.target.value)} className={`${inputClass} font-mono text-xs`} />
        </Field>
        <label className="flex items-center gap-2 text-sm md:col-span-2">
          <input type="checkbox" checked={useAi} onChange={(event) => setUseAi(event.target.checked)} />
          Write behavior tests with AI. Without AI, a baseline test checks that the file still parses and its functions still exist.
        </label>
        <div className="md:col-span-2">
          <Button type="submit" variant="primary" busy={busy} disabled={!target}>Generate test</Button>
        </div>
      </form>
      {error && <ErrorNotice error={new Error(error)} />}
      {testsError && <ErrorNotice error={testsError} onRetry={() => mutate()} />}
      {isLoading && <TableSkeleton rows={4} />}
      {tests?.length === 0 && <EmptyState title="No tests yet">Choose a file above to generate your first regression test.</EmptyState>}
      {tests && tests.length > 0 && selected && (
        <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
          <Card title={`Tests (${tests.length})`}>
            <ul className="-mx-2 space-y-0.5">
              {tests.map((test) => (
                <li key={test.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(test.id)}
                    aria-current={test.id === selected.id ? "true" : undefined}
                    className={`w-full rounded-lg px-2 py-1.5 text-left ${test.id === selected.id ? "bg-surface-3" : "hover:bg-surface-2"}`}
                  >
                    <span className="block font-mono text-xs break-all">{test.target_path}</span>
                    <span className="mt-0.5 block">{test.latest_run ? <RunStatus run={test.latest_run} /> : <span className="text-xs text-ink-3">Not run yet</span>}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <TestDetail
              key={selected.id}
              test={{ ...selected, notice: notices[selected.id] ?? selected.notice }}
              onChanged={() => void mutate()}
              sandboxReady={Boolean(sandbox?.available)}
            />
          </Card>
        </div>
      )}
    </div>
  );
}
