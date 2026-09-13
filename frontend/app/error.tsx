"use client";

import React, { useEffect } from "react";
import Link from "next/link";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log the error to client console for easy debugging
    console.error("StockPulse Client Error Boundary Caught:", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] px-4 text-center">
      <div className="bg-card border border-red-500/30 rounded-2xl p-8 max-w-lg w-full shadow-2xl space-y-4">
        <div className="text-4xl">⚠️</div>
        <h2 className="text-xl font-bold text-white tracking-tight">Something went wrong</h2>
        <p className="text-xs text-slate-400">
          An unexpected error occurred while rendering this view.
        </p>

        {error?.message && (
          <div className="bg-black/60 border border-border/80 rounded-lg p-3 text-left overflow-x-auto">
            <p className="font-mono text-xs text-red-400 break-words">{error.message}</p>
            {error.digest && (
              <p className="font-mono text-[10px] text-muted mt-1">Digest: {error.digest}</p>
            )}
          </div>
        )}

        <div className="flex items-center justify-center gap-3 pt-2">
          <button
            onClick={() => reset()}
            className="bg-accent text-black font-semibold text-xs px-4 py-2 rounded-lg hover:bg-accent/80 transition-colors"
          >
            Try Again
          </button>
          <Link
            href="/"
            className="bg-surface border border-border text-white text-xs px-4 py-2 rounded-lg hover:bg-surface/80 transition-colors"
          >
            Back to Dashboard
          </Link>
          <button
            onClick={() => window.location.reload()}
            className="text-xs text-muted hover:text-white transition-colors underline"
          >
            Reload Page
          </button>
        </div>
      </div>
    </div>
  );
}
