# RFC 0002: Unblocking dependency security updates — django-saml2-auth-community, uv, and one declared override

- **Status:** Discussion
- **Companion PR:** #679 · **Hotfix for pre-existing main regression:** #678
- **Primary reviewers:** @s1monj (architecture/process), @travis-sauer-oltech (SAML behavior)
- **Follows:** the provenance format piloted in RFC 0001 (#677)

**TL;DR:** Three upstream pins outside our control had wedged the app so that
no pyjwt or cryptography security update could ever resolve. Dependabot's
security job failed red on every run (`security_update_not_possible`) and
intermittently blocked dev deploys via the all-checks gate. The exit: rename
to the maintained `django-saml2-auth-community`, migrate the root app to uv
(the toolchain `/mcp_server` already uses), and declare a one-line resolver
override that pipenv structurally cannot express.

## 1. The problem

```
Pipfile: grafana-django-saml2-auth = "*"        ← frozen forever at 3.21.0
  └── django-saml2-auth-community == 3.21.0     ← pins pyjwt==2.12.1
        └── pysaml2 == 7.5.4                    ← pins pyopenssl<24.3.0
              └── pyopenssl 24.2.1              ← caps cryptography<44
```

Three upstream facts make this chain unfixable in place:

1. **Grafana archived their repo (2026-06-16).** `grafana-django-saml2-auth`
   3.21.0 is the final release under that name — an empty metapackage
   pointing at the community fork, permanently pinning pyjwt 2.12.1. The
   maintained continuation is
   [django-saml2-auth-community](https://github.com/mostafa/django-saml2-auth)
   (same import paths); its 3.22.0 pins pyjwt 2.13.0, the fixed version.
2. **pysaml2 is frozen.** Its `pyopenssl<24.3.0` cap guards one code path
   (`saml2/cert.py` `OpenSSLWrapper.verify`) that is only reachable via the
   `validate_certificate` option we don't set, and fails closed even then.
   The fix PRs ([#977](https://github.com/IdentityPython/pysaml2/pull/977),
   [#1021](https://github.com/IdentityPython/pysaml2/pull/1021)) are unmerged;
   the sole maintainer has been unreachable since 2026-02.
3. **Modern pyopenssl tracks modern cryptography in lockstep**, so pysaml2's
   stale cap transitively holds cryptography <44 (and leaves pyopenssl's own
   CVE-2026-27459 unpatched).

Meanwhile our `Pipfile.lock` was silently **unsound**: cryptography 48.0.1
alongside pyopenssl 24.2.1 (which declares `<44`) — a combination honest
resolution forbids, kept alive only because `pipenv sync` installs the lock
verbatim. Any honest relock downgrades cryptography to 43.0.3 (two HIGH
advisories). Not hypothetical: the #692 dependabot merge (2026-08-04) shipped
exactly that downgrade to main, plus both saml distributions locked at once.
This PR supersedes both artifacts.

Security driver, stated honestly: pyjwt 2.13.0 fixes five advisories, one
HIGH ([GHSA-xgmm-8j9v-c9wx](https://github.com/advisories/GHSA-xgmm-8j9v-c9wx)).
We verified it is **not currently exploitable in JHE** — the only JWKS-driven
decode (`core/oidc_verify.py`) allows RS/ES algorithms only. So this is
hygiene, not an emergency — but an auth stack that can never take a security
fix is the unacceptable part.

## 2. The change

1. **Rename** (forced by the archive): `grafana-django-saml2-auth` →
   `django-saml2-auth-community>=3.22.0`. Same imports, settings, URLs —
   zero code changes.
2. **Toolchain: pipenv → uv** (`Pipfile`/`Pipfile.lock` → `pyproject.toml` +
   `uv.lock`). Constraints carry over (django exact-pinned tracking main;
   `fhir.resources` 7.1.0; `omh-shim` 1.4.0). Dockerfile installs via
   `uv export --frozen | uv pip install --system`; CI uses `uv sync --frozen`;
   dependabot's root entry flips `pip` → `uv`.
3. **The override** — the reason uv is required:

   ```toml
   [tool.uv]
   override-dependencies = ["pyopenssl>=26.2"]
   ```

   | package | before (locked) | after (locked) |
   |---|---|---|
   | pyjwt | 2.12.1 (5 open advisories) | **2.13.0** |
   | cryptography | 48.0.1 (unsound pairing) | **50.0.0** |
   | pyopenssl | 24.2.1 (CVE-2026-27459) | **26.4.0** |
   | django-saml2-auth-community | 3.21.0 via dead shim | **3.22.0** direct |
   | everything else | — | unchanged (django tracks main: 5.2.16) |

## 3. Why uv and not something smaller

| Alternative | Why not |
|---|---|
| Merge dependabot's pyjwt PR | Impossible — that's the bug (`security_update_not_possible`). |
| Community fork under pipenv, honest relock | Tested: downgrades cryptography to 43.0.3 (two HIGH advisories). Strictly worse. |
| Keep hand-editing Pipfile.lock | pipenv has no override mechanism; every honest relock silently reverts the splice — how we got the unsound lock. |
| pip/pip-tools | Hard-errors on the resolution; no override support ([pip#8076](https://github.com/pypa/pip/issues/8076)). |
| Poetry | No overrides ([poetry#697](https://github.com/python-poetry/poetry/issues/697), closed not-planned). |
| Git-pin a patched pysaml2 fork | Works, but a git dependency in a healthcare auth stack that dependabot can't version-manage. Kept as fallback. |
| Drop SAML entirely | Product decision, out of scope (§5). |
| Wait for upstream | Maintainer unreachable ~6 months; indefinite timeline against a wedged security pipeline. |

Why the override is safe (verified empirically, not assumed): pysaml2 7.5.4
operates correctly under pyopenssl 26.x/cryptography 48–50 — a standard SP
executes no pyOpenSSL code beyond module import. The two degraded corners
(`validate_certificate`, PEFIM cert generation) sit behind config we don't
set and fail closed. Dependabot's uv updater shells out to `uv lock`, so the
override survives every future bot PR by construction. Production precedent:
[Flagsmith ships this exact override](https://github.com/Flagsmith/flagsmith/blob/main/api/pyproject.toml).

## 4. Pros / cons

**Pros:** wedged security updates unblock via honest resolution; the lock is
sound again; one toolchain (uv) across the repo; the workaround is one
grep-able line with documented retirement triggers.

**Cons:** an override asserts, against pysaml2's metadata, that pyopenssl
≥26.2 is compatible (evidence above; a frozen upstream won't contradict it);
`validate_certificate`/PEFIM must stay off while it exists (they are, and
fail closed); contributors switch pipenv → uv muscle memory (README updated);
anything parsing `Pipfile.lock` breaks (nothing in-repo does).

## 5. SAML: pre-existing gap, and its resolution

While auditing SAML reachability we found a pre-existing gap, untouched by
this PR: the production image never installs the `xmlsec1` binary that
pysaml2 needs for the request signing `jhe/settings.py` enables outside
DEBUG. Validated live inside the fly.io machine (2026-07-31):
`shutil.which("xmlsec1")` → None, `auth.sso.saml2 = 0`, no IdP metadata ever
configured. SAML has never been operable in any deployment — disabled,
unconfigured, unsignable.

**Resolution (2026-08-04):** @s1monj's review direction is that SAML should
join patient login/password reset on allauth. The community-fork swap here is
therefore **transitional**; the end-state is implemented in **#697 (RFC
0003)**, stacked on this PR, which also closes the xmlsec1 gap (RFC 0003 §5).
This PR changes no SAML behavior.

## 6. Retirement triggers (when to delete the override)

Delete `[tool.uv] override-dependencies` when **any** of:

1. pysaml2 releases with [#977](https://github.com/IdentityPython/pysaml2/pull/977)
   or [#1021](https://github.com/IdentityPython/pysaml2/pull/1021) merged.
2. `django-saml2-auth-community` moves to an uncapped pysaml2.
3. JHE migrates off pysaml2 — **the executed path**: #697 deletes the
   override.

## 7. Provenance (per RFC 0001's process)

Developed with Claude (Claude Code). Diagnosis went from the failing
dependabot job logs to a first naive fix (pipenv relock after the rename)
that was caught in lock-diff review downgrading cryptography, and rejected;
then five parallel investigations (pysaml2 upstream state, community-fork
state, JHE's actual pyjwt exploitability, override tooling across four
package managers, an empirical compatibility matrix in scratch venvs). Human
decisions: no cryptography downgrade, no hand-spliced lock, prefer the
declarative override, hand the SAML question to its author, RFC before
merge. Transcripts and the compat-matrix reproduction script available.
