/**
 * Identity module — the SINGLE place the product's name, marks and vocabulary live.
 *
 * Why this exists: concealment. No component may hardcode a product string or an
 * engine name. The old build leaked "Hermes" across 54 source occurrences and 14 files;
 * changing it meant a scavenger hunt. Here the rebrand is one file, and it stays done.
 *
 * Rule: if you are about to type the product name or the engine name in a component,
 * import it from here instead. The invariant suite enforces this.
 */

export const IDENTITY = {
  /** Product name. Reads as an engineering/OS utility. Reveals nothing. */
  name: "Ultron",
  /** Wordmark form for the status bar and splash. */
  wordmark: "ULTRON",
  /** Short form for tight spaces. */
  short: "ULT",
  /** What the surface calls itself in prose. */
  tagline: "Local Command Surface",
  /** Confinement label. Reinforces "this is a self-contained tool". */
  scopeLabel: "local",
} as const;

/**
 * Vocabulary map: internal domain terms → the words the UI is allowed to show.
 *
 * The engine's own name never appears in the interface. Anything that would name
 * the underlying runtime is translated here, once, instead of being patched per view.
 */
export const VOCABULARY = {
  engine: "core",
  agent: "operator",
  agentPlural: "operators",
  session: "run",
  cronJob: "scheduled task",
  cronJobs: "scheduled tasks",
  toolCall: "operation",
  tokenSpend: "resource use",
  provider: "route",
  approval: "authorization",
} as const;

export type VocabularyKey = keyof typeof VOCABULARY;

/** Resolve a vocabulary key, falling back to the key itself if unmapped. */
export function t(key: VocabularyKey): string {
  return VOCABULARY[key];
}

/**
 * Strings that must NEVER appear in user-visible output. The invariant suite greps
 * the built output against this list — it is the concealment test, in code.
 */
export const FORBIDDEN_STRINGS: readonly string[] = [
  "hermes",
  "nousresearch",
  "nous research",
  "openclaw",
] as const;