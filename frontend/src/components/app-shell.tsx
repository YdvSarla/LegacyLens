"use client";

import { Check, ChevronDown, ChevronsUpDown, Cpu, FolderGit2, LogOut, Moon, ScanSearch, Sun, UserRound } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import useSWR, { SWRConfig, mutate } from "swr";
import { ApiError, fetcher, postJson } from "@/lib/api";
import type { Project, User } from "@/lib/types";
import { ToastProvider } from "./toast";
import { Skeleton } from "./ui";

function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) close();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

function useTheme() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const saved = document.documentElement.dataset.theme;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- The theme is only known in the browser.
    setDark(saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    try {
      localStorage.setItem("legacylens-theme", next ? "dark" : "light");
    } catch {
      // Storage can be blocked. The theme still applies to this page view.
    }
  }

  return { dark, toggle };
}

function UserMenu({ user }: { user: User | undefined }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  const { dark, toggle } = useTheme();
  const initials = (user?.name ?? "?").trim().split(/\s+/).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");

  async function signOut() {
    await postJson("/api/auth/logout");
    // Nothing from this account may remain in memory for the next person to sign in.
    await mutate(() => true, undefined, { revalidate: false });
    router.replace("/login");
  }

  const itemClass = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-2";

  return (
    <div ref={ref} className="relative">
      <button type="button" aria-label="Account menu" aria-expanded={open} onClick={() => setOpen(!open)} className="flex items-center gap-1 rounded-lg p-1 hover:bg-surface-2">
        <span className="grid size-7 place-items-center rounded-full bg-accent text-xs font-semibold text-on-accent">{initials}</span>
        <ChevronDown className="size-3.5 text-ink-3" aria-hidden />
      </button>
      {open && (
        <div role="menu" className="animate-in absolute right-0 z-40 mt-1 w-60 rounded-lg border border-line-strong bg-surface p-1 shadow-lg">
          <div className="border-b border-line px-2 py-2">
            <p className="truncate text-sm font-medium">{user?.name}</p>
            <p className="truncate text-xs text-ink-3">{user?.email}</p>
          </div>
          <div className="pt-1">
            <Link role="menuitem" href="/account" onClick={() => setOpen(false)} className={itemClass}>
              <UserRound className="size-4 text-ink-2" aria-hidden /> Account settings
            </Link>
            <button type="button" role="menuitem" onClick={toggle} className={itemClass}>
              {dark ? <Sun className="size-4 text-ink-2" aria-hidden /> : <Moon className="size-4 text-ink-2" aria-hidden />}
              {dark ? "Light theme" : "Dark theme"}
            </button>
            <button type="button" role="menuitem" onClick={signOut} className={itemClass}>
              <LogOut className="size-4 text-ink-2" aria-hidden /> Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ProjectSwitcher({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  const { data: projects } = useSWR<Project[]>("/api/projects", fetcher, { revalidateOnFocus: false });
  const current = projects?.find((project) => project.id === projectId);
  return (
    <div ref={ref} className="relative min-w-0">
      <button type="button" aria-label="Switch project" aria-expanded={open} onClick={() => setOpen(!open)} className="flex max-w-56 items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium hover:bg-surface-2">
        <span className="truncate">{current?.name ?? "Project"}</span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-ink-3" aria-hidden />
      </button>
      {open && (
        <div role="menu" className="animate-in scroll-thin absolute left-0 z-40 mt-1 max-h-80 w-72 overflow-y-auto rounded-lg border border-line-strong bg-surface p-1 shadow-lg">
          {projects?.map((project) => (
            <Link key={project.id} role="menuitem" href={`/projects/${project.id}`} onClick={() => setOpen(false)} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface-2">
              <span className="min-w-0 flex-1 truncate">{project.name}</span>
              {project.id === projectId && <Check className="size-4 shrink-0 text-accent" aria-hidden />}
            </Link>
          ))}
          <div className="mt-1 border-t border-line pt-1">
            <Link role="menuitem" href="/projects" onClick={() => setOpen(false)} className="block rounded-md px-2 py-1.5 text-sm text-ink-2 hover:bg-surface-2">All projects</Link>
          </div>
        </div>
      )}
    </div>
  );
}

export function useCurrentUser() {
  return useSWR<User>("/api/auth/me", fetcher, { revalidateOnFocus: false, shouldRetryOnError: false });
}

function Shell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ id?: string }>();
  const { data: user, error, isLoading } = useCurrentUser();

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      const here = window.location.pathname + window.location.search;
      router.replace(`/login?next=${encodeURIComponent(here)}`);
    }
  }, [error, router]);

  const signedOut = error instanceof ApiError && error.status === 401;
  const links = [
    { href: "/projects", label: "Projects", Icon: FolderGit2 },
    { href: "/settings", label: "AI providers", Icon: Cpu },
  ];

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-surface">
        <div className="flex h-13 items-center gap-1 px-3 sm:gap-2 sm:px-4">
          <Link href="/projects" className="mr-1 flex items-center gap-2 rounded-lg px-1 py-1 font-semibold tracking-tight">
            <ScanSearch className="size-5 text-accent" aria-hidden />
            <span className="hidden sm:inline">LegacyLens</span>
          </Link>
          {params.id && (
            <>
              <span aria-hidden className="text-line-strong">/</span>
              <ProjectSwitcher projectId={params.id} />
            </>
          )}
          <nav aria-label="Main" className="ml-auto flex items-center gap-1">
            {links.map(({ href, label, Icon }) => {
              const active = href === "/projects" ? pathname === "/projects" : pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-label={label}
                  aria-current={active ? "page" : undefined}
                  className={`flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm ${active ? "bg-surface-2 font-medium" : "text-ink-2 hover:bg-surface-2"}`}
                >
                  <Icon className="size-4" aria-hidden />
                  <span className="hidden md:inline">{label}</span>
                </Link>
              );
            })}
          </nav>
          <UserMenu user={user} />
        </div>
      </header>
      {isLoading || signedOut ? (
        <div className="mx-auto w-full max-w-5xl space-y-3 p-6"><Skeleton className="h-8 w-48" /><Skeleton className="h-40" /></div>
      ) : (
        children
      )}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  return (
    <SWRConfig
      value={{
        // A client error will not fix itself, so retrying would only delay the message.
        onErrorRetry: (error, _key, _config, revalidate, { retryCount }) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return;
          if (retryCount >= 3) return;
          window.setTimeout(() => revalidate({ retryCount }), 2000 * (retryCount + 1));
        },
        onError: (error, key) => {
          // The session ended while the app was open.
          if (error instanceof ApiError && error.status === 401 && key !== "/api/auth/me") {
            const here = window.location.pathname + window.location.search;
            router.replace(`/login?next=${encodeURIComponent(here)}`);
          }
        },
      }}
    >
      <ToastProvider>
        <Shell>{children}</Shell>
      </ToastProvider>
    </SWRConfig>
  );
}
