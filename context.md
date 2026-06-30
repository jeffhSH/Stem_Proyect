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

Optimizaciones de descripción TTS:

- traductor_acciones.py eliminado — GPT ya genera campo `descripcion` en español en ejecutar_accion
- _ejecutar_con_verificacion ahora usa descripcion=args.get("descripcion") directo para el TTS de confirmación
- Resultado: sin regex parsing, descripción siempre en lenguaje natural correcto

WhatsApp vía Selenium — COMPLETO:

- whatsapp.py — módulo independiente con enviar_whatsapp(contacto, mensaje) y enviar_archivo_whatsapp(contacto, ruta_archivo)
- Singleton driver: una sola instancia de Brave WebDriver reutilizada entre llamadas (sin recargar la página)
- Perfil dedicado Stem_WA en AppData/Local/Brave/Profiles para mantener sesión de WhatsApp Web sin QR
- Buscador interno de WhatsApp Web: busca contacto por nombre en el input de búsqueda, sin navegar a URL con número
- Manejo del diálogo nativo de Windows "Brave quiere abrir..." al adjuntar archivos (send_keys directo al input file)
- contactos.json en raíz: dict nombre → número internacional (solo necesario para enviar_whatsapp por número)
- Tool GPT: enviar_archivo_whatsapp con parámetros contacto y ruta_archivo; retorna bool
- Probado en producción: texto y archivos funcionando end-to-end

Tool loop multi-ronda — IMPLEMENTADO:

- MAX_RONDAS=6 (subido desde 4)
- ejecutar_accion encadena con continue (antes hacía return), devuelve {"exito": bool} como tool result
- parallel_tool_calls=False en chat.completions.create para evitar tool_calls simultáneas
- Helpers de rutas en _build_exec_ns(): _get_escritorio(), _get_descargas(), _get_documentos()
  (todos leen desde winreg Shell Folders para compatibilidad con OneDrive-redirect)
- NameError en exec() → log explícito en consola + append a logs/agente_errores.log
- logs/ en .gitignore (no se versiona)

Test de precisión (test_agente_nodos.py):

- 50 pruebas generadas dinámicamente en runtime (2–4 nodos por prueba)
- Nodos posibles: crear archivo texto, abrir YouTube, enviar WhatsApp (texto y archivo)
- Resultado: ~94% precisión promedio (47/50 exitosas)
- 3 fallos: GPT invocaba _get_descargas()/_get_documentos() inexistentes → NameError → agotaban MAX_RONDAS
- Bug corregido: helpers añadidos a _build_exec_ns() en commit a03db6d

Pendiente Fase 2:

- Recordatorios
- Mensajes programados
- Comprimir/descomprimir archivos
- Indicador visual (overlay/notificación) de Stem activo
- Protección contra alucinaciones/falsos positivos de GPT en el tool loop
- Pre-cargar modelo Whisper al arranque (eliminar carga en frío ~8s primera vez)

Instrucciones para Claude

- Respuestas directas y cortas
- Stack: Python 3.14, Windows 11, Vosk, rapidfuzz, pyautogui, PowerShell vía subprocess
- Si necesito ver código, pedir el archivo específico
- No repetir lo que ya está funcionando salvo que sea relevante
