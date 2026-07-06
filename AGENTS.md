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

## Project Overview

Exordos Core is an open-source NoOps platform for managing corporate infrastructure and software ecosystems. It provides declarative, AI-ready infrastructure management with image-based provisioning and sovereign deployment capabilities.

## Project Structure and Module Organization

```text
exordos_core/
├── exordos_core/        # Main source code
│   ├── elements/        # Software elements (databases, messengers, services)
│   ├── iam/             # Identity and access management
│   ├── network/         # Network configuration and load balancers
│   ├── secret/          # Certificate and secret management
│   └── tests/           # Unit and functional tests
├── docs/                # Documentation (Markdown files)
├── output/              # Build artifacts and inventory files
└── tests/               # Additional test suites
```

## Build, Test, and Development Commands

### Testing

```bash
# Run all tests (unit + functional)
tox -e py310,py312,py314

# Run unit tests only
tox -e py310

# Run functional tests
tox -e py310-functional

# Run linters
tox -e ruff-check    # Check code style
tox -e mypy          # Type checking
```

### Build Commands

```bash
# Build core image
make build_core

# Bootstrap deployment
make bootstrap
```

### Development

```bash
# Start documentation server
tox -e docs

# Fix code style automatically
tox -e ruff

# Fix markdown linting
make mdlint
```

## Code Style and Naming Conventions

- **Language**: Python 3.10+
- **Formatter**: Ruff (format + linting)
- **Type Checking**: MyPy
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Tests**: Located in `exordos_core/tests/unit` and `exordos_core/tests/functional`
- **Comments for code**: write on english

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
- Run `make mdlint` for Markdown linting
- Keep `README.md` synchronized with new features

## Important Paths

- **Source**: `exordos_core/`
- **Tests**: `exordos_core/tests/`
- **Documentation**: `docs/` — see `docs/core-developer-guide/index.md` (architecture)
  and `docs/usage/troubleshooting.md` (debugging) first
- **Build output**: `output/`
