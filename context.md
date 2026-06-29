Stem — Contexto rápido para Claude

Qué es

Asistente de escritorio por voz en Python (Windows 11), estilo Jarvis personal. Wake word → comando fijo → ejecución directa. Sin IA en Fase 1.

Arquitectura actual

main.py          → orquesta todo (watcher, hilos, wake loop)
voz.py           → Vosk STT, wake word, rec_activo (gramática restringida)
comandos.py      → VARIANTES dict, fuzzy matching (rapidfuzz), _despachar()
macros.py        → medios, ventana, audio, teclado, zoom — todas las hotkeys
apps.py          → launch(), caché menú inicio, watcher de cambios
config.py        → auto-detecta WiFi/BT adapter, persiste en config.json
training.py      → modo entrenamiento interactivo (tecla T)
user_variants.json / user_macros.json → variantes personalizadas del usuario


Estado Fase 1 — COMPLETA

Funcionando:

- Wake word Vosk español (vosk-model-small-es-0.42)
- Activación por voz y tecla |
- Apps: apertura por nombre con caché + watcher del menú inicio
- Volumen y brillo: modo ajuste continuo + porcentaje directo
- Navegación de ventanas (Alt+Tab por voz)
- Macros de medios (prevtrack / playpause / nexttrack)
- Sistema: apagar pantalla, reiniciar, apagar equipo
- Fuzzy matching con rapidfuzz + variantes fonológicas por acento
- Gramática restringida en rec_activo para reducir falsos positivos
- Entrenamiento de variantes interactivo (user_variants.json, user_macros.json)
- Macros de teclado: screenshots, clipboard, ventanas, zoom (modo continuo)
- Refactor: bloquear, minimizar, cambiar_ventana, mute → macros.py

Diferido a Fase 3:

- WiFi on/off — _cmd_wifi() vacío, ejecutor PowerShell pendiente
- Bluetooth on/off — igual
- requirements.txt incompleto


Fase 2 — EN PROGRESO

Router definido — dos wake words, dos modos:

"comando" → Vosk → rapidfuzz → ejecuta directo (~0ms)
"stem"    → Vosk wake → Faster-Whisper → GPT-4o-mini → Edge TTS
              ↓ tras respuesta
           ventana Vosk 6s: "no" → sigue en modo IA | silencio/"sí" → cierra

- WAKE_WORDS_COMANDOS = ["comando", "comand", "komando"]
- WAKE_WORD_IA        = stem + variantes fonológicas
- Tecla I también activa modo IA directamente
- Faster-Whisper transcribe la pregunta (no es fallback de rapidfuzz, es el STT del modo IA)
- GPT-4o-mini interpreta y responde en texto plano (≤2 oraciones)
- Edge TTS sintetiza con voz es-MX-JorgeNeural vía miniaudio + sounddevice

Faster-Whisper — probado y funcionando (whisper-test/):

- Modelo: small, device=cpu, compute_type=float32
- Fix clave: dtype="float32" en sd.InputStream + .flatten() en concatenate
- Sin normalización por pico (destruye dinámica con ruido de teclado)
- Parámetros: beam_size=5, vad_filter=False, condition_on_previous_text=False
- Archivo de prueba: C:\Users\jeffh\OneDrive\Documentos\Pythom Proyects\whisper-test\

Pendiente Fase 2:

- Integrar Faster-Whisper en voz.py como fallback del router
- GPT-4o-mini para frases libres
- Edge TTS para respuestas por voz
- Refactor: separar audio, wake, recognizer en módulos independientes (hacer junto con la integración, no antes)

Próximo paso inmediato

→ Integrar Faster-Whisper en voz.py como capa de fallback cuando rapidfuzz no hace match

Instrucciones para Claude

- Respuestas directas y cortas
- Stack: Python 3.14, Windows 11, Vosk, rapidfuzz, pyautogui, PowerShell vía subprocess
- Si necesito ver código, pedir el archivo específico
- No repetir lo que ya está funcionando salvo que sea relevante
