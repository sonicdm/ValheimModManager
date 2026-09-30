import { useEffect, useState } from "react";

export type StatusTone = "info" | "ok" | "error";

export type StatusMessage = {
  text: string;
  tone?: StatusTone;
};

const toneClass: Record<StatusTone, string> = {
  info: "border-sea/30 bg-paper text-ink shadow-lg shadow-bark/10",
  ok: "border-moss/40 bg-paper text-ink shadow-lg shadow-bark/10",
  error: "border-danger/40 bg-paper text-danger shadow-lg shadow-bark/10",
};

/** Fixed corner popup for long-running dashboard / index actions. */
export function StatusToast({
  message,
  tone = "info",
  busy = false,
}: {
  message: string | null;
  tone?: StatusTone;
  busy?: boolean;
}) {
  if (!message) return null;
  return (
    <div
      className={`fixed bottom-6 right-6 z-50 max-w-md rounded-xl border px-4 py-3 text-sm ${toneClass[tone]}`}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        {busy && (
          <span
            className="mt-0.5 inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-sea/30 border-t-sea"
            aria-hidden
          />
        )}
        <p className="whitespace-pre-line leading-snug">{message}</p>
      </div>
      {busy && (
        <div className="mt-3 h-1 overflow-hidden rounded-full bg-mist">
          <div className="install-progress-bar h-full rounded-full bg-sea/70" />
        </div>
      )}
    </div>
  );
}

/** Track an in-progress / result toast; success clears after a short delay. */
export function useStatusToast(successMs = 3200) {
  const [message, setMessage] = useState<string | null>(null);
  const [tone, setTone] = useState<StatusTone>("info");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (busy || !message || tone === "info") return;
    const id = window.setTimeout(() => setMessage(null), successMs);
    return () => window.clearTimeout(id);
  }, [busy, message, tone, successMs]);

  function showBusy(text: string) {
    setTone("info");
    setBusy(true);
    setMessage(text);
  }

  function showOk(text: string) {
    setBusy(false);
    setTone("ok");
    setMessage(text);
  }

  function showError(text: string) {
    setBusy(false);
    setTone("error");
    setMessage(text);
  }

  function clear() {
    setBusy(false);
    setMessage(null);
    setTone("info");
  }

  return { message, tone, busy, showBusy, showOk, showError, clear };
}
