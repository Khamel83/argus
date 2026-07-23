# Container runtime profiles

`playwright-seccomp.json` is the upstream Docker default seccomp profile with
the user-namespace calls required by Chromium's sandbox. It is vendored from
Playwright tag `v1.58.0`:

<https://github.com/microsoft/playwright/blob/v1.58.0/utils/docker/seccomp_profile.json>

Keep the profile, the digest-pinned Playwright runtime image, and the locked
Python Playwright package on the same release.
