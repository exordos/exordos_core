# Exordos Core Agent Guide

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```text
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Code Style and Naming Conventions

- **Comments for code**: write comments in English

## VCS Conventions

### Commit Message Format

```text
<type>(<scope>): <subject>

<body>

<footer>
```

**Example:**

```text
feat(repo): add HTTP server proxy driver

- Implement SimplePythonRepoDriver for file serving
- Add port configuration and error handling
- Include unit tests for driver lifecycle

Closes #123
```

### Pull Request Requirements

- **Title**: Use imperative, present tense: "Add feature", not "Added feature"
- **Description**: Clear summary of changes

## Additional Guidelines

### License

All source files must include Apache 2.0 license header:

```python
#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")
```

### Dependencies

- Manage via `pyproject.toml` and `uv.lock`
- Specify version constraints (e.g., `>=8.1.7,<9.0.0`)
- Include license information in comments

### Documentation

- Before working on element/manifest/reconciliation internals, read
  `docs/core-developer-guide/index.md` (element/resource model, `ElementEngine`,
  reconciliation loop, manifest value rendering).
- Before debugging a stuck element, node, or config delivery, read
  `docs/usage/troubleshooting.md`.
- Update `docs/` for CLI changes
- Run `tox -e cli_docs` to regenerate CLI docs
- The OpenAPI documents under `docs/openapi/` and the resource table in
  `docs/em/api_documentation*.md` are not in the tree: `tox -e
  docs`/`docs-build` writes them with `ec-openapi-spec` and `ec-em-api-docs`
  before building the site. Never commit them; to look at one, run the
  command that writes it.
- Run `make mdlint` for Markdown linting
- Keep `README.md` synchronized with new features
