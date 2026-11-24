# Phase 3 local text privacy and reviewed provider access

Date: 24 November 2026. Base revision: `adc8495f80eae95a265e3c6cbacb4cfb577933f4`. Scope: approved local Stage B. **Implemented for review; not deployed or enabled on the Pi.**

Subsequent update: the user approved Stage C as Phase 4. See [Phase 4 reviewed tools and providers](phase-4-reviewed-tools-and-providers.md) for the added isolated local tools and fixed Anthropic/Gemini formats. This report retains the Phase 3 baseline and test results.

## Result and operating boundary

Added a bounded offline text minimizer and a separate parent-reviewed route to one fixed OpenAI Chat Completions endpoint. It accepts text supplied by an authenticated local parent, checks the supplied history and reference context locally, previews the exact serialized outbound payload, and requires a short-lived single-use approval before transmission. The new route is off by default and requires protected mode, a locally selected model and an API credential.

This is an experimental adult/parent review path, **not automatic anonymization or a child-chat release**. Children and devices cannot call it. The legacy task planner, provider adapters, generic tasks, tools, MCP, remote CLIs, manual app handoffs, attachments and live voice remain blocked in protected mode. Existing adult mode remains separate and is not retroactively protected by this work.

No production configuration, real transcripts, provider accounts, infrastructure or Pi services were changed. All provider tests use synthetic content and fake HTTP transports. No real inference request, new model download, paid service, deployment, commit or push was performed in this phase.

## Local detection and minimization

`switchboard/privacy.py` provides a pure offline component with no provider or network dependency:

- Unicode normalization removes format/bidi/zero-width characters and most controls, normalizes decimal digits, and rejects alphabetic scripts outside Latin/Arabic. Script acceptance is not language identification.
- Bounded deterministic patterns cover common/obfuscated email addresses, phone numbers, dates, coordinates, credentials/long token-like strings, URLs, handles, Canadian postal codes and common English/French address forms.
- An optional list of already-known local identifiers removes matching clauses. Already-configured credentials also act as local deny terms; they are not inserted into the provider context.
- English, French, Arabic and Darija identity-disclosure cues remove whole clauses rather than guessing a name boundary. Location/schedule/activity cues are assessed across the entire supplied context; combinations cause identifying context to be omitted. This covers specific indirect-identification scenarios without sending text to a remote classifier.
- All supplied history/context is inspected, including history later dropped from transmission. Only the latest four history entries are transmitted; memories/retrieval/reference values are labeled as untrusted data. No existing database memory, machine inventory or earlier work is silently appended.
- A separate final pass checks every serialized text field, including fixed instructions and model metadata. The same check runs again immediately before transmission using current configured secret deny terms. Responses receive local minimization and a final check before being returned.

The contextual layer is a conservative heuristic, not a pretrained semantic/NER model. Unlabelled names, novel obfuscations, short encoded identifiers and subtle re-identification may be missed. General questions involving a school, schedule or activity may be removed unnecessarily. Latin-script languages beyond the tested examples are not automatically recognized as unsupported. Parent review remains mandatory; these limitations prevent enabling child use or claiming measured general PII recall. No new child identity data is collected to improve detection.

## Authorized data flow

```text
Parent bearer credential
 -> bounded text request, no attachments or destination/model override
 -> local full-context checks and minimization
 -> fixed model/endpoint payload with store=false, stream=false and no tools
 -> parent reviews every outbound field and confirms its digest
 -> session / policy / model / credential / payload / expiry revalidation
 -> one HTTP request to the fixed approved endpoint
 -> bounded complete response, local privacy checks
 -> parent response, without transcript persistence or mirroring
```

The route bypasses the existing execution planner deliberately: relaxing its provider-client guard would reopen raw classification, tool loops and alternative providers. The legacy guard remains in place. Engine composes the new gateway alongside its existing modules; no separate backend, cloud identity service or new dependency was added.

## Approval and fail-closed behavior

Preview records are immutable, sanitized payloads held in memory, never raw input transcripts in SQLite or task folders. Each binds:

- The authenticated parent principal and session.
- The configured policy, selected model and fixed destination.
- The exact UTF-8 serialized body and its SHA-256 digest.
- A fingerprint of the provider credential, so an account/key change requires another review.
- A 120-second expiry and single-use random preview identity.

The parent sends only the preview identity, reviewed digest and strict boolean confirmation. There is no send-time prompt/history/model override. The preview is consumed before transmission; errors, cancellation and provider timeouts do not retry it or fall back to another provider. An in-flight request cannot be unsent; revocation/closure during the call suppresses its later answer and does not imply remote deletion.

Expired previews are removed by timers or activity checks; closing the gateway clears all previews and rejects later use. At most 16 previews and one active local analysis/provider operation are allowed per Hub instance. Validation/body limits, malformed local results, detector errors/timeouts, unsupported scripts, changed policy, changed credentials, mismatched ownership/digest, expired/revoked sessions or missing credentials deny transmission. Reusing a parent/shared Hub credential as the provider key is rejected.

The local check timeout is one second; provider work has a 30-second overall deadline plus bounded HTTP timeouts. Blocking checks run off the event loop, with bounded text and pattern work. A Python thread cannot forcibly terminate arbitrary broken detector code; no untrusted detector plugin is supported. OS isolation of arbitrary code and workers remains future work.

## Provider transport

The only destination is `https://api.openai.com/v1/chat/completions`. A request cannot choose another endpoint, use the existing configurable compatible-provider URLs, add tools, upload files, request streaming, or select another model. The model is selected locally in the protected configuration; no model or live provider permission was configured during implementation.

The dedicated transport uses HTTPX with environment proxies and redirects disabled, normal TLS verification, no automatic retries, an identity-encoding request and rejection of compressed responses. Request/response sizes are bounded before copying additional chunks. Non-success HTTP responses, malformed/oversized responses, tool/function calls and incomplete protocol results are rejected without exposing provider error bodies. Returned text is buffered and checked before display; no provider token stream is sent to a client.

OpenAI documents Chat Completions parameters in its [API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). `store=false` requests application storage controls; it is **not** Zero Data Retention or a promise of deletion of abuse-monitoring, cache or other third-party copies. Account/model/endpoint conditions still apply. See [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

The destination also receives necessary network and API-account metadata, including the connection's public IP. Content minimization does not make the household anonymous to its provider.

This path is not connected to the adult credit ledger or a monetary spending cap. One request and a bounded completion-token setting constrain its work; they do not guarantee a maximum bill. Any future real trial needs separate approval of the model, API account and budget. No paid provider was called in validation.

## API and configuration contract

New parent-only endpoints:

| Endpoint | Input and result |
|---|---|
| `POST /api/privacy/preview` | `prompt`; optional `history` entries with `role: user/assistant` and `content`; optional `context` entries with `kind: memory/retrieval/reference` and `text`. Returns the exact proposed payload, fixed destination, digest, expiry, redaction counts and review warning. |
| `POST /api/privacy/send` | `preview_id`, `payload_digest`, `confirm: true`. Returns only locally checked answer text, redaction counts and `persisted: false`. |
| `GET /api/security/policy` | Reports the limited detector availability, text-route enabled state and continued child-chat denial. Generic outbound decisions still deny; only the separately reviewed route can send. |

Unknown fields, JSON duplicate keys, non-finite constants, malformed bodies and unsupported shapes are rejected without echoing the submitted input in validation errors. Limits are 64 KB for the incoming JSON body, 8,000 characters per text item, at most eight history and eight context items, and 24,000 combined source characters. The final outbound body is capped at 48 KB and the provider response at 64 KB/8,000 answer characters. API responses carry `Cache-Control: no-store`.

Configuration adds `security.text_gateway` with `enabled` default false, an empty `model` default, `max_tokens` default 512 (range 1–1024), and optional `known_identifiers`. Enabling requires `security.enabled: true`, an explicit model and the separate `OPENAI_API_KEY` at send time. There is no API for silently enabling the policy or adding destinations. Existing parent authentication and relay restrictions apply to both endpoints.

Use opaque local profile labels. Protect any existing identifier/secret configuration through local filesystem permissions; it is not sent in policy/status responses. The normal Laith chat screen is not wired to this parent review API. No child onboarding or account-management UI was added.

## Logging and retention

The privacy logger emits fixed event names and validated redaction category names only. It does not log raw prompts, source context, parent/session credentials, provider keys, payload digests, response text or provider exception bodies. Existing legacy logging is not made privacy-safe globally by this phase.

The new path does not create task records, transcript/output files, mirrors, evidence snapshots or database history. It temporarily holds source data during local processing, sanitized preview bytes until consumption/expiry, and bounded response data while checking. This is not forensic RAM erasure. Previously stored chats, OS logs, browser exports and backups are not deleted or migrated. Cascading deletion and comprehensive managed-copy retention remain a later gated stage. Third-party copies remain outside Switchboard's deletion guarantee.

## Regression fix

The full suite exposed a pre-existing Studio resume race: a completed worker could remain in `jobs` until its cleanup callback, causing a paused manual handoff to be rejected as active. `studio.py` now rejects only a worker that is actually unfinished; existing cleanup already checks task identity before removing an entry. A deterministic regression verifies both active-worker rejection and resumption with a finished worker still registered. The existing manual-handoff output/approval checks remain enforced.

## Files changed

| Files | Purpose |
|---|---|
| `switchboard/privacy.py` | Offline minimization, normalization and cross-context disclosure cues |
| `switchboard/privacy_gateway.py` | Strict policy, bounded preview lifecycle, reviewed-payload validation and one fixed transport |
| `switchboard/web/privacy_api.py` | Bounded parent-only API with content-free validation errors |
| `switchboard/security.py`, `switchboard/web/security_api.py` | Narrow parent route admission and truthful capability/policy status |
| `switchboard/engine.py`, `switchboard/web/app.py` | Composition, close lifecycle and route registration |
| `switchboard/defaults/config.yaml` | Disabled-by-default text policy without an invented model or credentials |
| `switchboard/studio.py` | Completed-worker resume race correction |
| `tests/test_text_privacy.py`, `tests/test_local_security.py`, `tests/test_studio.py` | Disclosure, exact-body transport, authorization, failure and handoff regression coverage |
| This report and `docs/verification/phase-3/` | Test and local performance evidence |

## Validation and performance evidence

Full regression suite: **331 passed in 279.89 seconds**. An earlier run found two Studio handoff race failures; the final full run passed after the correction described above. Command: `PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen build/venv-macos-x86_64/bin/python -m pytest -q -p no:cacheprovider`.

Final focused privacy/authorization/handoff run: **96 passed in 30.15 seconds**. This run followed the final provider-key/expiry revalidation and Arabic cue refinements, and includes those cases. Counts overlap the full suite and must not be added together. See the [full regression log](verification/phase-3/regression-tests.log) and [focused log](verification/phase-3/focused-tests.log). A strengthened valid-compressed-response rejection case was also rerun separately; see the [compression check](verification/phase-3/compression-check.log).

Tests inspect the final serialized fake-provider request body, enforce zero transport calls on denial, check response minimization and generic errors, verify single-use/session/policy/key binding and keep all child/legacy routes blocked. Source parsing and whitespace validation supplement these tests. This is local macOS validation, not Pi, Linux, live-provider or production verification.

The [local detector benchmark](verification/phase-3/local-detector-benchmark.json) uses synthetic content on macOS x86_64/Python 3.13.18 with Python allocation tracing enabled. Ten iterations per case gave median detector times of approximately 2 ms for short text, 3.6 ms for mixed text, 164 ms for an 18,024-character context, and 357 ms for a 23,000-character token stress case. Largest measured Python allocation peak was about 242 KB. These are instrumented local measurements, not process RSS, total Hub memory, provider latency or Pi throughput. Regex cache warmup affects the first short sample. Actual Pi CPU/RAM/latency and supported request capacity remain unverified.

No local language model was installed. The synthetic corpus exercises defined behaviors and failure boundaries; it does not establish general detection accuracy or child-safety effectiveness.

## Next gate

Review the Phase 3 diff, tests and detector limitations. Stage C would extend mediation to individually reviewed tools, attachment extraction and other provider/capability paths, with worker isolation and additional language/context evaluation. Child response-risk policies, parent escalation and real child release remain later work. No deployment, live provider trial, cloud work or next-stage implementation is authorized by completing this phase.
