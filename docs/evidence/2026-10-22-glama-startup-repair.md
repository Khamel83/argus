# Glama catalog startup repair — October 22, 2026

Source base: fetched main `ee58289f2318cf168d311a55ba607ad2187a4026`.

## Failure and reproduction

[Glama test01a16494](https://glama.ai/mcp/servers/Khamel83/argus/admin/dockerfile/tests/01a16494-05ac-7916-8eed-05623d7a058d)
installed argus-search 1.6.5 successfully and failed when `argus mcp serve` built
its HTTP adapter without authority configuration. Its build spec supplied no
placeholder arguments and advertised legacy provider/database configuration.
This is a catalog configuration defect, not a new worker authentication report.

The same bare command was reproduced locally with dotenv/secret resolution off:
exit 1, `AuthorityConfigurationError: MCP requires ARGUS_AUTHORITY_URL and authority
authentication; standalone development must use the external development MCP
launcher`. A real MCP stdio client with only a loopback discard URL and dummy
token initialized and listed 15 tools. No authority or provider request was made.
This exact failure/success pair isolates the startup gate; no runtime code or
security policy needed to change.

## Applied settings and live result

Glama Admin → Dockerfile now uses the checked
[build spec](../../deploy/glama-build-spec.json) and
[environment schema](../../deploy/glama-environment.schema.json):
Python 3.12, frozen lock installation, unchanged stateless MCP command, and two
required inputs: authority URL and scoped bearer token. The previous provider
keys/database schema is removed from this adapter listing. Test placeholders
are `http://127.0.0.1:9` and `glama-discovery-placeholder`; neither is a working
credential or real endpoint. The UI normalizes schema fields, so the checked
schema retains only the supported properties/type/required fields read back.

[Corrected test01a1650d](https://glama.ai/mcp/servers/Khamel83/argus/admin/dockerfile/tests/01a1650d-da73-725e-926d-0b3cabecf580)
passed in 1m 27.9s. Its native logs show MCP initialize, tools/list, prompts/list
and resources/list success. A Glama 1.6.5 release was created from that successful
build and published at 12:21 PDT. Independent navigation back to Dockerfile shows
1.6.5 in Recent Releases, the successful test, saved authority-only schema,
placeholders, Python 3.12 and frozen build command. The built source is the
fetched `ee58289` source; the following source proposal records the settings and
corrects catalog metadata, without relabeling that earlier tested source.

`glama.json` now names the existing repository owner as required maintainer and
matches package version 1.6.5. The existing CI/release contract includes its
version so a future package bump cannot silently leave Glama behind. This does
not publish a new PyPI package or change the HTTP authority deployment.

Focused delivery/release/HTTP-authority tests:72 passed. Release contract and
`git diff --check` pass. Missing authority configuration still fails closed.
Real searches/extraction require a reachable, authenticated authority; catalog
startup/discovery is not retrieval proof.

## Security follow-up

The live reporter thread contains the original report plus two follow-ups:
credit accepted/no fixed disclosure date, then a question about who requests the
CVE. Repository advisories and GitHub's pip advisories query for argus-search
both returned empty. No new report or GitHub issue was found. No CVE was
requested or new email sent in this turn. The recommendation is to let the
researcher coordinate with a CNA and return the identifier, avoiding duplicate
requests. The owner receives suggested wording in chat.

## Preservation and remaining source gate

Reuse `.worktrees/worker-auth-fix` on `fix/glama-authority-config` from fetched
main. Its five prior publication-record edits are retained in local snapshot
commit `bbce917` on `fix/worker-auth-1.6.5`; ignored test/build data and the dirty
primary remain preserved. Keep this worktree through source acceptance and an
idle/preservation check before cleanup. Trusted exact-head review, native CI and
normal source merge remain required for the recorded configuration/metadata.
