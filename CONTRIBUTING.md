# Contributing to JupyterHealth Exchange

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
# Run all tests
pytest

# Run a specific test file
pytest tests/test_model_methods.py

# Run with coverage
pytest --cov=core --cov=jhe

# Run pre-commit hooks
pre-commit run --all-files
```

### Writing Tests

- Place test files in `tests/` with the naming convention `test_<module>.py`.
- Keep test methods focused: one assertion per behavior, descriptive method names.

## Pull Request Checklist

- [ ] Tests pass locally (`pytest`)
- [ ] Pre-commit hooks pass (`pre-commit run --all-files`)
- [ ] New/changed functions have tests
- [ ] No hardcoded secrets or credentials
- [ ] DB-backed settings use `get_setting()`; not `settings.X`; for runtime config (see [Settings Architecture](#settings-architecture))
- [ ] If there are large refactors, please flag it in advance and ensure other developers are made aware.

## Settings Architecture

Runtime configuration lives in the database via the `JheSetting` model, accessed through `get_setting(key, default)`.

**Rules:**
- `jhe/settings.py` ENV vars are for **Django startup only** (DB config, `ALLOWED_HOSTS`, middleware).
- Application code in `core/` must use `get_setting("key", fallback_default)` for runtime values.
- In `core/models.py`, use **lazy imports** to avoid circular dependencies:
  ```python
  def my_method(self):
      from core.jhe_settings.service import get_setting
      url = get_setting("site.url", settings.SITE_URL)
  ```
- Seed defaults are defined in `core/management/commands/seed.py` → `seed_jhe_settings()`.

## Branching & Commits

This project follows [Conventional Commits](https://www.conventionalcommits.org/).

### Branch Naming

```
<type>/<short-description>
```

| Prefix | Use |
|--------|-----|
| `feat/` | New feature |
| `fix/` | Bug fix |
| `docs/` | Documentation only |
| `refactor/` | Code restructuring (no behavior change) |
| `test/` | Adding or updating tests |
| `chore/` | Maintenance (deps, CI, config) |

### Commit Messages

```
<type>(<scope>): <short summary>
```

- **type**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `style`, `perf`
- **scope** (optional): module or area affected, e.g. `settings`, `seed`, `auth`
- **summary**: imperative mood, lowercase, no period

Examples:
```
feat(settings): migrate SITE_URL from ENV to JheSetting
fix(auth): use get_setting for redirect_uri in create_authorization_code
test(settings): add regression tests for get_setting migration
docs(readme): remove stale PKCE ENV var references
chore(ci): remove unused CHALLENGE/VERIFIER env vars
```

### Commit Discipline

- Group related changes into a single commit under one `<type>(<scope>):` header.
- Use the commit body to list what changed:
  ```
  feat(settings): migrate ENV config to DB-backed JheSetting

  - added get_setting() calls in place of settings.X references
  - wrote unit, integration, and regression tests
  - updated seed.py to create JheSetting entries on startup
  - cleaned up stale ENV vars in CI scripts
  ```
- Use `feat!:` or a `BREAKING CHANGE:` footer for breaking changes.

## Reporting Issues

Open a GitHub Issue with:
1. Steps to reproduce
2. Expected vs actual behavior
3. Relevant logs or screenshots

Tag with appropriate labels (`bug`, `enhancement`, `documentation`).
