# Phase 4 reviewed local tools attachments and provider mediation

Date: 24 November 2026. Approved scope: local Stage C from the corrected household-first plan. **Implemented for review, not deployed or enabled on the Pi.**

## Result

Extended the parent-reviewed privacy path to a locally configured choice of OpenAI, Anthropic or Gemini, with fixed provider destinations and no automatic fallback. Added three allowlisted local tools in a restricted, credential-free worker: arithmetic, text statistics and bounded text-attachment extraction. Tool execution and any later provider request require separate approvals.

Child/device access to these routes remains denied. Arbitrary commands, MCP tools, subscription CLIs, remote execution, PDF/DOCX/image/audio parsing, live voice and automatic model tool execution remain closed in protected mode. Unsupported or unavailable isolation causes failure, never an unrestricted-process fallback. These exclusions are deliberate boundaries for this stage, not implemented support for those capabilities.

All new features remain opt-in. No production configuration, Pi service, firewall, cloud identity or hosting was changed. No package/OS isolation utility was installed. Provider validation used fake transports and synthetic data; no live inference, provider account operation, real child data, deployment, commit or push was performed.

The Phase 3 working changes were already uncommitted when this phase started and were preserved. The Git base remains `adc8495f80eae95a265e3c6cbacb4cfb577933f4`; a future publication includes the reviewed Phase 3 and Phase 4 changes unless deliberately separated.

## Reviewed provider paths

| Configured provider | Fixed destination and authentication | Accepted response |
|---|---|---|
| `openai` | Existing `https://api.openai.com/v1/chat/completions`, `OPENAI_API_KEY` bearer | One completed text answer; tool/function requests rejected |
| `anthropic` | `https://api.anthropic.com/v1/messages`, `ANTHROPIC_API_KEY` in `x-api-key`, fixed `anthropic-version: 2026-10-21` | `end_turn`, text blocks only; empty citation metadata may be discarded; tools/nontext/nonempty citations rejected |
| `gemini` | Fixed `https://generativelanguage.googleapis.com/v1beta/models/<approved-model>:generateContent`, `GEMINI_API_KEY` in a header, never a query parameter | One `STOP` candidate with text parts; nontext/action blocks and thought output rejected; opaque thought-signature metadata discarded |

The provider and model are selected in local configuration, not by prompt or request-body overrides. One selection is active per gateway instance. Changing it requires a fresh configuration instance and review. Invalid identifiers, custom endpoints and Gemini path/RPC syntax abuse are rejected. No model was invented or enabled for an actual account.

Each transport uses the provider's native text-only body shape. The parent sees the exact destination and body before confirming. The final validation checks every field against that server-owned schema: tools, extra fields, altered token settings and unexpected roles cannot be slipped into the request. The approval binds the payload, parent session, current policy/provider/model, destination and provider credential. Credential changes during final checks prevent sending; changes/revocation during a request suppress its answer, but cannot unsend bytes already transmitted.

The existing bounded HTTP behavior remains: no environment proxy, redirects, automatic retries, external classifier or fallback, no streaming to the client, identity content encoding, bounded responses and fixed error codes. Every returned text answer is minimized and checked locally. Configured provider credentials remain local deny terms even when the legacy provider registry is absent.

The Anthropic version and text-only request were checked against its [Messages reference](https://platform.claude.com/docs/en/api/messages) and [2026 migration guide](https://platform.claude.com/docs/en/api/migration-2026-10-21). Gemini's endpoint/body follow the [GenerateContent reference](https://ai.google.dev/api/generate-content). These are fake-transport protocol checks, not successful live-model/account verification.

OpenAI's `store=false` setting is not sent as an invented parameter to Anthropic or Gemini. None of these request settings proves zero provider retention or deletion of every remote copy. Account/model terms and necessary network/account metadata still apply. The earlier assessment's provider-specific deletion limits remain relevant; no provider deletion was requested. This phase also does not add a monetary spending cap or credit-ledger integration.

## Local tool and attachment contract

| Tool | Allowed input | Scope |
|---|---|---|
| `calculate` | An expression of at most 256 characters | Numeric literals, unary signs and bounded addition/subtraction/multiplication/division/floor division/modulo. No `eval`, names, calls, attributes, indexing, powers or arbitrary code. AST/node/result limits apply. |
| `text.statistics` | Up to 8,000 text characters | Returns counts, without returning the source text. |
| `attachment.extract` | A declared format and base64 bytes, up to 16 KiB | UTF-8 text, Markdown, headered CSV or JSON only. No caller filename/path/URL is accepted. Binary containers, malformed input, large outputs, deep JSON and over-wide/long CSV are rejected. |

CSV cells are rendered as labeled, JSON-escaped values so embedded newlines cannot detach a sensitive value from its header. JSON becomes bounded reference text, not executable objects. There is no silent truncation: extracted output must fit the 8,000-character result limit. Raw attachments, metadata, filenames and hashes are not automatically forwarded to a model.

The local minimizer now removes whole sensitive identifier lines, including structured name/contact/school/age/credential labels. Explicit identity labels continued onto following lines are removed as well. This closes tested honorific and multiline fragments in English, French, Arabic and Darija examples. It is still a conservative heuristic: unlabelled names, subtle combinations and unfamiliar language/encoding can be missed, and harmless context can be removed. No trained local NER model or general detection-accuracy claim was added. Child use remains disabled.

Tool results are treated as untrusted data, minimized, independently checked and returned only to the parent. They are not instructions, model authority or permission to call another tool. To send one to a provider, the parent must create and approve a separate text preview, optionally supplying it as `context.kind: tool_result`. The full provider payload is sanitized again. Models cannot invoke these tools automatically; received provider tool/action blocks are rejected.

## Approval and lifecycle

Local-tool previews bind the parent session, allowlist/policy, canonical arguments, digest and a single-use random identity. They expire after 120 seconds, are limited to 16 per Hub instance, and allow only one active local tool operation per instance. The receipt reports the operation, input size, local destination and digest; it does not echo raw file contents or arguments.

Unlike a sanitized provider preview, a local-tool preview may temporarily retain raw caller-supplied text or attachment bytes in memory until consumption/expiry. This is required for the approved local operation and must not be described as sanitized storage. No task rows, transcript files, output mirrors or attachment database objects are created. Timers, expiry checks and close clear pending previews; this is not forensic RAM erasure. Engine shutdown closes both approval services. Existing histories, logs and backups are not deleted.

Before and after worker execution and result checking, the service rechecks session revocation/expiry, current local-tool policy and the privacy-policy snapshot. Changed privacy terms invalidate the operation rather than silently applying stale rules. A revoked operation's result is suppressed. Cancellation or failure does not retry the tool or initiate an external request.

## Worker isolation

The worker is a standalone standard-library script. The launcher uses fixed binaries and argument lists, never a shell or caller-selected executable. Sensitive arguments travel through bounded stdin, not process arguments or environment variables. Only minimal locale/path environment is supplied, inherited file descriptors are closed, and isolated Python starts without user-site/startup configuration or bytecode writes, with explicit UTF-8 mode.

| Platform | Enforcement and limits |
|---|---|
| macOS | Fixed `/usr/bin/sandbox-exec`, default-deny profile, no network/Mach/process-spawn permission, executable restricted to the selected interpreter. Readable data paths are the interpreter runtime, system libraries, worker source and specific devices; root-directory listing and global file metadata are also available for runtime startup. Other host file contents are denied. No blanket sysctl-read permission is granted. |
| Linux/Pi | Fixed `/usr/bin/bwrap`, required new user/network/PID namespaces, cleared environment, dropped capabilities, read-only runtime/library/worker mounts, private proc/dev and read-only temporary filesystem. No host home/configuration or root filesystem mount. If the utility or required namespace support is unavailable, execution fails closed. |
| Other platforms/frozen desktop packages | No unrestricted fallback. This implementation refuses unsupported launchers and frozen executables until a separately packaged worker is reviewed. |

The worker applies CPU (2 seconds), file-size/core-dump (zero), descriptor and process-creation limits; Linux also caps virtual address space at 256 MiB. The parent bounds IPC output and a four-second operation deadline, kills/reaps failed or cancelled workers and cleans its private temporary directory. The worker performs no document/output file writes; source data is passed in memory. These limits are not measured supported-user capacity or a guarantee against OS vulnerabilities.

Only the fixed small tools are supported. The runtime/worker code and readable runtime files must be trusted and protected by operator permissions. This is not permission to run arbitrary CLIs or native media parsers inside the same profile. Linux's namespace isolation is designed to remove access to the host/external network; it does not mean every socket operation in a private namespace is forbidden. The [bubblewrap documentation](https://github.com/containers/bubblewrap) explains that isolation depends on the configured policy.

## API and disabled defaults

All routes below require direct authenticated parent authority; existing relay, child/device and legacy-token restrictions remain:

| Route | Purpose |
|---|---|
| `GET /api/privacy/tools` | Report configured local allowlist/enabled state, without claiming launcher usability or child readiness |
| `POST /api/privacy/tools/preview` | `tool` plus a strictly validated `arguments` object |
| `POST /api/privacy/tools/run` | Preview identity, reviewed digest and strict `confirm: true` |
| Existing `/api/privacy/preview` and `/api/privacy/send` | Independent reviewed text transmission for the locally selected provider |

`security.local_tools` defaults to `enabled: false, allowed: []`. Each supported tool must be explicitly allowlisted. There is no `sandbox_required: false` override or permission to add shell/MCP/URL-based tools. `security.text_gateway.provider` defaults to `openai`; text egress itself remains disabled unless explicitly configured. Local tools can work without enabling or configuring any external AI provider.

The existing privacy API body cap, duplicate-key/constant rejection and no-store responses apply. Errors do not echo input. Tool logging contains only fixed events and registered tool IDs, never raw arguments, output, file names, digests, credentials, worker stderr or provider error bodies. Normal Laith/chat/device frontends are not newly connected to these review APIs.

## Validation

Final verification after all source changes:

- Full repository suite: **396 passed in 115.02 seconds**, recorded in `docs/verification/phase-4/regression-tests.log`.
- Focused privacy, authorization and Phase 4 suite: **154 passed in 14.17 seconds**, recorded in `docs/verification/phase-4/focused-tests.log`.
- Git whitespace checks and Python syntax parsing passed for the changed/new source files.

Test counts overlap; do not add them. These results supersede the earlier 381-test regression run before the final CSV/multiline/policy refinements. Tests ran on the Mac Python environment with Qt offscreen; they are not Pi or live-device validation.

Real macOS worker checks used a synthetic file outside allowed paths, a live local host listener and a sentinel environment variable. The restricted worker returned `read: false`, `network: false`, `secret_env: false`, while supported tool operations succeeded. These tests do not inspect or expose real private files or credentials. Linux command construction and unavailable/unsupported-launcher denial are checked separately; no actual Linux/Pi namespace, device, frozen-package or Windows worker verification was performed.

Provider tests inspect exact serialized request bytes, endpoint and selected authentication header for all three destinations, assert no request during preview, verify redaction/response minimization and reject action blocks with no fallback. Other checks cover parent/child/device separation, owner/digest/expiry/revocation/policy binding, invalid file formats, code/path/URL injection, no unrestricted fallback and independent outbound review. No new billable inference was used.

## Files and next gate

New components are `privacy_providers.py`, `privacy_tools.py`, `privacy_sandbox.py`, `privacy_worker.py`, `web/privacy_tools_api.py` and `tests/test_privacy_phase4.py`. Existing privacy policy/body handling, minimization, Engine composition/close and route admission were extended. Defaults and reports document the opt-in boundary. No infrastructure files or dependency-install steps were added.

Review this Phase 4 implementation before authorizing Stage D / Phase 5: child response-risk policy, safe fallbacks and parent-reviewed escalation rules. Deployment, installing a Pi isolation utility, live model trials, broader attachments/tools and actual child release require their own scope and approval. Nothing in this phase enables those actions automatically.
