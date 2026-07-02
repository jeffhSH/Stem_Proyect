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

`test_agente_nodos.py`, `test_diag_oversending.py`, and `test_falsopositivo.py` all originally called `ia.decidir_y_actuar(...)`, a function that no longer exists in `ia.py` (the tool loop is now `sesion_inteligente()` / `_ejecutar_turno()`, introduced with the Orchestrator).

- `test_agente_nodos.py` and `test_falsopositivo.py` have since had a compatibility shim added (`ia.decidir_y_actuar = _decidir_y_actuar_compat`, near the top of each file) that builds a one-turn `messages[]` and calls `ia._ejecutar_turno()` directly — they run and make real GPT calls (no mocking), so a full run is slow and costs API usage. Spot-checked subsets pass (3/3 and 3/4 on reduced samples as of the history-window change below); the one observed failure in `test_falsopositivo.py` was a model-level over-sending false positive unrelated to any code change, which is the kind of case that test exists to catch.
- `test_diag_oversending.py` has no such shim and still raises `AttributeError: module 'ia' has no attribute 'decidir_y_actuar'` as-is — the error is caught internally per case (script exits 0), so it looks like it "ran" but every case errors out. Don't assume this one runs; add the same shim pattern if it needs to work again.

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

Audio is amplified 2.5× with int16 clip before Vosk and before Whisper.

### Module roles

| File | Responsibility |
|------|---------------|
| `main.py` | Entry point. Loads user variants, starts the app watcher, preloads Whisper, launches the HUD, then starts daemon threads for training/IA hotkeys, ESC cancellation, and input-mode toggle before blocking on `escuchar_wake_word()`. The only file that imports `keyboard`. |
| `voz.py` | Vosk wake-word loop (`escuchar_wake_word`). Three lists: `WAKE_WORDS` (general), `WAKE_WORD_IA` (activates IA mode), `WAKE_WORDS_COMANDOS` (activates command mode). Merges `user_wakewords.json` at import. |
| `ia.py` | GPT tool loop (`_ejecutar_turno`, `sesion_inteligente`, `activar_modo_agente`), `_TOOLS_SYSTEM`, ESC cancellation event `_cancelar`. Builds `_TOOLS` from `tools.TOOL_SCHEMAS` and dispatches tool calls via `tools.TOOL_HANDLERS` — no per-tool `if tool_name == ...` blocks except `declarar_plan`/`responder_en_voz`, which have special confirmation/gatekeeper flow. Imports the pieces it needs from `ia_state.py`, `tts.py`, `stt.py`, `orchestrator.py`, `tools/`. |
| `ia_state.py` | Shared global state used across the AI modules: OpenAI client `_client`, Whisper model slot + lock, Cartesia client, TTS/barge-in `threading.Event`s, `VOICE`, `DEBUG_TEXTO` + `toggle_modo_entrada()`, and the `hud_control` shim (`_hud_set_estado`/`_hud_set_tx`). No functions beyond `toggle_modo_entrada()`. |
| `tts.py` | TTS pipeline: Edge TTS streaming (`_tts_bytes_async`, `hablar_edge`, `_hablar_edge_original`), Cartesia (`_hablar_cartesia`, `_hablar_stem`), playback with barge-in support (`_reproducir_audio`, `_reproducir_oracion`), sentence-queue worker (`_tts_worker`, `_flush_oraciones`), and `consultar_gpt`. `hablar_edge()` is the single entry point that decides Cartesia vs Edge TTS fallback. |
| `stt.py` | STT pipeline: Faster-Whisper (`_get_modelo`, `precargar_whisper`, `transcribir_whisper`), Vosk audio capture (`_capturar_audio`, `_drenar_audio`, `_capturar_y_transcribir`), voice confirmation parsing (`_escuchar_confirmacion`, `_escuchar_confirmacion_debug`, `_transcribir_respuesta`, `_es_confirmacion`, `_es_cancelacion`, `_normalizar_respuesta`). |
| `orchestrator.py` | `Orchestrator` class (plan confirmation, step authorization/deviation tracking) plus its GPT helpers: `_replannear`, `_humanizar_plan`, `_correccion_tiene_sentido`, `_requiere_confirmacion`. Flow: `declarar_plan` → `_humanizar_plan()` → `confirmar_con_usuario()` (up to 3 refinement rounds) → `autorizar()` per step → `reporte_final()`. |
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

Upcoming tools expected to follow this pattern: temporizadores (timers), compresión (archive/zip) — see `ROADMAP.md`/`context.md` for current branch status.

### GPT tool loop (`ia.py`)

- `sesion_inteligente()` runs one conversational session (up to `MAX_TURNOS=20`) with a single accumulated `messages` list; `_ejecutar_turno()` runs the multi-round tool loop for one user utterance (`MAX_RONDAS=12`)
- System message is rebuilt each session: `_TOOLS_SYSTEM` + `_get_rutas_contexto()` (real paths from Windows Registry, injected to prevent invented paths)
- `_cancelar` (`threading.Event`) is checked between every round; set by ESC key in `main.py`
- First tool call is forced to `declarar_plan` (`tool_choice={"type": "function", "function": {"name": "declarar_plan"}}`); after the Orchestrator confirms, `tool_choice` becomes `"required"`
- `_listar_carpeta()` (in `tools/filesystem.py`) returns **full absolute paths** (e.g. `C:\Users\...\OneDrive\Descargas\notas.txt`), not bare filenames — GPT must use these verbatim in generated code
- `_build_exec_ns()` (in `tools/filesystem.py`) provides the exec namespace: `subprocess`, `os`, `webbrowser`, `shutil`, `_get_escritorio`. `pyautogui` added only if available.
- A gatekeeper (`_quedan_pendientes`, max `MAX_BLOQUEOS_GATEKEEPER=2` blocks) re-asks GPT whether the original request is fully satisfied before honoring a `responder_en_voz` with `cerrar_sesion=true`, to stop premature closure on multi-step requests.
- Barge-in: `_escuchar_interrupcion()` runs in a parallel thread while TTS plays, listening via Vosk; 2+ words detected sets `_interrumpir_tts` + `_barge_in`, aborting the current turn (`_turno_id` invalidates stale listener threads).

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
