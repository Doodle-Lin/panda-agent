# Changelog

All notable changes to PandaAgent. The format is based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- MIT license and community health files: security reporting guidance, code of
  conduct, support guidance, issue forms, and a pull-request template.

### Changed

- Updated English and Chinese documentation for the embedded SQLite graph
  memory, current test baseline, and the remaining self-evolution limitations.

## [0.2.0] — 2025

Self-evolution becomes falsifiable: patches are gated on measured task
performance, not just on unit tests.

### Added

- **Regression benchmark gate** (`benchmark.py`). The previous gate asked "do
  tests pass?"; the new one asks "did the agent improve?". A task suite with
  deterministic scorers produces a weighted score before and after each patch;
  drops beyond a measured tolerance revert the patch. Verified end-to-end: an
  agent that merely drops line numbers from `search_files` output scores
  100 → 89.3 while passing every unit test, and is now rejected.
- **Strict evaluation parsing** (`parsing.py`). A parse failure previously
  silently became `score = 50`, so the Improver optimised against noise.
  Failures now retry once with the specific error, then return *no signal* so
  the orchestrator skips that round instead of guessing.
- **Test coverage for the evolution loop.** `orchestrator.py` went from zero
  tests to 19; the prompt bug below was found while writing the first of them.
  The suite as a whole grew from 19 to 197 cases.

### Changed

- **Patch application moved from regex to libcst** (`patching.py`). The old
  `^def name\(.*?(?=\ndef \w+\(|\Z)` replacement silently no-op-ed on
  decorated, `async`, nested and class-scoped definitions. Worst case, a
  function body containing the text `\ndef ` in a string literal truncated the
  file into a `SyntaxError` *before* pytest could catch it. The new path parses
  the module, replaces the definition, and validates that the result parses
  before anything is written to disk.
- **Execution boundaries enforced** (`security.py`). `run_command` previously
  used `shell=True`; `echo SAFE; echo INJECTED` ran both halves. Commands now
  parse to an argv allowlist with `shell=False`, shell metacharacters are
  rejected, and file tools resolve paths before a containment check so `..`
  traversal and symlink escapes are caught. Credential-shaped environment
  variables are stripped from subprocesses.

### Fixed

- **Improver prompt `KeyError`.** `_IMPROVE_PROMPT` contained a literal
  `{code_here}` that `str.format` treated as a field, so *every* call raised
  `KeyError` — the core self-improvement mechanism had never successfully run.
  Found by writing the first test that exercised it.
