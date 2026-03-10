# Contributing to JupyterHealth Exchange

## Development Setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/jupyterhealth/jupyterhealth-exchange.git
cd jupyterhealth-exchange

# 2. Install dependencies
pipenv install --dev
pipenv shell

# 3. Start the test database
source ci/test_env.sh
bash ci/start-db.sh

# 4. Install pre-commit hooks (runs linters automatically on each commit)
pre-commit install
```

## Code Quality Standards

Every pull request **must** include tests that cover the code you added or changed. PRs without adequate test coverage will not be merged.

### Test Categories

| Type | Purpose |
|------|---------|
| **Unit** | Test a single function/method in isolation |
| **Integration** | Test multiple components working together |
| **Regression** | Prove a specific bug stays fixed |

### Test Requirements

1. **New features**: Unit tests for new functions/methods. At least one integration test.
2. **Bug fixes**: A regression test that fails before the fix and passes after.
3. **Refactors**: Existing tests must still pass. Add tests if coverage gaps are found.

### Running Tests

```bash
# Run all tests (coverage is collected automatically via pyproject.toml)
pytest

# Run a specific test file
pytest tests/test_model_methods.py
```

### Writing Tests

- Place test files in `tests/` with the naming convention `test_<module>.py`.
- Keep test methods focused: one assertion per behavior, descriptive method names.

## Pull Request Checklist

- [ ] Tests pass locally (`pytest`)
- [ ] Pre-commit hooks pass (automatic if installed via `pre-commit install`)
- [ ] New/changed functions have tests
- [ ] No hardcoded secrets or credentials
- [ ] DB-backed settings use `get_setting()`; not `settings.X`; for runtime config (see [Settings Architecture](#settings-architecture))
- [ ] If there are large refactors, please flag it in advance and ensure other developers are made aware.

## Settings Architecture

Runtime configuration lives in the database via the `JheSetting` model, accessed through `get_setting(key, default)`.

**Rules:**
- `jhe/settings.py` ENV vars are for **Django startup only** (DB config, `ALLOWED_HOSTS`, middleware).
- Application code in `core/` must use `get_setting("key", fallback_default)` for runtime values.
- `get_setting` is imported at the top of `core/models.py`. The circular dependency with `core.jhe_settings.service` is resolved by a lazy import of `JheSetting` inside the service module.
- Seed defaults are defined in `core/management/commands/seed.py` → `seed_jhe_settings()`.

## Reporting Issues

Open a GitHub Issue with:
1. Steps to reproduce
2. Expected vs actual behavior
3. Relevant logs or screenshots

Tag with appropriate labels (`bug`, `enhancement`, `documentation`).
