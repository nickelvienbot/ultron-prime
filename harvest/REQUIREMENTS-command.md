# REQUIREMENTS — Command Plane (Phase 2)

Reverse-engineered **requirements specification** for the Phase 2 Command Plane of
`mission-control-v2`. This document describes *what must be true*, not code. It is
sufficient to reimplement the six modules from scratch. Field names and type
signatures are contracts and are reproduced verbatim; implementation bodies are
described, not copied.

## Reading scope

Modules read in full (6):
1. `src/lib/run-ledger.ts` (M3 — run ledger + allowlisted executor)
2. `src/lib/capabilities.ts` (M1 — capability registry)
3. `src/lib/approval-store.ts` (M2 — approval desk store)
4. `src/lib/emergency-stop.ts` (M4 — emergency stop)
5. `src/lib/phase2-auth.ts` — request authorization
6. `src/lib/phase2-state.ts` — shared state directory helpers

Supporting module consulted for cross-reference (not one of the six, needed for the
environment-dependent section): `src/lib/token.ts`.

Test files read in full (4):
`tests/approval-store.test.ts`, `tests/capabilities.test.ts`,
`tests/emergency-stop.test.ts`, `tests/run-ledger.test.ts`.

---

## Module 1 — `phase2-state.ts` (shared state layer)

### 1.1 Purpose
Provide the single canonical location and safe read/write/atomic primitives for all
Command Plane durable state, plus clock and id helpers.

### 1.2 Interfaces and contracts

- State directory constant: `PHASE2_STATE_DIR`
  - value = `process.env.PHASE2_STATE_DIR` if set, else
    `path.join(process.env.HOME || process.env.USERPROFILE || "/home/walker", ".local/share/ultron-mission-control")`.
  - Comment contract: the env override exists for tests and is never set in production.
- Derived path constants:
  - `APPROVALS_FILE` = `PHASE2_STATE_DIR/approvals.json`
  - `RUNS_FILE` = `PHASE2_STATE_DIR/runs.json`
  - `EMERGENCY_FILE` = `PHASE2_STATE_DIR/emergency.json`
  - `RUNS_DIR` = `PHASE2_STATE_DIR/runs`
- `writeJsonFile(file: string, data: unknown): Promise<void>`
- `readJsonFile<T>(file: string, fallback: T): Promise<T>`
- `nowIso(): string` — returns `new Date().toISOString()`.
- `randomId(len = 16): string` — random lowercase-hex string, default length 16,
  alphabet `0123456789abcdef`.

### 1.3 Rules and invariants
- **Atomic write**: `writeJsonFile` must `mkdir -p` the parent dir, write
  `JSON.stringify(data, null, 2)` to a temp file named
  `${file}.tmp-${process.pid}-${Date.now()}`, then `rename` temp → final so a reader
  never observes a torn file. Serialized output is pretty-printed with 2-space indent.
- **Fail-open read**: `readJsonFile` returns the `fallback` on *any* error (missing
  file, unreadable, malformed JSON). It never throws.
- `randomId` is not required to be cryptographically secure — it is an identifier
  source, not a secret.

### 1.4 Failure behaviour
- Missing directory/file on read ⇒ fallback value, no throw.
- Writes create directories as needed.

### 1.5 Storage / format
- Root dir: `~/.local/share/ultron-mission-control/` (XDG data path, matching the
  `ultron-voiceprint` / `ultron-memory` convention).
- All persisted state is JSON (2-space indented). Logs go under `RUNS_DIR`.

---

## Module 2 — `capabilities.ts` (capability registry, M1)

### 2.1 Purpose
Define the closed, code-reviewed set of concrete allowlisted command templates —
with risk classes and parameter validation — that is the sole source of executable
commands. No free-form commands exist.

### 2.2 Interfaces and types

```ts
export type RiskClass = "read" | "safe" | "sensitive" | "denied";
export const RISK_CLASSES: RiskClass[]; // exactly the four values above, in that order

export interface CapabilityParameter {
  name: string;
  label: string;
  pattern: string;   // full-match regex source applied to the entire value
  example: string;
}

export interface Capability {
  id: string;
  label: string;
  description: string;
  risk: RiskClass;
  template: string;          // human preview of the exact command (display form)
  command: string;           // absolute executable path (no PATH resolution)
  args: string[];            // argv template; {param} placeholders substituted post-validation
  parameters: CapabilityParameter[];
  effect: string;
  requiresApproval: boolean;
}

export interface ResolvedCommand {
  command: string;
  args: string[];
}
```

Exported functions:
- `getCapability(id: string): Capability | undefined` — lookup by exact id.
- `resolveCommand(cap: Capability, params: Record<string, string>): ResolvedCommand | { error: string }`.

### 2.3 Enumerations
- **Risk classes** (exact values): `"read"`, `"safe"`, `"sensitive"`, `"denied"`.
- Risk → approval policy (enforced): `read` ⇒ `requiresApproval === false`;
  `safe` and `sensitive` ⇒ `requiresApproval === true`. (`denied` is a defined class
  with no instance in the current registry.)

### 2.4 The registry (`CAPABILITIES`) — required entries
The registry is an ordered `Capability[]`. The current set contains these six
capabilities; each field is a contract:

| id | risk | requiresApproval | command | args |
|---|---|---|---|---|
| `svc.check-gateway` | `read` | `false` | `/usr/bin/systemctl` | `["--user","is-active","hermes-gateway.service"]` |
| `svc.restart-ultron` | `sensitive` | `true` | `/usr/bin/systemctl` | `["--user","restart","ultron-mcv2.service"]` |
| `svc.restart-gateway` | `sensitive` | `true` | `/usr/bin/systemctl` | `["--user","restart","hermes-gateway.service"]` |
| `cron.pause` | `sensitive` | `true` | `/home/walker/.local/bin/hermes` | `["cron","pause","{cronId}"]` |
| `cron.resume` | `sensitive` | `true` | `/home/walker/.local/bin/hermes` | `["cron","resume","{cronId}"]` |
| `router.probe` | `safe` | `true` | `/home/walker/.local/bin/router-status` | `["--probe"]` |

Template strings (display form) exactly:
`systemctl --user is-active hermes-gateway.service`,
`systemctl --user restart ultron-mcv2.service`,
`systemctl --user restart hermes-gateway.service`,
`hermes cron pause {cronId}`, `hermes cron resume {cronId}`, `router-status --probe`.

Binary path constants used by the registry: `HERMES_BIN = "/home/walker/.local/bin/hermes"`,
`SYSTEMCTL_BIN = "/usr/bin/systemctl"`, `ROUTER_STATUS_BIN = "/home/walker/.local/bin/router-status"`.

Parameter declaration for `cron.pause` and `cron.resume`:
- `name: "cronId"`, `label: "Cron job id"`, `example: "29a11e29fb4b"`,
  `pattern` = `"^[0-9a-f]{12}$"` (cron ids are 12 lowercase-hex characters; verified
  against the live registry e.g. `ebefed2ee84d`, `29a11e29fb4b`).

### 2.5 Validation rules and invariants (enforced by `resolveCommand`)
1. **Unknown-parameter rejection**: every key in `params` must be a declared parameter
   name; any undeclared key ⇒ `{ error }`.
2. **Required parameters**: a declared parameter whose value is `undefined` or `""`
   ⇒ `{ error }`.
3. **Full-match pattern validation**: value must be a `string` and
   `new RegExp("^(?:" + pattern + ")$").test(raw)` must be true (anchored full match,
   pattern alternation-safe via non-capturing group). Non-string or non-matching ⇒
   `{ error }`.
4. **Placeholder substitution**: each `args` element has every `{name}` token
   (matching `/\{([a-zA-Z0-9_]+)\}/g`) replaced with the validated `params[name]`;
   unresolved names fall back to empty string. Returns `{ command, args }`.
5. `resolveCommand` never touches the filesystem or process table; all failures return
   `{ error }` and no executable result.

### 2.6 Failure and refusal behaviour
- Unknown capability id ⇒ `getCapability` returns `undefined`.
- Unknown param, missing/empty required param, or pattern mismatch ⇒ `{ error: string }`.
- Injection is refused by construction: shell metacharacters, whitespace, `;`, `&&`,
  `|`, newlines, quotes, and path traversal all fail the anchored full-match pattern.

### 2.7 Storage
- None. The registry is in-code. Templates are described as code-reviewed by Sentinel
  before release (release gate M7).

---

## Module 3 — `approval-store.ts` (approval desk store, M2)

### 3.1 Purpose
Durably record command-execution requests, their decisions, and execution linkage,
with append-only decisions and read-time expiry.

### 3.2 Interfaces and types

```ts
export const APPROVAL_TTL_MS = 30 * 60 * 1000; // 30 minutes

export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "executed";

export interface Approval {
  id: string;
  capabilityId: string;
  params: Record<string, string>;
  template: string;
  resolved: { command: string; args: string[] } | null;
  preview: string;
  requester: string;
  status: ApprovalStatus;
  requestedAt: string;
  expiresAt: string;
  decidedAt?: string;
  decidedBy?: string;
  note?: string;
  runId?: string;
}
```

Persisted store shape (the on-disk `ApprovalsStore`):
```ts
interface ApprovalStore { pending: Approval[]; decisions: Approval[]; }
```

Exported functions:
- `createApproval(input: { capabilityId: string; params: Record<string, string>; requester: string }): Promise<Approval | { error: string }>`
- `listApprovals(): Promise<{ pending: Approval[]; decisions: Approval[] }>`
- `decideApproval(id: string, decision: "approve" | "deny", decidedBy: string, note?: string): Promise<Approval | { error: string }>`
- `markExecuted(id: string, runId: string): Promise<Approval | { error: string }>`
- `getApproval(id: string): Promise<Approval | undefined>`

### 3.3 Enumerations
- **Approval status** (exact values): `"pending"`, `"approved"`, `"denied"`,
  `"expired"`, `"executed"`.
- **Decision input** (exact values): `"approve"`, `"deny"`.

### 3.4 Rules and invariants
- **TTL** = `APPROVAL_TTL_MS` (30 minutes). `expiresAt = requestedAt + TTL`.
- **Read-time expiry**: expiry is computed on read, not written by a timer.
  `applyExpiry` flips any `pending` item with `expiresAt < now` to `"expired"`
  (in the in-memory store returned to callers). Only `pending` items are expired;
  decided items keep their status.
- **Append-only decisions**: once an approval leaves `pending` it is never re-opened.
  `decideApproval` moves exactly one item from `pending` → `decisions`; the count of
  `decisions` only ever grows.
- **One-way transitions**: `pending → approved | denied → executed`.
  `markExecuted` only matches a decision whose status is `"approved"`.
- **Preview**: `preview` = `` `${resolved.command} ${resolved.args.join(" ")}` `` —
  the exact command line shown to the operator.
- **Requester default**: `requester = input.requester || "sir"`.
- `getApproval` searches `decisions` first, then `pending`.

### 3.5 Failure and refusal behaviour
- `createApproval`:
  - unknown capability ⇒ `{ error: 'Unknown capability "<id>"' }`.
  - emergency ACTIVE ⇒ `{ error: "Emergency stop is ACTIVE — new approval requests are paused." }`.
  - parameter validation failure ⇒ `{ error }` from `resolveCommand`.
- `decideApproval`:
  - id not found in `pending` (unknown or already decided) ⇒
    `{ error: "Approval not found or already decided" }`.
  - item not `pending` ⇒ `{ error: "Approval is already <status>" }`.
  - `approve` while emergency ACTIVE ⇒ `{ error: "Emergency stop is ACTIVE — approvals are paused." }`
    (deny is not gated by emergency stop).
  - Approved/denied items with a past `expiresAt` cannot be approved (expiry checked
    before the transition; expiry compare uses `expiresAt < now`).
- `markExecuted`: no matching `approved` decision ⇒ `{ error: "Approved approval not found" }`.

### 3.6 Storage / format
- File: `~/.local/share/ultron-mission-control/approvals.json` (`APPROVALS_FILE`).
- Format: JSON object `{ "pending": Approval[], "decisions": Approval[] }`,
  2-space indent, written via the atomic temp-then-rename helper.
- Missing/corrupt file ⇒ treated as `{ pending: [], decisions: [] }`.

---

## Module 4 — `emergency-stop.ts` (emergency stop, M4)

### 4.1 Purpose
Provide a durable, single-sourced global kill switch whose ACTIVE state pauses
approval creation and blocks execution.

### 4.2 Interfaces and types

```ts
export interface EmergencyState {
  status: "ACTIVE" | "DISABLED";
  updatedAt: string;
  reason?: string;
}
```

Exported functions:
- `readEmergency(): Promise<EmergencyState>`
- `isEmergencyActive(): Promise<boolean>`
- `setEmergency(status: "ACTIVE" | "DISABLED", reason?: string): Promise<EmergencyState>`

### 4.3 Enumerations
- **Emergency status** (exact, uppercase values): `"ACTIVE"`, `"DISABLED"`.

### 4.4 Rules and invariants
- **Single sourced truth**: the on-disk `emergency.json` is authoritative. Default is
  `DISABLED` unless the stored `status` is exactly `"ACTIVE"` (any other/missing value
  normalises to `DISABLED`).
- `readEmergency` returns `updatedAt` = stored value or the literal string `"never"`
  when absent, and passes `reason` through (may be `undefined`).
- `setEmergency` stamps `updatedAt = nowIso()` and persists immediately.
- When ACTIVE: new approval requests are paused and execution is blocked (enforced in
  M2 and M3, not here).

### 4.5 Failure behaviour
- Missing file ⇒ `{ status: "DISABLED", updatedAt: "never" }`, `isEmergencyActive()` ⇒ `false`.

### 4.6 Storage / format
- File: `~/.local/share/ultron-mission-control/emergency.json` (`EMERGENCY_FILE`).
- Format: `{ "status": "ACTIVE"|"DISABLED", "updatedAt": "<ISO8601>", "reason"?: string }`.

---

## Module 5 — `run-ledger.ts` (run ledger + allowlisted executor, M3)

### 5.1 Purpose
Execute an approved capability's resolved argv via `child_process.execFile` (no
shell), record every attempt append-only, and capture an evidence log.

### 5.2 Interfaces and types

```ts
export type RunStatus = "running" | "completed" | "failed" | "blocked";

export interface RunRecord {
  runId: string;
  approvalId: string;
  capabilityId: string;
  requester: string;
  approvedBy: string;
  template: string;
  command: string;
  args: string[];
  startedAt: string;
  endedAt?: string;
  exitCode?: number;
  signal?: string | null;
  status: RunStatus;
  evidencePath?: string;
  stdoutTail?: string;
  error?: string;
}

export interface ExecResult {
  exitCode: number;
  stdout: string;
  stderr: string;
  signal: string | null;
}

export type ExecFn = (command: string, args: string[], timeoutMs: number) => Promise<ExecResult>;
```

Persisted store shape: `interface RunStore { runs: RunRecord[]; }`.

Constants: `EXEC_TIMEOUT_MS = 30_000`, `TAIL_LIMIT = 4096`.

Exported functions:
- `listRuns(): Promise<RunRecord[]>`
- `executeApprovedRun(input: { approvalId: string; approvedBy: string; execFn?: ExecFn }): Promise<RunRecord | { error: string }>`
- `runReadCapability(input: { capabilityId: string; params: Record<string, string>; approvedBy: string; execFn?: ExecFn }): Promise<RunRecord | { error: string }>`

Internal (input shapes): `RunInput = { approvalId, approvedBy, execFn? }`,
`OneTapInput = { capabilityId, params, approvedBy, execFn? }`.

### 5.3 Enumerations
- **Run status** (exact values): `"running"`, `"completed"`, `"failed"`, `"blocked"`.

### 5.4 Execution contract and invariants
1. **execFile only** — the default executor uses `child_process.execFile` with options
   `{ timeout: 30000, maxBuffer: 1024*1024, windowsHide: true }`. No shell, no string
   concatenation. (Modeled as `ExecFn` so tests inject a fake and never touch the
   process table.)
2. **Exit-code mapping**: exit `0` ⇒ status `"completed"`; non-zero ⇒ `"failed"`.
   Exec error object → numeric `code`, else `exitCode`, else `1`; `signal` defaults `null`.
3. **Defense in depth / run-time re-validation**: before executing, the executor
   re-fetches the capability from the registry and re-runs `resolveCommand` against the
   *stored* params. A tampered approval can never execute; failure → blocked.
4. **Emergency gate**: if emergency is ACTIVE, execution is refused and a blocked run is
   recorded.
5. **Ordered gates** in `executeApprovedRun`: (a) approval exists; (b) status is
   `approved`; (c) not expired (`expiresAt >= now`); (d) emergency not active;
   (e) capability still exists; (f) run-time validation passes.
6. **Append-only ledger**: every attempt appends exactly one `RunRecord` to `runs.json`.
7. **Evidence receipt**: stdout/stderr written to `RUNS_DIR/<runId>.log`; content is
   `stdout` then, if stderr non-empty, `"\n--- stderr ---\n" + stderr`. Created with
   `mkdir -p RUNS_DIR`.
8. **stdoutTail**: `stdout` plus `"\n[stderr] "+stderr` when stderr present, truncated
   to the last `TAIL_LIMIT` (4096) characters.
9. **Linkage**: on success the approval is marked executed with the new `runId`
   (`markExecuted`).
10. **Blocked records**: `recordBlocked` appends a `RunRecord` with
    `approvalId: "blocked"`, `requester: "sir"`, empty `command`/`args`, and equal
    `startedAt`/`endedAt`, status `"blocked"`, carrying `error` = reason. It does not
    touch the process table and returns `{ error: reason }`.
11. **One-tap read path**: `runReadCapability` is allowed only for capabilities with
    `requiresApproval === false`. It creates an approval, immediately records an
    `approve` decision (note `"one-tap read action"`), then executes — so a
    read-class run still produces an approval + decision + run triple.
12. **Identifiers**: each run `runId = randomId(16)`. Timestamps via `nowIso()`.

### 5.5 Failure and refusal behaviour
- Approval not found ⇒ `{ error: "Approval not found" }`.
- Status not approved ⇒ `{ error: "Approval is not approved (<status>)" }`.
- Expired ⇒ `{ error: "Approval expired — request a fresh approval" }`.
- Emergency ACTIVE ⇒ blocked record + `{ error: "Emergency stop is ACTIVE — execution blocked" }`.
- Capability gone ⇒ `{ error: "Capability <id> no longer exists" }`.
- Run-time validation failure ⇒ blocked record +
  `{ error: "Run-time validation failed: <detail>" }`.
- `runReadCapability`: unknown capability ⇒ `{ error: 'Unknown capability "<id>"' }`;
  capability requires approval ⇒
  `{ error: "Capability <id> requires the approval desk (one-tap is read-only)" }`;
  create/decide failures propagate as `{ error }`.

### 5.6 Storage / format
- Ledger file: `~/.local/share/ultron-mission-control/runs.json` (`RUNS_FILE`),
  shape `{ "runs": RunRecord[] }`, 2-space indent, atomic write.
- Evidence dir: `~/.local/share/ultron-mission-control/runs/` (`RUNS_DIR`), one
  `<runId>.log` per executed run.
- Missing/corrupt ledger ⇒ `{ runs: [] }`.

---

## Module 6 — `phase2-auth.ts` (request authorization)

### 6.1 Purpose
Authorize incoming Next.js API requests: allow same-machine (localhost) origins, and
otherwise require the overlay token header.

### 6.2 Interfaces
- `isAuthorized(req: NextRequest): boolean`
- Depends on `OVERLAY_TOKEN` (from `token.ts`).

### 6.3 Rules and invariants
- Local origin passes: request header `origin` starting with `http://localhost` **or**
  `http://127.0.0.1` ⇒ authorized (any port; host is what matters for a local-only
  service).
- Otherwise authorized iff header `x-ultron-token` strictly equals `OVERLAY_TOKEN`.
- `OVERLAY_TOKEN` = `process.env.OVERLAY_TOKEN || "dev-local-token-not-for-production"`
  (server-side single source of truth; the extension and wake service carry literals).

### 6.4 Failure behaviour
- No local origin and no/mismatched token ⇒ returns `false` (caller refuses the request).
- `https://localhost` is **not** matched (prefix check is `http://`).

### 6.5 Storage
- None. Token supplied via environment (systemd unit `Environment=OVERLAY_TOKEN=...`).

---

## INVARIANTS ASSERTED BY TESTS

Numbered list of distinct testable behaviours asserted by the four test files.
Each one is a one-line, independently checkable statement.

**Capability registry (`capabilities.test.ts`)**
1. Every capability has a non-empty unique `id`; the registry contains no duplicate ids.
2. Every capability has non-empty `label`, `description`, `template`, and `effect`.
3. Every capability has a non-empty `command` and a non-empty `args` array.
4. Every capability's `risk` is a member of `RISK_CLASSES` (`read|safe|sensitive|denied`).
5. Every capability's `requiresApproval` is a boolean.
6. Every `{placeholder}` appearing in a capability's `args` corresponds to a declared parameter.
7. Every declared parameter is used by at least one `{placeholder}` in `args`.
8. Every parameter `pattern` compiles as a RegExp and matches its own `example`.
9. `risk === "read"` ⇒ `requiresApproval === false`; `risk === "safe"|"sensitive"` ⇒ `requiresApproval === true`.
10. `resolveCommand` returns the exact `command` and `args` for valid parameters
    (`cron.pause` + `29a11e29fb4b` ⇒ `/home/walker/.local/bin/hermes`, `["cron","pause","29a11e29fb4b"]`).
11. `resolveCommand` rejects every shell-injection value (command-substitution, `&&` chain, `|` pipe, newline, quote, trailing space, path traversal).
12. `resolveCommand` rejects a missing required parameter with an error.
13. `resolveCommand` rejects an unknown/extra parameter with an error.
14. `resolveCommand` rejects any extra parameter supplied to a parameter-less capability.
15. `getCapability` returns `undefined` for an unknown id.
16. A fixed (non-parameterized) capability resolves verbatim (`svc.check-gateway` ⇒ `/usr/bin/systemctl`, `["--user","is-active","hermes-gateway.service"]`).

**Approval desk (`approval-store.test.ts`)**
17. `createApproval` rejects an unknown capability with an error result.
18. `createApproval` rejects an invalid parameter value with an error whose text includes "validation".
19. `createApproval` returns a `pending` approval with an `id` of length ≥ 8.
20. `createApproval` sets `resolved.command` ending in `hermes` and a `preview` containing the exact command line (`hermes cron pause 29a11e29fb4b`).
21. `createApproval` sets `expiresAt` ≈ 30 min after `requestedAt` (TTL > 0 and ≤ 31 min).
22. `listApprovals` surfaces pending items; `decisions` is empty before any decision.
23. `decideApproval("approve")` transitions the item to `approved` and records `decidedBy`.
24. `decideApproval` records the supplied `note` on the decided approval.
25. `decideApproval("deny")` transitions the item to `denied`.
26. `decideApproval` on an unknown id returns an error.
27. `decideApproval` on an already-decided approval returns an error (no re-decision).
28. `decideApproval` on an expired approval returns an error (cannot be approved).
29. Each decision appends exactly one entry to the `decisions` log (append-only).
30. `markExecuted` sets status `executed` and links the given `runId`.

**Emergency stop (`emergency-stop.test.ts`)**
31. With no state file, `readEmergency` returns `DISABLED` and `isEmergencyActive()` is `false`.
32. `setEmergency("ACTIVE", reason)` persists `status=ACTIVE` + `reason` and `isEmergencyActive()` becomes `true`.
33. `setEmergency("DISABLED")` persists and `isEmergencyActive()` becomes `false`.
34. While ACTIVE, `createApproval` returns an error whose text includes "paused".
35. `emergency.json` is the single sourced truth: raw file records `status`, `reason`, and `updatedAt`.

**Run ledger / executor (`run-ledger.test.ts`)**
36. A successful execution yields `status=completed`, `exitCode=0`, and a `stdoutTail` containing the stdout text.
37. A successful execution writes a non-empty evidence file that contains the stdout.
38. A non-zero exit marks the run `failed` with the recorded exit code.
39. `executeApprovedRun` rejects an unknown approval id with an error.
40. `executeApprovedRun` rejects a denied approval with an error.
41. `executeApprovedRun` rejects an expired approved approval (past `expiresAt`) with an error.
42. While emergency is ACTIVE, execution returns an error and a `blocked` run is recorded in the ledger.
43. Tampered stored `params`/`resolved` fail run-time re-validation and are rejected before execution.
44. The ledger is append-only: each execution appends exactly one run record.
45. `runReadCapability` runs a read capability without a prior approval, returns `completed`, and still records an `approvalId` linkage.
46. `runReadCapability` is refused for a non-read (sensitive) capability with an error.

**Total distinct invariants: 46.**

---

## AMBIGUOUS OR ENVIRONMENT-DEPENDENT

1. **Absolute binary paths.** `capabilities.ts` hardcodes `/usr/bin/systemctl`,
   `/home/walker/.local/bin/hermes`, `/home/walker/.local/bin/router-status`. These are
   machine-specific; a rewrite must parameterise them or accept them as deployment
   config. Tests assert the literal `/usr/bin/systemctl` and `/home/walker/.local/bin/hermes`.
2. **`PHASE2_STATE_DIR` env var.** Overrides the state root; documented as test-only,
   never set in production. All four module tests set it to a `mkdtemp` directory.
3. **`HOME`/`USERPROFILE` fallback.** State path derivation falls back through
   `HOME` → `USERPROFILE` → the literal `/home/walker`. Production assumes
   `~/.local/share/ultron-mission-control/`.
4. **`OVERLAY_TOKEN` env var.** Falls back to the literal
   `"dev-local-token-not-for-production"`. Auth strength depends entirely on the
   deployment setting this; the fallback is explicitly not for production. Token
   comparison is a plain string equality (no timing-safe compare).
5. **Localhost-only trust.** `phase2-auth` trusts any request whose `origin` begins with
   `http://localhost` or `http://127.0.0.1` — port is ignored by design. Note
   `https://localhost` and `[::1]` are not matched, and a request with **no** `origin`
   header falls through to the token check.
6. **Expiry boundary.** Expiry compares `expiresAt < now` (strictly less-than); an
   approval exactly at its expiry instant is still valid this tick. Tests force expiry
   by rewriting the JSON file on disk (1000 ms in the past) rather than waiting.
7. **Approval-id / signature characteristics.** `randomId` uses `Math.random`, not a CSPRNG.
   Tests inject fixed ids like `deadbeefdeadbeef` for the "unknown id" case.
8. **Exact error-message substrings are relied upon.** Tests assert messages include
   `"validation"` (approval param failure) and `"paused"` (emergency gate). A rewrite
   must preserve these substrings, or the tests must change.
9. **Cron id format assumption.** `CRON_ID_PATTERN = "^[0-9a-f]{12}$"` is claimed verified
   against a live cron registry (examples `ebefed2ee84d`, `29a11e29fb4b`). If real ids
   ever deviate (length/case), validation would reject legitimate ids.
10. **`blocked` run id semantics.** Blocked records store `approvalId: "blocked"` (a
    sentinel, not a real approval id) and `requester: "sir"` hardcoded — a magic-string
    coupling that a rewrite should make explicit.
11. **`requiresApproval` vs one-tap.** One-tap (`runReadCapability`) keys off
    `requiresApproval === false`, not off `risk === "read"`. Currently
    `svc.check-gateway` is the only such capability; if a non-read capability were
    given `requiresApproval: false` it would become one-tappable, contradicting the
    "read-only" intent expressed in comments.
12. **Emergency stop on `deny`.** The emergency gate applies only to `approve`; denying
    an approval is always permitted while ACTIVE. Stated in code, not asserted by tests.
13. **Executor injection seam.** `execFn` exists solely so tests avoid the process table;
    production always uses the `execFile` default. A rewrite must expose an equivalent
    injection point to keep the tests runnable.
14. **Return-type unions.** Nearly every store function returns `T | { error: string }`,
    and callers discriminate with `"error" in out`. This convention is load-bearing
    across modules and must be preserved.
15. **`updatedAt: "never"` sentinel.** `readEmergency` returns the literal string
    `"never"` (not an ISO date) when no file exists — an intentional sentinel a rewrite
    must keep or replace deliberately.
