# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the assistant

```bash
python main.py                        # normal mode
python main.py vosk-model-small-es-0.42   # explicit model path
STEM_DEBUG_TEXTO=1 python main.py     # text input instead of mic (no audio needed)
```

`app.manifest` declares `requireAdministrator`, but nothing currently embeds it into a build (no build script/spec references it — it looks prepared for a future packaged `.exe`, see `ROADMAP.md` Fase 3). Running `python main.py` directly does not get elevated automatically; if `keyboard`/`pyautogui` actions fail silently against elevated target windows, try running the terminal as Administrator.

Keys while running:
- `T` — opens training mode (records phonological variants; pauses the wake-word loop)
- `I` — manually activates IA mode (skip wake word)
- `F2` — toggles input mode between voice and text (`STEM_DEBUG_TEXTO`)
- `ESC` — cancels the current IA session mid-response

Dependencies: `requirements.txt` only lists `vosk`, `sounddevice`, `watchdog`, `keyboard`, `cartesia`. Install the rest manually:
```
pip install openai faster-whisper python-dotenv rapidfuzz pycaw pyautogui wmi pynput selenium edge-tts miniaudio numpy
```

Vosk model must be downloaded and placed at `vosk-model-small-es-0.42/` in the project root (not versioned — see `.gitignore`).

There is no lint/build/CI tooling in this repo (no `pyproject.toml`, no test runner config) — it's a set of plain scripts.

### Tests

`test_agente_nodos.py`, `test_diag_oversending.py`, and `test_falsopositivo.py` all originally called `ia.decidir_y_actuar(...)`, a function that no longer exists in `ia.py` (the tool loop is now `sesion_inteligente()` / `_ejecutar_turno()`, introduced with the Orchestrator). All three now have the same compatibility shim (`ia.decidir_y_actuar = _decidir_y_actuar_compat`, near the top of each file) that builds a one-turn `messages[]` and calls `ia._ejecutar_turno()` directly — they run and make real GPT calls (no mocking), so a full run is slow and costs API usage.

- `test_agente_nodos.py` — full run 2026-07-04: **30/30** (precision 1.00 average across all 30 cases, 0 errored, 0 round-outs, 0 gatekeeper blocks).
- `test_falsopositivo.py` — same run: Batería A **6/6**, Batería B **5/5** (**11/11** total).
- `test_diag_oversending.py` — same run: **6/6**, no over-sending. Tests 16 and 28 (2 archivos × 2 contactos each) had been flagging `OVER-SENDING` intermittently across repeated runs (3/6–5/6) — the root cause was **ambiguity in the test prompt itself**, not a tool-loop bug: "envíaselos a X y Y como archivos" is genuinely ambiguous in Spanish between "1 file each" and "both files to both". Rewrote both templates (here and the duplicated generator inside `test_diag_oversending.py`) to the explicit form "envíale A a X y B a Y como archivos por WhatsApp" — resolved to 6/6 with no residual flakiness. `_verificar_asignaciones()` (`tools/whatsapp_tools.py`) — a focused GPT-4o-mini call with no history that re-checks each (contacto, archivo) pair against the original request whenever the existing `Counter` detects a file assigned to 2+ contacts — stays in place as an extra safety net for genuinely ambiguous real user phrasing (fail-safe: keeps the original list on any parse failure).

`test_wa.py` is a standalone Selenium/WhatsApp smoke script with no `ia.py` dependency and is runnable on its own.

## Architecture

### Audio flow

```
sounddevice.RawInputStream
  → Vosk KaldiRecognizer (wake word detection)        [voz.py: escuchar_wake_word()]
      "stem"/variant    → activar_ia() → ia.sesion_inteligente()
      "comando"/variant → comandos.texto_a_comando() (fuzzy match, no GPT, ~0ms)
                           → known system/media/window command, or apps.launch(cmd) as fallback
  → Faster-Whisper transcription                      [stt.py: transcribir_whisper()]
      → ia.sesion_inteligente() — one turn per user utterance, accumulated `messages` history
          → _ejecutar_turno(): GPT-4o-mini tool_choice loop (MAX_RONDAS=12)
              declarar_plan     → Orchestrator confirms plan with user before any execution
              (registered tools)→ tools.TOOL_HANDLERS[name](args, ctx) → JSON tool result
              responder_en_voz  → gatekeeper checks nothing pending → hablar_edge() → TTS
```

Wake-word audio is amplified 2.5× with int16 clip before Vosk (`voz.py::escuchar_wake_word`). Whisper transcription uses peak normalization instead (see STT accuracy below) — the two paths are no longer symmetric.

### STT accuracy (`stt.py`/`voz.py`)

Four independent accuracy fixes, landed together:
- **Silence-based capture**: `stt.py::_capturar_audio` waits for 900ms of sustained silence after speech starts (not a fixed word/time cutoff), so multi-sentence utterances aren't truncated. `voz.py::_capturar_audio_ia` used to duplicate an older, buggier version (cut on the first Vosk final result) — it now delegates to `stt._capturar_audio` directly.
- **Peak normalization**: `transcribir_whisper` normalizes audio to 90% of peak amplitude (`arr = arr / peak * 0.9`) instead of a fixed `np.clip(arr * 2.5, -1, 1)` — avoids clipping loud audio and under-amplifying quiet audio.
- **Domain `initial_prompt`**: `stt.py::_construir_initial_prompt()` builds (and caches by `contactos.json` mtime) a short prompt with domain vocabulary (`Stem`, `WhatsApp`, `YouTube`, `escritorio`, `descargas`, `documentos`) plus contact names, passed to `transcribe(initial_prompt=...)` on every call.
- **STT-tolerance system prompt**: `ia.py::_TOOLS_SYSTEM` tells GPT the user's text comes from speech recognition and may contain phonetic/transcription errors — infer intent instead of reading literally, especially for proper names.

### Module roles

| File | Responsibility |
|------|---------------|
| `main.py` | Entry point. Loads user variants, starts the app watcher, preloads Whisper, launches the HUD, then starts daemon threads for training/IA hotkeys, ESC cancellation, and input-mode toggle before blocking on `escuchar_wake_word()`. The only file that imports `keyboard`. |
| `voz.py` | Vosk wake-word loop (`escuchar_wake_word`). Three lists: `WAKE_WORDS` (general), `WAKE_WORD_IA` (activates IA mode), `WAKE_WORDS_COMANDOS` (activates command mode). Merges `user_wakewords.json` at import. |
| `ia.py` | GPT tool loop (`_ejecutar_turno`, `sesion_inteligente`, `activar_modo_agente`), `_TOOLS_SYSTEM`, ESC cancellation event `_cancelar`. Builds `_TOOLS` from `tools.TOOL_SCHEMAS` and dispatches tool calls via `tools.TOOL_HANDLERS` — no per-tool `if tool_name == ...` blocks except `declarar_plan`/`responder_en_voz`, which have special confirmation/gatekeeper flow. Imports the pieces it needs from `ia_state.py`, `tts.py`, `stt.py`, `orchestrator.py`, `tools/`. |
| `ia_state.py` | Shared global state used across the AI modules: OpenAI client `_client`, Whisper model slot + lock, TTS/barge-in `threading.Event`s, `VOICE`, `DEBUG_TEXTO` + `toggle_modo_entrada()`, and the `hud_control` shim (`_hud_set_estado`/`_hud_set_tx`). Also owns Cartesia client construction/rotation: `_CARTESIA_KEYS` (primary `CARTESIA_API_KEY` + `CARTESIA_API_KEYS_BACKUP` list), `_construir_cartesia_client()`, `_rotar_cartesia_client()` — `tts.py` reads `ia_state._cartesia_client` live rather than importing it by value. |
| `tts.py` | TTS pipeline: Edge TTS streaming (`_tts_bytes_async`, `hablar_edge`, `_hablar_edge_original`), Cartesia (`_hablar_cartesia`, `_hablar_stem`), playback with barge-in support (`_reproducir_audio`, `_reproducir_oracion`), sentence-queue worker (`_tts_worker`, `_flush_oraciones`), and `consultar_gpt`. `hablar_edge()` is the single entry point that decides Cartesia vs Edge TTS fallback. `_hablar_cartesia` retries across all configured Cartesia keys (round-robin via `ia_state._rotar_cartesia_client()`) before falling back to Edge TTS. |
| `stt.py` | STT pipeline: Faster-Whisper (`_get_modelo`, `precargar_whisper`, `transcribir_whisper`, `_construir_initial_prompt`), Vosk audio capture (`_capturar_audio`, `_drenar_audio`, `_capturar_y_transcribir`), voice confirmation parsing (`_escuchar_confirmacion`, `_escuchar_confirmacion_debug`, `_transcribir_respuesta`, `_es_confirmacion`, `_normalizar_respuesta`). `_escuchar_confirmacion` also checks the kill switch (`kill_switch._es_apagate`) per Vosk result. |
| `orchestrator.py` | `Orchestrator` class (plan confirmation, step authorization/deviation tracking) plus its GPT helpers: `_replannear`, `_humanizar_plan`, `_correccion_tiene_sentido`, `_requiere_confirmacion`. Flow: `declarar_plan` → `_humanizar_plan()` → `confirmar_con_usuario()` (up to 3 refinement rounds) → `autorizar()` per step → `reporte_final()`. See "Plan confirmation & kill switch" below. |
| `kill_switch.py` | Global "apágate" kill switch: `_es_apagate()` (fuzzy match, threshold 85) and `_activar_kill_switch()` (sets `_cancelar`, the same event ESC uses). Checked at every point user text is captured in an active session. |
| `tools/` | Registry package: one module per tool (or per related group), each exporting a schema dict and a `handle(args, ctx) -> dict\|list` function. `tools/__init__.py` assembles `TOOL_SCHEMAS` and `TOOL_HANDLERS`. See "Adding a new tool" below. |
| `comandos.py` | Command detection: `VARIANTES` dict + fuzzy matching via `rapidfuzz`. Handles continuous adjustment modes (volume/brightness), window navigation, macros dispatch. |
| `macros.py` | Keyboard/media macro execution via `pyautogui`. Loads `user_macros.json`. |
| `apps.py` | App launcher: scans Start Menu, builds `apps_cache.json`, launches via `.lnk` / AUMID / URL. Watches filesystem for new installs with `watchdog`. |
| `training.py` | Interactive training mode: records variants for commands, media macros, and wake words. Persists to `user_variants.json`, `user_macros.json`, `user_wakewords.json`. |
| `whatsapp.py` | WhatsApp Web automation via Selenium + a dedicated Brave profile (`Stem_WA`, no QR re-scan needed). Singleton driver reused across calls. `enviar_whatsapp(envios)` / `enviar_archivo_whatsapp(envios)` take a list of `{contacto, mensaje|archivo}` dicts and return `bool`. Looks up numbers in `contactos.json`. Handles the native Windows file-attach dialog directly via `send_keys`. |
| `hud_control.py` | Public API for the visual HUD: `set_estado`/`set_transcripcion` write state atomically (tmp + rename) to a JSON file in temp dir; safe to import from any audio-pipeline module (no UI deps). `lanzar_hud()`/`cerrar_hud()` manage the HUD as a subprocess. `preguntar_hud()`/`esperar_respuesta_hud()`/`responder_pregunta_hud()` implement a yes/no confirmation channel via a second atomic JSON file. Known bug: `lanzar_hud()` doesn't kill orphaned HUD processes before launching a new one. |
| `hud_window.py` | The HUD itself (`HudWindow` class) — a separate process (not imported directly, launched via subprocess by `hud_control.py`). Tkinter always-on-top overlay, top-right corner, polls the state JSON every 80ms, animated equalizer + live transcription. |
| `traductor_acciones.py` | Translates GPT-generated Python code to Spanish for TTS confirmation. No imports from `ia.py`. |
| `config.py` | Auto-detects WiFi/Bluetooth adapter names at first run, saves `config.json`. Exposes `STEM_MEDIOS_ACTIVO`, `WIFI_ADAPTER`, `BT_ADAPTER`. |

### Adding a new tool (`tools/` registry pattern)

Each tool lives in `tools/<name>.py` (or grouped with related tools, e.g. `tools/whatsapp_tools.py`) and exports:
- `SCHEMA` (or `SCHEMA_<NAME>` if a file has more than one tool): the OpenAI tool-calling schema dict.
- `handle(args: dict, ctx: ToolContext) -> dict | list`: the tool's execution logic. `ctx` (`tools/context.py`) carries `audio_q`, `rec`, `orchestrator`, `hud_set_estado`, `hud_set_tx` — pull only what the handler needs, never the full `messages` list.

To register it, add it to `tools/__init__.py`: append the schema to `TOOL_SCHEMAS` and the handler to `TOOL_HANDLERS` (keyed by the tool's `name`). `_ejecutar_turno` needs no changes — it dispatches any name in `TOOL_HANDLERS` generically. The `declarar_plan` "accion" enum is generated from `list(TOOL_HANDLERS.keys()) + ["responder_en_voz"]`, so a newly registered tool becomes plannable automatically.

`declarar_plan` and `responder_en_voz` (`tools/plan.py`) are the only tools with no `handle()` — their execution has special confirmation/gatekeeper flow and stays in `ia.py::_ejecutar_turno`.

`comprimir_archivos`/`descomprimir_archivo` (`tools/compresion.py`) shipped following this pattern — stdlib `zipfile` only, zip-slip protection on extract. Upcoming: temporizadores (timers) — see `ROADMAP.md` for current branch status.

### GPT tool loop (`ia.py`)

- `sesion_inteligente()` runs one conversational session (up to `MAX_TURNOS=20`) with a single accumulated `messages` list; `_ejecutar_turno()` runs the multi-round tool loop for one user utterance (`MAX_RONDAS=12`)
- System message is rebuilt each session: `_TOOLS_SYSTEM` + `_get_rutas_contexto()` (real paths from Windows Registry, injected to prevent invented paths)
- `_cancelar` (`threading.Event`) is checked between every round; set by ESC key in `main.py`
- First tool call is forced to `declarar_plan` (`tool_choice={"type": "function", "function": {"name": "declarar_plan"}}`); after the Orchestrator confirms, `tool_choice` becomes `"required"`
- `_listar_carpeta()` (in `tools/filesystem.py`) returns **full absolute paths** (e.g. `C:\Users\...\OneDrive\Descargas\notas.txt`), not bare filenames — GPT must use these verbatim in generated code
- `_build_exec_ns()` (in `tools/filesystem.py`) provides the exec namespace: `subprocess`, `os`, `webbrowser`, `shutil`, `_get_escritorio`. `pyautogui` added only if available.
- A gatekeeper (`_quedan_pendientes`, max `MAX_BLOQUEOS_GATEKEEPER=2` blocks) re-asks GPT whether the original request is fully satisfied before honoring a `responder_en_voz` with `cerrar_sesion=true`, to stop premature closure on multi-step requests.
- Barge-in: `_escuchar_interrupcion()` runs in a parallel thread while TTS plays, listening via Vosk; 2+ words detected sets `_interrumpir_tts` + `_barge_in`, aborting the current turn (`_turno_id` invalidates stale listener threads).
- Gatekeeper only runs when the plan has real pending actions (`_requiere_confirmacion(plan) and paso_actual < len(plan)`) — skipped for purely conversational plans or once all steps are done, saving a GPT round.
- `declarar_plan` accepts `respuesta_directa`: for a purely conversational plan, GPT delivers the reply in the same call and `ia.py` speaks it without a second round. Farewells are excluded — they always go through the normal `cerrar_sesion` flow.
- `declarar_plan` accepts `resumen_natural`: the Orchestrator uses it on the first confirmation attempt instead of calling `_humanizar_plan()`, saving that GPT call. Later refinement rounds still call `_humanizar_plan()`.
- `_recortar_historial()` applies a sliding window over `sesion_inteligente`'s accumulated `messages`: once more than 8 recent turns accumulate, older turns are summarized into a single system message (GPT-4o-mini, 200 tokens) so per-turn latency doesn't grow unbounded in long sessions.
- `explorar_carpeta` (`tools/filesystem.py`) truncates results to 40 files to avoid inflating the rounds that follow.

### Plan confirmation & kill switch (`orchestrator.py`, `kill_switch.py`)

`Orchestrator.confirmar_con_usuario` has exactly 2 outcomes per response: confirmation (`_es_confirmacion`) or natural-language correction (validated by `_correccion_tiene_sentido`, then replanned via `_replannear`). There is no explicit "no cancels" branch anymore — `_es_cancelacion` was removed from `stt.py`; an unconfirming "no" now falls through to the correction path like any other reply.

The only 2 ways to end a session: a farewell (`responder_en_voz` with `cerrar_sesion=true`, unchanged) and the global kill switch, "apágate". `kill_switch.py::_es_apagate(texto, threshold=85)` fuzzy-matches (`rapidfuzz.fuzz.ratio`, accent-normalized via `stt._normalizar_respuesta`) against "apagate". `_activar_kill_switch()` just sets `_cancelar` (`ia_state.py`) — the same `threading.Event` ESC already uses — so the existing `_cancelado()` checks already scattered through `_ejecutar_turno`/`confirmar_con_usuario` propagate the cut with no extra logic. Checked as a hard cutoff, before any GPT/Orchestrator processing, at the 3 points where user text is captured in an active session: `sesion_inteligente` (right after capture), `Orchestrator.confirmar_con_usuario` (right after capture), `stt.py::_escuchar_confirmacion` (per Vosk result in its loop).

### Path resolution (Windows/OneDrive)

User folders may be OneDrive-redirected (e.g. `C:\Users\jeffh\OneDrive\Escritorio` not `C:\Users\jeffh\Desktop`). Always resolve via:
```python
import winreg
key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
desktop = winreg.QueryValueEx(key, "Desktop")[0]
downloads = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
documents = winreg.QueryValueEx(key, "Personal")[0]
```

### User-persisted JSON files

| File | Purpose | Modified by |
|------|---------|-------------|
| `user_variants.json` | Extra phonological variants for commands | `training.py` |
| `user_macros.json` | Extra variants for media macros | `training.py` |
| `user_wakewords.json` | Extra wake word variants | `training.py` |
| `apps_cache.json` | Discovered app paths (auto-rebuilt) | `apps.py` |
| `config.json` | WiFi/BT adapter names (auto-generated) | `config.py` |
| `contactos.json` | WhatsApp contact name → international phone number | manually maintained, gitignored (personal data) |

### Key constraints

- `keyboard` import only in `main.py` — other modules must not import it
- `traductor_acciones.py` must not import from `ia.py`
- `tools/*.py` must not import from `ia.py` (one-directional: `ia.py` depends on `tools/`, never the reverse) — same rule already applied to `ia_state.py`/`tts.py`/`stt.py`/`orchestrator.py`
- `shutil` only inside `_build_exec_ns()` in `tools/filesystem.py`, not at module level
- `_corregir_con_vision()` exists in `tools/filesystem.py` but is not called anywhere — do not delete it
- Do not touch: `transcribir_whisper`, `_get_modelo`, `precargar_whisper`, `_tts_worker`, `_flush_oraciones`, `_build_exec_ns`, `_corregir_con_vision` unless specifically asked

## Environment

`.env` in project root — loaded via `python-dotenv`:
```
OPENAI_API_KEY=...
YOUTUBE_API_KEY=...          # YouTube Data API v3
CARTESIA_API_KEY=...         # optional: TTS falls back to Edge TTS if missing
CARTESIA_API_KEYS_BACKUP=... # optional: comma-separated backup keys, rotated in when the active one is exhausted
STEM_DEBUG_TEXTO=1           # optional: skip mic, use text input
```

### Audio device setup (Windows, one-time)

Stem requests 16000 Hz from `sounddevice.RawInputStream` (`voz.py`/`training.py`) regardless of the device's native rate; per `DEPLOY.md` the input device must be set to 48000 Hz in `mmsys.cpl` → Recording → Properties → Advanced so WASAPI resamples correctly. Also set `mmsys.cpl` → Communications → "Do nothing" to stop Windows from ducking playback volume on detected speech. Bluetooth headsets switch to the HFP profile on mic use, degrading playback audio — no software fix; prefer a laptop's built-in mic or a wired/USB-dongle device. Full checklist in `DEPLOY.md`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
