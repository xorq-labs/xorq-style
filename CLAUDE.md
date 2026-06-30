# xorq-style

A lint tool that enforces project conventions by **modeling Python's AST
surface** — statement, expression, and operator node types. The dominant failure
mode in this codebase is *per-shape churn*: a rule classifies the AST node shapes
we remembered, a construct we didn't (`match` in 3.10, `type` aliases in 3.12, a
comparison operator in a `TYPE_CHECKING` guard, a conditional binding) slips past,
and we ship another "close N more gaps" fix. The branch history of the `init-all`
work is eight such fixes in a row. Don't add to it — design against the class.

## Invariants for any rule that classifies AST nodes

- **Classify against the TOTAL set, not a remembered subset.** When logic
  branches on AST node type, derive the universe from the runtime
  (`_all_subclasses(ast.stmt)`, `ast.cmpop`, an assignment-target shape set, …)
  and consciously bucket *every* member. A hand-written allowlist of shapes with
  no completeness check is a latent bug, not a finished rule.

- **Soundness contract: fail toward silence.** A construct the checker cannot
  fully model produces **no** finding (a silent miss), never a false positive.
  Every rule here leans on this — `__all__` it can't reconstruct, a binding form
  it can't place, a guard it can't evaluate all degrade to "unverifiable → skip,"
  not to a wrongful flag.

- **Every shape-classifying model needs a forcing-function test** that enumerates
  the full subclass set and **fails on anything unclassified**, so a future node
  type lands in the test instead of in a bug report. The canonical examples:
  - `test_build_module_scope_classifies_every_statement_type` (statements)
  - `test_type_checking_guard_folds_every_boolean_operator` (operators)
  - `test_mutates_dunder_all_shape_set_pinned` (`__all__`-mutation shapes)
  This *is* the structural fix for the allowlist class — not a proportionality
  fallback. If you fix a bug in one of these models, extend its forcing test **in
  the same change**.

- **Prefer the runtime oracle** for `__all__`/binding correctness:
  `test_init_all_sound_against_runtime_all` `exec`s a fixture, reads the real
  runtime `__all__`, and asserts the static result agrees. Test against ground
  truth, not against the cases you remembered to enumerate.

- **Before the SECOND fix in a "close another gap" series, stop.** Name the
  generating mechanism (which model, which missing dimension) and fix the class —
  add the missing axis and its forcing test — rather than patching the instance.

## Orientation

- One pass builds `_ModuleScope` (`_build_module_scope` → `CheckContext.scope`);
  it is the single chokepoint for binding and `__all__` information. Rules read
  it instead of re-walking the tree.
- A rule is a class with `rule` + `check(ctx)`, a `RuleId` in `enums.py`, a
  description in the `RULES` map, and an entry in `ALL_RULES`.

## Done bar

`pre-commit run --all-files` (ruff, ruff format, mypy, and the `xorq-check-style`
self-lint) must pass before declaring work complete.
