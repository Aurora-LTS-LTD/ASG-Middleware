/**
 * Aurora LTS — Business Owner Portal (landing).
 *
 * Scaffold placeholder. Batch 2 replaces this with the real email+password
 * login wired to M1's business-owner auth; once signed in the owner lands on
 * the invoice dashboard.
 */
export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background p-12 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-indigo-600 text-2xl font-bold text-white shadow-lg shadow-indigo-500/20">
        A
      </div>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Aurora LTS — Business Portal
      </h1>
      <p className="max-w-md text-sm text-muted-foreground">
        Your invoices, their full lifecycle, and tax-compliance status — in one place.
        Secure sign-in arrives in the next batch.
      </p>
    </main>
  );
}
