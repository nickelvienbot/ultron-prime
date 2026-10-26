import { IDENTITY } from "@/core/identity";

/**
 * Phase 0 placeholder.
 *
 * Deliberately shows the design system rather than pretending to be the product.
 * No data, no figures, no sources — because a number without provenance is the
 * exact failure mode this project exists to avoid. Real surfaces arrive in Phase 1
 * and only on top of the truth layer.
 */
export default function Page() {
  return (
    <main className="min-h-dvh p-8">
      <header className="panel-raised panel-chamfer max-w-3xl p-6">
        <p className="label text-steel-500">{IDENTITY.scopeLabel}</p>
        <h1 className="mt-2 text-4xl font-semibold tracking-tight">
          {IDENTITY.wordmark}
        </h1>
        <p className="mt-1 text-sm text-steel-300">{IDENTITY.tagline}</p>
      </header>

      <section className="panel-inset mt-6 max-w-3xl p-6">
        <h2 className="label text-prime-amber">Phase 0 — foundation</h2>
        <ul className="mt-3 space-y-2 text-sm text-steel-300">
          <li>Repository initialised with the requirement set harvested from the prior build.</li>
          <li>Design tokens and the identity module are in place.</li>
          <li>
            Next: the invariant suite — 100 extracted invariants written as executable
            tests before any feature code, so the rewrite cannot lose its guarantees.
          </li>
        </ul>
        <p className="mt-4 text-xs text-steel-500">
          No figures are displayed here on purpose. Every value this surface eventually
          shows must carry its source and observation time.
        </p>
      </section>
    </main>
  );
}