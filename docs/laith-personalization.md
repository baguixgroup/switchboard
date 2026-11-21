# Laith personalization: Windows, macOS and Raspberry Pi

Open **Laith → Chat settings → Personality & appearance**, or **Personalize**
above the conversation. The native companion also links to this screen from
its settings and context menu. All changes use the same authenticated hub API.
Mobile applications are on hold.

## What is implemented

- One versioned SQLite profile per hub, shared by its connected clients.
- Independent learning, personality reflection, profession, appearance, quiet
  and private-personalization controls. Learning and reflection default off.
- Ten profession presets; multiple selected roles with one explicit active role.
  Presentation is independent of profession. Roles never grant tool permissions.
- Explicit tone/reply-length preferences with correction, locking and deletion.
- Replaceable inference with a deterministic first strategy. It recognizes only
  entire direct requests such as `Please keep it brief.` or `Be detailed.`.
  Minimal/balanced/deep modes need 4/3/2 distinct dispatched messages. It never
  infers health, gender, profession or agent permissions. No inference API call.
- Inferred candidates require approval. Explicit choices always win, even if
  unlocked. A rejected candidate is suppressed; deleting a trait clears its
  evidence/candidates and suppresses relearning of that trait. Reset clears all
  suppressions and restores consent-off defaults.
- Appearance preview/approval, independently persisted presentation/outfit/
  accessory choices, and an asset manifest with a functional default fallback.
- Browser character/activity labels on Pi, Mac and Windows. Native Qt uses
  task activity states and optional bounded movement of the existing sprite.
  Quiet/private mode and local animation pause stop decorative movement.
- Native settings synchronize every five seconds. A failed settings fetch
  suppresses motion until the hub is reachable. This is eventual synchronization,
  not instant cross-device revocation.

## Architecture decisions

`Companion` owns `Personalization`, following existing Engine/service construction;
no new DI framework or provider stack. Pure policy and behavior code lives in
`switchboard/personalization.py`. The singleton `laith_profile` table stores a
versioned JSON document with atomic transactional revision checks. API writes
must supply the revision they reviewed; stale edits return HTTP 409.

Settings, preferences, profession and appearance are durable hub state. Animation
state is local to each client. Desktop coordinates, size, animation pause and model
selection remain in the existing local JSON preferences. OS-specific Qt code does
not enter the profile policy layer. The Pi runs the hub/browser UI without Qt.

Conversation preparation snapshots the effective profile. A changed profile
invalidates a pending send: prepare again rather than silently changing its
approved context. Learning consumes only a dispatched message with an authorized
snapshot and matching consent epoch; previews, history, polling and retries are
not learning signals. Reset or settings changes invalidate old consent epochs.
Inference outputs pass the trait allowlist and cannot authorize actions.

The existing provider-neutral conversation path carries validated style choices
as reference context and tells the model to apply them only to communication.
This does not grant additional agent permissions or replace tool/billing gates.

## Privacy and persistence boundaries

The profile stores normalized values, confidence, timestamps and turn identifiers,
not copies of raw conversation text. Confidence is a deterministic count-based
signal strength, not a clinical or statistical assessment of the person.

**Private personalization mode is not incognito chat.** It stops new learning,
clears pending suggestions/evidence, and disables inferred/saved personality and
profession application and adaptive appearance. Existing explicit settings and
preferences remain for later use. Switchboard still saves chats, prepared task
prompts and output files under its existing behavior; output mirroring is unchanged.
Reset deletes the current personalization profile contents, not prior chats,
task snapshots, SQLite backups or the separate explicit memory shelf.

One hub token does not identify separate people. Do not interpret this singleton
profile as safe per-user isolation on a shared multi-person hub.

## Presentation and platform limits

The bundled character is 2D artwork, not a rigged 3D model. Only the default lion
and one paw-wave clip exist. Alternate gender presentations, outfits, accessories,
facial expressions and natural walking/sleep cycles are **not implemented assets**.
Selectors persist requests and show the default lion when artwork is unavailable.
No profession outfit is silently generated. Appearance `off` uses default display;
`suggest` and `approved` both retain explicit review for any new appearance when
ask-before-change is on. Approved mode reuses saved approvals, not a blanket grant.

Behavior exposes idle, roaming, observing, listening, speaking, working, reacting,
resting and sleeping. The first renderer supports idle/working/reacting/resting,
bounded desktop roaming and static sleep state. Observing/listening/speaking are
reserved unsupported capabilities, with no screen reading, recording or TTS.
Task work preempts roaming; cancellation returns to idle or quiet rest. Movement
is a short horizontal slide every 45–90 seconds, never a claim of walking animation.

- **Windows/macOS:** transparent Qt helper and shared dashboard. Desktop options
  can open the shared personalization screen. Actual window-manager behavior
  needs platform testing. The existing Mac helper requires macOS 14.4.
- **Pi/Linux:** hub, browser settings/chat, state labels and CSS feedback. No Qt
  desktop overlay, background roaming or mobile client is claimed.
- **Mobile:** deferred; no Android/iOS packages, overlays or widgets are added.

## Validation

Use the existing pytest suite. `tests/test_personalization.py` exercises consent,
explicit precedence, confidence thresholds, approval/rejection, revision conflicts,
reset/deletion, persistence, inference validation, state policies, authentication
and real companion/mock-provider integration. Qt tests also exercise bounded
movement, quiet mode and missing animation fallback. No live paid model is required.

Build with existing platform packaging scripts on each target OS. The pure Python
wheel includes the profile module and asset manifest for all three platforms.
Windows installers and Pi binaries must be built/tested on their respective hosts;
a successful Mac build does not establish those results.

### Implementation validation (2026-11-21)

- Full regression run: **187 passed**. Subsequent focused runs: **41 passed**
  (profile/desktop) and **45 passed** (profile/companion, against later shared-tree edits).
  These counts overlap and must not be added together.
- Browser flow exercised against an isolated mock-only hub: profession persistence,
  appearance approval/fallback, explicit preference saving and locking.
- Wheel and source distribution built. Wheel profile/manifest loading was also
  checked with Qt unavailable, for the headless Pi deployment path.
- Mac x86_64 development app built, ad-hoc signed and signature verified. Frozen
  hub/API/current snapshot assets and Qt helper IPC/hide/show/exit smoke checks
  passed. Qt ran offscreen; this is not a physical display check. DMG verified.
- Fixed the common PyInstaller spec to collect package assets from the checkout,
  avoiding stale assets from an older installed build package.
- Windows installer and Pi binary builds, physical Windows/Pi runtime checks,
  production installation and live AI-provider checks were not performed.
- The Mac artifact uses a captured source snapshot. Other work continued in the
  shared checkout afterward; the artifact is a development build, not a published
  release containing every later concurrent change.

Logs, the test-profile screenshot, development DMG and Python packages are under
`Output/Laith-personalization/` in the workspace parent.
