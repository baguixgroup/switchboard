# Security and correctness audit of Phases 5–7

Date: 24 November 2026. Scope: the current household-local security working tree, including its legacy entry points. Base commit: `5c864325a870c0edeaf7c35634446474b1f0df8e`. Findings were checked against source and synthetic reproductions; routine fixes were implemented locally. Previous Phase 5–7 work was preserved. No real data, infrastructure, installed configuration or provider account was modified, and no deployment, commit or push was performed.

## Outcome

The audit found real execution-boundary, retention, restore, authorization and availability bugs that earlier tests missed. Added **36 regression cases** in `tests/test_security_audit.py`; failing pre-fix reproductions are retained in `docs/verification/security-audit/`. Passing earlier suites were insufficient evidence for these cases.

The fixes keep the current release decision **NO-GO**. Child access remains disabled. The registered vault still does not migrate legacy history, and the reviewed device/agent/client workflows remain incomplete. This audit does not certify the entire application, all operating systems or general PII/safeguarding accuracy.

## Findings fixed

P1 means a boundary/privacy failure to address before enabling the affected protected workflow; P2 means a correctness, authorization-consistency or availability defect. These are source/design priorities, not claims of a live internet-facing incident. All tested data and credentials were synthetic.

| ID / priority | Reproduced behavior and concrete conditions | Implemented correction |
|---|---|---|
| A01 / P1 | An authenticated parent `GET /api/status` invoked provider availability hooks. A configured installed CLI with a required feature could execute `features list` with inherited host environment outside the restricted worker. No conversation prompt was passed, and actual external transmission was not demonstrated. Remote inventory could also advertise blocked legacy CLIs as available. | Protected status builds static unavailable descriptors without calling provider hooks. Engine availability/location and CLI/handoff availability refuse legacy providers in protected mode. Existing adult/standalone compatibility remains covered. Files: `engine.py`, `providers/cli_bridge.py`, `providers/handoff.py`, `web/app.py`. |
| A02 / P1 | Starting the legacy agent with a protected configuration reached inventory/probe work before any mode guard; inventory could spawn/probe devices, and the legacy relay constructed an outbound client plus unauthenticated local controls outside the Hub middleware. Actual reachability depends on the selected local service/configuration. | `agent.run_agent`, `inventory.build_inventory` and `localproxy.build_proxy_app` now refuse protected-mode startup before those side effects. Legacy adult mode remains supported. The missing reviewed device connector was not silently substituted with the old one. |
| A03 / P1 | A registered backup inherited the longer conversation/root deadline even when it contained a 15-minute queue/cache/temp copy. The source could expire while a decryptable managed backup retained its content. | A backup derives from the earliest-expiring captured artifact and inherits that earlier deadline. Root deadline remains an upper bound. Read, maintenance and restore tests cover the expired backup and preservation of longer-lived unrelated copies. File: `retention.py`. |
| A04 / P2 | Restore checked file/database conflicts during application, so a later conflicting target could reject the request after earlier copies were already recreated. A disk failure while repairing an existing `ready` row could leave it incorrectly readable/ready and outside staging cleanup. | Preflight every existing target before writing. Mark repaired/missing copies `staging` before I/O; successful copies become ready after checks. Failed staged copies stay unavailable and maintenance can remove them before a successful retry. Unexpected I/O can still interrupt a multi-copy restore; this is recovery handling, not a cross-filesystem atomic-restore guarantee. File: `retention.py`. |
| A05 / P2 | A managed filename replaced by a FIFO could block `open` before regular-file checks ran. This requires local interference/corruption inside the private vault, not an arbitrary HTTP filename. | Open managed reads nonblocking and reject non-regular files before consuming bytes. The synthetic FIFO is rejected without waiting for a writer. File: `retention.py`. |
| A06 / P2 | Retention preview acquired a blocking filesystem/database lock directly on the HTTP event loop. A slow concurrent operation stalled unrelated async work. Expiry callbacks also acquired that lock on the loop. | Prepare previews in a worker thread; arm/cancel timers on the owning loop and perform expiry cleanup in a worker. A held-lock reproduction now leaves the event loop responsive. File: `retention.py`. |
| A07 / P2 | Agent acknowledgments ignored current retention policy/key changes. Local tool approvals ignored changed child-safety policy. Invalid live policy fields or a malformed server-side security section could produce an internal exception rather than uniform authorization denial. | Share current retention policy/key checks with acknowledgment handling; bind local tool checks to the safety snapshot; validate the security section before nested lookups and normalize invalid policies into authorization denial. Tests prove no worker call or acknowledgment mutation after invalidation and fixed denials for null/list configuration. HTTP clients cannot modify those server policies. |
| A08 / P2 | Explicit false/zero/empty lineage parents were treated like an omitted parent, admitting malformed ancestry and snapshots that could not restore consistently. | Only `None` means no parent; every supplied parent must pass opaque-ID, same-root, ready-state and expiry checks. File: `retention.py`. |
| A09 / P2 | The overlong-answer branch discarded already-detected abuse/distress categories and returned a generic fallback. | Retain the risk finding while marking the length check unavailable, so an overlong disclosure still gets the supportive trusted-adult fallback. No extra source text is retained. File: `child_safety.py`. |
| A10 / P2 | Retention reads/snapshots checked authority or deadlines only before I/O, allowing a delayed result after revocation/expiry. | Recheck before returning content/snapshots and before restoring copies. Snapshot export rejects sources that expired during reading. Synthetic revocation and clock-advance injections verify suppression. File: `retention.py`. |
| A11 / P2 | Root cleanup trusted only artifact registry rows; payload/index rows still explicitly attributed to that root could survive after their registry row was lost or content-only recovery produced an orphan. | Finish root cleanup with root-scoped payload/FTS deletion. Other roots survive. Unknown files without attributable registry ownership are not swept. File: `retention.py`. |
| A12 / P2 | Preview storage errors escaped the async boundary instead of returning the documented fixed error. | Preserve authorization denials and normalize storage errors to a content-free retention failure. No path or input is reflected. File: `retention.py`. |
| A13 / P2 | A valid enrolled device could send an oversized, chunked heartbeat that was fully parsed and retained in memory. A device revoked while the body was being read could still update inventory. | Protected heartbeats use bounded streaming JSON validation (64 KB), with fixed no-echo errors, then reauthenticate before storing inventory. The original legacy heartbeat handler remains separate for compatibility. File: `web/app.py`. |

No actual subscription CLI was invoked by the reproductions: subprocess/probe hooks were stubbed or forbidden to detect their reachability. The old agent was stopped at a forbidden test hook before network activity. Retention failure tests use private temporary stores and synthetic files. The FIFO test includes a rescue timer so the pre-fix reproduction cannot hang indefinitely.

## Validation

Final verification after the source fixes:

- Full repository suite: **561 passed in 164.28 seconds** (`regression-tests.log`).
- Focused audit/security/retention/runtime/agent integration suite: **332 passed in 62.33 seconds** (`focused-tests.log`).
- Real Firefox replay: **25/25 checks passed** (`local-release-probe.json`), with exactly two fake-provider calls, minimized payloads, denied stale snapshot restoration and stopped temporary services.
- Syntax parsing and tracked/new-file whitespace checks passed; the working-tree fingerprint is in `source-manifest.json` and static results in `static-checks.json`.
- The refreshed Pi probes still returned `EHOSTDOWN` on 22 and 8765 (`pi-reachability.json`), so its deployed state remains unverified.

Evidence directory: `docs/verification/security-audit/`. Test counts overlap; do not add focused, full, browser and historical counts. The machine-readable current decision is `audit-result.json`; release remains no-go.

The initial reproductions include 15 failed cases, followed by separate failing restore-I/O, CLI-probe, legacy-entry-point, heartbeat and configuration-shape cases. Additional tests cover orphan cleanup, revocation/deadline rechecks and no-echo errors. Cosmetic trailing whitespace in diagnostic logs is normalized; their failure content is preserved. None of those initial logs is the final test result.

The 25-case Firefox loopback probe uses a disposable profile, fixed fake model responses and a temporary configuration. It exercises real browser-origin behavior and parent review/deletion against current code, without installing or enabling anything in the real application. Pi/other OS/device behavior must be established separately. Current evidence does not guarantee OS-wide outbound isolation, physical/RAM erasure or deletion of third-party copies.

## Missing implementation and remaining release blockers

| Area | What remains missing or unverified |
|---|---|
| Actual deployment | Current protected settings, application revision, runtime/isolation, listening interfaces, firewall/proxy and capacity on the Pi. Existing default configurations/installed apps were not switched into protected mode by this work. |
| Child-facing product | Parent setup/sign-in/client integration, an authorized child chat path, supported-device onboarding and an appropriate safe-adult escalation design. Child and device permissions are backend primitives; child chat/media are still closed. |
| PII and safety quality | General local semantic/NER and indirect-identification accuracy, independent multilingual/adversarial evaluation, developmental/native-language review. The existing small corpus still has two risk misses and two overblocks. Whole-line minimization can also remove useful nonsensitive context. These fixes do not solve those limits. |
| Storage coverage | Ordinary Laith/Studio/task writers and historical plaintext DB/FTS/log/output/mirror copies are not migrated into the registered vault. Root IDs are not automatically attached to all current ephemeral gateway contexts or external exports. End-to-end lineage/adoption needs an explicitly reviewed implementation. |
| Managed agents | Actual protected device transport, deletion dispatch and independently validated cleanup. Current capability acknowledgments are authenticated claims; offline copies remain pending. Legacy agent/relay startup is now intentionally refused in protected mode until a reviewed replacement exists. |
| Backup and keys | Operationally proven full-system restore/tombstone reconciliation, master-key custody and rotation tooling, and retention/garbage-collection rules for deletion metadata. Data-only restore protection cannot prevent an operator from rolling back the entire key/control ledger. |
| Providers and other paths | Product/model/account eligibility, actual retention arrangements and supported deletion mechanisms. There is no universal remote deletion adapter or guarantee. The standalone reviewed text route has no credit-ledger integration or hard monetary spending cap. CLI/consumer-plan, live voice and broader attachments remain outside the approved protected path. |
| Resource/operations | Pi measurement, real supported clients, broader load/timeouts/rate controls, safe operational monitoring and supported frozen-worker packaging. Local bounded tests do not establish commercial household capacity. |

These are implementation or deployment gaps, not completed features hidden behind a setting. They remain explicitly visible in the Phase 7 no-go assessment. Future cloud household/user management remains a separate architecture phase.

## Next review

Review these fixes and the preserved Phase 5–7 changes before publication or further implementation. Continue actual-device validation once connectivity is available, and approve concrete remediation scopes for the missing components. Keep child access disabled. No production data migration/deletion, provider action, deployment or release approval follows automatically from this audit.
