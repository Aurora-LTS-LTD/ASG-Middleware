"use client";

import { useState } from "react";
import { onboarding, ApiClientError } from "@/lib/api/client";
import { REQUIRED_DOCS_BY_STRUCTURE, DOC_LABELS } from "@/types/onboarding";
import type { LegalStructure } from "@/types/onboarding";
import { Button } from "@/components/ui/button";
import { StepCard, ErrorBanner, StubNotice, StepProps } from "./common";

type DocState = "idle" | "uploading" | "done" | "error";

export function DocumentsStep({ token, state, onAdvance }: StepProps) {
  const identity = (state.draft_payload?.identity ?? {}) as { legal_structure?: LegalStructure };
  const legal: LegalStructure = identity.legal_structure ?? "osek_morshe";
  const required = REQUIRED_DOCS_BY_STRUCTURE[legal] ?? [];

  const [statuses, setStatuses] = useState<Record<string, DocState>>({});
  const [error, setError] = useState<string | null>(null);
  const [advancing, setAdvancing] = useState(false);

  async function handleFile(docType: string, file: File | undefined) {
    if (!file) return;
    setError(null);
    setStatuses((s) => ({ ...s, [docType]: "uploading" }));
    const mime = file.type || "application/pdf";
    try {
      const init = await onboarding.initUpload(token, docType, mime, file.size);
      await onboarding.putBytes(init.upload_url, file, mime);
      const fin = await onboarding.finalizeUpload(token, init.doc_id);
      setStatuses((s) => ({ ...s, [docType]: "done" }));

      if (fin.advanced_to_next_step) {
        setAdvancing(true);
        const next = await onboarding.state(token);
        onAdvance(next.current_step);
      }
    } catch (err) {
      setStatuses((s) => ({ ...s, [docType]: "error" }));
      setError(err instanceof ApiClientError ? err.detail.message || "Upload failed." : "Network error during upload.");
    }
  }

  const allDone = required.every((t) => statuses[t] === "done");

  return (
    <StepCard title="Identity documents" description="Upload the documents required to verify your business with the tax authority.">
      <ErrorBanner message={error} />
      <StubNotice>
        Test mode (KYC_BACKEND=stub): files are stored locally for the demo and not sent to cloud storage. In
        production these go to encrypted GCS via signed URLs.
      </StubNotice>

      <div className="space-y-3">
        {required.map((docType) => {
          const st = statuses[docType] ?? "idle";
          return (
            <div key={docType} className="flex items-center justify-between gap-4 rounded-lg border border-border bg-background/40 px-4 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{DOC_LABELS[docType] ?? docType}</p>
                <p className="text-xs text-muted-foreground">
                  {st === "done" ? "✓ Uploaded" : st === "uploading" ? "Uploading…" : st === "error" ? "Failed — try again" : "PDF, JPG or PNG · max 10 MB"}
                </p>
              </div>
              <label className="shrink-0">
                <input
                  type="file"
                  accept=".pdf,.jpg,.jpeg,.png"
                  className="hidden"
                  disabled={st === "uploading" || advancing}
                  onChange={(e) => handleFile(docType, e.target.files?.[0])}
                />
                <span className={`inline-flex cursor-pointer items-center rounded-md border border-border px-3 py-1.5 text-xs font-medium transition-colors ${st === "done" ? "text-emerald-400" : "text-foreground hover:bg-accent"}`}>
                  {st === "done" ? "Replace" : "Choose file"}
                </span>
              </label>
            </div>
          );
        })}
      </div>

      {allDone && !advancing && (
        <Button
          className="mt-4 w-full bg-indigo-600 text-white hover:bg-indigo-700"
          onClick={async () => {
            setAdvancing(true);
            try {
              const next = await onboarding.state(token);
              onAdvance(next.current_step);
            } catch {
              setError("Couldn't move to the next step. Please try again.");
              setAdvancing(false);
            }
          }}
        >
          Continue
        </Button>
      )}
      {advancing && <p className="mt-4 text-center text-sm text-muted-foreground">Continuing…</p>}
    </StepCard>
  );
}
