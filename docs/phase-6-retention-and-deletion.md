# Phase 6 registered retention and deletion

Date: 24 November 2026. Scope: approved Stage E, implemented and tested locally. Phases 5 and 6 are uncommitted working changes on base `5c864325a870c0edeaf7c35634446474b1f0df8e`. No production configuration, existing real data, Pi service, infrastructure or provider account was modified. Child access remains disabled.

Subsequent update: the user approved [Phase 7 local release validation](phase-7-local-release-validation.md). Local checks passed; actual Pi/device validation is incomplete and the release decision is no-go. This report retains the Phase 6 scope and evidence.

Audit follow-up: [additional retention and authorization fixes](security-audit-phase-5-7.md) corrected backup deadlines, restore preflight/staging, event-loop blocking and other edge cases. The newer audit contains current verification; original counts below remain historical evidence.

## Result and coverage boundary

Added a read-only legacy inventory and an opt-in storage backend for explicitly registered data. Registered copies share an immutable lineage root, finite deadlines, encrypted payloads, scoped keyword indexes, reviewed root-wide deletion, deletion tombstones, supported data-only backups/restores and separate agent/provider receipts. Existing privacy and safety review paths continue to avoid persistent transcripts.

**Legacy stores remain inventory-only.** The backend does not adopt existing chats, documents, task outputs, logs or agent files automatically. Current chat/Studio/capability writers are not migrated to it. There is no general filesystem eraser, content-creation HTTP route, legacy-deletion flag, remote deletion dispatcher or automatic provider-deletion implementation. New trusted writers must use the registry before creating retained copies; bypassing it would leave copies outside this deletion contract. This is a registered-storage foundation, not a claim that the current installation's historical data is comprehensively deletable.

This boundary preserves the instruction to leave real data untouched. A reviewed legacy adoption/migration and actual agent integration remain release blockers. Synthetic tests prove the new deletion contract; they do not prove deletion from every pre-existing application store.

## Read-only inventory and existing copies

`retention_inventory.py` opens existing SQLite databases with `mode=ro` and query-only connections, counts fixed known tables, and bounds queries by a deadline. It never initializes an absent database or selects payload/title/filename fields. Filesystem scans collect counts and aggregate bytes only, cap traversal at 2,000 entries, skip symlinks and use directory descriptors without following child-directory links. Results are live observations, not atomic snapshots or ownership maps.

| Copy category | Existing locations grounded in source | Implemented scope |
|---|---|---|
| Conversations and personal context | `companion.py`: `laith_chats`, `laith_turns`, `laith_memories`, reminders; `personalization.py`: `laith_profile` | Legacy table counts only. New registered `conversation` copies are encrypted. No child/chat writer is newly enabled. |
| Task and agent transcripts | `store.py`: `tasks` prompt/plan/result/error; `outputs.py`: `prompt.md`, `response.md`, `meta.json`, artifacts; engine output mirrors | Local metadata counts only; remote mirrors remain unverified. New registered `transcript`/`agent_result` copies participate in deletion. |
| Retrieval and full-text search | `knowledge.py`: `studio_documents`, `studio_chunks`, plaintext `studio_fts`; Studio runs/goals carry additional context | Legacy counts and source assessment only. New managed full-text index contains root-keyed search tokens, with no plaintext terms. Deletion clears managed payload/index rows. |
| Caches and queues | Capability job/asset/meeting/voice tables; agent `capability-executions.db` `executions`; in-memory legacy jobs/gateways | Existing database counts only; no agent connection or legacy job resumption. New registered cache/queue/result copies have shorter deadlines and reject late writes to deleted roots. |
| Logs and telemetry | `cli.py` rotating `data/*.log` files, with three rotated generations | Metadata counts only. Existing logging is not rewritten or erased. Registered `log` payloads are encrypted; the new maintenance logger uses a fixed content-free warning. |
| Temporary files | Media/voice/tool `TemporaryDirectory` use, including worker scratch; crash leftovers are not globally enumerable | Existing scoped cleanup remains. New registered `temporary` payloads expire early. No OS temporary-directory sweep or unrelated-file deletion. |
| Evidence | Phase 5 metadata-only review queue, with 15-minute memory expiry; any older screenshots/exports are unregistered | Phase 5 remains unchanged. Registered `evidence` storage is available to explicitly authorized future writers; nothing automatically captures raw evidence. |
| Backups and exports | Operator/OS backups, `.bak` configuration exports, external copies and user exports have no complete ownership registry | Not enumerated or erased. New managed backups contain encrypted data only, stay registered under the same root and omit the live control/key ledger. |

The actual Mac default-configuration inventory is saved in `docs/verification/phase-6/local-legacy-inventory.json`. At inspection, its main `switchboard.db` was absent, an agent execution database had one record, and the output scan hit its entry cap. The recorded file/byte values are **partial scanned totals**, not the complete output tree. An absent database in this configuration does not mean other installations lack chat history. Neither the Pi at `10.0.0.17`, other app configurations, external agents nor operator backups were inspected.

## Registered storage and encryption

The new namespace is fixed at `<configured-home>/data/retention-v1`, with private directory/file permissions and a POSIX single-writer lock. No directory is created when the feature is disabled. Enabling requires protected mode and an explicitly supplied, valid `SWITCHBOARD_RETENTION_KEY`; there is no automatic master-key generation or plaintext fallback. A wrong key, unavailable FTS/required POSIX primitives or a symlinked vault/database path refuses initialization. The key is included in the privacy gateway's local secret deny terms even when supplied only through the environment.

Each server-generated root receives a fresh data key wrapped by the master key. Database and file payloads use [Fernet authenticated encryption](https://cryptography.io/en/latest/fernet/). Payload kinds are conversation, transcript, log, cache, queue, temporary, evidence, backup and agent result. The control registry retains opaque identifiers, lineage links, type, storage choice, size, timestamps and state. These metadata are sensitive and protected by local permissions; they are not encrypted or anonymous.

Managed search uses domain-separated HMAC tokens derived from the root's data key. SQLite FTS stores these tokens and opaque references instead of plaintext words. Search operates within an explicitly selected root. Equal terms can be correlated within a root; metadata, token counts and access patterns remain observable to someone who can read the database. This is bounded keyword lookup, not semantic retrieval or a claim of hiding all search leakage. Legacy Studio FTS remains unchanged.

Limits are 256 active roots, 64 artifacts per root, 8 MB registered plaintext size per root and 32 MB globally. Ordinary payloads are at most 64 KB; encrypted snapshot payloads are capped at 4 MB. Inputs use server-generated opaque filenames, never a caller-selected path. File operations use a pinned directory descriptor, exclusive creation, no-follow flags and regular-file checks; symlinks or unexpected hard links prevent cleanup rather than affecting their targets. A staging record precedes content creation. Interrupted staging writes remain inaccessible and are removed during maintenance.

`create`, `put`, `read`, `search`, `backup`, `restore` and copy-registration methods are backend interfaces for trusted code exercising current parent authority. They are not exposed as storage/upload/import APIs. The new HTTP surface provides inventory, policy and deletion review only. No raw conversation persistence was wired into Laith or the reviewed provider gateway.

## Finite retention and cascading deletion

Configuration defaults:

```yaml
security:
  retention:
    enabled: false
    seconds: 86400
```

The root deadline is fixed at creation from a strictly bounded 60-second to 30-day policy. Changing policy/key settings denies existing operations until the service is deliberately reconfigured; it does not silently extend previously recorded deadlines. Temporary, cache, queue and agent-result copies have a maximum 15-minute deadline. Descendants inherit the earlier parent/root deadline, and an expired parent cannot receive new copies. Following the audit fix, registered backups inherit the earliest captured source deadline, bounded by the root deadline. Expired data cannot be read or searched through the backend.

When explicitly enabled, the protected Hub runs maintenance at startup and approximately every 60 seconds. Maintenance enforces previously recorded deadlines, recovers interrupted deletion and removes staged copies. A parent may also invoke it. It operates only on registered vault entries. With the application stopped, physical cleanup waits for the next maintenance run; wall-clock correctness and filesystem availability affect timing. Failures remain pending and visible rather than falsely reporting completion.

Manual deletion before expiry requires an authenticated parent preview, showing the exact registered lineage metadata and known agent/provider records. The immutable plan is bound to that principal, a digest, a single-use random preview ID and 120-second expiry. At most 16 previews are retained. Changed lineage invalidates the confirmation. A boolean `true` is required; arbitrary path/SQL/legacy overrides and reflected invalid input are rejected.

Deletion commits a tombstone and removes the root's locally wrapped data key **before** deleting any payload. Reads, search, new writes and supported restores then deny the root. Cleanup removes registered database payloads, managed FTS rows, files, caches, queues, evidence and backups, including all descendants. An interrupted operation stays inaccessible and is retryable after restart. A filesystem failure produces `cleanup_pending`, not successful local deletion. Unrelated registered roots and legacy rows/files remain unchanged.

Receipts separate `local_deleted`, `cleanup_pending`, `local_key_destroyed`, agent state and provider state. No receipt promises physical erasure of disk blocks, RAM, OS snapshots, database journals or copies of old keys. SQLite secure-delete is enabled for the new databases, with rollback journaling, but this does not establish forensic erasure. Retaining a data key, an old key ledger or an unmanaged export can defeat cryptographic deletion outside this contract.

Opaque root/tombstone records and outstanding agent/provider metadata outlive content deletion to prevent replay and resurrection. They currently have no automatic garbage collection: removing them safely requires proof that old backups/copies can no longer return. They contain no content, names, prompt digests or remote object IDs, but are still metadata requiring an operational retention decision before release. API previews expire in memory; shutdown clears their timers and grants.

## Backups, recovery and delayed copies

Managed backups are authenticated, encrypted snapshots of non-backup artifacts, stored as registered leaf copies under their root. They exclude the master key, wrapped root keys and control/tombstone ledger. Snapshot restoration requires the **current** live ledger and key, validates root identity, immutable artifact metadata, bounds, acyclic lineage and unexpired deadlines, and cannot overwrite conflicting content. Root deletion makes old supported snapshots unrestorable. Tests cover restoring missing database/file copies before deletion and rejecting the same snapshot after deletion/restart.

A full OS/filesystem restore that rolls back the control ledger and keys is outside this restore interface. The code cannot detect every such rollback or guarantee protection against it. Deployment needs an independently preserved deletion ledger or a proven restore procedure that reconciles current tombstones before opening restored data. Loss of the live key/control state can also make undeleted backups unrecoverable; key custody and disaster recovery need operator review. No OS backup was edited or restored here.

The managed-agent interface registers an opaque agent ID and a deletion-only random capability, retaining only its hash. Deletion marks the copy pending. A matching capability may acknowledge only a tombstoned root; repeated acknowledgments are idempotent and cannot read/write content, register copies or undo deletion. State `reported_deleted` means an authenticated agent claim, not independent verification of its filesystem. Offline agents remain pending across restarts. There is no implemented transport, device endpoint or legacy-agent cleanup adapter in this phase, so no acknowledgment was obtained from a real agent. Future connectors must bind delivery to the authorized household/device and refuse late results.

## Provider-specific deletion limits

No new network operation or deletion adapter was added. Backend provider registration supports only the three reviewed text providers and records `no_request_level_deletion_adapter`; this describes the implemented route, not every API feature a provider offers. Registration is a trusted caller's declaration, not proof of an outbound request. Existing ephemeral gateway calls are not automatically associated with vault roots. Current receipts never claim remote deletion or collect extra child identity to request it.

| Provider/product | Supported mechanism to consider separately | Limit for current reviewed path |
|---|---|---|
| OpenAI API | Stored Chat Completions have a [delete endpoint](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/delete), restricted to completions created with storage enabled. Files and other stateful resources have their own operations. | This gateway sends `store: false`, retaining no deletable stored-completion resource locally. Provider monitoring/retention exceptions remain subject to [account data controls](https://developers.openai.com/api/docs/guides/your-data). Deleting a stored object would not prove erasure of every provider copy. |
| Anthropic API | [Files API deletion](https://platform.claude.com/docs/en/build-with-claude/files) manages uploaded files; stateful agent sessions and consumer/enterprise products use other controls. | No request-level Messages deletion adapter is implemented. [API retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) varies with model, feature and organization arrangements; some covered models require retained content. Do not assume an eligible endpoint proves an account/model has zero retention. |
| Gemini Developer API | Uploaded [files](https://ai.google.dev/api/files) and explicit [cached content](https://ai.google.dev/api/caching) have separate delete operations. | The current text-only `generateContent` route creates neither resource through Switchboard. No per-request deletion adapter is implemented. Applicable [service terms](https://ai.google.dev/gemini-api/terms), account/product controls and retention exceptions remain outside local deletion. |

These checks used public documentation only. There were no live provider requests, account changes, supported-delete calls, new credentials or legal-agreement acceptance. ChatGPT/Claude/Gemini consumer subscriptions and CLI histories are separate products/copies; the earlier assessment's manual/account-specific controls remain relevant. **Switchboard cannot guarantee deletion of third-party copies outside its control.**

The current Gemini Developer API terms also exclude API clients directed toward or likely accessed by under-18s. This is a concrete provider-eligibility blocker for a future child-directed release, separate from data minimization. Current tests are adult-controlled synthetic simulations; technical support for a provider format is not commercial child-use approval. A product/account eligibility review remains necessary before any child release.

## Validation and review gate

The focused retention/safety/privacy/authorization suite passed **283 tests in 24.49 seconds** (`focused-tests.log`). The full repository suite passed **525 tests in 151.98 seconds** (`regression-tests.log`). Evidence is in `docs/verification/phase-6/`; these counts overlap and must not be added. Python syntax and tracked/new-file whitespace checks also passed across the Phase 5–6 working changes. Tests use isolated temporary configurations, synthetic content, offline copy-capability calls and HTTP ASGI clients on this Mac; Pi/Linux runtime behavior remains unverified. No real legacy payload was deleted, no existing content was migrated/encrypted and no production key was generated or installed.

Tests cover encrypted database/files for every kind; managed FTS and lineage cleanup; unrelated-root and legacy preservation; exact parent/digest/expiry/replay binding; policy/key changes; missing/wrong key and multiple-writer refusal; symlink/file failure and repair; staging/root crash recovery; expiry and inherited deadlines; late writes; backup authentication/restore/tombstone precedence; persistent offline-agent pending state and idempotent claims; provider limitation receipts; no raw preview/log content; protected role/relay denial; read-only legacy DB/FTS inventory; and secret deny-term handling.

Remaining release blockers include reviewed legacy adoption and writer integration, actual managed-agent deletion/ack transport, OS backup/ledger recovery, key custody/rotation, metadata retention policy, actual Pi/Linux resource/permission validation, browser/device/media paths, and the prior risk/PII detection and safeguarding limitations. This phase does not establish complete historical-data erasure or authorize real child use.

Review the Phase 5–6 working diff and reports before authorizing Stage F / Phase 7 local release validation or additional legacy-migration implementation. Production cleanup, Pi deployment, live provider trials, cloud accounts, real child traffic, committing and pushing all require their own explicit instruction. No later phase was started.
