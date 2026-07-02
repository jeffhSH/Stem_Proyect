# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the assistant

```bash
python main.py                        # normal mode
python main.py vosk-model-small-es-0.42   # explicit model path
STEM_DEBUG_TEXTO=1 python main.py     # text input instead of mic (no audio needed)
python test_agente.py                 # evaluate GPT tool-calling against 5 fixed prompts
```

Keys while running:
- `T` — opens training mode (records phonological variants; pauses the wake-word loop)
- `I` — manually activates IA mode (skip wake word)
- `ESC` — cancels the current IA session mid-response

Dependencies not in `requirements.txt` (install manually):
```
pip install openai faster-whisper python-dotenv rapidfuzz pycaw pyautogui wmi pynput
```

Vosk model must be downloaded and placed at `vosk-model-small-es-0.42/` in the project root.

## Architecture

### Audio flow

```
sounddevice.RawInputStream
  → Vosk KaldiRecognizer (wake word detection)        [voz.py]
      wake word → escuchar_wake_word callback → launch() or activar_ia()
  → Faster-Whisper transcription                      [ia.py: transcribir_whisper()]
      → decidir_y_actuar(texto)                       [ia.py]
          → GPT-4o-mini tool_choice="required" loop (max 4 rounds)
              explorar_carpeta → _listar_carpeta() → GPT continues
              ejecutar_accion  → _ejecutar_con_verificacion() → exec()
              buscar_y_abrir_youtube → YouTube Data API v3 → Brave
              responder_en_voz → hablar_edge() → Edge TTS stream
```

Audio is amplified 2.5× with int16 clip before Vosk and before Whisper.

### Module roles

| File | Responsibility |
|------|---------------|
| `main.py` | Entry point. Starts ESC watcher thread, training listener thread, app watcher, then blocks on `escuchar_wake_word()`. The only file that imports `keyboard`. |
| `voz.py` | Vosk wake-word loop. Three lists: `WAKE_WORDS` (general), `WAKE_WORD_IA` (activates IA mode), `WAKE_WORDS_COMANDOS` (activates command mode). Merges `user_wakewords.json` at import. |
| `ia.py` | GPT tool loop (`_ejecutar_turno`, `sesion_inteligente`, `activar_modo_agente`), tool schemas (`_TOOLS`, `_TOOLS_SYSTEM`), action execution (`_ejecutar_con_verificacion`, `_ejecutar_silencioso`, `_build_exec_ns`), folder/path helpers, ESC cancellation event `_cancelar`. Imports the pieces it needs from `ia_state.py`, `tts.py`, `stt.py`, `orchestrator.py`. |
| `ia_state.py` | Shared global state used across the AI modules: OpenAI client `_client`, Whisper model slot + lock, Cartesia client, TTS/barge-in `threading.Event`s, `VOICE`, `DEBUG_TEXTO` + `toggle_modo_entrada()`, and the `hud_control` shim (`_hud_set_estado`/`_hud_set_tx`). No functions beyond `toggle_modo_entrada()`. |
| `tts.py` | TTS pipeline: Edge TTS streaming (`_tts_bytes_async`, `hablar_edge`, `_hablar_edge_original`), Cartesia (`_hablar_cartesia`, `_hablar_stem`), playback with barge-in support (`_reproducir_audio`, `_reproducir_oracion`), sentence-queue worker (`_tts_worker`, `_flush_oraciones`), and `consultar_gpt`. |
| `stt.py` | STT pipeline: Faster-Whisper (`_get_modelo`, `precargar_whisper`, `transcribir_whisper`), Vosk audio capture (`_capturar_audio`, `_drenar_audio`, `_capturar_y_transcribir`), voice confirmation parsing (`_escuchar_confirmacion`, `_escuchar_confirmacion_debug`, `_transcribir_respuesta`, `_es_confirmacion`, `_es_cancelacion`, `_normalizar_respuesta`). |
| `orchestrator.py` | `Orchestrator` class (plan confirmation, step authorization/deviation tracking) plus its GPT helpers: `_replannear`, `_humanizar_plan`, `_correccion_tiene_sentido`, `_requiere_confirmacion`. |
| `comandos.py` | Command detection: `VARIANTES` dict + fuzzy matching via `rapidfuzz`. Handles continuous adjustment modes (volume/brightness), window navigation, macros dispatch. |
| `macros.py` | Keyboard/media macro execution via `pyautogui`. Loads `user_macros.json`. |
| `apps.py` | App launcher: scans Start Menu, builds `apps_cache.json`, launches via `.lnk` / AUMID / URL. Watches filesystem for new installs with `watchdog`. |
| `training.py` | Interactive training mode: records variants for commands, media macros, and wake words. Persists to `user_variants.json`, `user_macros.json`, `user_wakewords.json`. |
| `traductor_acciones.py` | Translates GPT-generated Python code to Spanish for TTS confirmation. No imports from `ia.py`. |
| `config.py` | Auto-detects WiFi/Bluetooth adapter names at first run, saves `config.json`. Exposes `STEM_MEDIOS_ACTIVO`, `WIFI_ADAPTER`, `BT_ADAPTER`. |
| `test_agente.py` | Standalone evaluation: 5 fixed prompts → GPT loop → GPT self-description → static CC verdict → `test_resultados.csv`. No imports from `ia.py`. |

### GPT tool loop (`ia.py: decidir_y_actuar`)

- Called fresh each IA session with a new `messages` list
- System message is rebuilt each call: `_TOOLS_SYSTEM` + `rutas_contexto` (real paths from Windows Registry, injected to prevent invented paths)
- `_cancelar` (`threading.Event`) is checked between every round; set by ESC key in `main.py`
- `_listar_carpeta()` returns **full absolute paths** (e.g. `C:\Users\...\OneDrive\Descargas\notas.txt`), not bare filenames — GPT must use these verbatim in generated code
- `tool_choice="required"` — GPT always returns a tool call, never plain text
- `_build_exec_ns()` provides the exec namespace: `subprocess`, `os`, `webbrowser`, `shutil`, `_get_escritorio`. `pyautogui` added only if available.

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

### Key constraints

- `keyboard` import only in `main.py` — other modules must not import it
- `traductor_acciones.py` must not import from `ia.py`
- `test_agente.py` must not import from `ia.py`
- `shutil` only inside `_build_exec_ns()` in `ia.py`, not at module level
- `_corregir_con_vision()` exists in `ia.py` but is not called anywhere — do not delete it
- Do not touch: `transcribir_whisper`, `_get_modelo`, `precargar_whisper`, `_tts_worker`, `_flush_oraciones`, `_build_exec_ns`, `_corregir_con_vision` unless specifically asked

## Environment

`.env` in project root — loaded via `python-dotenv`:
```
OPENAI_API_KEY=...
YOUTUBE_API_KEY=...          # YouTube Data API v3
STEM_DEBUG_TEXTO=1           # optional: skip mic, use text input
```
