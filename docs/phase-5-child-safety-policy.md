# Phase 5 child response and escalation policy

Date: 24 November 2026. Base: `5c864325a870c0edeaf7c35634446474b1f0df8e`. Approved scope: Stage D of the household-local plan. Implemented for local parent review, with child access still disabled. No deployment, production configuration, real child data or live provider calls were used.

Subsequent update: the user approved [Phase 6 registered retention and deletion](phase-6-retention-and-deletion.md). This report retains the Phase 5 implementation baseline and validation; child release remains disabled.

Audit follow-up: [the Phase 5–7 review](security-audit-phase-5-7.md) preserved risk categories in overlong disclosure fallbacks and strengthened approval invalidation. Its verification supersedes earlier counts for the amended source; the limited detector still prevents child release.

## Implemented behavior

Added a portable local policy component, `switchboard/child_safety.py`, and integrated it into the existing parent-reviewed text gateway for all three configured provider formats. The component performs no network operations and needs no provider credential for local evaluation. This is a bounded lexical policy prototype, not a general semantic detector, clinical assessment or child-release approval.

When the new safety policy is enabled, the parent text gateway follows this sequence:

1. Authenticate the current parent and check the server-owned privacy and safety policy snapshots.
2. Check the complete raw prompt, supplied history and reference/retrieval/memory/tool context locally, before privacy minimization can remove disclosure cues. This includes history that will not be transmitted.
3. On a risk match or unavailable check, return a fixed local fallback without creating an outbound approval. Otherwise minimize, check the resulting texts again, validate the provider-native body and issue the existing exact-payload parent approval.
4. A valid, single-use parent approval may send only that reviewed payload to the configured fixed destination. The system message includes the locally selected age band and response language, without collecting a birth date or child identity.
5. Buffer the entire provider answer. Check the raw answer, privacy-minimized answer and final privacy validation before returning text to the authenticated parent. No partial answer is streamed or sent to speech playback.
6. A flagged or unavailable response check returns the fixed fallback instead of the model answer. A no-match answer is explicitly marked `delivery: parent_review_only`, `child_delivery_enabled: false` and warns that no match is not proof of safety.

Changing the safety policy, including disabling it, invalidates existing operations through snapshot checks before and after asynchronous work. Expired/revoked parent authority, changed provider credentials and existing privacy approval constraints still apply. Acknowledging an incident never overrides these constraints or authorizes a provider request.

Safety exceptions, malformed detector results, unsupported scripts, detector timeouts and busy detection produce a local fallback. Privacy-check failures in this review mode also produce a fallback before egress or instead of the unchecked answer. Provider/response failures never retry or fall back to a different provider. Permission failures still deny the operation. Malformed HTTP bodies, exhausted preview capacity and missing/misconfigured credentials retain the bounded error behavior rather than initiating a request.

For an input fallback, `external_transfer` is false. For a blocked answer returned by a provider it is true, because input has already left the Hub. An exceptional send reports `external_transfer: null, external_transfer_possible: true`: a failure may happen before or after transmission. No response suppression can unsend an earlier request or guarantee deletion of a provider's copies.

## Policies and local responses

| Policy area | Current behavior and limits |
|---|---|
| Age bands | Explicit `6-8`, `9-12` or `13-17`, with no automatic age inference. System instruction uses the band; returned model-text caps are 1,200 / 2,400 / 4,000 characters respectively. Overlong answers are replaced, never silently truncated. Selected puberty/reproduction topics for the youngest band get adult-supported local discussion rather than automated model handling. These choices require developmental review before release. |
| Disclosure/distress | Pattern matches for abuse, self-harm and violence return supportive local text, encourage help from a safe trusted adult, avoid blame and avoid investigating or demanding details. A safe adult need not be a parent. Immediate danger wording suggests asking a safe adult to contact local emergency services; no helpline numbers or automated reporting are added. |
| Other risk | Patterns cover sexual material, dangerous activities, secrecy/exclusive dependency, requests for private contact and medical-treatment questions. Fixed text pauses the request and encourages safe adult-supported discussion. Detection is not medical or legal advice. |
| Language | Fixed fallback templates are available in English, French, Arabic and Darija (`en`, `fr`, `ar`, `ary`). Normalization covers Unicode formatting, accents, Arabic variants and whitespace. Rules include selected Latin Darija examples. This is not comprehensive language recognition or dialect support; unrecognized Latin expressions can be missed. Other alphabetic scripts cause an unavailable-check fallback. |
| Uncertainty | A no-match is labelled as such and still needs parent review. The detector cannot reliably discover subtle meaning, distinguish quotes/negation, classify all age-sensitive topics or prove helpfulness/safety. No numeric risk score or invented confidence is reported. |

Fallbacks do not promise secrecy, tell a child to confront an alleged abuser, require parental disclosure, or claim help has been contacted. The response design draws on [NSPCC disclosure guidance](https://www.nspcc.org.uk/keeping-children-safe/reporting-abuse/what-to-do-child-reveals-abuse/): listen, reassure, avoid blame and avoid confronting the alleged abuser. The human-oversight, transparency and privacy boundaries are consistent with [UNICEF's AI and children guidance](https://www.unicef.org/innocenti/reports/policy-guidance-ai-children). These sources inform the design; they do not validate this code, its translations or clinical suitability. Independent safeguarding and native-language review remain required.

## Parent review and evidence lifecycle

The optional review queue is **off by default**. When enabled, a fallback may add a record visible only to the parent principal/session that exercised the review. The record contains an opaque random ID, fixed category names, check-unavailable flag, processing stage and creation/expiry timestamps. Its owner reference is also held locally. Categories and timing are sensitive metadata even without source content.

No raw input, answer, quote, child identity, attachment, prompt hash or freeform parent note is retained in this queue. Records stay in memory for at most 15 minutes while the service is running, with timer removal plus activity pruning. There are at most 32 records per service instance. If full, the fallback still works and reports the full queue; it does not evict other records silently or claim an escalation was delivered. `acknowledge` and `dismiss` remove a record; replay, another principal and revoked/expired authority are denied. Engine/gateway close clears all records and timers. Restart loses pending reviews: this is deliberate transient review, not a reliable safeguarding case-management service.

No notification, email, webhook, SMS, external report or background escalation is sent. Review actions do not contact anyone. There is no automatic incident snapshot. The metadata queue is not connected to an actual child session or a possibly unsafe parent. Resolving safe recipients and disclosure-specific consent is an unresolved product boundary before real child release.

No new transcript, task, output mirror, database evidence table or backup is created. Fixed operational logs do not include disclosure text or category details from this component. The existing privacy gateway's fixed event/redaction-category logging remains unchanged. Source text exists transiently during request processing and in the bounded local detector thread; timeout does not forcibly erase RAM or terminate a Python thread. Inputs are bounded to 17 text fields and 24,000 total characters, with one active detector operation per service. Existing historical storage and backups are untouched. Comprehensive managed-copy deletion remains Phase 6; third-party deletion cannot be guaranteed here.

## Configuration and API

The shipped defaults add:

```yaml
security:
  child_safety:
    enabled: false
    age_band: "9-12"
    language: en
    review_queue: false
```

Enabling the policy requires protected mode. Unknown settings, invalid bands/languages, truthy strings and an enabled queue with a disabled policy are rejected. Request bodies cannot override the policy, age band, notifications or evidence settings. No existing installation was enabled. Enabling this policy does not enable the text gateway or allow a child/device to call it; the text gateway still needs its own explicit configuration and provider credential. Local simulation needs neither.

All new routes require direct parent authentication, inherit protected origin/relay rules and no-store responses, and accept bounded JSON without reflecting validation input:

| Route | Behavior |
|---|---|
| `GET /api/safety/policy` | Report fixed local policy, version, limits and disabled child delivery/notifications. |
| `POST /api/safety/evaluate` | Supply `input` as 1–17 text strings and `candidate` as a bounded answer string. Evaluate input then candidate locally. Return a fallback or `parent_review_required`, never echo the candidate or call a provider. Mark `simulation: true, external_transfer: false`. Optional metadata queue behavior is the same as gateway review. |
| `GET /api/safety/reviews` | List the current parent's unexpired metadata records. |
| `POST /api/safety/reviews/resolve` | Exact `review_id` and `action: acknowledge` or `dismiss`; remove the record without dispatching anything. |

Normal Laith/chat, child/device, live voice, TTS/streaming and legacy execution routes remain denied in protected mode. Local-tool outputs remain parent-only; they are not automatically delivered to a child or externally transmitted. Submitting one as provider context subjects it to both input gates and the separate parent payload review. No frontend child experience, external classifier, cloud user management or platform installation was added.

## Validation and remaining blockers

The final focused safety/privacy/authorization suite passed **250 tests in 20.55 seconds**, recorded in `docs/verification/phase-5/focused-tests.log`. The full repository suite passed **492 tests in 150.26 seconds**, recorded in `docs/verification/phase-5/regression-tests.log`. These test counts overlap; do not add them. Python syntax checks passed for all seven changed/new Python files, along with tracked and new-file whitespace checks. Verification uses synthetic input, fake provider responses and HTTP ASGI clients, not actual browsers, speech playback, provider accounts or Pi hardware.

The hand-authored 50-case corpus is in `tests/fixtures/child_safety_cases.json`; content-free counts and error IDs are in `docs/verification/phase-5/corpus-results.json`. It currently yields **36 true positives, 10 true negatives, 2 false negatives and 2 false positives**. This is a small developer-reviewed lexical corpus, not an independent held-out benchmark, clinical assessment or general detection-accuracy claim. Tests deliberately retain the four known limitations so they cannot disappear from the assessment:

- Missed distress expressed as a metaphor and in an unfamiliar Darija transliteration.
- Overblocking of benign prevention education and a negated secrecy instruction.

Tests also exercise full-history/context scanning, input-before-minimization, all three provider formats, exact age-specific system/body binding, response buffering, final minimized-text checks, malformed/failed/timed-out detectors, privacy/provider failure fallbacks, no retries, policy changes, session revocation, queue ownership/capacity/expiry/removal/close, no raw evidence, disabled defaults, relay/role denial and no policy overrides.

The first new-suite run caught a policy mutation that raised an internal configuration error instead of uniform authorization denial. The guard now converts malformed/changed policy into denial; subsequent regression results supersede `docs/verification/phase-5/initial-child-safety-tests.log`.

Real child release remains blocked by limited risk/PII recall, unvalidated developmental/translation choices, unresolved safe-recipient escalation design, missing comprehensive retention/deletion, actual Pi/Linux and browser/device verification, and independent review. This phase does not relax any of those gates.

## Next gate

Review Phase 5 before authorizing Stage E / Phase 6: dry-run managed-store inventory, retention/lineage contracts and cascading deletion using synthetic data. Do not erase existing real data, install Pi utilities, enable child traffic, deploy infrastructure or implement cloud accounts as part of this phase. No commit or push is performed without a new publication instruction.
