# Phase 7 local release validation

Date: 24 November 2026, America/Winnipeg. Approved scope: Stage F validation. **Release decision: NO-GO.** Local checkout validation passed; actual Pi/Linux and supported-device validation remain incomplete. Child access remains disabled. No deployment, infrastructure/configuration change, real-data deletion, live AI request, commit, push or release approval was performed.

The machine-readable decision, evidence references and outstanding blockers are in `docs/verification/phase-7/release-readiness.json`.

Follow-up: the [Phase 5–7 security audit](security-audit-phase-5-7.md) found and corrected additional bugs and records fresh verification for the amended working tree. This report and its source fingerprint retain the original Phase 7 baseline. Release remains no-go.

## Assessed version and reproducibility

The checkout is `/Users/hicham/Switchboard/repo`, on `main` at `5c864325a870c0edeaf7c35634446474b1f0df8e`, with Phase 5–6 working changes preserved. This phase adds the development-only `scripts/validate-local-release.py` and validation evidence/reports. Application behavior and shipped feature defaults were not changed. The Python/YAML/JSON working-tree fingerprint is in `docs/verification/phase-7/source-manifest.json`; it distinguishes the tested working tree from the published commit. It is not a signed installer/build attestation or a fingerprint of all visual/client assets.

The validation command uses the existing development environment and installed Firefox:

```sh
PYTHONDONTWRITEBYTECODE=1 build/venv-macos-x86_64/bin/python scripts/validate-local-release.py --report docs/verification/phase-7/local-release-probe.json
```

It creates a temporary synthetic configuration, data store, credentials and browser profile; serves two ephemeral IPv4 loopback ports; substitutes a fixed offline provider transport; runs actual Firefox requests; then stops its browser/services and removes temporary data. It does not load the installed application's configuration, credentials, browser profile or chat history. Its additional harness pages/callbacks exist only in this temporary process and are not registered in the production app. Do not use this fixture as a deployment server.

## Pi reachability and deployed settings

Read-only TCP probes from this Mac to the user-supplied Pi address `10.0.0.17`, on ports **22** and **8765**, returned macOS `EHOSTDOWN` (errno 64). The bounded checks were repeated while local validation progressed, with the same result; see `pi-reachability.json`. No SSH session was established. No password/key/configuration was changed and no installer was run. A clarification about Pi power/network/address is pending.

The observed error does not establish whether the cause is Pi power, addressing, routing, local network restrictions or another network condition. It also proves neither an effective firewall nor absence of internet exposure. Pi OS/version, deployed application revision, effective protected mode, service bindings, reverse proxy/TLS, firewall/router forwarding, runtime libraries, resource availability, logs and data copies remain **unverified**. Older notes about its state are not evidence of its current deployment.

A separate read-only observation of this Mac's default configuration found role `agent`, configured bind `0.0.0.0:8765`, no explicit protected-mode setting, and no managed retention vault. With the current source defaults, an unset security flag selects legacy adult mode. This is a configuration-file observation, not proof that any running installed binary uses it. A current-user `lsof` check observed no listener on 8765; other users' visibility and firewall state were not established. No secrets file was opened or credential values recorded. See `mac-config-observation.json`.

## Actual browser and local end-to-end checks

The installed **Firefox 160.0.1** ran headless with a fresh temporary profile. All **25 checks** passed: 22 same-origin checks and three foreign/null-origin cases. Evidence: `local-release-probe.json` and `.log`.

| Boundary exercised | Observed result |
|---|---|
| Anonymous loopback, query token and legacy cookie | HTTP 401; no inherited parent authority. Cookies were created only in the disposable test profile. |
| Authenticated parent policy and child restrictions | Parent could inspect policy; child administration and text-preview calls returned 403; `child_chat_enabled` remained false. |
| Parent credential through a relay | HTTP 403. |
| Legacy tasks, Laith chat, local TTS and live voice | HTTP 403 in protected mode; no media/device/provider path was enabled. |
| Disclosure handling | Local supportive fallback, no provider request, no automatic notification. |
| Reviewed local arithmetic | A real restricted macOS worker calculated `2+3*4` as `14`; output required separate external review and was not automatically transmitted. |
| PII minimization and parent approval | Synthetic identity/email were absent from final fake-provider payloads. Unconfirmed sends and approval replays were denied. |
| Safe and unsafe provider answers | The safe answer was parent-only; the unsafe fake answer was buffered and replaced by local text, with child delivery disabled. |
| Registered retention deletion | Metadata-only preview, confirmed root/key/file removal, honest provider/legacy limitations and replay denial. A saved snapshot was denied restoration after deletion. |
| Foreign origin with bearer/preflight | Real browser fetch failed; the Hub observed and rejected the OPTIONS preflight with 403. |
| Foreign simple POST | Real browser fetch failed; the Hub recorded a 403 POST. |
| Sandboxed iframe with `Origin: null` | Real browser fetch failed; the Hub recorded a 403 POST. |

The probe made exactly **two fake-provider calls**, both using minimized payloads. No legacy task/transcript rows were created. The owned synthetic file was removed, old supported snapshot restore was refused, and both temporary servers stopped. Tests do not establish that every OS/browser background packet stayed on loopback; application test traffic and all model responses were explicitly local/fake. No real child data or model account was used.

These are browser-executed API/HTTP checks. They do not constitute a usable child onboarding/chat UI, a visual Laith/avatar review, real microphone/TTS playback, arbitrary DNS-rebinding validation, all browser versions, mobile/native-device testing or proof of Linux isolation. Browser origin checks complement bearer authorization; native local programs can omit Origin and must still authenticate. A stolen parent credential or compromised trusted OS account remains a separate exposure condition.

## Regression and resource evidence

The fresh full repository run passed **525 tests in 151.09 seconds** (`regression-tests.log`). This includes the prior privacy, safety, retention, revocation/failure/recovery tests and real macOS worker isolation tests. The separate browser probe is a different evidence level; its 25 checks are not additional pytest cases. Syntax parsing passed for all 13 changed/new Python files in the combined working tree, alongside tracked/new-file whitespace checks. No Pi, Windows, frozen-package or supported child-device run was obtained.

The probe records a 20-iteration offline minimizer workload of **19,000 characters**, a bounded loopback policy workload of **16 requests at concurrency four**, and the test Python process's peak RSS. Exact measurements are in `local-release-probe.json`. These measurements include the synthetic Hub/development imports and exclude Firefox/worker child-process RSS from the Python RSS value. They do not measure model inference, voice/media pipelines, loaded household capacity, real account latency, Pi memory or network TLS overhead. No supported-user/concurrency limit is inferred from them.

The 50-case hand-authored safety corpus was re-evaluated against current code: **36 true positives, 10 true negatives, two false negatives and two false positives** (`safety-corpus-review.json`). The known missed metaphor/transliteration and educational/negation overblocks remain. The corpus is developer-authored, not an independent safeguarding benchmark; passing the regression tests includes retaining these known limitations, not proving their removal. General PII/semantic risk recall and developmental/translation suitability remain unvalidated.

## Release blockers and trust boundaries

| ID | State and concrete exposure/release condition | Required evidence/remediation |
|---|---|---|
| PI-01 | Actual Pi deployment is unverified because the known ports are unreachable from this Mac. | Restore network access, then inspect OS/architecture/runtime, service revision/status/configuration, listening interfaces, firewall/proxy and available memory read-only. No deployment is implied. |
| SAFETY-01 | Known risk misses and unmeasured PII/indirect-identity recall; simple text filters can miss meaning and overblock helpful discussion. | Independent multilingual safeguarding/PII evaluation, age-band and native-language review, safe-recipient design and tested helpful fallbacks before real child use. |
| CLIENT-01 | Child/device chat is intentionally denied; current parent review APIs are not connected to a commercial child UI or a safe adult-recipient workflow. | Review and implement the approved client/auth/consent/escalation experience, then exercise actual supported clients with synthetic data. Avoid automatically disclosing abuse reports to a potentially unsafe parent. |
| LEGACY-01 | Historical conversations, plaintext retrieval/FTS, logs, task outputs and mirrors are outside the new registered vault. Default adult-mode paths retain their existing behavior. | Review explicit writer/adoption migration, map every managed copy and test synthetic deletion/late-result handling in the actual legacy adapters. Obtain separate scope before deleting real data. |
| AGENT-01 | Agent deletion is a capability/receipt contract; no real agent delete transport or filesystem verification exists. | Integrate authorized device-bound requests and acknowledgments, test offline/retry/late-result behavior on actual managed agents, and retain pending/unknown receipts honestly. |
| BACKUP-01 | Supported data-only restores honor current tombstones; an old full filesystem/control/key rollback can defeat them. Key custody/rotation and deletion-metadata retention remain operational gaps. | Prove a restore procedure that preserves/reconciles authoritative tombstones before serving data; review custody, recovery, rotation and metadata expiry rules. |
| TRANSPORT-01 | Source Hub startup uses ordinary HTTP unless an external transport layer is configured. LAN bearer tokens/content would need confidentiality; the Pi's actual proxy/network controls were not observed. | Verify an approved confidential LAN transport and supported client trust configuration, service bindings and household access controls. Local loopback HTTP in a trusted environment is not classified as a critical internet-facing vulnerability. |
| PROVIDER-01 | Technical API-format support does not establish eligible child use, zero retention or third-party deletion. | Verify provider/product/model/account arrangements and required safeguards. Keep incompatible destinations closed to child traffic. Details below. |
| ARTIFACT-01 | Phases 5–7 remain a working tree; no new reviewed deployment artifact was built or installed. Frozen worker execution also remains unsupported. | Review/publish the intended source, validate actual supported packaging/runtime and get explicit deployment/release authorization after blockers are resolved. |

There is no observed internet-facing exploit in this validation. The initial commercial boundary remains one local household Hub with no intended external inbound access. Loopback and local administrative operation are intentional. Concrete risks depend on untrusted local applications, accessible LAN bindings, leaked credentials, unmediated legacy execution or exposed components; actual Pi reachability/settings must establish those conditions. A failed network probe cannot replace that assessment.

## Provider/product eligibility checked

- The current [Gemini Developer API terms](https://ai.google.dev/gemini-api/terms) exclude API clients directed toward or likely accessed by under-18s. The reviewed Gemini transport remains an adult synthetic test capability; it is not an eligible child-product path by virtue of running on a local Hub.
- [OpenAI under-18 guidance](https://developers.openai.com/api/docs/guides/safety-checks/under-18-api-guidance) calls for additional safeguards and says processing personal data of under-13s or children below the applicable digital-consent age requires implementing API zero data retention first. No account enrollment or applicable model/feature eligibility was verified. `store: false` and heuristic sanitization are not evidence that those requirements have been met. See the separate [data-control arrangements](https://developers.openai.com/api/docs/guides/your-data).
- [Anthropic's current policy explanation](https://www.anthropic.com/policy) distinguishes its adult-only consumer Claude service from API developers serving minors, who face additional safeguards. No compliance/account review or approved child workflow was obtained. Existing-plan/CLI/consumer paths have their own product terms and remain blocked in protected mode.

These are source-backed release checks, not a legal opinion, acceptance of an agreement, account verification or a recommendation to buy another API. No supplier was contacted. Deleting local copies cannot unsend earlier requests or guarantee deletion of third-party copies outside Switchboard's control.

## Next review and remaining Phase 7 work

Local validation and this readiness assessment are complete. **Phase 7 device/release validation remains incomplete; child release is blocked.** Once Pi connectivity is available, continue with read-only device inspection and decide which synthetic tests can safely run on its existing runtime. Inspect bound ports and effective protected settings before any claim about exposure. Measure actual Pi memory/latency and prove worker namespace restrictions there; do not install missing utilities, replace the app, enable protected/child features, change firewall/router rules or run real provider trials without separate authorization.

Resolve the listed implementation/operational gaps through explicitly approved remediation scopes and repeat affected checks after changes. Review the combined Phase 5–7 diff and evidence. No new cloud/user-management phase, production data cleanup, deployment or automatic release approval follows from these passing local checks.
