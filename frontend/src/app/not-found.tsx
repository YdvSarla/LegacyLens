import { SearchX } from "lucide-react";
import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-6 text-center">
      <SearchX className="mb-3 size-8 text-ink-3" aria-hidden />
      <h1 className="text-lg font-semibold">This page does not exist</h1>
      <p className="mt-1 text-sm text-ink-2">The link may be old, or the page may have moved.</p>
      <Link href="/projects" className="mt-4 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover">Go to projects</Link>
    </main>
  );
}
