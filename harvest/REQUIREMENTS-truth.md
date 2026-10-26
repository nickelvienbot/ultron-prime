# REQUIREMENTS — Observation / Truth / Provenance Layer

Source of record (READ-ONLY, not modified): `/home/walker/ventures/mission-control-v2`
Target: a from-scratch reimplementation (Next.js 16 + React 19 + TypeScript).

This document specifies **requirements**, not code. Quoted type/interface signatures and
field names are normative and must be reproduced verbatim. Everything else is behaviour
to re-implement.

Guiding principle asserted throughout the source and its tests: **unavailable is honest.**
There are no mock/fixture fallbacks. Every read surface either returns real observed data
with provenance metadata, or returns an explicit `unavailable` envelope with a safe error.

---

## 1. THE SOURCE CONTRACT (heart of the system — exact)

Canonical module: `src/lib/observation.ts`.

### 1.1 Enum: observation state

```ts
export type ObservationState = "verified" | "observed" | "stale" | "unavailable" | "error";
```

Five allowed values, no others. Ordering as written is the canonical display order.
Note: `observed` and `error` are permitted values of the type but are **not currently
produced by the factory functions** (see §1.4); only `verified` and `unavailable` are
emitted by `observeValue`/`unavailable` today. `stale` is a reserved state reachable only
through `isStale` policy at a higher layer (currently unwired — see §2).

### 1.2 Interface: safe error

```ts
export interface SafeObservationError {
  code: string;
  safeMessage: string;
}
```

- `code`: machine-stable, snake_case-or-upper identifier for the class of failure.
- `safeMessage`: human-readable, **must not leak paths, prompts, credentials, or internals**.
  The word "safe" is a security requirement, not decoration.

### 1.3 Interface: SourceMeta (provenance envelope — reproduce exactly)

```ts
export interface SourceMeta {
  source: string;
  observedAt: string;
  sourceObservedAt?: string;
  verified: boolean;
  stale: boolean;
  state: ObservationState;
  error?: SafeObservationError;
}
```

Field-by-field requirements:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `source` | `string` | yes | Canonical identity of the upstream source. Either an absolute file path or a short symbolic label (see §5.3 for the exact vocabulary in use). |
| `observedAt` | `string` | yes | ISO-8601 timestamp of **when this observation was taken** (wall clock at read time), always `.toISOString()` form. |
| `sourceObservedAt` | `string` | **optional** | ISO-8601 timestamp of **when the upstream source itself last changed** (e.g. file mtime). Present only when a real source timestamp exists; **omitted entirely otherwise** (never `null`, never `undefined` key). |
| `verified` | `boolean` | yes | `true` only when the value is a direct, successful observation of the canonical source. Must be `false` for any unavailable/error envelope. |
| `stale` | `boolean` | yes | `true` only when a freshness budget has been exceeded. Default `false` on fresh observation and on unavailable envelopes. |
| `state` | `ObservationState` | yes | The single derived state label. |
| `error` | `SafeObservationError` | **optional** | Present **only** on `unavailable`/`error` envelopes. Omitted on success. |

### 1.4 Interface: SourceResponse

```ts
export interface SourceResponse<T> {
  data: T | null;
  meta: SourceMeta;
}
```

- `data` is `T` on success and **`null`** on any failure. It is never a fixture, never an
  empty-array substitute for "couldn't read", never a default shape standing in for
  missing reality. (Empty array is only correct when the source was read successfully and
  genuinely contained zero items.)

### 1.5 Input types

```ts
export interface ObservationInput {
  source: string;
  observedAt: string;
  sourceObservedAt?: string;
}

export interface UnavailableInput extends ObservationInput {
  code: string;
  safeMessage: string;
}
```

### 1.6 Factories (exact required behaviour)

```ts
export function observeValue<T>(data: T, input: ObservationInput): SourceResponse<T>
export function unavailable(input: UnavailableInput): SourceResponse<null>
export function isStale(observedAt: string, freshnessMs: number, now = new Date().toISOString()): boolean
```

`observeValue` MUST produce exactly:
```
{ data, meta: { source, observedAt, verified: true, stale: false, state: "verified",
                ...(sourceObservedAt ? { sourceObservedAt } : {}) } }
```
- `sourceObservedAt` is spread in **conditionally**, so the key is absent when falsy.
- No `error` key.
- Always `verified: true`, `stale: false`, `state: "verified"`.

`unavailable` MUST produce exactly:
```
{ data: null,
  meta: { source, observedAt, verified: false, stale: false, state: "unavailable",
          error: { code, safeMessage } } }
```
- Note: `unavailable` does **not** carry `sourceObservedAt` even if supplied in the input.

`isStale` MUST return `true` when either timestamp is unparseable (fail-closed), and
otherwise `nowMs - observedMs >= freshnessMs` (**inclusive** boundary).

### 1.7 The second, parallel envelope (ApiMeta / ApiResponse)

`src/data/types.ts` defines a **second, older** metadata envelope used by the client-facing
adapters in `src/data/adapters.ts`:

```ts
export interface ApiMeta {
  source: string;
  observedAt: string;
  verified: boolean;
  stale: boolean;
  total?: number;
  count?: number;
}

export interface ApiResponse<T> {
  data: T;          // NOTE: non-nullable, unlike SourceResponse<T>.data
  meta: ApiMeta;
}
```

Differences that a reimplementation must preserve (or consciously reconcile):
- `ApiResponse.data` is **non-nullable** `T`; `SourceResponse.data` is `T | null`.
- `ApiMeta` has **no `state`** and **no `error`**; instead it carries optional `total`/`count`.
- The API routes commonly **widen** `SourceResponse` with `count` (and the cron/agents/activity
  routes even reassemble `meta` by hand) so the wire format is a superset: `SourceMeta` fields
  **plus** `count`. See §6 invariants and §7 ambiguity.

Helper signatures in `types.ts` (normative):
```ts
export function normNum(v: unknown): number | null            // finite-or-parseable number, else null; preserves real 0
export function normPct(v: unknown): number | null            // clamp 0..100 then round, else null
export function normStr(v: unknown, fallback = ""): string    // string→itself, number→String(n), else fallback
export function mkMeta(source: string, verified = true): ApiMeta   // { source, observedAt: now, verified, stale:false }
export function safeVal(v: unknown): string                   // null/undefined/NaN/empty → "Unavailable"
export function safeNum(v: number | null): string             // null/undefined → "Unavailable"
```

### 1.8 Complete enumeration of ALL enum values in the observation/truth layer

Reproduce these literal unions exactly.

| Type | Allowed values |
|---|---|
| `ObservationState` | `verified` \| `observed` \| `stale` \| `unavailable` \| `error` |
| `GateEvidence.status` | `pass` \| `fail` \| `pending` |
| `ObservedAgent.state` | `registered` \| `observed` \| `unknown` |
| `ObservedCronJob.lastStatus` | `ok` \| `error` \| `no-data` |
| `ServiceState` / `ObservedService.state` | `active` \| `inactive` \| `activating` \| `error` \| `no-data` |
| `ObservedProject.status` | `complete` \| `in_progress` \| `blocked` \| `unknown` |
| `ObservedProject.health` | `verified` \| `observed` \| `unknown` |
| `ObservedTask.status` | `completed` \| `blocked` \| `pending` |
| `ObservedTask.priority` | `high` \| `medium` |
| `ExecutionEvent.kind` | `task` \| `gate` \| `cron` \| `voice` |
| `RiskClass` | `read` \| `safe` \| `sensitive` \| `denied` |
| `ApprovalStatus` | `pending` \| `approved` \| `denied` \| `expired` \| `executed` |
| `RunStatus` | `running` \| `completed` \| `failed` \| `blocked` |
| `EmergencyState.status` | `ACTIVE` \| `DISABLED` |
| `Approval.risk` (legacy `data/types.ts`) | `low` \| `medium` \| `high` |
| `Approval.status` (legacy) | `pending` \| `approved` \| `rejected` \| `expired` \| `completed` |
| `ActivityEvent.severity` | `info` \| `warning` \| `error` \| `success` |
| `GraphEdge.type` | `wikilink` \| `alias` \| `md` |

Note the two approval vocabularies differ: legacy `Approval.status` uses `rejected`/`completed`,
the Phase-2 `ApprovalRecord`/`ApprovalStatus` uses `denied`/`executed`. Do not silently unify.

---

## 2. FRESHNESS POLICY

### 2.1 The single freshness primitive

There is exactly **one** freshness predicate: `isStale(observedAt, freshnessMs, now?)`.
It compares timestamps in milliseconds and returns `true` at `now - observedAt >= freshnessMs`
(inclusive) or if either timestamp is unparseable (fail-closed).

### 2.2 Budgets actually wired

**No per-source-class freshness budget constant exists in the observation layer.**
`isStale` accepts `freshnessMs` as a parameter, but no observation module or API route calls
it, and no `_FRESH`/`freshnessMs` constant is defined anywhere in `src/`. Consequently
`meta.stale` is currently always `false` on the Phase-1 observation surfaces, and the `stale`
state value is never emitted (see §7).

The **only** freshness budget exercised by the tests is the example value used in
`tests/observation.test.mjs`: **`60_000` ms (60 seconds)**, and the test asserts the
boundary semantics of `isStale` at that value, not that any source uses it.

### 2.3 Other time budgets in the system (present, but not freshness for observations)

These are timeouts/TTLs that a reimplementation must preserve, distinguished from
observation freshness:

| Constant / literal | Value | Applies to | Location |
|---|---|---|---|
| `APPROVAL_TTL_MS` | `30 * 60 * 1000` (30 min) | Approval expiry, computed at read time | `approval-store.ts` |
| `EXEC_TIMEOUT_MS` | `30_000` (30 s) | Capability execution timeout | `run-ledger.ts` |
| `TAIL_LIMIT` | `4096` | Run stdout/stderr tail kept in the record | `run-ledger.ts` |
| router probe timeout | `3000` ms (default param) | `probeRouter(timeoutMs = 3000)` HTTP abort | `operations-observation.ts` |
| systemctl probe timeout | `5000` ms | `execFile("systemctl", [...], { timeout: 5000 })` | `operations-observation.ts` |
| overlay TTL | `24 * 60 * 60 * 1000` (24 h) | Orb-overlay host entries pruned in `/api/overlay` | `api/overlay/route.ts` |
| session cookie `maxAge` | `60*60*24` (24 h) | Auth cookie lifetime | `auth.ts` |
| TTS timeout | `90_000` ms | `/api/tts` | `api/tts/route.ts` |
| STT timeout | `30_000` ms | `/api/stt` | `api/stt/route.ts` |

**Requirement for the rewrite:** define freshness budgets per source class explicitly
(they are absent today). The intended classes map onto the modules in §3; a reasonable
schedule is: cron registry and system service probes ≤ a few seconds; AI-OS markdown
(Task Logs / portfolio) on the order of minutes; voice activity on the order of the wake
heartbeat. Whatever values are chosen, `isStale` semantics (§1.6) must be honoured and
`meta.state`/`meta.stale` must be driven from the same predicate.

---

## 3. OBSERVATION MODULES — canonical source, returned shape, failure behaviour

### 3.1 `src/lib/observation-sources.ts` — the path/allowlist authority

Exports (exact):

```ts
export const HOME_DIR = "/home/walker";
export const AI_OS_ROOT = path.join(HOME_DIR, "Documents", "Obsidian Vault", "AI-OS");
export const HERMES_ROOT = path.join(HOME_DIR, ".hermes");
export const HERMES_CRON_FILE = path.join(HERMES_ROOT, "cron", "jobs.json");
export const PROFILES_ROOT = path.join(HERMES_ROOT, "profiles");
export const CRON_OUTPUT_ROOT = path.join(HERMES_ROOT, "cron", "output");
export const AI_OS_PROJECTS_ROOT = path.join(AI_OS_ROOT, "Projects");
export const AI_OS_TASK_LOG = path.join(AI_OS_PROJECTS_ROOT, "Task-Log.md");
export const ROUTER_MODELS_URL = "http://127.0.0.1:8090/v1/models";

export const APPROVED_SERVICE_UNITS = [
  "hermes-gateway.service",
  "ultron-mcv2.service",
  "ultron-wake.service",
  "llm-free-router.service",
  "freebuff2api.service",
] as const;

export type ApprovedServiceUnit = (typeof APPROVED_SERVICE_UNITS)[number];
export function isApprovedService(unit: string): unit is ApprovedServiceUnit
export function isWithinAiOs(candidate: string): boolean
export function isApprovedRouterHealthUrl(candidate: string): boolean
```

Requirements:
- All canonical paths are **absolute, Linux-rooted under `/home/walker`**, built with
  `path.join`. Windows-era paths (`C:\Users\...`, `AppData`) must never appear.
- `isApprovedService` is an **exact membership test** against the 5-element readonly tuple
  (write it as `APPROVED_SERVICE_UNITS.includes(unit as ApprovedServiceUnit)`). It is a
  type guard. It must reject command-injection payloads such as
  `"hermes-gateway.service; restart ssh.service"` because it is exact equality, not prefix.
- `isWithinAiOs` resolves both the root and the candidate with `path.resolve`, then returns
  `resolved === root || resolved.startsWith(root + path.sep)`. This rejects `..` traversal
  and sibling directories (`.../Obsidian Vault/Personal/...`).
- `isApprovedRouterHealthUrl` is an **exact string equality** against `ROUTER_MODELS_URL`.
  `localhost` vs `127.0.0.1`, `https`, other hosts, and other paths all return `false`.

Failure behaviour: these are pure functions; they throw nothing. Containment/allowlist
failures return `false`.

### 3.2 `src/lib/cron-observation.ts` — Hermes cron registry

- **Canonical source:** `HERMES_CRON_FILE` = `/home/walker/.hermes/cron/jobs.json`
  (exported as `CRON_SOURCE`).
- **Returns:** `ObservedCronJob[]` via `parseCronRegistry(raw: string): ObservedCronJob[]`.

```ts
export interface ObservedCronJob {
  id: string;
  name: string;
  schedule: string;
  enabled: boolean;
  script: string | null;
  noAgent: boolean;
  lastRunAt: string | null;
  lastStatus: "ok" | "error" | "no-data";
  lastError: string | null;
  nextRunAt: string | null;
  deliver: string;
  outputDir: string | null;
}
```

Mapping requirements:
- Registry JSON may be either a bare **array** or an object with a `jobs` array.
- `id` = `String(id ?? name ?? "")`; `name` = `String(name ?? "untitled")`.
- `schedule` is produced by `displaySchedule(raw)`: pass through a string; for an object,
  prefer `display`, then `expr`, then (if `kind === "interval"` and numeric `minutes`)
  `"every {minutes}m"`, then (if `run_at`) `"once at {run_at}"`; otherwise `String(value ?? "")`.
  The source falls back to `schedule_display` if `schedule` is absent.
- `enabled` = `raw.enabled !== false && raw.state !== "paused"`.
- `script` = the string only if it is a non-empty string, else `null`.
- `noAgent` = `Boolean(raw.no_agent)`.
- `lastRunAt` / `nextRunAt` = the value only if it is a `string`, else `null`.
- `lastStatus` = `"ok"` when `last_status === "ok"`; else `"error"` when `last_status` is any
  truthy value; else `"no-data"`.
- `lastError` = non-empty string **truncated to 200 chars**, else `null`.
- `deliver` = non-empty string, else `"local"`.
- `outputDir` = `path.join(CRON_OUTPUT_ROOT, id)` when `id` is truthy, else `null`.
- **Never emitted:** `prompt`, `skills`, or any raw config key. Only the whitelisted fields above.

**Failure behaviour:** a corrupt registry **throws** — `parseCronRegistry` throws
`Error("corrupt cron registry: ...")` on `JSON.parse` failure and
`Error("corrupt cron registry: no jobs array")` when the `jobs` array is missing. The
`/api/cron` route catches this and returns an `unavailable` envelope
(`code: "cron_unavailable"`). Internally the parser has **no empty-array fallback** — that is
deliberate (honesty over silence).

### 3.3 `src/lib/execution-observation.ts` — activity timeline

- **Canonical sources:** the AI-OS Task Log (`AI_OS_TASK_LOG`), the cron registry
  (`HERMES_CRON_FILE`), and `~/.local/share/ultron-activity.json` (the ultron-wake activity file).
- **Returns:** `ExecutionEvent[]`.

```ts
export interface ExecutionEvent {
  id: string;
  at: string;
  kind: "task" | "gate" | "cron" | "voice";
  title: string;
  source: string;
  verified: boolean;
  sourceObservedAt?: string;
}
```

Three parsers:

1. `parseTimelineFromTaskLog(markdown, sourcePath, scope): ExecutionEvent[]`
   - Matches lines `^\s*- \[x\]\s+(.+)$` (case-insensitive) — **completed items only**.
   - Each event: `id = "{scope}-{n}"` where `n` is a 1-based running index; `at = ""`;
     `kind = "task"`; `title = captured text trimmed, truncated to 240 chars`;
     `source = sourcePath`; `verified = true`.
   - Events are **sorted ascending by `id`** (`localeCompare`) so order is deterministic even
     though there is no timestamp.
2. `parseCronEvents(raw, sourcePath): ExecutionEvent[]`
   - JSON parse; on failure returns `[]`; if no `jobs` array, returns `[]`.
   - Emits an event **only** for jobs whose `last_run_at` is a string.
   - `id = "cron-{id-or-index}"`; `at = last_run_at`; `kind = "cron"`;
     `title = "cron: {name-or-id-or-'job'} {status}"` where status is `ok|error|no-data`
     (same truthiness rule as §3.2); `source = sourcePath`; `verified = true`;
     `sourceObservedAt = last_run_at`.
   - **Never exposes prompt content.**
3. `parseVoiceActivity(raw, sourcePath): ExecutionEvent[]`
   - Parses to `VoiceObservation { status: string; detail: string; ts: string }`.
   - Returns `[]` on parse failure or if `ts` is missing/empty — **no event is fabricated**.
   - On success emits a single event: `id = "voice-{ts with all non-digits stripped}"`;
     `at = ""`; `kind = "voice"`;
     `title = "voice: {status-or-'unknown'}"` plus `" — {detail}"` (detail truncated to 120
     chars) only when detail is non-empty; `source = sourcePath`; `verified = true`;
     `sourceObservedAt = ts`.

**Failure behaviour:** all three parsers are **total and non-throwing**; on any problem they
return `[]` (honest empty), never a synthetic "standby" event.

`/api/activity` merges all three, sorts descending by `at` then ascending by `id`, and
returns an `observeValue` envelope (with a hand-assembled `meta` carrying `count`). Its
catch returns `unavailable({ code: "execution_unavailable" })`.

### 3.4 `src/lib/operations-observation.ts` — services + router

- **Canonical sources:** `systemctl --user is-active <unit>` for each approved unit, and the
  router health URL `ROUTER_MODELS_URL`.
- **Returns:** `ObservedService[]` and a `RouterProbe`.

```ts
export type ServiceState = "active" | "inactive" | "activating" | "error" | "no-data";

export interface ObservedService {
  unit: string;
  state: ServiceState;
  observedAt: string;
}

export interface RouterProbe {
  online: boolean | null;
  observedAt: string;
}
```

Functions:
- `normalizeServiceState(raw: string | undefined): ServiceState` — trim + lowercase, then:
  `active`→`active`; `inactive`→`inactive`; `activating` **or** `deactivating`→`activating`;
  `failed` **or** `unknown`→`error`; anything else (incl. `""`/`undefined`)→`no-data`.
- `isApprovedServiceUnit(unit)` — delegates to `isApprovedService`.
- `readServiceState(unit): Promise<ObservedService>` — if the unit is **not** allowlisted it
  returns `{ unit, state: "error", observedAt }` **without running anything**. Otherwise it
  runs `execFile("systemctl", ["--user", "is-active", unit], { timeout: 5000 })`. Because
  systemctl exits non-zero for inactive/failed units but still prints the state, the catch
  path normalizes `err.stdout` rather than failing.
- `readAllServices(): Promise<ObservedService[]>` — `Promise.all` over `APPROVED_SERVICE_UNITS`.
- `probeRouter(timeoutMs = 3000): Promise<RouterProbe>` — `fetch(ROUTER_MODELS_URL)` with an
  `AbortController` abort timer; returns `online: res.ok` on success, `online: null` on any
  throw (timeout, connection refused, etc.).

**Failure behaviour:** never throws to the caller. Disallowed units → `state:"error"`;
unreachable router → `online:null`. `/api/status` catches any residual error and returns
`unavailable({ code: "operations_unavailable" })`.

### 3.5 `src/lib/team-observation.ts` — observed agents / team

- **Canonical sources:** profile directories under `PROFILES_ROOT`
  (`/home/walker/.hermes/profiles/`) and the AI-OS Task Log (`AI_OS_TASK_LOG`).
- **Returns:** `ObservedAgent[]` and `GateEvidence[]`.

```ts
export interface GateEvidence {
  gate: string;
  status: "pass" | "fail" | "pending";
  title: string;
  sourcePath: string;
}

export interface ObservedAgent {
  id: string;
  name: string;
  role: string;
  state: "registered" | "observed" | "unknown";
  evidence: GateEvidence[];
  sourcePath: string;
}
```

- `ROLE_LABELS` is a fixed `Record<string, { name: string; role: string }>` mapping profile
  ids to display name + role. Known keys: `aegis`(Aegis/review), `apollo`(Apollo/ux),
  `argus`(Argus/qa), `athena`(Athena/architecture), `hephaestus`(Hephaestus/frontend),
  `sentinel`(Sentinel/security), `devops`("DevOps SRE"/devops), `backend`(Backend/backend),
  `benchweak`(Benchweak/benchmark), `phase11live`("Phase 11 Live"/live), `shared`(Shared/shared).
  Unknown ids fall back to `{ name: titleCase(id), role: id }` where `titleCase` upper-cases
  the first character and replaces `[-_]+` with spaces.
- `parseGateEvidence(markdown, sourcePath): GateEvidence[]` — scans each line for
  `/gate\s+([A-Za-z][A-Za-z0-9_-]*)/i`. Default `status = "pending"`; if the lowercased line
  matches `/fail/i` **or** `/block/i` → `"fail"`; else if it matches
  `/pass|complete|cleared|verified|re-?check/i` → `"pass"`. `title` strips a leading
  `- [ ]`/`* [x]`/etc. checkbox prefix and truncates to 200 chars. Every item carries the
  provided `sourcePath`.
- `buildObservedAgents(profileDirs, taskLogMarkdown, taskLogPath): ObservedAgent[]` — for each
  profile id: resolve label; `evidence` = gates whose `gate` lowercases equal the id lowercased;
  `state` = `"observed"` when evidence is non-empty, else `"registered"`. `sourcePath` =
  `path.join(PROFILES_ROOT, id)`.

**Never exposed per agent:** `model`, `provider`, `api_key`/`apiKey`/`access_token`, or any
credential-shaped field. The agent object is deliberately *narrow*. `state` is never
`"active"`/`"working"` — a profile with no gate evidence is only ever `"registered"`.

**Failure behaviour:** pure functions, no throws. The `/api/agents` route lists profile
dirs (excluding hidden `.`-prefixed dirs) and reads the task log; on error it returns
`unavailable({ code: "profiles_unavailable" })`.

### 3.6 `src/lib/aios-projects.ts` — projects + tasks

- **Canonical source:** the AI-OS portfolio `Task-Log.md`
  (`AI_OS_PROJECTS_ROOT/Task-Log.md`) and each project's own `Task-Log.md`.
- **Returns:** `ObservedProject[]` and `ObservedTask[]`.

```ts
export interface ObservedProject {
  id: string;
  name: string;
  status: "complete" | "in_progress" | "blocked" | "unknown";
  milestone: string;
  path: string;
  blocked: boolean;
  health: "verified" | "observed" | "unknown";
  evidencePaths: string[];
}

export interface ObservedTask {
  id: string;
  title: string;
  project: string;
  status: "completed" | "blocked" | "pending";
  assignee: string;
  priority: "high" | "medium";
  blocker: string;
  sourcePath: string;
  evidencePaths: string[];
}
```

- `projectId(name)` — slug: lowercase, replace `[^a-z0-9]+` with `-`, strip leading/trailing `-`.
- `statusOf(status)` — lowercased; contains `complete`→`complete`; contains `block` or `fail`→
  `blocked`; contains `progress`/`active`/`authorized`→`in_progress`; else `unknown`.
- `evidencePaths(cell, projectsRoot)` — extract every `[[...]]` wikilink, strip a leading
  `./`, and resolve to `path.join(projectsRoot, "{target}.md")`.
- `parsePortfolioProjects(markdown, portfolioPath): ObservedProject[]`:
  - Reads **only** the `## Active projects` section (regex
    `/## Active projects\s*\n+([\s\S]*?)(?=\n## |$)/i`).
  - Takes table lines (trimmed, starting `|`), **skips the first two** (header + separator).
  - Requires exactly **4 cells** and a non-empty first cell, else the row is dropped.
  - Columns: `[name, rawStatus, milestone, evidence]`.
  - `id = projectId(name)`; `status = statusOf(rawStatus)`;
    `path = dirname(firstEvidencePath)` (or `""` if none);
    `blocked = status === "blocked"`;
    `health = "verified" if complete, "unknown" if unknown, else "observed"`.
- `parseTaskLogItems(markdown, sourcePath, project): ObservedTask[]`:
  - Matches `^\s*- \[([ xX])\]\s+(.+)$`. `id = "{project}-{n}"` (1-based).
  - `gateFailed` = title has word `gate` AND word `fail`/`failed`.
  - `pendingApproval` = title has word `blocked` OR phrase `pending approval`.
  - `status` = checked `[x]` ? (`gateFailed` ? `"blocked"` : `"completed"`) :
    (`pendingApproval` ? `"blocked"` : `"pending"`).
  - `assignee` = the token after `gate ` (`/\bgate\s+([a-z0-9_-]+)/i`) if present, else the
    text inside the first `(...)`, else `""`.
  - `priority` = `"high"` if `gateFailed`, else `"medium"`.
  - `blocker` = `"Gate failure recorded in Task Log"` if `gateFailed`, else the trailing
    `blocked...` phrase if `pendingApproval`, else `""`.
  - `evidencePaths = [sourcePath]`.

**Failure behaviour:** pure functions. `/api/projects` and `/api/tasks` read the portfolio,
stat it for `sourceObservedAt` (mtime), and wrap with `observeValue`. Per-project task-log read
failures are swallowed to `[]` (a missing project log is not fatal). If the portfolio itself is
unreadable, both routes return `unavailable({ code: "AIOS_PORTFOLIO_UNAVAILABLE" })`.

### 3.7 `src/lib/auth.ts` — session auth

- In-memory `Set<string>` of valid sessions (resets on restart — accepted for single-user).
- `createSession(): string` → `session_{Date.now()}_{random36(8)}`; adds to the set.
- `destroySession(token)` removes it; `isValidSession(token|null)` = membership (false on null).
- `checkPassword(input)` compares to `process.env.DASHBOARD_PASSWORD || "290615"` (plain
  string equality — see §7, credential).
- Cookie `session`: `httpOnly`, `secure:false`, `sameSite:"lax"`, `path:"/"`, `maxAge` 24 h.
  `getSessionToken`, `setSessionCookie`, `clearSessionCookie` operate on the `session` cookie.

### 3.8 `src/lib/token.ts` — overlay API token

- `OVERLAY_TOKEN = process.env.OVERLAY_TOKEN || "dev-local-token-not-for-production"`.
- Single source of truth **server-side**; the browser extension and wake service carry the
  same value as literals (MV3 extension pages and Python urllib cannot read `process.env`).
- Used by `isAuthorized(req)` (§5.4) and `/api/page-snapshot`.

### 3.9 `src/lib/attachments.ts` — client attachment hook (UI-adjacent)

- `useAttachments()` React hook. Constants `MAX_FILES = 4`, `MAX_SIZE = 25 * 1024 * 1024`
  (25 MB).
- `AttachFile { name, size, type, dataUrl, preview? }`. Files are read as data URLs; `preview`
  is set only for `image/*`. Oversized files are skipped (`size > MAX_SIZE`); the list is
  capped at `MAX_FILES`; duplicates by `name` are ignored. Supports drag-drop, paste of
  image items, and a picker ref. `formatBytes` renders MB/KB.
- Not observation/truth, but part of the surface inventory; listed because it was named in scope.

### 3.10 `src/data/adapters.ts` — client fetch adapters (no fallbacks)

- Thin `fetch()` wrappers, each returning `Promise<ApiResponse<T>>` and **directly returning
  `res.json()`** with no normalization and no mock fallback.
- Read adapters → endpoints: `/api/projects`, `/api/tasks`, `/api/agents`, `/api/cron`,
  `/api/approvals`, `/api/knowledge`, `/api/status`, `/api/automations`, `/api/activity`,
  `/api/capabilities`, `/api/runs`, `/api/emergency-stop`.
- Mutation adapters return `{ data?, error? }`: `requestApproval`, `decideApprovalRequest`
  (`/api/approvals/{id}/approve|deny`), `executeRunRequest` (`POST /api/runs`),
  `setEmergencyRequest` (`POST /api/emergency-stop`). On non-OK they return
  `{ error: json.error || "… ({status})" }`. `requestApproval` hard-codes
  `requester: "sir"`.

---

## 4. COMPLETE SIGNAL SET EXPOSED PER DOMAIN

Fields below are the **exact** surfaced fields. Anything not listed is not exposed.

### 4.1 Agents / Team (`/api/agents`)
`ObservedAgent`: `id`, `name`, `role`, `state` (`registered|observed|unknown`), `evidence[]`
(each `GateEvidence`: `gate`, `status` (`pass|fail|pending`), `title`, `sourcePath`),
`sourcePath`. Envelope `source: "hermes-profiles + AI-OS task log"`, `count`.
**Excluded:** model, provider, api keys/tokens, permissions, tools.

### 4.2 Projects (`/api/projects`)
`Project`: `id`, `name`, `status`, `progress` (always `null` here), `tasks` (always `null`),
`done` (always `null`), `milestone`, `path?`, `agents?`, `blocked?`, `health?`, `evidencePaths?`.
Envelope `source: "ai-os:Projects/Task-Log.md"`, `sourceObservedAt` (file mtime), `count`.
Note the route maps `ObservedProject` → `Project`, forcing `progress/tasks/done` to `null`
(honest "not measured", not fabricated numbers).

### 4.3 Tasks (`/api/tasks`)
`TaskItem`: `id`, `title`, `project`, `status`, `assignee`, `priority`, optional `created`,
`updated`, `phase`, `qaState`, `approvalState`, `blocker`, `repairCycles`, `sourcePath`,
`evidencePaths`. Envelope `source: "ai-os:Projects/*/Task-Log.md"`, `sourceObservedAt`,
`count`.

### 4.4 Cron (`/api/cron`)
`ObservedCronJob`: `id`, `name`, `schedule`, `enabled`, `script`, `noAgent`, `lastRunAt`,
`lastStatus` (`ok|error|no-data`), `lastError`, `nextRunAt`, `deliver`, `outputDir`.
Envelope `source: <HERMES_CRON_FILE absolute path>`, `count`.
**Excluded:** prompt, skills, raw config.

### 4.5 Executions / Activity (`/api/activity`)
`ExecutionEvent`: `id`, `at`, `kind` (`task|gate|cron|voice`), `title`, `source`, `verified`,
`sourceObservedAt?`. Merged, sorted newest-first then by id.
Envelope `source: "AI-OS task log + cron registry + ultron activity"`, `count`.

### 4.6 Operations (`/api/status`)
`OpsStatus`: `os` (string `"{platform} {release}"`), `memory`
(`{ total, free, usedPercent: number|null }` from `os.totalmem/freemem`, rounded percent),
`services[]` (each `ObservedService`: `unit`, `state` (`active|inactive|activating|error|no-data`),
`observedAt`), `router` (`{ online: boolean|null, observedAt }`),
`voice` (`{ note, observedAt }` — note is a static explanatory string in Phase 1).
Envelope `source: "systemctl --user probes + router health"`.

### 4.7 Command plane (Phase 2 — provenance-bearing read surfaces)
- `/api/capabilities` → `Capability[]` (`id`, `label`, `description`, `risk`, `template`,
  `command`, `args`, `parameters[]` (`name`,`label`,`pattern`,`example`), `effect`,
  `requiresApproval`). Envelope `source: "capability-registry"`, `count` (no `stale`/`state`).
- `/api/approvals` → `{ pending: ApprovalRecord[], decisions: ApprovalRecord[] }`.
  `ApprovalRecord`: `id`, `capabilityId`, `params`, `template`, `preview`, `requester`,
  `status`, `requestedAt`, `expiresAt`, `decidedAt?`, `decidedBy?`, `note?`, `runId?`.
  Envelope `source: "approval-desk"`, `pendingCount`.
- `/api/runs` → `RunRecord[]` (`runId`, `approvalId`, `capabilityId`, `requester`,
  `approvedBy`, `template`, `command`, `args`, `startedAt`, `endedAt?`, `exitCode?`,
  `signal?`, `status`, `evidencePath?`, `stdoutTail?`, `error?`). Envelope `source: "run-ledger"`,
  `count`.
- `/api/emergency-stop` → `EmergencyState` (`status`, `updatedAt`, `reason?`).
  Envelope `source: "emergency-file"`.
- `/api/automations` → always `unavailable({ code: "no_automation_source" })` — no canonical
  source in Phase 1; deferred.

### 4.8 Knowledge (adjacent, for completeness)
`/api/knowledge` → `KnowledgeItem[]` (`id`, `title`, `project`, `type`, `verified`, `updated`,
`sourcePath`, `tags`, `excerpt`). Walks `~/ventures/second-brain` for `.md` (max 200 files),
sanitizes bytes, tags from `#hashtags` (max 8), excerpt from lines 2-4 (max 180 chars).
`/api/knowledge/graph` builds `GraphNode`/`GraphEdge` and, **uniquely**, returns `stale:true`
on failure — the one place `stale` is ever set.

---

## 5. REDACTION AND ALLOWLIST RULES

### 5.1 Never exposed (hard redaction requirements)
- **Cron `prompt` and `skills`** and any raw registry config — asserted by
  `cron-observation.test.mjs` and `execution-observation.test.mjs` (searched for the literal
  secret strings and for the keys themselves).
- **Agent credentials** — `model`, `provider`, `api_key`, `apiKey`, `access_token` must not
  appear on `ObservedAgent` (asserted by `team-observation.test.mjs`).
- **Environment/secret values** are not surfaced in any `SafeObservationError.safeMessage`.
- Output byte/size caps that bound exposure: cron `lastError` 200 chars; task-log title 240
  chars; event title 240 chars; voice detail 120 chars; run `stdoutTail` 4096 chars; page
  snapshot text 25000 chars; knowledge excerpt 180 chars; graph excerpt 200 chars; graph
  backlinks/outgoing 50 each; graph unresolved 500, ambiguous 100.

### 5.2 Path containment
- Every AI-OS read must be inside `AI_OS_ROOT` (enforced by `isWithinAiOs` for any
  candidate path). `..` traversal and sibling personal-vault paths are rejected.
- Agent `sourcePath` must be rooted at `/home/walker/.hermes/profiles/` (asserted by test).
- All canonical paths are absolute and built with `path.join`; no Windows paths anywhere.

### 5.3 Source-name vocabulary (symbolic labels in use)
`"systemctl --user probes + router health"`, `"hermes-profiles + AI-OS task log"`,
`"ai-os:Projects/Task-Log.md"`, `"ai-os:Projects/*/Task-Log.md"`,
`"AI-OS task log + cron registry + ultron activity"`, `"capability-registry"`,
`"approval-desk"`, `"run-ledger"`, `"emergency-file"`, `"obsidian-second-brain"`,
`"automation-registry"` (in the unavailable envelope), and raw absolute file paths for
`/api/cron` and the agent/task `source`/`sourcePath` fields.

### 5.4 Service / endpoint allowlists
- **Services:** only the 5 units in `APPROVED_SERVICE_UNITS` are observable; every read goes
  through `isApprovedService` (exact match). Disallowed unit → immediate `state:"error"`,
  no subprocess spawned.
- **Router endpoint:** only the exact string `http://127.0.0.1:8090/v1/models`
  (`isApprovedRouterHealthUrl`). No other host, scheme, or path.
- **Command origin auth:** `isAuthorized(req)` returns `true` for any
  `http://localhost*`/`http://127.0.0.1*` origin, otherwise requires header
  `x-ultron-token === OVERLAY_TOKEN`. `/api/page-snapshot` accepts a **narrower** rule:
  local origins `http://localhost:3000`/`http://127.0.0.1:3000` (fixed port) or the token
  header, else 403.
- **Command execution:** no shell ever. Capabilities are a fixed, code-defined registry
  (6 entries: `svc.check-gateway` read, `svc.restart-ultron` sensitive,
  `svc.restart-gateway` sensitive, `cron.pause` sensitive, `cron.resume` sensitive,
  `router.probe` safe). Parameters are validated by full-match regex against the whole value
  (`^(?:pattern)$`); unknown params and empty/missing params are rejected. Cron ids match
  `^[0-9a-f]{12}$`. Binary paths are absolute (`/usr/bin/systemctl`,
  `/home/walker/.local/bin/hermes`, `/home/walker/.local/bin/router-status`). Execution uses
  `execFile` with the resolved argv and timeouts; argv is **re-validated at run time**
  (defense in depth) so a tampered approval cannot execute.

---

## 6. INVARIANTS ASSERTED BY TESTS

Each line is a distinct, one-line testable statement extracted from the 8 in-scope test files.

**`tests/observation.test.mjs`**
1. `observeValue(data, {source, observedAt, sourceObservedAt})` deep-equals `{ data, meta: { source, observedAt, sourceObservedAt, verified:true, stale:false, state:"verified" } }`.
2. `unavailable(...)` returns `data === null`, `meta.verified === false`, `meta.stale === false`, `meta.state === "unavailable"`, and `meta.error` deep-equal to `{ code, safeMessage }`.
3. `isStale("…T08:00:00.000Z", 60_000, "…T08:00:59.999Z") === false`.
4. `isStale("…T08:00:00.000Z", 60_000, "…T08:01:00.000Z") === true` (inclusive boundary).

**`tests/observation-sources.test.mjs`**
5. `AI_OS_ROOT === "/home/walker/Documents/Obsidian Vault/AI-OS"`.
6. `HERMES_CRON_FILE === "/home/walker/.hermes/cron/jobs.json"`.
7. `AI_OS_ROOT` contains neither `"C:\\Users"` nor (cron file) `"AppData"`.
8. `APPROVED_SERVICE_UNITS` deep-equals the exact ordered 5-tuple `["hermes-gateway.service","ultron-mcv2.service","ultron-wake.service","llm-free-router.service","freebuff2api.service"]`.
9. `isApprovedService("hermes-gateway.service") === true`.
10. `isApprovedService("ssh.service") === false`.
11. `isApprovedService("hermes-gateway.service; restart ssh.service") === false` (exact match, injection rejected).
12. `isWithinAiOs("…/AI-OS/Projects/Task-Log.md") === true`.
13. `isWithinAiOs("…/AI-OS/../Personal/private.md") === false` (traversal rejected).
14. `isWithinAiOs("…/Obsidian Vault/Personal/private.md") === false` (sibling rejected).
15. `isApprovedRouterHealthUrl("http://127.0.0.1:8090/v1/models") === true`.
16. `isApprovedRouterHealthUrl` is `false` for `http://localhost:8090/v1/models`, `https://example.com/health`, and `http://127.0.0.1:8090/v1/chat/completions`.

**`tests/cron-observation.test.mjs`**
17. `parseCronRegistry` returns one entry per job (length 2 for the fixture).
18. A schedule object `{kind:"interval", minutes:2, display:"every 2m"}` is surfaced as `"every 2m"`.
19. A string schedule (`"0 9 * * 1"`) is passed through unchanged.
20. `enabled === true` for `{enabled:true, state:"scheduled"}`.
21. `enabled === false` for `{enabled:false, state:"paused"}` (pause wins].
22. `noAgent === true` when `no_agent` is `true`.
23. `lastStatus === "ok"` for `last_status:"ok"`; `lastStatus === "error"` for `last_status:"error"`.
24. `lastError` is surfaced for the errored job (`"Gateway shutdown killed the job"`).
25. The serialized output contains no `"SECRET PROMPT"` text, no `prompt` key, and no `skills` key.
26. `parseCronRegistry("not json at all")` throws an error matching `/corrupt/i`.
27. `parseCronRegistry(JSON.stringify({nope:true}))` throws an error matching `/corrupt/i`.

**`tests/execution-observation.test.mjs`**
28. `parseTimelineFromTaskLog` extracts only `- [x]` items — at least 3 from the fixture.
29. Every produced timeline event has `kind === "task"` and `verified === true`.
30. Pending `- [ ]` items never appear in the output.
31. Timeline event ids are unique.
32. Timeline events are in ascending `id` order (deterministic).
33. `parseCronEvents` emits exactly one event for two jobs when only one has a real `last_run_at` (length 1).
34. That cron event has `kind === "cron"` and `sourceObservedAt === "2026-08-13T08:00:00+08:00"`.
35. The cron events contain no `"SECRET"` prompt content.
36. `parseTimelineFromTaskLog("## No items\n", …)` deep-equals `[]` (no synthetic event).
37. `parseCronEvents(JSON.stringify({jobs:[]}), …)` deep-equals `[]`.

**`tests/operations-observation.test.mjs`**
38. `normalizeServiceState`: `"active"→"active"`, `"inactive"→"inactive"`, `"failed"→"error"`, `"unknown"→"error"`, `"activating"→"activating"`, `"deactivating"→"activating"`, `""→"no-data"`, `undefined→"no-data"`.
39. `isApprovedServiceUnit` is `true` for `hermes-gateway.service`, `ultron-mcv2.service`, `llm-free-router.service`.
40. `isApprovedServiceUnit` is `false` for `ssh.service`, `"hermes-gateway.service; rm -rf /"`, `"../../etc/passwd"`, and `""`.

**`tests/team-observation.test.mjs`**
41. `parseGateEvidence` extracts ≥3 gate events from the fixture Task Log.
42. The `aegis` gate event has `status === "fail"`; the `sentinel` gate event has `status === "pass"`.
43. Every gate evidence item carries the exact provided `sourcePath`.
44. `buildObservedAgents(["aegis","sentinel","benchweak"], …)` returns exactly 3 agents.
45. An agent with matching gate evidence (aegis) has `state === "observed"`.
46. An agent with no matching evidence (benchweak) has `state === "registered"`.
47. No agent object exposes `model` or `provider`.
48. No agent object exposes `api_key`, `apiKey`, or `access_token`.
49. Every agent `sourcePath` starts with `/home/walker/.hermes/profiles/`.
50. Every agent has an `evidence` array.
51. With no gate evidence at all, an agent's `state === "registered"` and `evidence === []` (never "active"/"working").

**`tests/aios-projects.test.mjs`**
52. `parsePortfolioProjects` reads only the Active projects table and returns the two rows deep-equal to the expected `ObservedProject` objects (id slug, `status`, `milestone`, `path` = dir of first evidence, `blocked`, `health`, `evidencePaths`).
53. A portfolio with no `## Active projects` heading yields `[]` (tables outside the section are ignored).

**`tests/aios-tasks.test.mjs`**
54. `parseTaskLogItems` returns the three checklist items deep-equal to the expected `ObservedTask` objects: completed item → `status:"completed"`, `assignee:"Hermes"`, `priority:"medium"`, `blocker:""`; gate-fail item → `status:"blocked"`, `assignee:"sentinel"`, `priority:"high"`, `blocker:"Gate failure recorded in Task Log"`; blocked item → `status:"blocked"`, `assignee:""`, `priority:"medium"`, `blocker:"blocked pending approval"`.

**Distinct invariants extracted: 54.**

---

## 7. AMBIGUOUS OR ENVIRONMENT-DEPENDENT

1. **Two competing metadata envelopes.** `SourceMeta` (with `state`/`error`, nullable data)
   vs `ApiMeta` (with `count`/`total`, non-null data, no state/error). The API routes mix
   them: several call `observeValue` then hand-spread `count` into `meta`, and three routes
   (`/api/cron`, `/api/agents`, `/api/activity`) rebuild `meta` from scratch. Decide
   explicitly which is canonical before reimplementing; today the wire contract is
   "SourceMeta ∪ count".
2. **`state`/`stale` are effectively dead on Phase-1 surfaces.** `isStale` is never called in
   `src/`; `state` is only ever `"verified"` or `"unavailable"`; `stale` is only ever `false`
   (except `/api/knowledge/graph`, which sets `stale:true` on error — inconsistent with the
   `unavailable` convention). The `observed`/`stale`/`error` enum members are unreached.
3. **Absolute, host-specific paths.** `HOME_DIR = "/home/walker"` is **hard-coded**, not
   derived from `os.homedir()`/`$HOME`. Every canonical path assumes user `walker` on Linux.
   `/api/activity` and `/api/overlay`/`/api/page-snapshot` instead use
   `os.homedir()`; `/api/knowledge` and `/api/knowledge/graph` use
   `process.env.HOME || "C:\\Users\\nickelvien"` — a leaked Windows-era fallback.
4. **Credentials / secrets embedded in source.**
   - `auth.ts`: `checkPassword` falls back to the literal password `"290615"` when
     `DASHBOARD_PASSWORD` is unset; also uses plain `===` comparison (timing-insensitive).
   - `token.ts`: `OVERLAY_TOKEN` falls back to `"dev-local-token-not-for-production"`; the
     same token is duplicated as literals in the browser extension and wake service.
5. **Env vars referenced:** `OVERLAY_TOKEN`, `DASHBOARD_PASSWORD`, `PHASE2_STATE_DIR`
   (test-only override, "never set in production"), `HOME`, `USERPROFILE`, `process.pid`.
6. **Mutable state files outside the repo** (absolute, XDG):
   `~/.local/share/ultron-mission-control/{approvals.json,runs.json,emergency.json,runs/}`
   (overridable via `PHASE2_STATE_DIR`), `~/.local/share/ultron-activity.json`,
   `~/.local/share/ultron-overlay.json`, `~/.local/share/ultron-page-request.json`,
   `~/.local/share/ultron-page-snapshot.json`.
7. **Write-adjacent surfaces exist in an otherwise read-only layer.** The "Observation/
   Truth" layer is read-only, but the same app contains a Phase-2 command plane that writes
   approvals, runs, evidence logs, and emergency state. The rewrite must keep the read
   surfaces strictly read-only and the write surfaces allowlisted (see §5.4). Two of the
   capability registry's "read-only" claims are inconsistent: `router.probe` is risk `safe`
   but `requiresApproval:true` while still being labelled a read.
8. **Session store is in-memory and non-durable** (auth resets on restart); `Math.random()`
   is used for session tokens and run/approval ids (`randomId`), not a CSPRNG.
9. **`/api/page-snapshot` hard-codes dev port `3000`** in its origin check, whereas
   `isAuthorized` accepts any localhost port — inconsistent local-origin policy.
10. **`ObservedService.observedAt` vs the observed state snapshot.** `readServiceState`
    stamps `observedAt` per unit at call time; `/api/status` also stamps a top-level
    `observedAt`; `probeRouter` stamps its own. There are three independent timestamps in one
    `OpsStatus` payload, which may not agree.
11. **`CRON_SOURCE` vs `source`.** `cron-observation.ts` exports `CRON_SOURCE = HERMES_CRON_FILE`
    but the `/api/cron` route sets `source` to the same absolute path literal directly; the
    exported constant is not used by the route. Minor dead-export.
12. **`test/observation.test.ts` also exists** (`tests/observation.test.ts`, TypeScript) alongside
    the `.mjs` suite; the in-scope suite is the 8 `.mjs` files. Confirm whether the `.ts` test
    adds invariants before dropping it in the rewrite.
13. **Schedule multitool.** `displaySchedule` handles object schedules but the human-readable
    `humanSchedule` field on the legacy `CronJob` type is not produced by the observed path.
14. **No explicit freshness budget for any source class** — see §2.2. This must be decided,
    not inherited.
