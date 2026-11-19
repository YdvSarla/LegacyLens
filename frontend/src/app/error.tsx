"use client";

import { TriangleAlert } from "lucide-react";
import { useEffect } from "react";

export default function ErrorPage({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main className="flex min-h-[70vh] flex-col items-center justify-center p-6 text-center">
      <TriangleAlert className="mb-3 size-8 text-serious" aria-hidden />
      <h1 className="text-lg font-semibold">This page could not be shown</h1>
      <p className="mt-1 max-w-md text-sm text-ink-2">Something went wrong in your browser. Your projects and data are not affected.</p>
      <button type="button" onClick={reset} className="mt-4 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover">Try again</button>
    </main>
  );
}
