import { useEffect, useRef } from "react";
import { api, DashboardStats } from "../api/client";
import { StatusToast, useStatusToast } from "./StatusToast";

const SCAN_DONE_EVENT = "vmm:scan-done";
const REFRESH_DONE_EVENT = "vmm:indexes-done";

export function dispatchScanDone() {
  window.dispatchEvent(new Event(SCAN_DONE_EVENT));
}

export function dispatchIndexesDone() {
  window.dispatchEvent(new Event(REFRESH_DONE_EVENT));
}

/** Polls dashboard job flags and shows a toast for background startup work. */
export function BackgroundJobToaster() {
  const toast = useStatusToast(4000);
  const wasScanning = useRef(false);
  const wasRefreshing = useRef(false);
  // Don't steal the toast while a page-level action owns it via a different hook.
  // This toaster only reports server-side background jobs.

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const stats = await api.get<DashboardStats>("/api/dashboard");
        if (cancelled) return;

        if (stats.scan_in_progress) {
          wasScanning.current = true;
          toast.showBusy("Scanning plugins on disk…");
        } else if (wasScanning.current) {
          wasScanning.current = false;
          toast.showOk("Plugin scan finished.");
          dispatchScanDone();
        } else if (stats.package_refresh_in_progress) {
          wasRefreshing.current = true;
          toast.showBusy("Updating store indexes…");
        } else if (wasRefreshing.current) {
          wasRefreshing.current = false;
          toast.showOk("Store indexes updated.");
          dispatchIndexesDone();
        }
      } catch {
        // Not signed in yet / transient — ignore.
      }
    }

    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <StatusToast message={toast.message} tone={toast.tone} busy={toast.busy} />;
}

export { SCAN_DONE_EVENT, REFRESH_DONE_EVENT };
