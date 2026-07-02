# Stem — Roadmap

## Fase 1 — Comandos del sistema (COMPLETA)
Control por voz sin IA. Wake word → comando fijo → ejecución directa.

**Completado**
- Wake word detection con Vosk (español)
- Activación por voz y por tecla `|`
- Apertura de apps por nombre con caché del menú inicio y watcher
- Control de volumen y brillo (modo ajuste continuo + porcentaje directo)
- Navegación de ventanas por voz (Alt+Tab)
- Macros de medios (play/pausa/siguiente/anterior)
- Comandos de sistema: apagar pantalla, bloquear, reiniciar, apagar equipo
- Fuzzy matching con rapidfuzz para variantes fonológicas
- Gramática restringida en modo activo (rec_activo) para reducir falsos positivos
- Sistema de entrenamiento de variantes (user_variants.json, user_macros.json)
- Modo entrenamiento interactivo (tecla T)
- Refactor `_despachar()` — lógica de despacho centralizada, eliminada triplicación

**Pendiente — diferido a Fase 3**
- WiFi encender/apagar
- Bluetooth encender/apagar
- requirements.txt

---

## Fase 2 — IA + control total (EN PROGRESO)
Integración de IA para frases libres. Stem entiende lenguaje natural.

**Arquitectura del pipeline IA**
```
Vosk (wake word)
      ↓
Faster-Whisper (re-transcripción de mayor calidad)
      ↓
GPT-4o-mini tool loop (tool_choice="required", MAX_RONDAS=6)
      ↓ ejecutar_accion / explorar_carpeta / buscar_y_abrir_youtube / enviar_archivo_whatsapp
Edge TTS (responde por voz, streaming por oraciones)
```

**Completado**
- Pipeline modo IA end-to-end (Whisper → GPT → TTS streaming)
- GPT tool loop multi-ronda: ejecutar_accion encadena con `continue`, devuelve `{"exito": bool}`
- `parallel_tool_calls=False` — GPT nunca devuelve tool_calls simultáneas
- Helpers de rutas OneDrive-safe: `_get_escritorio()`, `_get_descargas()`, `_get_documentos()`
- Logging de errores en exec(): consola + `logs/agente_errores.log` (append, UTF-8)
- WhatsApp vía Selenium — envío de texto y archivos funcionando, probado en producción
  - Singleton driver, perfil dedicado Stem_WA, buscador interno sin recargar página
  - Manejo del diálogo nativo de Windows al adjuntar archivos
- Test de precisión: 50 pruebas dinámicas (2–4 nodos), resultado ~94% (47/50 exitosas)
- TTS migrado a Cartesia (voz Mateo, sonic-3.5) con fallback a Edge TTS — rama cartesia-tts mergeada
- Orchestrator: declarar_plan → humanizar → confirmar_con_usuario → autorizar por paso → reporte_final
- Barge-in: interrupción por voz mientras Stem habla, reproducción no bloqueante, threading.Event + _turno_id
- HUD visual bidireccional (Tkinter, subprocess, poll 80ms) con confirmación sí/no desde la UI
- Tool esperar_archivo_y_confirmar: watcher en background (rapidfuzz, umbral 60%) que activa el HUD al encontrar un archivo

**En progreso — 2 ramas paralelas desde main**
- Rama `hud-frontend`: input de texto en el HUD → GPT (bypasea audio), animación sin flicker (canvas.coords/itemconfig en vez de delete+recrear)
- Rama `comprimir-temporizadores`: tools comprimir_archivo/descomprimir_archivo y crear_temporizador

**Pendiente**
- Recordatorios
- Mensajes programados
- Protección contra alucinaciones/falsos positivos de GPT en el tool loop
- Pre-cargar modelo Whisper al arranque (eliminar carga en frío ~8s primera vez)
- Fix lanzar_hud(): no mata procesos HUD huérfanos antes de lanzar uno nuevo
- Unificar _escuchar_confirmacion() (legacy) con el set _PALABRAS_SI/_PALABRAS_NO del Orchestrator

---

## Fase 3 — Producción
Empaquetado y despliegue como herramienta personal.

- WiFi / Bluetooth encender-apagar (diferido desde Fase 1)
- UI de configuración
- Voz propia de Stem (personalidad definida)
- Instalador
- Todo lo anotado en el .md de notas personales
