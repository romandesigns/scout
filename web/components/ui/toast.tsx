"use client";

import * as React from "react";
import { Toast as BaseToast } from "@base-ui/react/toast";

// shadcn Base UI toast (https://ui.shadcn.com/docs/components/base/toast), used specifically
// for the plain-browser client (not the installed Tauri desktop app, which uses native OS
// toast, and not the installed mobile PWA, which uses native device push) -- see the
// 2026-08-19 per-client notification priority: native toast/push is primary everywhere it's
// available; this in-page toast is what a tab-open browser session gets instead, since
// browser tabs have no OS-level notification surface of their own to borrow.

type ScoutToastData = { deepLink?: string };

export const toastManager = BaseToast.createToastManager<ScoutToastData>();

export function ScoutToastProvider({ children }: { children: React.ReactNode }) {
  return (
    <BaseToast.Provider toastManager={toastManager}>
      {children}
      <ScoutToastViewport />
    </BaseToast.Provider>
  );
}

function ScoutToastViewport() {
  const { toasts, close } = BaseToast.useToastManager<ScoutToastData>();
  return (
    <BaseToast.Portal>
      <BaseToast.Viewport className="scout-toast-viewport">
        {toasts.map((toast) => (
          <BaseToast.Root
            key={toast.id}
            toast={toast}
            className="scout-toast-root"
            data-type={toast.type}
            role={toast.data?.deepLink ? "link" : undefined}
            tabIndex={toast.data?.deepLink ? 0 : undefined}
            onClick={(event) => {
              if (!toast.data?.deepLink || (event.target as HTMLElement).closest("button")) return;
              window.dispatchEvent(new CustomEvent("scout:open-finding", { detail: toast.data.deepLink }));
              close(toast.id);
            }}
            onKeyDown={(event) => {
              if (!toast.data?.deepLink || !["Enter", " "].includes(event.key)) return;
              event.preventDefault();
              window.dispatchEvent(new CustomEvent("scout:open-finding", { detail: toast.data.deepLink }));
              close(toast.id);
            }}
          >
            <BaseToast.Title className="scout-toast-title" />
            <BaseToast.Description className="scout-toast-description" />
            <BaseToast.Close aria-label="Dismiss" className="scout-toast-close">
              ×
            </BaseToast.Close>
          </BaseToast.Root>
        ))}
      </BaseToast.Viewport>
    </BaseToast.Portal>
  );
}
