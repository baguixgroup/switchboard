# Audio, files and model services

Open **Audio & files** or attach a file to Laith. Describe your request, review the machine, data destination and estimate, then approve. The request and its actual files appear in Activity, the output folder and the linked conversation. Settings stay behind **Options** and **Connect service**.

## Compatibility

| Capability | Implemented transport | Required setup | Verification in this release |
|---|---|---|---|
| Chat, code, translation | Existing model router and connected CLI agents | Existing provider or plan CLI | Offline suite plus a real plan-backed Codex reply through the hub and remote agent |
| Document text and DOCX/HTML | Local or connected machine job | None | Real extraction and remote dispatch tests |
| PDF text | Local or connected machine job | pypdf included in native installers | Real parser tests and installed-app PDF extraction on Windows, macOS and Pi |
| Scanned images/PDF | Tesseract; pdftoppm for scanned PDFs | Install tools and language data on the selected machine | Real OCR on the Pi; availability depends on machine inventory |
| Transcription and meeting notes | OpenAI-compatible audio HTTP API | User-owned API account or compatible local transcription service | Simulated service contract; paid transcription not exercised |
| Speech synthesis | OpenAI-compatible speech HTTP API | Model, voice and API key, or a compatible local service | WAV validation and simulated service contract |
| Realtime API voice | Server-mediated WebRTC session with direct encrypted media | Compatible realtime API, microphone permission, localhost or HTTPS | Session, billing approval, transcript and hangup contract tests; live audio not exercised |
| Image/video generation | ComfyUI prompt/history/view APIs | Independently installed worker, models and reviewed API workflow | Recovery, polling and actual artifact validation with simulated worker |
| Media conversion | Allowlisted FFmpeg jobs | FFmpeg installed on an online machine | Real FFmpeg audio conversion on Windows; codecs depend on the installed build |
| Embeddings/local inference | Ollama embed/generate APIs | Separately installed Ollama and a compatible model | Simulated service contract |
| Other inference | JSON HTTP endpoint | Endpoint accepting `inputs` and `parameters` | Simulated service contract |
| Search and tool actions | Existing authenticated MCP gateway | Connected MCP server with appropriate tools | Existing gateway, permission and remote-agent tests |

**Deployment checks (2026-11-21):** all three installed apps reported 0.8.0. PDF extraction, OCR, audio conversion and a real Codex-backed chat completed with retrieved, hash-checked files. Plan-backed image generation also delivered a real PNG through the existing image provider. The complete working checkout, including personalization and the media playback regression, passed 213 automated tests. Paid speech/transcription/realtime services and GPU generation workers were tested with simulated endpoints, not live accounts.

**Configured** means settings and required credentials exist. A discovery check only confirms an endpoint responds. **Verified** requires successful execution and validated delivery during the current hub process. Unconfigured services remain **Setup required**. There are no preconfigured paid connections or bundled model weights.

## Connect your service

Use **Connect service**, choose its type and enter the base URL and model available on your account. OpenAI-compatible audio, speech and realtime connections use `https://api.openai.com/v1`; choose the exact model and voice your API account supports. For timestamped transcription use a segment-capable model such as `whisper-1`. Models without timestamps retain the transcript but cannot produce aligned captions. Speech output is labelled AI-generated.

Enter API keys only in the password field. Keys are saved in the hub's existing restricted `secrets.env`, never in prompts, workflows or results. A configured `OPENAI_API_KEY` can be reused for the official API. A consumer ChatGPT or Claude subscription does not imply API access. Saving a service does not create accounts, purchase access, accept terms or download models.

For a local Ollama server, use `http://localhost:11434`, enter an installed model and mark the service local. For a ComfyUI worker, enter its base URL, select image or video output and paste a reviewed **API workflow**. Tazrivo replaces only `{{prompt}}` strings. Install and license every required node and model on the worker first. Do not embed keys in workflow JSON. Cloud services require HTTPS; local network services can use HTTP on your trusted network.

For document/OCR/FFmpeg work, agents advertise tools they actually find. Install Tesseract, PDF rendering tools or FFmpeg separately, then allow the agent's inventory to refresh. Choose a machine under **Options → Connection**. On source installs, PDF text support is `python -m pip install '.[documents]'`; the standalone installers include it. Heavy model engines stay in independently installed services.

## Notes, knowledge and voice

Transcription returns text, JSON and WebVTT when segments exist. Edit meeting notes and save explicitly. Concurrent edits require reloading; a generated draft never replaces notes without your choice. **Draft with my plan model** requires an available plan/free chat model and sends the transcript to that displayed model service. **Add to Knowledge** explicitly indexes extracted text for the existing searchable library.

Live voice requests microphone access only after **Start microphone**. Review the service and billing first. Mute and End remain visible while navigating. Sessions last 1–10 minutes and have a server-side termination deadline. Available input/output transcript events are saved as a task artifact. If remote termination is unconfirmed, Activity shows interruption and the service should be checked. No hard spend cap or exact realtime billing reconciliation is claimed.

## Limits, approvals and recovery

Files and combined job inputs are limited to 20 MB; four inputs maximum, with adapter-specific limits (document reading/transcription currently take one). Jobs allow two concurrent executions and a 15-minute execution timeout. Input storage is limited to 200 files/500 MB. Long recordings, very large documents and large video exports should be split before upload. The API provides authenticated job events as SSE; the desktop relay preserves streaming. The interface polls saved status across reconnects.

Approval consumes a stored preview exactly once and expires after ten minutes. It retains the reviewed assets, configuration and destination. Cloud APIs with unknown prices require explicit unpriced-use approval and a positive estimate allowance. Estimates gate dispatch and are **not guaranteed billing caps**. Unknown actual costs stay marked unreconciled rather than being presented as free.

Restart recovery resumes only safely queued work or polling an acknowledged generation ID. Unknown in-flight outcomes become interrupted. **Check existing worker job** resumes polling without creating another generation. **Inspect and retry** creates a new reviewed request and may incur another charge. Agent journals reject duplicate executions and payload changes. Stop cancels waiting and removes a precisely identified queued render when possible; it never issues a global worker interrupt. A remote operation may continue after disconnection or cancellation.

## Dependencies and terms

The added runtime dependency is optional `pypdf` (7.1.1 used for this build), BSD-3-Clause; its complete notice ships in `packaging/licenses/pypdf.txt` and native bundles. The new adapters are Tazrivo implementation code; no external application code or model weights are vendored. Existing third-party notices remain in the package.

FFmpeg, Tesseract, PDF rendering programs, model servers, model weights, voices and custom worker nodes are installed independently and have separate terms. Record their versions and applicable licenses when deploying them. Registry entries expose operator-supplied license and hardware metadata; they do not constitute license clearance. Use service documentation and account terms for hosted access.


### Dictation and plan-backed voice chat

The chat composer includes Dictate message and Voice chat controls. A bundled CPU speech runtime
loads a checksum-verified Whisper tiny model after the user approves its one-time download. Speech
is decoded on the local agent (or hub for direct hub access), without a transcription API. Raw audio
is never written to the output store. Dictation inserts an editable draft and never sends it.

Voice chat sends recognized messages through the existing Laith preparation, spending and tool
approval flow. Device speech synthesis reads replies; microphone capture stops during transcription,
model processing and playback. Mute, End, permission errors, network errors and the session deadline
release capture resources. Model setup and transcription endpoints enforce hub authentication,
same-origin access, recording size/format and a single decoder per process.

Automated lifecycle tests use controlled audio tracks. Physical-host checks use generated speech
recordings; these do not constitute a test of the user's microphone or speaker hardware.
