"use client";

import { ArrowDown, ArrowUp, CheckCircle2, CircleX, Clock, KeyRound, Laptop, Pause } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, ErrorNotice, Notice, PageHeader, TableSkeleton, TableWrap, tdClass, thClass, usePageTitle } from "@/components/ui";
import { errorMessage, fetcher, postJson } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ProviderInfo, ProviderModel, ProvidersResponse } from "@/lib/types";

const LABELS: Record<string, string> = { gemini: "Google Gemini", groq: "Groq", ollama: "Ollama" };

const REASONS: Record<string, string> = {
  quota: "Free quota reached",
  auth: "The key was rejected",
  unavailable: "Service unavailable",
  model_not_found: "Model not found",
  daily_budget: "Daily safety budget used",
  minute_budget: "Per-minute safety budget used",
};

function ModelState({ model }: { model: ProviderModel }) {
  if (model.state === "ready") {
    return <span className="inline-flex items-center gap-1 text-xs font-medium"><CheckCircle2 className="size-3.5 text-good" aria-hidden /> Ready</span>;
  }
  if (model.state === "not_configured") {
    return <span className="inline-flex items-center gap-1 text-xs font-medium"><KeyRound className="size-3.5 text-ink-3" aria-hidden /> No key</span>;
  }
  const Icon = model.state === "cooldown" ? Clock : Pause;
  return (
    <span className="text-xs">
      <span className="inline-flex items-center gap-1 font-medium"><Icon className="size-3.5 text-serious" aria-hidden /> {REASONS[model.reason ?? ""] ?? "Paused"}</span>
      {model.available_at && <span className="block text-ink-3">Back at {formatDate(model.available_at)}</span>}
    </span>
  );
}

function ProviderCard({ provider, position, total, onMove, onChanged }: {
  provider: ProviderInfo;
  position: number;
  total: number;
  onMove: (direction: -1 | 1) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const toast = useToast();
  const paused = provider.models.some((model) => model.state === "cooldown");

  async function test() {
    setBusy("test");
    setResult(null);
    try {
      const reply = await postJson<{ model: string; reply: string }>(`/api/ai/providers/${provider.name}/test`);
      setResult({ ok: true, text: `${reply.model} replied: ${reply.reply}` });
    } catch (caught) {
      setResult({ ok: false, text: errorMessage(caught) });
    } finally {
      setBusy(null);
      onChanged();
    }
  }

  async function clear() {
    setBusy("clear");
    try {
      await postJson(`/api/ai/providers/${provider.name}/clear-cooldown`);
      toast.success(`${LABELS[provider.name] ?? provider.name} will be tried again.`);
    } catch (caught) {
      toast.error(errorMessage(caught));
    } finally {
      setBusy(null);
      onChanged();
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="grid size-6 place-items-center rounded-full bg-surface-3 text-xs font-semibold" aria-label={`Priority ${position + 1}`}>{position + 1}</span>
        <h2 className="font-medium">{LABELS[provider.name] ?? provider.name}</h2>
        {provider.is_local && <Badge tone="good"><Laptop className="size-3" aria-hidden /> Local</Badge>}
        {!provider.is_local && <Badge>Free tier</Badge>}
        <div className="ml-auto flex items-center gap-1">
          <button type="button" aria-label={`Move ${provider.name} up`} disabled={position === 0} onClick={() => onMove(-1)} className="grid size-8 place-items-center rounded-lg hover:bg-surface-2 disabled:opacity-30">
            <ArrowUp className="size-4" aria-hidden />
          </button>
          <button type="button" aria-label={`Move ${provider.name} down`} disabled={position === total - 1} onClick={() => onMove(1)} className="grid size-8 place-items-center rounded-lg hover:bg-surface-2 disabled:opacity-30">
            <ArrowDown className="size-4" aria-hidden />
          </button>
        </div>
      </div>
      <p className="mt-1 text-xs text-ink-2">{provider.privacy}</p>
      <div className="mt-3">
        <TableWrap>
          <table className="w-full min-w-[34rem]">
            <thead>
              <tr>
                <th className={thClass}>Model</th>
                <th className={thClass}>Status</th>
                <th className={`${thClass} text-right`}>Requests today</th>
                <th className={`${thClass} text-right`}>This minute</th>
                <th className={`${thClass} text-right`}>Tokens today</th>
              </tr>
            </thead>
            <tbody>
              {provider.models.map((model) => (
                <tr key={model.model}>
                  <td className={`${tdClass} font-mono text-xs break-all`}>{model.model}</td>
                  <td className={tdClass}>
                    <ModelState model={model} />
                    {model.detail && <span className="mt-0.5 block max-w-xs text-xs break-words text-ink-3">{model.detail}</span>}
                  </td>
                  <td className={`${tdClass} text-right tabular-nums`}>{model.requests_today}{model.daily_budget !== null && ` / ${model.daily_budget}`}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{model.requests_last_minute}{model.minute_budget !== null && ` / ${model.minute_budget}`}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{model.tokens_today.toLocaleString("en-US")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {provider.configured && <Button busy={busy === "test"} onClick={test}>Test connection</Button>}
        {paused && <Button busy={busy === "clear"} onClick={clear}>Retry now</Button>}
        {!provider.configured && (
          <p className="text-xs text-ink-2">
            Set <code className="rounded bg-surface-2 px-1 font-mono">{provider.name.toUpperCase()}_API_KEY</code> in the server&apos;s environment to enable this provider.
          </p>
        )}
        {provider.configured && !provider.is_local && <span className="text-xs text-ink-3">The test uses one free request.</span>}
      </div>
      {result && (
        <div className="mt-2 flex items-start gap-1.5 text-sm" role="status">
          {result.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-hidden /> : <CircleX className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden />}
          <span className="min-w-0 break-words">{result.text}</span>
        </div>
      )}
    </Card>
  );
}

export default function ProvidersPage() {
  usePageTitle("AI providers");
  const { data, error, isLoading, mutate } = useSWR<ProvidersResponse>("/api/ai/providers", fetcher, { refreshInterval: 15000 });
  const [saveError, setSaveError] = useState<string | null>(null);

  async function saveOrder(order: string[] | null) {
    setSaveError(null);
    try {
      await postJson("/api/ai/preferences", { provider_order: order }, "PUT");
      await mutate();
    } catch (caught) {
      setSaveError(errorMessage(caught));
    }
  }

  function move(index: number, direction: -1 | 1) {
    if (!data) return;
    const order = [...data.order];
    const target = index + direction;
    [order[index], order[target]] = [order[target], order[index]];
    void saveOrder(order);
  }

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 space-y-4 p-4 sm:p-6">
      <PageHeader
        title="AI providers"
        description="LegacyLens tries these in order. When one reaches its free quota it moves on to the next automatically. If all are unavailable, features keep working without AI, and nothing ever switches to a paid service."
      />
      <Notice tone="warning">
        <strong className="font-medium">Keep your keys on the free tier.</strong> LegacyLens only calls free-tier models and stops at its own daily
        budgets, but it cannot see your billing. Use a Google AI Studio project with no billing account linked, and stay on Groq&apos;s Free plan.
      </Notice>
      {error && <ErrorNotice error={error} onRetry={() => mutate()} />}
      {saveError && <ErrorNotice error={new Error(saveError)} />}
      {isLoading && <TableSkeleton rows={6} />}
      {data?.providers.map((provider, index) => (
        <ProviderCard
          key={provider.name}
          provider={provider}
          position={index}
          total={data.providers.length}
          onMove={(direction) => move(index, direction)}
          onChanged={() => void mutate()}
        />
      ))}
      {data?.custom_order && <Button variant="ghost" onClick={() => saveOrder(null)}>Restore default order</Button>}
    </main>
  );
}
