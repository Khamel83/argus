# Legacy egress worker authentication patch — October 21, 2026

Source base: fetched `origin/main` `62d0d2243ac9e8315fd955135058c40d3d9adf65`.
Reporter: Syed Anas Mohiuddin, Cognivators LLC, GitHub `SyedAnas01`.

## Confirmation and change

The reported `_check_auth` returned normally for an unset or empty
`ARGUS_EGRESS_SHARED_SECRET`. The CLI bound to `0.0.0.0:8273` by default;
the authority gate rejected only an explicit production environment. These
source conditions confirm unauthenticated free-provider dispatch exposure on
reachable workers. No evidence of exploitation or deployed exposure was collected.

Version 1.6.5 requires a nonblank secret and explicit `ARGUS_ENV=development`
at worker startup. Per-request authentication independently rejects absent,
blank or incorrect bearer credentials before provider construction. Comparisons
use `secrets.compare_digest`. There is no unauthenticated development exception.
The CLI defaults to `127.0.0.1:8273`; custom binds still require authentication.
The paid-provider denial remains enforced after authentication.

The residential extraction server is a separate implementation with its own
shared-secret and allowlist controls. SECURITY.md now distinguishes the two.
Historical design documents retain their original examples with a current-default
notice. Release metadata, lock metadata, live-release test identities and
release-bound provider attestations are synchronized at 1.6.5. Historical
manifest/contract fixtures retain their historical versions.

## Verification

- Worker/authority focused suite: 72 passed.
- Release workflow checks after correcting both literal and regex version
  assertions: 9 passed.
- Provider fixture exact check and release contract: passed, schema head unchanged.
- Wheel and source distribution built with `uv build`; both pass `twine check`.
- Wheel installed using isolated `uv run --no-project` outside the repository;
  confirmed imports from site-packages, version 1.6.5, reported PoC raises 401,
  and missing-secret startup raises RuntimeError. No provider request was made.
- Full-suite first run: 79 failures caused by release-bound fixtures and active
  version assertions retaining 1.6.4. After updating those references, 3065
  passed with one remaining old-version regex assertion; that assertion is fixed.
  Final full suite: 3066 passed, 49 skipped, four existing cookie deprecation
  warnings, in 55.25 seconds. Local PostgreSQL tests skip without a disposable
  configured test database; native PostgreSQL CI remains required.
- GitHub private vulnerability reporting: GET returned `enabled=false`, PUT
  enabled it, independent GET returned `enabled=true`.

Local logs and package artifacts are retained under the ignored worktree
`.artifacts/` directory. Native CI, exact-head trusted review, normal merge,
PyPI publication and installed-PyPI verification are separate gates. No
production deployment or reporter message is established by this source note.
