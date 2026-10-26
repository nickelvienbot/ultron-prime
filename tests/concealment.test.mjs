// The concealment invariant.
//
// This project exists behind a different face. The old build leaked the engine's
// name across 54 occurrences in 14 files; changing it was a scavenger hunt. The
// fix was to give the identity module one place to hold the vocabulary — so the
// first executable invariant is that nowhere else may name the engine.
//
// The forbidden list is READ FROM the identity module, never duplicated here.
// A second copy would be the same bug this test exists to prevent.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const IDENTITY_MODULE = join(SRC, "core", "identity", "index.ts");

/** Every file under src/, recursively. */
function sourceFiles(dir = SRC) {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? sourceFiles(full) : [full];
  });
}

/** The forbidden list, extracted from the identity module — the single source. */
function forbiddenStrings() {
  const match = readFileSync(IDENTITY_MODULE, "utf8").match(
    /FORBIDDEN_STRINGS[^=]*=\s*\[([^\]]*)\]/,
  );
  assert.ok(match, "the identity module must declare FORBIDDEN_STRINGS");

  const terms = [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1].toLowerCase());
  assert.ok(terms.length > 0, "FORBIDDEN_STRINGS must not be empty");
  return terms;
}

test("the identity module declares the forbidden list", () => {
  const terms = forbiddenStrings();
  assert.ok(
    terms.includes("hermes"),
    `expected the engine name in FORBIDDEN_STRINGS, got: ${terms.join(", ")}`,
  );
});

test("no source file outside the identity module names the engine", () => {
  const terms = forbiddenStrings();
  const leaks = [];

  for (const file of sourceFiles()) {
    if (file === IDENTITY_MODULE) continue; // the one place allowed to hold them

    const text = readFileSync(file, "utf8").toLowerCase();
    for (const term of terms) {
      if (text.includes(term)) {
        leaks.push(`${file.replace(SRC + "/", "src/")} → "${term}"`);
      }
    }
  }

  assert.deepEqual(leaks, [], `engine name leaked into:\n  ${leaks.join("\n  ")}`);
});

test("the product name reveals nothing", () => {
  const name = readFileSync(IDENTITY_MODULE, "utf8").match(/name:\s*"([^"]+)"/)?.[1] ?? "";

  assert.equal(name, "Ultron");
  for (const term of forbiddenStrings()) {
    assert.ok(!name.toLowerCase().includes(term), `product name leaks "${term}"`);
  }
});