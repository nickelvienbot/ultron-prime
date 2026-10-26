# Harvest — requirement set extracted from mission-control-v2

Extracted 2026-10-26 so that `ultron-prime` can be built **from zero** without silently losing the
guarantees v2 earned through five independent review gates. v2's folder is deleted after this; these
documents are what survives.

## Contents

| File | Lines | What it holds |
|---|---:|---|
| `REQUIREMENTS-command.md` | 554 | Security / command plane: capability registry, approval desk, run ledger, emergency stop, request auth, shared state. **46 invariants.** |
| `REQUIREMENTS-truth.md` | 833 | Observation / truth / provenance: the `SourceMeta` contract, freshness policy, 10 modules, per-domain signal sets, redaction + allowlists. **54 invariants.** |
| `PRESERVED.md` | 30 | Voice scripts copied out before deletion (SHA-256 verified). |
| `preserved/` | — | `ultron-wake.py`, `stt.py`, `enroll-voice.py`, `backup-local-state.sh`. |

**100 invariants total.** These are the executable specification the Phase 0 invariant suite encodes.

Everything below was **verified against the live source**, not taken from the workers' summaries.

---

## Delta list — what the rewrite must change, not reproduce

The specs describe v2 as it was. These items must be **deliberately changed**, because v2 is being
deleted or because the spec itself flagged them as fragile.

### 1. `ultron-mcv2.service` must be removed everywhere — 3 code sites, 5 doc mentions

Verified in source:

| Site | Content |
|---|---|
| `src/lib/capabilities.ts:60-64` | capability `svc.restart-ultron` — template and args both target `ultron-mcv2.service` |
| `src/lib/observation-sources.ts:15` | `APPROVED_SERVICE_UNITS` includes `ultron-mcv2.service` |

The capability and the allowlist entry both die with the unit. Replace with the new service unit.

### 2. `ultron-wake.service` — decide deliberately

It is in the allowlist, its service is disabled, and voice is parked. Recommendation: **keep it
allowlisted and rendered as `inactive`** — the honest state — rather than hiding it. Do not re-enable.

### 3. Hardcoded absolute binary paths → deployment config

v2 hardcodes `/usr/bin/systemctl`, `/home/walker/.local/bin/hermes`,
`/home/walker/.local/bin/router-status`. **All three verified present on this machine** (so the spec
is accurate), but v2's own tests assert the literals. A rewrite should parameterise them rather than
bake in one machine's layout.

### 4. `ROUTER_MODELS_URL` — verified live, keep it

`http://127.0.0.1:8090/v1/models`, guarded by an **exact-match** allowlist check. Confirmed listening
on 8090 at harvest time, so this is current, not stale. Keep the exact-match guard; do not loosen it
to a prefix or host check.

### 5. `OVERLAY_TOKEN` — weakest point in the design

Falls back to the literal `"dev-local-token-not-for-production"`, and comparison is **plain string
equality, not timing-safe**. The spec flags this. A rewrite should: drop the dev fallback in
production paths, use a timing-safe compare, and keep the token out of the systemd unit file (v2 put
it in a plain `Environment=` line).

### 6. `randomId` uses `Math.random`, not a CSPRNG

Fine — ids are identifiers, not secrets. The rule to carry forward: **never use a run id or approval
id as a bearer credential.**

### 7. Error strings are load-bearing

Tests assert substrings including `"validation"` and `"paused"`. Keep them, or change them and the
tests together, deliberately.

### 8. One-tap execution keys off the wrong field

`runReadCapability` allows any capability with `requiresApproval === false`, rather than checking
`risk === "read"`. Today only one capability qualifies, so it is safe by accident. **Tighten it**:
require both `risk === "read"` and `requiresApproval === false`.

### 9. State directory — a real decision

v2 used `~/.local/share/ultron-mission-control/`, which also holds the surviving run history
(2 approvals, 1 run, 4 logs) and is protected by `PHASE2_STATE_DIR` as a test seam. Options: inherit
that path so the history continues, or start a clean path for `ultron-prime`. **Needs Sir's call** —
nothing is lost either way, but inheriting means the old ledger rows show up in the new console.

### 10. Keep these or the tests cannot run

- `execFn` injection seam — lets tests avoid the process table entirely. Required.
- `PHASE2_STATE_DIR` env override — test-only, never set in production. Required.
- The `T | { error: string }` return convention, discriminated with `"error" in out`. Load-bearing
  across nearly every store function.
- `updatedAt: "never"` sentinel for a missing emergency file. Keep it or replace it consciously.

---

## Verification performed on this harvest

- Both files exist with the claimed content: 554 and 833 lines.
- Claimed constants checked against live source: `APPROVAL_TTL_MS = 30 * 60 * 1000` ✓,
  `EXEC_TIMEOUT_MS = 30_000` ✓, `TAIL_LIMIT = 4096` ✓, `execFile` (no shell) ✓,
  `APPROVED_SERVICE_UNITS` 5-tuple ✓.
- v2 source, tests and scripts were **not modified** during the harvest (`find -newermt` returned
  nothing); folder intact at 726 MB.
- Hardcoded binaries and the router health URL confirmed live.
- Worker self-reports were **not** trusted on their own — every claim above was re-checked.

## Not harvested

- v2's UI (3,375 lines) — being rewritten from zero; only requirements were taken.
- v2's 23 test files — read as requirement sources; their behaviour is captured as the 100 invariants.
- `node_modules`, `.next` — regenerable.