# Phase 2 local security foundation

Date: 23 November 2026. Scope: approved household-local Stage A from the corrected assessment. **Implemented locally, pending review; not deployed or enabled on the Pi.**

Subsequent update: the user approved Stage B as Phase 3 on 24 November 2026. See [Phase 3 local text privacy](phase-3-local-text-privacy.md) for the separate reviewed text route and validation. This report describes the Phase 2 baseline; generic protected execution and child chat remain closed.

## Result

Added an opt-in protected mode with local parent, child and device credentials and a closed outbound execution policy. Protected mode is a foundation for later privacy/safety implementation, not a usable child-chat release: no provider, tool, CLI, manual handoff, capability execution or live voice is enabled in it. Parent-only security administration and device registration are available. Existing adult installations continue in their previous mode unless explicitly configured otherwise.

No PII detector, sanitizer, child response classifier, content retention/deletion service, cloud user management or infrastructure deployment was implemented. Those stages remain gated. No live credentials/configuration, user transcripts or Pi services were changed. Git publication was subsequently explicitly authorized by the user; deployment remains outside that authorization.

## Authorization behavior

| Principal | Protected mode permissions |
|---|---|
| Anonymous, invalid credential or old shared Hub token | No API access, including loopback. No cookie/query token fallback. |
| Parent bootstrap credential or current parent session | Inspect own principal, policy, settings/status; create/list/revoke local sessions. No task/tool/provider execution or runtime settings changes in this foundation. |
| Child session | Inspect only its own authenticated principal. No existing Laith/task/history/settings/admin access and no mode switching. Child chat remains disabled. |
| Device session | Inspect own principal and register a heartbeat only for the bound node. No job polling/acknowledgement/result submission, execution or admin API. |
| Local dashboard relay | Cannot forward parent authority in protected mode, even if configured with a parent credential. A child session can inspect itself; the old relay token cannot authenticate. |

Unknown API routes/actions are denied. Browser-origin requests require the exact current origin, including port; foreign, alternate localhost and null origins are rejected. Native clients still need a valid bearer credential. These are local application boundaries, not proof of OS process isolation or internet exposure.

Protected mode requires a distinct random `SWITCHBOARD_PARENT_TOKEN`, between 32 and 256 URL-safe characters, separate from the existing shared `SWITCHBOARD_TOKEN`. Do not reuse a pairing/agent credential. Missing, malformed or reused bootstrap credentials prevent protected Engine initialization. Malformed or unknown security settings fail validation rather than silently selecting adult mode.

Sessions use cryptographically random bearer tokens returned once on creation. Only SHA-256 token hashes and opaque subject/device metadata are stored in the new `security_sessions` table. Sessions default to 15 minutes, with allowed lifetimes of one minute to 24 hours. Authentication checks expiry and revocation on each request. Revoked parent sessions cannot issue further sessions. Changing the parent bootstrap credential and restarting invalidates sessions issued under the previous authority. Session-list responses never return token hashes or tokens. Revoked/expired metadata cleanup is deferred to the retention stage.

## Execution boundaries

- Protected startup skips legacy job recovery, the network scheduler, inventory/MCP startup and discovery. It cannot silently resume an earlier adult task.
- Guards reject task start/run, LLM classification, tool gateways, installations, handoffs, mirrors and balance refresh/scheduler work before their execution side effects.
- Config-bound API-provider clients reject before SDK/transport creation. Config-bound CLI/manual-handoff adapters, MCP startup, executor jobs and direct capability-service execution also reject. Notifications are suppressed.
- Every protected API outside the small role allowlist is blocked before its handler, including media, live voice, tasks, pairing and generic tool calls.
- Portable immutable principal, outbound-intent and decision contracts are defined. All outbound decisions deny in this phase; there is no destination override or privileged role exemption.

The legacy standalone CLI adapter can still be embedded with `config=None`, as previously supported; Hub-created adapters always carry their deployment configuration. Trusted Python code can construct objects or change configuration, and these application checks are not a sandbox for arbitrary code. OS isolation, mediated tool networking and the full serialized-payload gateway remain future work. Do not claim that this phase controls unrelated apps or all network traffic on a household device.

## Files changed

| Files | Purpose |
|---|---|
| `switchboard/security.py` | Strict protected-mode validation, principal/intent/decision contracts, hashed sessions, role allowlist and execution denial helper |
| `switchboard/web/security_api.py` | Parent session administration and minimal self/policy endpoints |
| `switchboard/web/app.py` | Central protected API admission, exact-origin checks, no local/shared-token exemption, no-store responses, bound-device heartbeat and protected startup |
| `switchboard/localproxy.py` | Mandatory relay marker so the protected Hub rejects forwarded parent authority |
| `switchboard/engine.py` | Security composition and execution/recovery guard integration |
| `switchboard/providers/{openai_compat,anthropic_provider,cli_bridge,handoff}.py` | Denial before protected provider/CLI/handoff execution; API availability probes suppressed |
| `switchboard/capabilities/{adapters,hub}.py`, `switchboard/executor.py`, `switchboard/mcp_hub.py`, `switchboard/notify.py` | Guards on alternate execution paths |
| `switchboard/defaults/config.yaml` | Explicit default `security.enabled: false` with foundation limitations |
| `tests/test_local_security.py` | Role/escalation, lifecycle, relay, session persistence/rotation and transport-denial regression tests |
| Phase 1 assessment and this document | Approval status, operating limits and phase handoff |

## Local review procedure

Do not enable this on the installed household Hub as a child-ready release. For an isolated synthetic test instance, use a separate `SWITCHBOARD_HOME` and output directory, set `security.enabled: true`, and supply a new locally generated `SWITCHBOARD_PARENT_TOKEN` through its protected environment/secret file. Keep that credential out of URLs, logs, terminal history and screenshots. No real credential is embedded in the repository.

Use direct local bearer-authenticated requests for parent administration; the existing anonymous dashboard relay is intentionally not a parent-login mechanism. The foundation exposes APIs, not a finished parent/child onboarding screen. The normal chat interface is not functional against a protected foundation Hub.

| Method and path | Use |
|---|---|
| `GET /api/security/me` | Current opaque subject, role, session and optional device binding |
| `GET /api/security/policy` | Parent-only phase/policy status, explicitly reporting child chat and PII sanitization unavailable |
| `POST /api/security/sessions` | Parent creates a bounded session with `subject`, `role`, `seconds` and device node only for device roles; extra fields are rejected |
| `GET /api/security/sessions` | Parent inspects metadata, not reusable credentials |
| `DELETE /api/security/sessions/{id}` | Parent revokes a session; later authentication fails |

Use opaque local labels rather than children's names in session subjects/device bindings. Issue only the minimum role needed. If testing on an approved LAN connection later, provide an approved confidential transport; this phase did not change the current LAN transport or firewall.

## Validation

Full regression suite: **269 passed in 154.27 seconds**. Command: `PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen build/venv-macos-x86_64/bin/python -m pytest -q -p no:cacheprovider`.

After adding final defense-in-depth guards to toolbox and remote-CLI entry points, the focused security suite plus legacy image-adapter test passed again: **29 passed in 7.84 seconds**. These tests overlap the full suite; do not add the counts. Command: `PYTHONDONTWRITEBYTECODE=1 build/venv-macos-x86_64/bin/python -m pytest -q -p no:cacheprovider tests/test_local_security.py tests/test_image_execution.py::test_image_execution_requires_an_actual_image_file`.

Environment: existing Python 3.13.18 development environment on macOS x86_64. GUI-related regression tests ran offscreen; this is not physical-device or Pi verification. Source parsing and whitespace checks also passed.

Evidence: [full regression log](verification/phase-2/regression-tests.log) and [final focused log](verification/phase-2/focused-tests.log).

Tests use temporary homes, synthetic credentials and fake transports/subprocesses. No real provider request or child data is required. Relevant denial tests replace SDK/HTTP/subprocess constructors with assertions and verify that protected execution never reaches them or creates task records.

## Limits and next gate

This mode is not activated on the Pi or any installed application. Device permissions currently cover registration, not trusted job execution. No existing raw conversations or shared records are made child-readable; per-conversation child ownership and safe chat contracts belong to later work. Existing adult-mode local trust behavior remains outside protected mode and must not be described as child-safe.

Before activating a different mode on an existing installation, separately approve deployment and inspect/stop active adult workers, provider sessions and voice calls. This phase does not terminate external requests already in flight or promise removal of their data. Retaining the default false value is compatibility, not automatic protection of existing traffic.

Review the Phase 2 changes and validation results. The next proposed stage is Stage B: locally analyze/minimize full text context and authorize one reviewed provider path using fake transports first. It requires separate approval. No next-stage implementation or deployment is included here.
