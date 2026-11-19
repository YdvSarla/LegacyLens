"use client";

import { Bot, Plus, Send, ShieldCheck, Square, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import useSWR from "swr";
import { CopyButton } from "@/components/copy-button";
import { Markdown } from "@/components/markdown";
import { useProject } from "@/components/project-context";
import { useToast } from "@/components/toast";
import { Badge, Button, CodeLink, ConfirmDialog, ErrorNotice, Notice, PageHeader, inputClass, usePageTitle } from "@/components/ui";
import { ApiError, deleteRequest, errorMessage, fetcher, request } from "@/lib/api";
import { linkCitations } from "@/lib/citations";
import type { ChatMessage } from "@/lib/types";

interface ConversationSummary {
  id: string;
  title: string;
}

const SUGGESTIONS = [
  "Explain the architecture of this project.",
  "What are the main entry points and how does a request flow?",
  "Which parts of the code look the most risky to change?",
];

let pendingCounter = 0;

function pendingMessage(content: string): ChatMessage {
  pendingCounter += 1;
  return {
    id: `pending-${pendingCounter}`, role: "user", content, citations: [], sources: [],
    provider: null, model: null, degraded: false, created_at: "",
  };
}

function Message({ message, projectId }: { message: ChatMessage; projectId: string }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-xl bg-accent-soft px-3 py-2 text-sm break-words whitespace-pre-wrap">{message.content}</p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-line bg-surface p-3.5">
      {message.degraded && (
        <div className="mb-2"><Badge tone="warning">Answered without AI</Badge></div>
      )}
      <Markdown>{linkCitations(message.content, message.citations, projectId)}</Markdown>
      <div className="mt-1 flex justify-end"><CopyButton value={message.content} label="Copy answer" showLabel /></div>
      {!message.degraded && message.citations.length > 0 && (
        <div className="mt-3 border-t border-line pt-2">
          <h3 className="mb-1 text-xs font-medium text-ink-2">References</h3>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {message.citations.map((citation) => (
              <li key={`${citation.path}:${citation.start_line}`}>
                <CodeLink projectId={projectId} path={citation.path} line={citation.start_line} endLine={citation.end_line}>
                  {citation.path}:{citation.start_line}-{citation.end_line}
                </CodeLink>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!message.degraded && message.citations.length === 0 && message.sources.length > 0 && (
        <p className="mt-3 border-t border-line pt-2 text-xs text-ink-2">
          This answer did not point to specific lines. Check it against the code searched below.
        </p>
      )}
      {(message.provider || (message.sources.length > 0 && !message.degraded)) && (
        <div className="mt-2 text-xs text-ink-3">
          {message.provider && <p>Answered by {message.provider} ({message.model}). AI can be wrong, so check important details in the code.</p>}
          {message.sources.length > 0 && !message.degraded && (
            <details className="mt-1" open={message.citations.length === 0}>
              <summary className="cursor-pointer">Code searched ({message.sources.length} sections)</summary>
              <ul className="mt-1 space-y-0.5">
                {message.sources.map((source) => (
                  <li key={`${source.path}:${source.start_line}`}>
                    <CodeLink projectId={projectId} path={source.path} line={source.start_line} endLine={source.end_line}>
                      {source.path}:{source.start_line}-{source.end_line}
                    </CodeLink>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function Waiting({ onStop }: { onStop: () => void }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-3 text-sm text-ink-2">
      <span className="flex items-center gap-1" aria-hidden>
        {[0, 1, 2].map((dot) => (
          <span key={dot} className="size-1.5 animate-pulse rounded-full bg-accent" style={{ animationDelay: `${dot * 180}ms` }} />
        ))}
      </span>
      <span>Searching the code and writing an answer ({seconds}s).</span>
      {seconds > 20 && <span className="text-xs text-ink-3">Local models on a CPU can take a few minutes.</span>}
      <Button variant="ghost" className="ml-auto" onClick={onStop}><Square className="size-3.5" aria-hidden /> Stop</Button>
    </div>
  );
}

export default function AssistantPage() {
  usePageTitle("AI assistant");
  const toast = useToast();
  const { project } = useProject();
  const base = `/api/projects/${project.id}`;
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [provider, setProvider] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [pendingDelete, setPendingDelete] = useState<ConversationSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const { data: conversations, mutate } = useSWR<ConversationSummary[]>(`${base}/conversations`, fetcher);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, busy]);

  async function open(id: string) {
    setError(null);
    try {
      const data = await fetcher<{ messages: ChatMessage[] }>(`${base}/conversations/${id}`);
      setConversationId(id);
      setMessages(data.messages);
    } catch (caught) {
      setError(caught);
    }
  }

  function startNew() {
    setConversationId(null);
    setMessages([]);
    setError(null);
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteRequest(`${base}/conversations/${pendingDelete.id}`);
      if (pendingDelete.id === conversationId) startNew();
      await mutate();
      setPendingDelete(null);
    } catch (caught) {
      toast.error(errorMessage(caught));
    } finally {
      setDeleting(false);
    }
  }

  async function ask(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setInput("");
    const pending = pendingMessage(text);
    setMessages((current) => [...current, pending]);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const reply = await request<{ conversation_id: string; message: ChatMessage }>(`${base}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversationId, provider: provider || null }),
        signal: controller.signal,
      });
      setConversationId(reply.conversation_id);
      setMessages((current) => [...current, reply.message]);
      await mutate();
    } catch (caught) {
      if (!(controller.signal.aborted || (caught instanceof ApiError && caught.code === "aborted"))) setError(caught);
      setInput(text);
      setMessages((current) => current.filter((item) => item.id !== pending.id));
    } finally {
      abort.current = null;
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(input);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[14rem_1fr]">
      {pendingDelete && (
        <ConfirmDialog title="Delete this chat?" confirmLabel="Delete chat" busy={deleting} onConfirm={confirmDelete} onCancel={() => setPendingDelete(null)}>
          &ldquo;{pendingDelete.title}&rdquo; will be removed permanently.
        </ConfirmDialog>
      )}
      <aside className="order-2 lg:order-1">
        <Button onClick={startNew} className="mb-2 w-full"><Plus className="size-4" aria-hidden /> New chat</Button>
        <ul className="space-y-0.5">
          {conversations?.map((conversation) => (
            <li key={conversation.id} className={`group flex items-center rounded-lg ${conversation.id === conversationId ? "bg-surface-3" : "hover:bg-surface-2"}`}>
              <button type="button" onClick={() => open(conversation.id)} className="min-w-0 flex-1 truncate px-2 py-1.5 text-left text-sm">
                {conversation.title}
              </button>
              <button type="button" aria-label={`Delete chat: ${conversation.title}`} onClick={() => setPendingDelete(conversation)} className="p-1.5 text-ink-3 hover:text-critical-text">
                <Trash2 className="size-3.5" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <div className="order-1 flex min-h-[70vh] min-w-0 flex-col lg:order-2">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <PageHeader title="AI assistant" />
          <label className="ml-auto flex items-center gap-1.5 text-xs text-ink-2">
            Provider
            <select value={provider} onChange={(event) => setProvider(event.target.value)} className="rounded-lg border border-line-strong bg-surface px-2 py-1.5 text-sm text-ink">
              <option value="">Automatic with fallback</option>
              {!project.local_ai_only && <option value="gemini">Gemini only</option>}
              {!project.local_ai_only && <option value="groq">Groq only</option>}
              <option value="ollama">Ollama only (local)</option>
            </select>
          </label>
        </div>
        {project.local_ai_only ? (
          <div className="mb-3 flex items-center gap-2 text-xs text-ink-2">
            <ShieldCheck className="size-4 text-good" aria-hidden />
            Local only: this project&apos;s code is only sent to your Ollama server.
          </div>
        ) : (
          <div className="mb-3">
            <Notice tone="info">
              Questions and matching code go to the active cloud provider. On Gemini&apos;s free tier, Google may use them to improve its
              products. For private code, turn on local AI in <Link className="text-accent underline" href={`/projects/${project.id}/settings`}>project settings</Link>.
            </Notice>
          </div>
        )}
        <div className="flex-1 space-y-3" aria-live="polite">
          {messages.length === 0 && (
            <div className="rounded-xl border border-dashed border-line-strong p-6 text-center">
              <Bot className="mx-auto mb-2 size-6 text-ink-3" aria-hidden />
              <p className="text-sm font-medium">Ask anything about this code</p>
              <p className="mt-1 text-xs text-ink-3">Answers cite the exact files and lines they come from.</p>
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => ask(suggestion)} className="rounded-lg border border-line-strong px-2.5 py-1.5 text-xs hover:bg-surface-2">
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message) => <Message key={message.id} message={message} projectId={project.id} />)}
          {busy && <Waiting onStop={() => abort.current?.abort()} />}
          {Boolean(error) && <ErrorNotice error={error} />}
          <div ref={bottom} />
        </div>
        <form onSubmit={submit} className="sticky bottom-0 mt-3 flex items-end gap-2 bg-bg py-2">
          <textarea
            aria-label="Your question"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void ask(input);
              }
            }}
            rows={2}
            maxLength={8000}
            placeholder="Ask about this code. Press Enter to send."
            className={`${inputClass} resize-y`}
          />
          <Button type="submit" variant="primary" busy={busy} disabled={!input.trim()} aria-label="Send">
            <Send className="size-4" aria-hidden />
          </Button>
        </form>
      </div>
    </div>
  );
}
