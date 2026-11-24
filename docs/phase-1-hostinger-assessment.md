# Switchboard household-local architecture and security assessment

Revised 23 November 2026, America/Winnipeg. **Baseline assessment; subsequent approved implementation is tracked separately below.**

Update: the user subsequently approved local Stage A as Phase 2. Its implementation and review status are recorded in [Phase 2 local security foundation](phase-2-local-security-foundation.md). The remaining stages and any deployment still require separate approval; findings below describe the assessed baseline revision.

Latest update: Stage B was approved as Phase 3 on 24 November 2026. See [Phase 3 local text privacy](phase-3-local-text-privacy.md) for its separate parent-reviewed route and validation; child execution and all later stages remain gated.

Subsequent approval: Stage C / Phase 4 adds [reviewed local tools and provider mediation](phase-4-reviewed-tools-and-providers.md). Child execution, deployment and Stage D remain gated.

## Architecture correction and scope

Switchboard and Laith run together on a household Raspberry Pi Hub in the current test environment, as confirmed by the owner. The initial commercial model is one local Hub per household, with no intended external inbound access. Loopback communication is internal operation. The immediate security boundary is **outbound disclosure and action authorization**, with Switchboard as the mandatory enforcement point before managed requests reach external AI providers or tools.

This revision replaces the earlier cloud-first assessment, including its proposed next step of Hostinger provisioning. That recommendation used the wrong initial deployment model. The previous loopback test demonstrated a local trust assumption, not an internet-facing critical vulnerability. No public exposure or remote exploit of the deployed Pi has been established.

Central multi-household hosting, cloud accounts, PostgreSQL migration, Caddy deployment and Hostinger setup are deferred. They are not prerequisites for the local privacy gateway and are not authorized by this assessment. Preserve the Python/FastAPI application, its SQLite persistence and existing adult workflows; introduce no cloud user management now.

The local design still needs a distinction between parent administration, child use, paired devices and untrusted content. A household is not automatically one equally privileged principal. An authorized parent may choose adult provider use; a child request must not obtain that authority merely by sharing a device, browser or Hub.

### Evidence baseline

- Checkout: `/Users/hicham/Switchboard/repo`; `main` at `28d48ecd216be0db2edfe2441f5e0adb1026656e`. Origin: `baguixgroup/switchboard`. No remote fetch was performed.
- Application: `switchboard-ai` 0.10.0, Tazrivo branding; Python/FastAPI/Uvicorn, SQLite, API/CLI/manual-handoff providers, MCP, agents, Studio, Laith and media capabilities.
- Worktree before revision: only the previous untracked assessment document. This revision changes that document only, retaining its filename so the existing review link continues working.
- Source observations and isolated synthetic tests describe this revision of the repository. They do not establish which binary/configuration is installed on the Pi.
- Owner supplied Pi address `10.0.0.17`. Existing SSH configuration maps alias `pi` to it. Read-only SSH with existing keys and strict host-key checking failed with “Host is down.” Separate TCP attempts to ports 22 and 8765 returned the same result. No credentials, real conversations or live settings were retrieved; no remote command executed.
- Consequently, deployed version, interface bindings, router forwarding/UPnP, IPv6 exposure, firewall rules, tunnels, paired devices and file permissions remain **unverified**. An unreachable local host is not proof of a secure firewall or internet isolation.

## 1 Revised threat model

### Assets and attackers

Protect child identity, conversation content, history, voice, attachments, local memories/documents, parent decisions, provider credentials, device permissions and derived outputs. Protect deletion state so cancelled or deleted work cannot later disclose or recreate data.

Relevant adversaries are a curious child or guest, an untrusted local application, a compromised paired household device, an unauthenticated LAN client, hostile retrieved/model/tool content, and an external provider receiving more information than authorized. An internet attacker becomes relevant only if a tunnel, forwarded port, globally reachable IPv6 service or externally exposed intermediary actually reaches the Hub.

A fully compromised parent OS account, same-UID process with access to all credentials, or root can undermine an in-process gateway. Local session permissions alone cannot protect secrets from such compromise. Stronger isolation requires separate OS identities/permissions and an isolated credential-holding egress component; this is a design option to implement only after approval. No application can guarantee control over unrelated apps or devices on the whole home network.

### Trust boundaries

| Boundary | What is trusted and what must be checked |
|---|---|
| Parent versus child | Parent administration is privileged. Child mode and permissions come from validated local sessions/policy, never a client-supplied flag or a shared admin token. This requires local role/session controls, not cloud account infrastructure. |
| Local process versus privileged Hub | Loopback identifies a network interface, not the person or process. Intentional parent-only local access is acceptable within that threat assumption; arbitrary local apps and child browsers must not automatically inherit parent powers in a child-capable product. |
| Household device/LAN versus Hub | Pairing grants capability, not universal trust in all software on a device. Shared secrets, LAN confidentiality, origin checks and per-device permissions matter even without internet ingress. |
| Hub versus tool/CLI subprocess | Tool code, remote agents and CLI provider processes may open sockets, read files and persist independent transcripts. Treat them as separate execution and storage boundaries. |
| Hub/client versus provider | No raw sensitive content may leave through any managed path without local minimization, current policy authorization and destination verification. Outbound TLS protects transit, not provider retention. |
| Raw local data versus storage and derived copies | Conversation rows, retrieval indexes, files, mirrors, logs and backup generations need consistent ownership, provenance, retention and deletion. Local copying alone is not disclosure to an AI provider. |
| Untrusted content versus decisions | Model output, retrieved documents and tool responses cannot authorize a provider, change child policy, enable a tool or create a new external destination. |

## 2 Reclassified blockers and concrete exposure conditions

Priority is contextual: a **local child-release blocker** prevents claiming safe child use; a **conditional hardening issue** needs its stated exposure condition. None of these findings establishes an internet-facing exploit on the Pi.

| Finding | Evidence and concrete reachability | Revised classification |
|---|---|---|
| Shared administrative access | `web/app.py:145` provides one token dependency for tasks, settings and agent APIs. `web/pairing.py:110` redeems a valid single-use code for that token; `web/manage.py:205` returns connection details to an authorized caller. A synthetic remote peer with the token could read `/api/settings`; absent/wrong credentials returned 401. A token-holding paired device or its local relay has broad powers. | Acceptable only for an intentionally trusted single-administrator test environment. **Local child-release blocker** if a child, guest or compromised device can use the same credential/relay. No cross-household cloud identity system is needed to fix local parent/child/device authorization. |
| Loopback authentication exemption | `web/app.py:133–153` and `localproxy.py:30`: native loopback requests can read administrative settings without a token. A real isolated loopback TCP check confirmed this. Host/Origin checks reject ordinary foreign origins/hosts. Origin checks accept other localhost ports and `Origin: null`; a nonempty Authorization header skips the Hub origin guard, and default loopback auth then ignores token validity. | **Intentional local operation with a broad process trust assumption**, not automatically a vulnerability or critical internet finding. It becomes a local privilege-boundary weakness if untrusted apps/children can reach the trusted loopback service. It becomes a remote issue only if a reachable proxy/tunnel exposes it or a header-trust error makes remote traffic appear local. Neither deployed condition is demonstrated. |
| Local agent relay | `localproxy.py:43–94,105` binds loopback and inserts the Hub token for accepted requests, excluding agent-job routes. Tests confirmed anonymous settings access through the relay even when the Hub requires local tokens. It accepts an Origin on a different localhost port. | **Conditional local privilege bridge**. Turning on `require_token_locally` on the Hub alone does not segregate a child using the relay. A native local process can set its own headers; ordinary browser cross-origin response access is additionally subject to browser/CORS/private-network rules. No browser exfiltration exploit is claimed from these HTTP checks. |
| Persistent transcript copying | `store.py:115`, `companion.py:28–59`, `outputs.py:38,82`, `engine.py:514,638` persist content, prompt-derived paths and optional mirrors. Mirroring depends on an online agent advertising `mirror_outputs`; recent-work sharing depends on configuration/execution path. No deployed mirror inventory was obtained. | Local persistence is intended functionality, **not inherently an external breach**. A retention/deletion and least-privilege blocker for child release when copies reach a shared/untrusted device, re-enter a later provider context, survive deletion or are readable outside their intended user. |
| Missing child safety controls | `engine.py:127` can send raw prompt text to an LLM classifier; `engine.py:384–510` dispatches providers, fallbacks and remote CLI jobs. Provider adapters append history/tool results; capability services and live voice have other paths. No common enforced child PII/policy boundary exists. | **Immediate local outbound privacy and child-release blocker**, regardless of internet ingress. External requests are ordinary functioning code paths when configured and funded. Model refusals, spending confirmations and personalization consent do not establish these controls. |

### Reachability conclusions

- **Untrusted native local application:** application-level loopback access is demonstrated in a temporary native TCP server. Host/Origin checks do not authenticate the process. Access to the deployed Pi still requires code execution on that Pi, a trusted relay on another device, or another concrete path.
- **Unauthenticated LAN client:** protected Hub APIs reject the simulated remote peer without the correct token. Default configuration binds `0.0.0.0:8765` and enables discovery (`defaults/config.yaml:20`), making LAN reachability possible subject to real settings/firewall. LAN access is not loopback access. Public dashboard/assets/health, pairing redemption and installer-download routes are distinct from protected admin APIs.
- **Credentialed LAN client:** the current shared credential grants broad access. Default documented LAN URLs use HTTP, so LAN observers may see traffic/tokens if that is the deployed transport; actual transport is unverified. Restrict allowed devices and provide an approved confidential local transport for sensitive use.
- **Web content:** ordinary foreign Origin and DNS-rebinding Host attempts were rejected. Null-origin and alternate-loopback-origin acceptance need targeted real-browser evaluation. Accepting a synthetic HTTP request does not prove a browser can read its response or perform every operation.
- **Internet/external component:** no configured ingress, port forwarding or public intermediary was found or verified. The user's intended architecture excludes them. Lack of intended inbound access does not itself verify the network configuration.

### Deferred read-only Pi checks

When `10.0.0.17` is reachable, verify installed version, listeners and process ownership; selected nonsecret Hub binding/discovery/mirroring settings; firewall and IPv6 state; tunnel/reverse-proxy services; access from an approved LAN device. Inspect router forwarding/UPnP and externally reachable IPv6 separately with permission/access to that equipment. Do not test this by opening a public port or disabling a firewall. No WAN scan or router change was performed.

## 3 Actual data flow and trust boundaries

Current repository behavior, with enabled routes depending on runtime configuration:

```text
Household browser / desktop / CLI / MCP client
  -> Pi Hub: shared auth, routing, Laith, Studio, capabilities
       -> SQLite: chats, memories, prepared context, tasks, jobs, results
       -> output files / uploads / retrieval indexes / optional device mirrors
       -> API provider: prompt + history + images + tool results
       -> remote household agent -> subscription CLI -> provider
       -> MCP HTTP service or stdio subprocess -> tools / possible external services
       -> media / inference / transcription / speech service
       -> manual handoff -> app / clipboard -> user-submitted provider request

Live voice: browser microphone <---- WebRTC ----> configured external provider
                          Pi negotiates session and later receives transcripts
```

### Outbound path inventory

| Route and source | Why a single chat filter is insufficient |
|---|---|
| Planning/classification, `engine.py:120–149` | Optional LLM classification transmits part of the prompt before task execution. A preview can therefore be an outbound operation; keep classification local until gated. |
| API chat/tool loop, `providers/openai_compat.py:76`, `anthropic_provider.py:60` | Full history and image bytes are included. Tool schemas/arguments/results and continued model rounds need revalidation. Anthropic configuration can request provider-managed fallback; approved routing must include actual allowed model/provider behavior. |
| Failover/remote CLI, `engine.py:395–510`, `providers/cli_bridge.py:202` | Alternative providers must be independently authorized. CLI processes receive prompt/history, inherited environment and files; filesystem read-only mode does not prevent network disclosure or provider-owned session logs. |
| Laith/Studio, `companion.py:137–238`, `studio.py:363–395` | Prepared prompts include prior turns, memories, retrieved excerpts, machine/work status, personality and instructions. Team outputs become later agent inputs. A fresh user message alone is not the payload. |
| MCP and device/server tools, `mcp_hub.py:84`, `engine.py:186–307` | HTTP MCP targets and stdio workers may make their own network calls. Tool authorization must cover payload, side effect and destination, including tools embedded in approved provider rounds. |
| Capability adapters, `capabilities/hub.py:140`, `capabilities/adapters.py:91` | Media, embeddings, inference, speech/transcription and other services can execute directly, outside Engine's provider call. A service's `local` configuration label is not proof it is trusted or offline. |
| Live voice, `web/capability_api.py:190`, `web/static/capabilities.js:60–65` | Browser media tracks go to the negotiated WebRTC peer. Saving a transcript to the Hub afterward cannot redact audio already transmitted. Disable this path for protected child use until a locally reviewed audio pipeline exists. |
| Manual app handoff, `providers/handoff.py:30`, `executor.py:234` | Copying context to clipboard/app is itself a disclosure to another process; user submission creates another provider copy. Disable for child sessions until enforceable and retention-accountable. |
| Notifications and background services | `notify.py:13` can send alert text to ntfy. Balance/model checks, downloads and discovery are separate operational network traffic, not all child-content transmission. Their schemas/logging must still forbid raw child context. |

The local Hub is the desired enforcement point, but it is not yet a complete mandatory gateway. A managed CLI/tool that can open its own external socket creates another boundary that must be removed, mediated or denied in protected mode.

## 4 Recommended local outbound privacy enforcement

### Security contract

Implement a deny-by-default policy for external destinations and sensitive actions. Preserve supported adult use through explicit adult authorization; do not silently apply a child policy to existing adult data or delete existing transcripts.

```text
Validated local principal and server-owned policy
 -> local full-context PII/risk analysis
 -> minimization and attachment transformation
 -> authorized immutable request and destination
 -> final serialized-payload enforcement at egress
 -> approved provider / individually approved tool
 -> validate response before display, speech, storage or another agent call
```

A portable contract should carry an opaque household/profile/session reference, parent/child/device/service role, purpose, age band, policy revision, data classes, retention class and request lineage. The egress permit binds the authorized principal, sanitized payload digest, destination/model/tool/action, expiry, bounded retries and policy revision. Bind attachments, resolved payload and follow-up tool results too. Reject stale, reused or modified permits. Avoid persisting content hashes of short sensitive identifiers as audit evidence; request-binding hashes need only live for their authorization lifetime.

For one household the household identifier can be local and opaque. Parent administration needs locally verified authority and expiring/revocable permissions; child devices receive restricted capabilities, not the existing shared Hub token. Global registration, email accounts, billing identities and cloud multi-tenancy remain deferred.

### Enforcement requirements

1. **Deny before planning or sending.** A configured API key, funded plan, model selection, `confirm: true`, `tools: auto` or `child_mode: false` is not authorization. Policy comes from the validated local session/device. Unknown roles, destinations, purposes and unsafe content are denied. No default-provider fallback when safety fails.
2. **Local PII and contextual risk analysis.** Combine deterministic patterns with a locally executable contextual detector, explicit parent-maintained identifiers and indirect/quasi-identifier analysis. Detect combinations such as a rare activity, school schedule and locality as well as names/phones/emails/addresses/birth dates, credentials and device/family identifiers. Do not collect additional child identity just to improve detection. Local models must be benchmarked on the Pi; no model is selected or assumed available in this assessment.
3. **Multilingual minimization.** Cover English, French, Arabic, Darija, mixed language, transliteration, Unicode variation and adversarial obfuscation. Normalize safely, reduce precision, omit unnecessary facts and use per-request aliases where useful. Keep any reversal map local and short-lived; do not silently reinsert identity into an external follow-up. Unsupported language, uncertain indirect identification or detector failure gets a safe local fallback or parent review. Detection cannot guarantee anonymity.
4. **Complete context.** Examine current input, earlier turns, explicit memories, retrieval chunks, system/reference instructions, machine status, tool schemas/results, filenames and metadata. Context summaries can still identify someone. Re-sanitize complete context at each external call, including retries, tool rounds, workflow revisions and provider fallback.
5. **Attachments/audio.** Validate format and size locally; strip metadata, locally extract/OCR where supported, sanitize extracted text, and transmit only necessary derivatives. Reject unsupported files/images rather than forwarding originals. For child voice, prefer local speech-to-text then the same text gate and validated local speech output; direct cloud audio/video remains off. Preserve the original only under an approved local retention policy.
6. **Tools and destinations.** Default child tools to none. Allowlist operation, resource, arguments and destination; recheck at execution. A model cannot authorize messaging, strangers, purchases, location disclosure or unrelated files. Validate URL scheme, DNS/address resolution, redirects and endpoint identity; restrict private/link-local/metadata targets unless separately approved for a specific local service. Arbitrary provider `base_url`, MCP target or `local: true` flag cannot expand the allowlist.
7. **Bypass prevention.** Make one egress API the only owner of provider credentials and approved transports. Route classifier, providers and capabilities through it. Run untrusted tools with no provider credentials and restricted filesystem/network access; an ordinary Python wrapper alone does not contain a subprocess. Initially disable subscription CLIs, manual handoffs, arbitrary MCP and live WebRTC for protected sessions. Re-enable only after proving no alternate outbound content path. An isolated egress broker may be justified for OS enforcement without redesigning the rest of the app into microservices.
8. **Fail closed.** Missing/crashed/timed-out detector, malformed policy, unverified identity, stale approval, unsupported attachment or changed destination causes zero external content calls. Provider/network failure may retry only a still-authorized sanitized request. Do not spool raw data for later transmission. Return an understandable safe fallback; record a content-free reason code.
9. **Response and child safety.** Buffer output until validated before screen, TTS, persistence or tool execution. Handle bullying, violence, sexual/grooming/secrecy content, dangerous activities, self-harm concerns and identity solicitation contextually. A child seeking help is not a policy offender. Define safe escalation, including the risk of notifying an implicated caregiver. Parent choices cannot disable core safety through ordinary child settings.
10. **Privacy-safe observability.** Emit opaque request IDs, policy versions, category/severity, allow/deny reason, destination ID, latency and byte counts. Never raw prompts, transcripts, aliases/maps, school/location, tool arguments/results, auth headers, SDP, provider error bodies or full URLs with queries. Use structured redacted exceptions and test crash/debug/export paths; resource metrics do not need content. Notifications contain generic text and require authorized detail retrieval.

**Scope of guarantee:** enforce Switchboard-managed traffic and controlled child clients/workers. Do not claim control of unrelated ChatGPT/Claude/Gemini apps, another household computer, OS administrator traffic or provider-internal copying. If a supported execution route cannot be mediated, protected mode must reject it.

## 5 Transcript retention and deletion strategy

### Managed and conditional copy inventory

This inventory identifies source-defined stores, not counts or contents on the inaccessible Pi. Conditional stores must be checked against installed versions and configured agents. There is no global content lifecycle registry or comprehensive cascading deletion in the assessed source.

| Store/copy | Evidence and current behavior | Required lifecycle coverage |
|---|---|---|
| Main conversation/task SQLite | `config.py` places `data/switchboard.db` under `SWITCHBOARD_HOME`; `store.py` stores prompts/results/errors/plans; `companion.py:28` stores chats, turns, prepared contexts, memories, abilities and reminders. Archive/deactivate is not deletion. | Delete owned conversations, tasks and derived contexts; separately handle explicit memories/abilities containing copied content. Child deletion must stop future inference from them. |
| Personalization | `personalization.py:208` persists the singleton profile, preferences, suggestions and evidence references. Reset/private mode affects this profile, not all transcripts. | Track provenance of inferred facts; delete/recompute affected derived values without treating a profile reset as a complete erasure. |
| Studio and retrieval copies | `studio.py:140` stores goals/runs/steps and snapshots; `knowledge.py:16` stores source text, chunks and FTS. Document deletion removes its current index rows but not excerpts already copied into runs/chats. | Cascade through source-derived copies and search indexes, using lineage. Preserve unrelated user documents and explicit independent saves through a clear ownership policy. |
| Uploads/capability state | `web/app.py:206` writes image uploads; `capabilities/hub.py:17–49` writes `data/capability-inputs`, asset metadata, job/meeting/voice JSON and staged provider inputs. | Cover original bytes, derivative files, names, JSON checkpoints and extracted transcripts; expire abandoned uploads. |
| Task/output artifacts | `outputs.py:38,82` writes prompt-derived directories, `prompt.md`, `response.md`, `meta.json`, extracted code and artifacts; Engine writes error files. Capability outputs include transcripts and meeting notes (`web/capability_api.py:186`, `capabilities/hub.py:228`). | Opaque paths for protected content; manifest all files and derivative ownership. Delete bounded owned paths safely; do not delete unrelated user folders. |
| Household mirrors | `engine.py:638` sends output files to agents; `executor.py` writes them locally. No deletion acknowledgement protocol exists. | Protected mirroring off initially. Legacy managed mirrors need authenticated deletion jobs, acknowledgements and offline retry status. An offline device means deletion is incomplete there. |
| Agent execution database | `capabilities/machine.py:10` writes `data/capability-executions.db`; results can contain extracted text and encoded artifacts. | Purge payloads but retain minimal opaque deduplication/deletion tombstones long enough to prevent re-execution or resurrection. |
| CLI/provider-client transcripts | `providers/cli_bridge.py:130,202` writes task MCP configs, sometimes CLI result files, and launches external CLIs. A result file is unlinked after reading, but independent CLI home/session logs are outside that cleanup. | Inventory each supported CLI/version's stores before enabling. Use isolated homes where supported, sanitized data only and an adapter-specific deletion contract. Never indiscriminately erase the user's other CLI projects. |
| Logs/errors/telemetry | `cli.py:30` rotates Hub/agent logs at 5 MB with three backups; Engine/MCP/agents may include exception text. Terminal/systemd/OS logging is conditional. ntfy can retain notification copies. | Redact at creation; bounded retention for all rotations and approved OS logs. External notification deletion depends on that service, not the Hub's DB. No general analytics platform is assumed present. |
| In-memory queues/caches | `nodes.py:34` holds job payloads/pending results; Engine holds running tasks/gateways; Companion keeps per-chat locks; browser Ask/history/audio/voice arrays hold content. | Cancel/drain work by request lineage, invalidate permits and suppress late responses. Clearing a database row does not clear queued work or browser memory. |
| Browser storage/cache/clipboard | `app.js:102` stores token in localStorage/cookie, holds chat/history in memory; `sw.js` excludes `/api/` but caches other successful same-origin GETs. API file paths therefore differ from non-API downloads. Handoffs use clipboard/app state. | Clear owned client state on deletion/logout; inspect actual HTTP cache headers, non-API URLs and browser downloads. Do not claim the service worker caches all chats. Clipboard/downloads/screenshots or user exports may be unmanaged copies requiring user action. |
| Temporary files/audio | `capabilities/adapters.py:53` and `voice.py:60` use temporary directories; abnormal process/host termination may leave files. Model-download caches normally contain assets, not transcripts. | Private task-owned scratch directories with expiry/startup cleanup; local audio buffers/session teardown. Distinguish content-bearing temp/cache entries from reusable model assets. |
| Evidence snapshots | No dedicated child incident evidence store is implemented. Personalization evidence references and existing transcript snapshots are different features. | If added, retain only justified redacted excerpts, encrypted and lineage-bound, with parent-only access and expiry; no arbitrary device screenshots. |
| DB remnants/backups | SQLite rollback journal/free pages and possible WAL/SHM if configured can retain old bytes. Config `.bak` files exist. OS/SD-card images, desktop backup tools and manual exports may copy data; no integrated backup policy is established. | Inventory actual backup destinations, generations and keys. Logical deletion must not be represented as forensic erasure from an SD card or SSD. |

### Proposed retention defaults for review

These are design proposals, not settings applied to existing data. Child raw input/audio remains transient in memory by default; no raw provider-task transcript files or mirrors. Optional locally encrypted chat history is off initially; a parent may approve a documented finite duration. Abandoned uploads/scratch files expire within 24 hours as a maximum crash-recovery window and are removed earlier at session completion. Minimal content-free operational/security events expire after 30 days. Optional redacted incident evidence defaults off; if enabled, propose 7 days with explicit review of necessity. Managed backup generations containing protected data expire within 30 days, unless per-object cryptographic erasure provides an earlier boundary. Confirm these durations and deletion completion targets before enabling retention. Device-offline and third-party limits must remain visible.

### Consistent cascading deletion

1. Assign local opaque profile, conversation, request and artifact IDs and record provenance on every persisted derivative and transmission. Legacy paths need a dry-run inventory/migration; incomplete legacy lineage must be reported rather than guessed.
2. Authorize deletion locally, mark a durable tombstone and atomically invalidate pending permits before cancellation. Stop scheduled work, queues, remote jobs, gateway sessions, live voice and mirrors. Require acknowledgements where meaningful; a third-party request already in flight cannot be unsent.
3. Traverse lineage to remove conversation/turn/task payloads, derived memories, profile evidence, Studio snapshots, knowledge excerpts/index rows, uploads, capability results, output files, local caches and approved evidence. Shared references require a documented choice: remove the sensitive content everywhere it was derived, or report independently retained copies; reference counting alone must not silently defeat a deletion request.
4. Dispatch authenticated idempotent deletion to managed agents/clients and record content-free status. Offline agents must apply tombstones before serving/restoring/retrying old jobs when they reconnect. Reject late writes or results for deleted objects, including deduplicated machine results.
5. For SQLite, cover tables and FTS, journal/WAL lifecycle and free-page handling under a tested maintenance plan. Encryption/key lifecycle and appropriate secure-delete/compaction can reduce recoverability but do not promise physical erasure on wear-levelled storage. No such maintenance command was run.
6. Encrypt protected records/artifacts with explicit key scope. Where cryptographic erasure is used, ensure deleted keys are absent from retained backups/key escrow; otherwise it is not effective erasure. Keep only minimal opaque deletion markers; avoid reversible content hashes or identifiers in them.
7. Expire/prune or rewrite managed backups according to policy. On restore, apply a current deletion ledger before any reads, jobs or external requests. Restoring an older backup must not resurrect content or re-enable revoked policy. Copying encrypted data together with all decryption keys does not provide deletion separation.
8. Produce a deletion receipt with separate states: active local stores removed, managed agents pending/confirmed, backups pending expiry, provider deletion requested/acknowledged/not supported, and unmanaged user copies requiring action. A timeout or provider acknowledgement is not proof of universal erasure.

### Third-party deletion and retention

Provider behavior was checked against primary documentation on 23 November 2026. Recheck exact product, endpoint, model, account tier and contract before enabling a route. A privacy/support request is distinct from a resource deletion API, and neither guarantees removal of legal/safety logs or all internal copies.

| Product/path | Mechanism and documented limitation |
|---|---|
| OpenAI API in this checkout | Uses Chat Completions and Images, not a Responses/Conversations store. It does not explicitly set `store` or preserve provider resource IDs for deletion. Add endpoint-specific retention settings and a minimized transmission ledger. The stored-completion DELETE API applies only to completions created with `store=true`; it cannot delete an ordinary unstored call's abuse-monitoring logs. Default API abuse monitoring can retain content for up to 30 days, with endpoint/model/account exceptions and separately approved retention controls. `store=false` is not ZDR. [Data controls](https://developers.openai.com/api/docs/guides/your-data), [stored completion deletion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/delete). |
| Anthropic API | Current adapter sends Messages with inline content. Anthropic says paid API requests do not support ad hoc deletion; standard inputs/outputs are deleted within 30 days subject to contract, covered-model, safety and legal exceptions. If Files API is introduced, delete its uploaded resources separately via the Files deletion API; that does not delete every inference/log copy. [API deletion limits](https://privacy.claude.com/en/articles/7996875-can-you-delete-data-that-i-sent-via-api), [retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data), [Files API](https://platform.claude.com/docs/en/api/files). |
| Gemini API | Current Gemini route uses the OpenAI-compatible adapter with inline payloads; deleting a Google Files object is not a deletion mechanism for those inline requests. If uploaded Files are used, `files.delete` is available and documented files expire after 48 hours. For supported explicit cache resources, use `cachedContents.delete`/expiry. Implicit caches and abuse-monitoring data are separate; current usage-policy documentation describes 55-day abuse-monitoring retention. [Files](https://ai.google.dev/gemini-api/docs/files), [cache deletion](https://ai.google.dev/api/caching), [abuse monitoring](https://ai.google.dev/gemini-api/docs/usage-policies). |
| ChatGPT app/manual handoff | Delete chats through the account controls; archived chats remain stored. Separately saved Library/Space files, project knowledge and memories may require their own deletion actions. OpenAI describes scheduled deletion within 30 days with de-identification/security/legal exceptions. Hub deletion cannot perform or verify all these actions. [Chat and file retention](https://help.openai.com/en/articles/8983778-chat-and-file-retention-in-chatgpt). |
| Claude app and subscription Claude Code | Use account conversation deletion/data controls and the supported installed client's local session cleanup; API retention terms cannot be assumed for consumer plans. Deleting local CLI files does not prove provider-side deletion. Consumer documentation describes backend deletion within 30 days after conversation deletion, with exceptions. [Consumer retention](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-my-data). |
| Gemini app and CLI | Delete relevant Gemini history/activity through account controls; linked app data and reviewed copies have separate limits. Google documents reviewed chats retained up to three years even after history deletion. CLI authentication/product path may use different API or account terms: verify it before making any deletion promise. [Gemini privacy controls](https://support.google.com/gemini/answer/13594961?hl=en). |
| Other compatible providers, MCP, remote services and notifications | OpenAI-compatible transport does not imply OpenAI retention terms. Each enabled destination needs its own resource-ID/deletion adapter and verified retention contract; otherwise mark remote erasure unsupported and keep it disabled for protected data. |

No provider deletion was requested or executed. **Switchboard cannot guarantee deletion of third-party copies outside its control.** Prior provider transmissions cannot be undone by later sanitization. For existing content, identify destinations/available IDs without making new disclosures, perform supported authorized deletion requests, and report unsupported/unknown copies honestly.

## 6 Small testable implementation phases

Stage A was subsequently explicitly approved as local Phase 2 and has been implemented for review. All remaining stages below are **proposals** requiring separate approval; each implementation stage stops for its own review. The earlier Hostinger Phase 2 is no longer the recommended next step.

| Stage | Small deliverable after approval | Acceptance evidence and stop gate |
|---|---|---|
| A — local policy and principals | Define portable contracts; implement local parent/child/device scopes, child-safe endpoint restrictions and destination deny-by-default. Preserve explicitly authorized adult behavior. | Anonymous/child/guest cannot administer or change mode; expired/revoked scopes fail; existing adult workflows pass. Review diff before continuing. |
| B — text egress enforcement | Gate classifier and one explicitly approved API route; locally minimize full text/history/context, use content-free audit and fail closed. Disable all unmediated protected routes. | Capture final serialized requests using fake transports: direct/indirect synthetic PII absent; denied/time-out/malformed inputs produce zero external calls; no fallback leakage. Measure detector latency/RAM on Pi. Review. |
| C — tools, attachments and alternate paths | Extend the same gate to each remaining provider/tool/capability, local file extraction, workflow retry and managed worker. Keep WebRTC/manual handoff/CLIs blocked until separately proven. | Synthetic tool-result, retrieval, attachment metadata and bypass tests; changed payload/destination invalidates permit; worker cannot use hidden credentials or direct external sockets. Real-browser local-origin tests. Review. |
| D — child response and escalation policy | Add age-appropriate response checks, safe local fallback and parent-reviewed incident rules with minimal optional evidence. | Reviewed multilingual synthetic risk corpus, false-positive/negative analysis, helpful handling of disclosures, no unsafe streamed/TTS output, no sensitive notifications. Review. |
| E — retention and deletion | Add artifact/lineage registry, configurable finite retention, encrypted storage where approved, deletion tombstones, managed-agent acknowledgements and provider-specific receipts. Dry-run legacy inventory first. | Delete synthetic content from DB/FTS/files/queues/agent result caches; offline/retry/late-result tests; crash recovery, key/backup handling and restore-with-tombstones prevent resurrection. Do not erase real data without authorized scope. Review. |
| F — local release validation | Validate on the actual Pi and supported child devices, resource/load behavior, confidential LAN transport if used, supported-client data copies and failure recovery. | Document network reachability, end-to-end synthetic disclosure tests and deletion limits; review remaining risks and get explicit release approval before real child use. |
| Future — cloud design only | Client-side minimization before upload; independent cloud policy validation and household/identity authorization. | Separate architectural approval and scope; no cloud account, hosting or infrastructure implementation in these stages. |

## 7 Portable future cloud architecture

A future Switchboard client begins detection/minimization on the device before data crosses the household boundary. The cloud Hub treats client results as untrusted claims, independently validates content and policy, authenticates user/device identity and derives household membership server-side. Every conversation, artifact, job, tool capability, event and deletion request is household-authorized. Cloud provider egress reuses the same policy/minimization/permit contracts, with independent destination authorization. Never upload raw child identity merely to decide whether to sanitize it.

Local and cloud deployments can share policy objects, data-classification types, minimized envelopes, audit schemas and deletion receipts. Identity resolution, key custody and storage adapters vary by deployment. A singleton local profile is not cloud isolation, but it also does not justify implementing cloud registration now. Raw audio, attachments, telemetry and recovery/backup channels must obey the same client-to-cloud privacy boundary. Centralized hosting requires a new threat model and explicit infrastructure approval.

## 8 Validation and remaining uncertainty

### Existing regression checks

Ran 12 selected tests in the existing Python 3.13.18 environment: **12 passed in 7.29 seconds**. Selected Hub/relay cross-site tests, pairing expiry/failure/single-use tests, personalization private-mode/reset tests, live-voice billing/termination test, agent deduplication test and all three output tests. Command used `PYTHONDONTWRITEBYTECODE=1` and `pytest -q -p no:cacheprovider`; temporary synthetic homes/mock fixtures only. These tests verify current behavior, not child-safety completeness. The earlier 60-test run remains historical evidence and was not repeated in full.

### Additional access checks

Sixteen assertions passed using temporary synthetic Hub/agent configuration, HTTPX ASGI transports and one native loopback TCP listener. No Pi service, real proxy or browser was used:

| Case | Observed result |
|---|---|
| Loopback own Host, anonymous status and settings | 200 |
| Loopback foreign Origin or foreign Host, no Authorization header | 403 |
| Loopback different localhost-port Origin or null Origin | 200 |
| Loopback foreign Host and invalid nonempty bearer, default local auth | 200 |
| Simulated network peer, absent or invalid credential | 401 |
| Simulated network peer, valid shared token, settings read | 200 |
| Strict local-token setting, anonymous or invalid bearer | 401 |
| Native loopback TCP request, anonymous settings read, default setting | 200 |
| Agent relay anonymous settings read against strict-token Hub | 200, relay inserts token |
| Relay foreign Origin | 403 |
| Relay alternate localhost-port Origin | 200 |

These results identify process/role boundaries. They do not prove that arbitrary internet traffic can reach loopback, that a real browser can exfiltrate responses, or that Pi deployment matches the test configuration.

### Pi exposure check

`10.0.0.17` was unreachable from this Mac on both SSH (22) and the expected Hub port (8765), returning “Host is down.” No remote state was inspected. Internet ingress, LAN exposure and installed configuration remain unverified. Repeat only read-only checks when connectivity is available; no firewall/tunnel/port-opening action is authorized here.

### Documentation and scope checks

Rechecked source-defined outbound/data-copy paths and primary provider deletion documentation. This file is the only changed repository artifact. No application code, dependencies, runtime settings, infrastructure, provider data or real user storage was modified; no deletion, deployment, commit or push occurred.

**Current review decision:** review Stage C / Phase 4 and its validation report before authorizing Stage D. Do not resume Hostinger provisioning or implement cloud user management.
