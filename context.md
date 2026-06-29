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

Pipeline modo IA — IMPLEMENTADO:

"stem"    → Vosk wake → Faster-Whisper → GPT-4o-mini streaming → Edge TTS streaming → audio
              ↓ tras respuesta
           ventana Vosk 6s: "no" → sigue en modo IA | silencio/"sí"/"eso es todo" → cierra
"comando" → Vosk wake → rapidfuzz → ejecuta directo (~0ms)

- WAKE_WORDS_COMANDOS = ["comando", "comand", "komando"]
- WAKE_WORD_IA        = stem + variantes fonológicas
- Tecla I también activa modo IA directamente
- GPT stream: acumula tokens, detecta . ? ! → envía cada oración al TTS sin esperar el fin
- TTS stream: Communicate.stream() → bytes en memoria → miniaudio.decode() → sd.play()
- Sin archivo temporal en disco

Parámetros Faster-Whisper actuales (ia.py):

- Modelo: base, device=cpu, compute_type=int8, cpu_threads=6
- beam_size=1, best_of=1, vad_filter=True (min_silence_duration_ms=300, threshold=0.5)
- word_timestamps=False, condition_on_previous_text=False
- np.ascontiguousarray(arr) antes de transcribir

Latencia medida (estable, aceptada):

- Whisper (base, int8):    ~1.5s
- GPT-4o-mini streaming:   ~0.8s al primer token
- Edge TTS streaming:      ~0.9s
- Total:                   ~3.2s

Optimizaciones aplicadas:

- GPT streaming por oraciones → bajó de 2.9s a ~0.8s ✅
- Edge TTS streaming → bajó de 1.2s a ~0.9s ✅
- small → base → mejora ~40% en Whisper ✅
- beam_size 5 → 1 → reducción adicional ✅
- vad_filter=True → evita procesar silencios ✅
- cpu_threads=6 → usa todos los núcleos del Ryzen 5 5500U ✅
- float32 → int8 → sin mejora significativa en este hardware

Pendiente Fase 2:

- Pre-cargar modelo Whisper al arranque (eliminar carga en frío ~8s primera vez)
- Loop de verificación: GPT ejecuta → pregunta si salió bien → screenshot si falla
- pyautogui + GPT-4o vision para control de PC

Instrucciones para Claude

- Respuestas directas y cortas
- Stack: Python 3.14, Windows 11, Vosk, rapidfuzz, pyautogui, PowerShell vía subprocess
- Si necesito ver código, pedir el archivo específico
- No repetir lo que ya está funcionando salvo que sea relevante
