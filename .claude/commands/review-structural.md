---
description: Structural review — raise root-cause / class-level problems (invariants enforced by convention, drifting proxies, duplicated contracts, parallel state) the diff introduces or extends, and prescribe the construction that makes the class impossible to write again. For a sampled list of individual instance bugs, use /code-review or /review.
allowed-tools: Bash(gh pr view:*), Bash(gh pr diff:*), Bash(gh pr list:*), Bash(gh api:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(rg:*), Bash(grep:*), Task
disable-model-invocation: true
---

Structural review of: `$ARGUMENTS` (a PR number, a branch name, a file path, or
empty = the current working diff).

## What this is for

Most automated reviewers **sample and cap**: they hunt for concrete, individually
crashing bugs, dedup to the crispest instance of each, and keep a bounded list of
the worst. That is the right shape for catching *unrelated* one-off defects, and
`/code-review` (working diff) and `/review` (a GitHub PR) do it well — prefer them
for that.

This command exists for the failure mode that shape cannot fix. When **one
invariant is enforced by convention across many call sites**, a sampler hits a
*different site of the same bug* in each pass: dedup fragments them into separate
line-items, each ranks on its own (often as "cleanup"), and the cap drops all but
a couple. The class is never named, the structural fix is never proposed, and the
next pass surfaces the next site — never converging.

This command inverts that: it **enumerates** sites instead of sampling, **clusters**
them by root cause instead of deduping to one, ranks by **aggregate** risk (every
current site plus the next one someone adds), and prescribes the
**construction-level** fix that makes the whole class fail to compile. You are
reviewing for **convergence**: that after acting on this review, the class cannot
recur — not that the few worst lines are patched.

This is a heavier, more speculative lens than a bug sampler. Two disciplines keep
it honest, and both are mandatory:

- **Stay anchored to the diff (Phase 0 / Phase 5).** A class only belongs in this
  review if *this diff introduced it, extended it, or added a fresh site to it*.
  A convention that predates the diff and that the diff merely sits next to is not
  this PR's finding — note it at most as context.
- **Earn every structural fix (Phase 4).** A proposed sum type / decoder / SSOT is
  a real refactor with real cost. If a convention has few, stable sites and a test
  pins it, "keep the convention, add the test" is the correct answer — say so
  rather than manufacturing a refactor.

## Phase 0 — Scope the diff

Resolve `$ARGUMENTS` to a unified diff and the right base:

- **PR number/URL** → `gh pr view <n> --json title,body,baseRefName,headRefName,additions,deletions,changedFiles` then `gh pr diff <n>`.
- **Branch name** → diff against the merge base: `git diff $(git merge-base main <branch>)...<branch>` (falls back cleanly if `main` isn't the base — use the PR's `baseRefName` when known).
- **File/dir path** → `git diff HEAD -- <path>` (do **not** treat a path as a branch revision).
- **Empty** → try `git diff @{upstream}...HEAD`; if that command *fails* (no upstream configured) or returns nothing, fall back to `git diff main...HEAD`, and additionally run `git diff HEAD` to pick up uncommitted work.

Treat the diff as the scope, but **read the surrounding code freely** — structural
problems live in how the diff relates to the rest of the system, not in the hunk.

Also read the CLAUDE.md files governing the changed files (repo root + any in an
ancestor directory of a changed file). Their stated philosophy is itself a source
of "an invariant the code should hold structurally."

## Phase 1 — Map the cross-cutting contracts (BEFORE hunting defects)

Identify the **invariants and boundaries the diff touches**. For each, write down
three things — this enumeration is what replaces sampling:

1. **The contract** — the rule that must hold, stated in the repo's own terms.
   Derive it from what the diff actually touches and from the governing CLAUDE.md
   (Phase 0), not from a fixed catalog. The recurring *shapes* it takes are
   language-agnostic — instantiate them in the idioms of the diff's language(s),
   naming the real function/field/type rather than an analogy from another
   project:
   - "this boundary function always returns a safe value on error, never throws";
   - "this state is read through one accessor, never raw";
   - "an explicit/user signal must always win over an inferred default";
   - "every member of this closed set is handled, with no silent fallthrough."
2. **The chokepoint** — the single place that should enforce it (one decoder, one
   type, one helper, one field). If there is none, that absence is itself a candidate
   finding.
3. **Every site that must honor it** — by **exhaustive enumeration, not sampling**.
   Grep every consumer of the boundary function, every setter of the field, every
   reader of the shared state. List them all with `file:line`.

If the agent tool is available, run this as parallel agents, **one per contract**,
each returning the contract, the chokepoint (or "none"), and the complete site
list. If it is not, do it serially — do not claim parallelism you didn't perform.

Bias toward over-identifying *candidate* contracts here; a contract with only one
site is cheap to discard in Phase 3. Over-identification is a Phase-1 search
tactic, **not** a license to report speculative findings — Phase 5 is the filter.

## Phase 2 — Find class-level defects against each contract

For each contract, find the sites that violate it, and watch for these four
structural smells:

- **Invariant-by-convention across N sites** — the rule is held by "remember to
  call X at each site" rather than by a type/chokepoint. Every site is a chance to
  forget; list the ones that already did and the ones that will.
- **Proxy / overloaded field that will drift** — a concept is inferred from a
  stand-in (a presence check, a count, or a string match standing in for a
  category) rather than represented directly. Name what it mis-handles the moment
  the taxonomy grows.
- **Duplicated contract / copy-pasted translation** — the same decode/translate
  block appears at multiple call sites instead of once at the boundary.
- **Parallel state coupled only by prose / call-ordering** — two maps/caches whose
  "only valid while the other is empty" relationship is enforced by statement
  order and a comment, not by structure. Enumerate every lifecycle op (clear,
  evict, invalidate) and check each touches both.

Apply the **fix-as-new-code** lens: for any change that is itself a fix (especially
fixes from a prior review pass), ask whether it *generalized the mechanism* or just
*patched one site* — a per-site fix to a convention-shaped problem is a future
finding.

A defect's failure scenario here may legitimately be **"the next site added will
silently violate this."** That is the signal a sampler discards, so don't drop a
defect merely for lacking a single-line crash. **But** such a forecast is only
admissible when it is concrete and falsifiable: name the existing sites, the
chokepoint they should route through, and the *specific* most-likely next site and
what it would do wrong. "Someone could one day misuse this" with no named site and
no plausible mechanism is not a finding — discard it.

## Phase 3 — Cluster by root cause (the key inversion)

Group every defect by **shared root cause**. If two or more defects are instances
of the same missing structure, **collapse them into ONE finding stated at the
pattern level**. Dedup *up* to the pattern, never *down* to the single worst site.

Rank clusters by **aggregate risk**: (number of current violating sites) +
(likelihood and blast radius of the next site) + (how silent the failure is). A
class with five convention-bound sites outranks one concrete crash, because the
crash is bounded and the class is not.

## Phase 4 — Prescribe the construction-level fix (and check it's worth it)

For each cluster, propose the fix that makes the class **fail to compile or
impossible to write**, not a per-site patch:

- a **sum type at the boundary** + exhaustive `match`/`switch` (no default), so a
  consumer that forgets a case doesn't build;
- a **single decoder/translator** at the boundary returning the already-classified
  result, so the translation can't differ per site;
- a **semantic field** replacing a proxy, so the concept is represented not inferred;
- a **single source of truth** replacing parallel state, so there's one lifecycle;
- a **completeness check over a closed set** — enumerate the total set (every
  subclass, enum variant, route, …) plus a forcing-function test that fails on any
  unclassified member — when the contract is "handle every shape" and no type can
  enforce it at compile time. For this class the test **is** the construction-level
  fix, not a fallback.

Before prescribing, run two checks:

- **Feasibility / boundary check.** Confirm the chokepoint the fix relies on can
  actually carry it. Compiler-enforced exhaustiveness or type-level guarantees
  often hold *within* a language or process but **not across a boundary** —
  serialization, FFI, an IPC/network hop, or a dynamically-typed layer — where the
  value is re-decoded and the guarantee must be re-established with a runtime
  decoder + test rather than assumed. If the fix is impractical at the real
  boundary the diff crosses, say so and prescribe the next-best enforceable shape.
- **Proportionality check.** State why the structure beats the convention *here*.
  If the class has few, stable sites and the cheap fix is "add a chokepoint test +
  a comment," recommend **that** and say the refactor isn't yet warranted.
  Distinguish two kinds of test, though: a *pinning* test for a small, closed
  convention is a proportionality fallback; a *forcing-function* test over an
  open-ended set — one that fails when a new member is added — is the primary fix,
  not a fallback. Do not prescribe a refactor whose cost exceeds the risk it removes.

Name the existing violating sites the fix closes and the future sites it forecloses.
Where the repo's CLAUDE.md already prescribes the shape or names the repo's own
structural weapon, cite it — the CLAUDE.md is the source of this repo's concrete fix
vocabulary, so prefer it over a generic analogy.

## Phase 5 — Verify the class, then anchor to the diff

Before writing the report, put each surviving cluster through two gates, in order.

**1. Verify it is real (not guarded elsewhere).** The dominant false positive for a
structural finding is "site X doesn't route through the chokepoint!" when X is in
fact handled one frame up, or the missing case is unreachable. For each cluster,
prove at least one site is *genuinely* unguarded: quote the line that breaks the
contract **and** confirm nothing between it and the boundary re-establishes the
invariant — read the enclosing function and the caller, don't infer from the name.
State a verdict per cluster:
- **confirmed** — a named site is unguarded; quote the exact line.
- **refuted** — the invariant is held after all (cite the guard you found) → drop the cluster.

A cluster you cannot confirm against a real, quoted line does not ship — no matter
how plausible the pattern.

**2. Anchor to the diff.** For each *confirmed* cluster, tag whether **this diff
introduced, extended, or added a site to the class**:
- **introduced/extended by this diff** → a finding for this review;
- **pre-existing, diff only sits adjacent** → demote to a one-line "pre-existing
  context" note, not a finding — this PR is not on the hook for the whole codebase.

Drop any cluster that survives only as an unfalsifiable forecast (no named next
site, no mechanism). A short, true review beats a padded one.

## Output

Lead with a 2–3 sentence map of the contracts the diff touches. Then present the
**clusters**, highest aggregate-risk first, each as:

- **Class** — one sentence naming the invariant and how it's currently enforced.
- **Sites** — the current violating/at-risk locations as `file:line`, including the
  **quoted unguarded line** that confirmed the class (Phase 5), plus the named
  most-likely next site.
- **Why convention fails here** — the concrete cost (which sites already diverged,
  what the next one silently does wrong).
- **Structural fix** — the type/chokepoint/field/SSOT that closes the class, with
  its feasibility/proportionality verdict and the CLAUDE.md rule it satisfies if any.

Cap on **distinct root causes** (≤5), not on instances — one class may cite many
sites and that is the point. If a genuine one-off bug surfaces that has no class,
do **not** crowd it in: list it under a short "instance bugs — run `/code-review`
to triage" pointer so this review stays about structure.

If no cross-cutting contract is introduced or extended by the diff — it is
genuinely local — say so plainly and recommend `/code-review` (or `/review` for a
PR) instead. A clean structural review is a valid result.
