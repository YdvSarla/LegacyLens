"use client";

import { Bot, FlaskConical, GitCompareArrows, Network, ScanSearch, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import useSWR from "swr";
import { errorMessage, fetcher, postJson } from "@/lib/api";
import { safeRedirect } from "@/lib/safe-redirect";
import { Button, ErrorNotice, Field, Notice, inputClass } from "./ui";

const HIGHLIGHTS = [
  { Icon: Network, title: "See how the code fits together", text: "Architecture, APIs, models and an interactive dependency graph." },
  { Icon: Bot, title: "Ask questions about your code", text: "Answers point to the exact files and lines they come from." },
  { Icon: GitCompareArrows, title: "Know what a change could break", text: "Compare commits to find affected modules, endpoints and risks." },
  { Icon: FlaskConical, title: "Add a safety net", text: "Generate regression tests and run them in isolated Docker containers." },
];

function AuthFormInner({ mode }: { mode: "login" | "register" }) {
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isRegister = mode === "register";
  const { data: config } = useSWR<{ registration_open: boolean }>("/api/auth/config", fetcher, { revalidateOnFocus: false });

  useEffect(() => {
    document.title = `${isRegister ? "Create account" : "Sign in"} · LegacyLens`;
  }, [isRegister]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await postJson(`/api/auth/${mode}`, Object.fromEntries(form));
      window.location.assign(safeRedirect(params.get("next")));
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  const closed = config?.registration_open === false;

  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <section className="hidden flex-col justify-between border-r border-line bg-surface p-10 lg:flex">
        <div className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <ScanSearch className="size-6 text-accent" aria-hidden /> LegacyLens
        </div>
        <div>
          <p className="max-w-md text-2xl font-semibold tracking-tight">Understand and safely change code that nobody wants to touch.</p>
          <ul className="mt-8 space-y-5">
            {HIGHLIGHTS.map(({ Icon, title, text }) => (
              <li key={title} className="flex gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft"><Icon className="size-4.5" aria-hidden /></span>
                <span>
                  <span className="block text-sm font-medium">{title}</span>
                  <span className="block text-sm text-ink-2">{text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="flex items-center gap-2 text-xs text-ink-3">
          <ShieldCheck className="size-4" aria-hidden /> Self-hosted. Runs on free AI tiers or entirely on local models.
        </p>
      </section>
      <section className="flex items-center justify-center p-4 sm:p-8">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex items-center justify-center gap-2 text-lg font-semibold tracking-tight lg:hidden">
            <ScanSearch className="size-6 text-accent" aria-hidden /> LegacyLens
          </div>
          <h1 className="text-xl font-semibold tracking-tight">{isRegister ? "Create your account" : "Sign in to LegacyLens"}</h1>
          <p className="mt-1 mb-5 text-sm text-ink-2">{isRegister ? "Start by importing a repository or a ZIP." : "Welcome back."}</p>
          {isRegister && closed ? (
            <Notice tone="warning">Registration is closed on this server. Ask the person who runs it for an account.</Notice>
          ) : (
            <form onSubmit={submit} className="space-y-3.5">
              {error && <ErrorNotice error={new Error(error)} />}
              {isRegister && (
                <Field label="Name">
                  <input name="name" required maxLength={200} autoComplete="name" className={inputClass} />
                </Field>
              )}
              <Field label="Email">
                <input name="email" type="email" required autoComplete="email" className={inputClass} />
              </Field>
              <Field label="Password" hint={isRegister ? "Use at least 10 characters." : undefined}>
                <input
                  name="password"
                  type="password"
                  required
                  minLength={isRegister ? 10 : 1}
                  autoComplete={isRegister ? "new-password" : "current-password"}
                  className={inputClass}
                />
              </Field>
              <Button type="submit" variant="primary" busy={busy} className="w-full">
                {isRegister ? "Create account" : "Sign in"}
              </Button>
            </form>
          )}
          {(isRegister || !closed) && (
            <p className="mt-4 text-center text-sm text-ink-2">
              {isRegister ? "Already have an account? " : "New to LegacyLens? "}
              <Link href={isRegister ? "/login" : "/register"} className="font-medium text-accent hover:underline">
                {isRegister ? "Sign in" : "Create an account"}
              </Link>
            </p>
          )}
        </div>
      </section>
    </main>
  );
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  return (
    <Suspense>
      <AuthFormInner mode={mode} />
    </Suspense>
  );
}
