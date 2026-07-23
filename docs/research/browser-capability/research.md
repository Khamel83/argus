# Container browser and extraction capability contract

Date: 2026-07-22
Wayfinder ticket: [#27 — Choose the container browser and extraction capability contract](https://github.com/Khamel83/argus/issues/27)

## Decision

The homelab production image should **install and promise one local
browser-backed extraction capability: Playwright's bundled Chromium headless
shell**. The package, browser binary, and Linux dependencies must be installed
from one frozen Playwright version during the image build. The production
service should share one browser process, admit at most one browser extraction
at a time, and create a fresh non-persistent browser context for every
extraction.

The browser-enabled service should start with a **1 GiB hard production memory
limit**, admitted only after the bounded canaries below. The existing 512 MiB
case remains a mandatory failed-launch/leak regression, not a permanent ceiling
for a working browser. A hard limit is a safety boundary, not preallocated RAM;
the measured working set and headroom remain the relevant operating evidence.
If the enabled image cannot pass, the safe rollback is to
deliberately disable Playwright and advertise that fact; raising the memory
limit without lifecycle evidence, silently retrying impossible launches, or
substituting paid extraction does not satisfy this contract.

This is a design conclusion from the primary evidence below, not a claim that
the current image already meets the contract.

## Why the current contract is unsafe

The current Python dependency declaration allows any Playwright version from
1.40 onward, while the Docker build uses `pip install` without the repository
lock file. The final image copies the Python package but installs neither a
browser binary nor its system dependencies. Consequently, importing Playwright
is not proof that Chromium can launch.
([`pyproject.toml`](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/pyproject.toml#L30-L41);
[`Dockerfile`](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/Dockerfile#L1-L32))

The 2026-07-22 live audit found all three kinds of drift at once: the deployed
homelab Python package reports Playwright 1.61.0, the repository `uv.lock`
records 1.58.0, and the deployed image has no matching browser executable.
The lock evidence is durable in the repository; the deployed version and
missing executable are sanitized live-runtime observations from this audit.
([`uv.lock`](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/uv.lock#L1939-L1956);
[`Dockerfile`](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/Dockerfile#L10-L25))

That mismatch is already a production incident, not a hypothetical risk.
Issue #22 records a missing Chromium executable followed by repeated
Playwright-driver leaks, a 512 MiB container OOM, exit 137, automatic restarts,
and failed in-flight callers.
([issue #22](https://github.com/Khamel83/argus/issues/22))

The leak follows directly from the current lifecycle. Argus starts a
Playwright runtime, attempts to launch Chromium, and returns `None` on failure
without stopping or clearing the runtime. `_check_playwright()` only checks
whether the Python module exists. There is no serialized initialization or
failed-capability latch.
([`playwright_extractor.py`](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/argus/extraction/playwright_extractor.py#L28-L81))

Request cleanup is also incomplete: `context` is assigned inside the `try` but
used unconditionally in `finally`, and application shutdown never calls the
module's existing `close_browser()` function.
([request cleanup](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/argus/extraction/playwright_extractor.py#L84-L148);
[`close_browser()`](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/argus/extraction/playwright_extractor.py#L156-L175);
[API lifespan](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/argus/api/main.py#L82-L119))

The repository Compose service currently has restart and HTTP health checks but
does not encode any memory limit, an init process, browser shared-memory
configuration, sandbox support, or a browser-aware readiness signal.
([`docker-compose.yml`](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/docker-compose.yml#L1-L27))

## Primary-source constraints

Playwright requires browser binaries matched to each Playwright release and
warns that upgrades may require reinstalling them. Its CLI supports installing
one browser with its system dependencies, and a headless-only deployment may
install only the Chromium headless shell. That makes a single frozen
Playwright-plus-Chromium installation the narrowest supported capability, while
installing all three engines would add image weight without serving Argus's
contract.
([Playwright browser installation](https://playwright.dev/python/docs/browsers#install-browsers);
[system dependencies](https://playwright.dev/python/docs/browsers#install-system-dependencies);
[Chromium headless shell](https://playwright.dev/python/docs/browsers#chromium-headless-shell))

Playwright's Docker guidance says a custom image needs Python, Playwright
browsers, and browser system dependencies. It also says version mismatches can
prevent Playwright from locating executables. The official Playwright image is
described as a testing/development image and is not recommended as-is for
visiting untrusted sites, so Argus should retain its own production image
rather than adopt that image wholesale.
([Playwright Docker guide](https://playwright.dev/python/docs/docker#introduction);
[image version pinning](https://playwright.dev/python/docs/docker#image-tags);
[custom image requirements](https://playwright.dev/python/docs/docker#build-your-own-image))

For crawling untrusted websites, Playwright recommends a separate container
user plus its seccomp allowance so Chromium sandboxing can operate. It also
recommends an init process to reap child processes and host IPC for Chromium to
avoid shared-memory crashes. The current Argus image already runs as a
non-root user, but its launch arguments explicitly disable Chromium's sandbox,
so non-root alone is not the completed security contract.
([Playwright crawling guidance](https://playwright.dev/python/docs/docker#crawling-and-scraping);
[recommended Docker configuration](https://playwright.dev/python/docs/docker#recommended-docker-configuration);
[current launch arguments](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/argus/extraction/playwright_extractor.py#L74-L77))

Playwright browser contexts are isolated sessions; non-persistent contexts do
not write browsing data to disk. `browser.new_context()` does not share
cookies or cache with other contexts, and Playwright recommends explicitly
closing each context. Closing a context closes all of its pages.
([BrowserContext isolation](https://playwright.dev/python/docs/api/class-browsercontext);
[`browser.new_context()`](https://playwright.dev/python/docs/api/class-browser#browser-new-context);
[`browser_context.close()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-close))

Persistent contexts instead store cookies and local storage in a user data
directory; the same directory cannot be used by multiple browser instances.
That state and locking behavior are unnecessary for retrieval. Authenticated
extraction can inject the permitted cookies into a fresh request context
without retaining a profile.
([`launch_persistent_context`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context))

Playwright emits `browser.on("disconnected")` when the browser closes or
crashes, and its graceful order is to close explicitly created contexts before
closing the browser.
([browser disconnect event](https://playwright.dev/python/docs/api/class-browser#browser-event-disconnected);
[browser close lifecycle](https://playwright.dev/python/docs/api/class-browser#browser-close))

Docker does not impose a memory limit unless one is configured; under OOM it
normally kills container processes. Compose can encode the memory limit,
init/reaping behavior, shared-memory configuration, and shutdown grace period,
so these must be part of the deployment definition rather than operator
conventions.
([Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/);
[Compose `mem_limit`](https://docs.docker.com/reference/compose-file/services/#mem_limit);
[Compose `init`](https://docs.docker.com/reference/compose-file/services/#init);
[Compose `shm_size`](https://docs.docker.com/reference/compose-file/services/#shm_size);
[Compose `stop_grace_period`](https://docs.docker.com/reference/compose-file/services/#stop_grace_period))

## Options considered

| Option | Reliability and capability | Cost and risk | Decision |
|---|---|---|---|
| Install version-matched Chromium headless shell | Preserves local JS rendering and authenticated extraction; removes the known package-without-browser mismatch | Larger image and a live browser inside a strict memory/security envelope; requires lifecycle, sandbox, concurrency, and canary controls | **Production target, initially bounded by a 1 GiB hard limit** |
| Deliberately disable Playwright | Eliminates browser launch and browser-memory failure modes; basic HTTP extraction and later fallbacks still work | Loses the principal local JS-rendering step and authenticated browser extraction, so fewer pages work without external services | **Rollback/emergency mode, explicitly reported as degraded in production** |
| Use the official Playwright image wholesale | Provides matched browsers and OS dependencies | Officially positioned for test/development and not recommended as-is for untrusted crawling; broadens Argus's image beyond its needs | Rejected |
| Install all Playwright browsers | Maximizes engine choice | Argus uses Chromium only; adds download and image surface with no current contract benefit | Rejected |
| Depend on remote CDP/Obscura | Can move browser cost out of the Argus process | Introduces another runtime/readiness dependency and does not remove the need for honest local capability state | Not part of the minimum production contract |

## Production contract

### 1. Image and browser installation

1. Build from a supported glibc Python base.
2. Make the repository lock file authoritative for the production build; do
   not resolve `playwright>=...` afresh during `docker build`.
3. From that exact installed Playwright package, run the equivalent of
   `python -m playwright install --with-deps --only-shell chromium` as root
   during image construction. Put browser binaries in an explicit
   image-owned path readable by the runtime user.
4. Fail the image build if the browser or dependency installation fails.
5. Record the Playwright package version and browser revision in build
   metadata. Do not use an arbitrary system Chrome `executable_path`;
   Playwright warns that it works best with its bundled Chromium.
   ([Playwright launch API](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch))
6. Run the service and browser as the non-root `argus` user with Chromium
   sandboxing enabled and the documented seccomp user-namespace allowance.
   Remove the production `--no-sandbox` and `--disable-setuid-sandbox`
   overrides.
7. Encode `init: true`, browser IPC/shared-memory configuration, the initial
   1 GiB hard limit, and a shutdown grace period longer than the maximum
   in-flight browser timeout in the canonical homelab deployment.

### 2. Startup capability detection

Importability is only a static prerequisite. At application startup Argus must
run one bounded, network-free capability probe:

1. Confirm production mode is `enabled`.
2. Confirm the expected browser executable inventory exists.
3. Start Playwright and launch the bundled Chromium.
4. Create a fresh context and page.
5. render a deterministic bundled or `data:` document and verify a known
   marker.
6. Close the canary page/context.

On success, keep the validated shared browser and publish capability state
`available`. On any failure, close anything created, stop the Playwright
runtime exactly once, clear every browser/runtime/context handle, and publish
`unavailable` with a bounded reason code. The HTTP process may continue so
non-browser retrieval remains usable.

Liveness must only prove that the Argus process/event loop responds. It must
never launch a browser or depend on a public website. Readiness and detailed
health expose browser capability separately.

### 3. Capability state and recovery

Maintain one process-local state machine guarded by a single async
initialization lock. This vocabulary is intentionally reusable across homelab
services that host optional local runtimes (browser, model, converter, or
worker): capability state remains separate from whole-service readiness.

```text
disabled
   └─ explicit configuration only

unknown → probing → available
                  ↘ unavailable_static
                  ↘ cooldown → probing

available → disconnected/cancelled → cooldown
```

- `unavailable_static` covers missing executable inventory or a proven
  package/browser mismatch. Requests skip Playwright without starting another
  runtime. Only a deployment restart or authenticated administrative reset
  clears it.
- Launch failure with an installed matching executable, browser crash, or
  disconnect enters a bounded exponential cooldown. Exactly one internal local
  canary may retry, with backoff capped at five minutes; caller traffic never
  creates parallel recovery attempts.
- An authenticated administrative reset is explicit and idempotent: close and
  clear all handles, return to `unknown`, then run one serialized probe.
- The public status contains only `configured`, `state`, and a bounded
  `reason_code`. Admin detail may also contain sanitized probe timestamps,
  browser/package versions, retry time, and failure counts. It must not expose
  cookies, URLs, filesystem secrets, headers, or raw exception payloads.
- The browser's `disconnected` event atomically invalidates the cached handle;
  no request may receive a disconnected browser merely because a Python object
  still exists.

### 4. Request isolation and resource bounds

- Share at most one healthy browser process.
- Use a global browser-extraction semaphore of **1** under the initial 1 GiB
  contract. Queue with a bounded timeout; do not create another browser to
  bypass it. Increase concurrency only through a later measured capacity
  decision.
- Initialize `context`, `page`, and any CDP session to `None` before the
  request `try`.
- Create one non-persistent context per extraction and never reuse it across
  callers, domains, or requests.
- For authenticated extraction, load only the cookies authorized for that
  request/domain into its new context. Do not cache domain contexts or use
  persistent user-data directories.
- Disable downloads unless the extraction contract needs them. Do not persist
  HAR, video, trace, browser cache, or profile artifacts in production.
- Apply bounded navigation and total-operation timeouts. Cancellation and
  every exception path close CDP session, page, and context in `finally`.
- If cleanup fails or the browser disconnects, invalidate the browser and
  enter recovery; never return that handle to the pool.

### 5. Shutdown

The FastAPI lifespan owns browser shutdown:

1. Mark browser capability `draining` and reject/skip new browser work.
2. Allow the single active extraction to finish within the bounded grace
   period, then cancel it if necessary.
3. Close any remaining contexts.
4. Close the browser.
5. Stop the Playwright runtime.
6. Clear all handles and reach `stopped`.

Compose must forward `SIGTERM`, reap children with `init: true`, and grant
enough `stop_grace_period` for this sequence. A graceful shutdown test must
show no orphan Playwright driver or Chromium process.

### 6. Health semantics

For the production profile, Chromium is an expected optional capability:

- `available`: no effect on overall readiness.
- `cooldown`, `unavailable`, or emergency `disabled`: overall service is
  **degraded** while authenticated API, persistence, minimum HTTP extraction,
  and search still work.
- Browser failure alone does not make the service **unready**.
- Failure of the minimum extraction path or durable service contract remains
  **unready**, independently of Playwright.

The existing `/api/health` only infers health from enabled search providers and
does not observe extraction capabilities, so it cannot satisfy this contract.
([current health route](https://github.com/Khamel83/argus/blob/f9aa1adaa219c80aef209b7e9b994333b37c3adc/argus/api/routes_health.py#L14-L34))

## Required bounded evidence before promotion

No production or paid probe was run for this research. The implementation must
produce sanitized aggregate evidence for all of these gates:

1. **Build inventory:** the frozen Playwright package version and installed
   Chromium revision match; the image build fails when browser installation is
   intentionally removed.
2. **Startup positive path:** the deterministic local launch/render/context
   cleanup probe reaches `available` without network access.
3. **Startup negative path:** an image/test fixture without the executable
   reports `unavailable_static` and overall `degraded`; twenty extraction
   attempts start no additional Playwright runtime.
4. **Launch-failure cleanup:** injected launch failure calls
   `playwright.stop()` exactly once, clears all handles, and leaves zero driver
   or Chromium child processes.
5. **Recovery:** explicit reset and one transient disconnect/cooldown path can
   return the capability to `available`, with no parallel launches.
6. **Request cleanup:** navigation error, timeout, cancellation, unsafe
   redirect, and content-quality failure each close the request context and
   page.
7. **512 MiB failed-launch regression:** use the deliberately browser-missing
   image/fixture from issue #22, one Uvicorn worker, a 512 MiB hard limit, and
   twenty sequential extraction attempts. It must produce:
   - one bounded capability failure rather than twenty runtime starts;
   - zero OOM events;
   - zero container restarts;
   - zero connection resets/refusals;
   - continuously responsive liveness;
   - truthful degraded capability state;
   - zero orphan Playwright driver/Chromium processes after shutdown.
8. **Browser-enabled production canary:** use the version-matched image, one
   Uvicorn worker, browser concurrency one, a 1 GiB hard limit, and twenty
   sequential representative public-page extractions. It must produce:
   - zero OOM events;
   - zero container restarts;
   - zero connection resets/refusals;
   - continuously responsive liveness;
   - truthful readiness/capability state;
   - zero orphan driver/Chromium processes after shutdown.
9. **Headroom:** peak cgroup working memory should remain at or below 80% of the
   1 GiB limit (about 819 MiB). This 20% reserve is a design safety margin,
   not a Playwright guarantee. If the canary only survives by approaching the
   OOM boundary, the image is not reliable enough to promote.

The canary records counts, peak memory, capability transitions, loaded image
identity, and restart count. It must not retain private source URLs, cookies,
page content, auth headers, or provider credentials.

## Residual uncertainty

Official documentation does not promise a Chromium memory ceiling. Therefore
neither 512 MiB nor 1 GiB can be proven sufficient from documentation or unit
tests; only the bounded image-level canary can admit the enabled profile. The
initial 1 GiB envelope should later be reduced or increased from measured
headroom and fleet contention, not preserved from inertia. Likewise,
successful browser launch does not guarantee extraction quality on every
public site. Browser lifecycle/capability correctness and the separate
quality-gate behavior should remain distinct work, as issue #22 already
requires.
