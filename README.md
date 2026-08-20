# Stem — Asistente de voz con agente LLM

Asistente de escritorio para Windows 11 escrito en Python. Escucha por palabra clave, transcribe, decide qué herramientas ejecutar mediante un agente con *tool-calling* y responde con voz sintetizada.

El objetivo del proyecto no fue "conectar un LLM a un micrófono", sino resolver el problema real de estos sistemas: **que el modelo ejecute acciones que el usuario no pidió**. Buena parte de la arquitectura existe para eso.

---

## Cadena de procesamiento

```
Vosk (wake word)  →  Faster-Whisper (STT)  →  Agente GPT (tool-calling)  →  Cartesia TTS
                                                        ↓
                                              registro de herramientas
```

Con respaldo a Edge TTS si Cartesia no está disponible o se agotan las claves.

---

## Decisiones de diseño

### El modelo declara un plan; no ejecuta directamente

El ciclo del agente sigue un patrón de orquestador:

```
declarar_plan  →  humanizar  →  confirmar  →  ejecutar
```

El LLM produce una **estructura de datos verificable** con las acciones que propone. El código valida esa estructura, se la describe al usuario en lenguaje natural y solo ejecuta tras confirmación. Ninguna herramienta se invoca como efecto directo de la salida del modelo.

Esto convierte una alucinación en un plan rechazado, no en un archivo borrado o un mensaje enviado a la persona equivocada.

### Registro de herramientas

Cada módulo en `tools/` expone un `SCHEMA` (formato de tool-calling de OpenAI) y un `handle(args, ctx)` con firma uniforme. `tools/__init__.py` arma el registro que consume el agente:

```python
TOOL_HANDLERS = {
    "ejecutar_accion":             filesystem.handle_ejecutar_accion,
    "explorar_carpeta":            filesystem.handle_explorar_carpeta,
    "enviar_whatsapp":             whatsapp_tools.handle_enviar_whatsapp,
    "enviar_archivo_whatsapp":     whatsapp_tools.handle_enviar_archivo_whatsapp,
    "comprimir_archivos":          compresion.handle_comprimir_archivos,
    "descomprimir_archivo":        compresion.handle_descomprimir_archivo,
    "buscar_y_abrir_youtube":      youtube.handle,
    "esperar_archivo_y_confirmar": esperar_archivo.handle,
}
```

Agregar una herramienta es agregar un módulo. No se toca el ciclo del agente.

### Contención de falsos positivos

- **Kill switch global** por coincidencia difusa (rapidfuzz, umbral ≥85%) que corta cualquier ejecución en curso.
- **Verificación determinística de rutas** antes de cualquier operación sobre el sistema de archivos.
- **Coincidencia difusa de contactos** al 72% antes de enviar mensajes, para no escribirle a la persona equivocada por un error de transcripción.
- Cancelación inmediata con `ESC`.

### Control de costo y latencia

- Ventana deslizante de historial limitada a 8 turnos; los turnos anteriores se resumen.
- `respuesta_directa` y `resumen_natural` evitan viajes de ida y vuelta al modelo cuando no aportan nada.

---

## Pruebas

Suite de regresión sobre el ciclo del agente, no solo sobre funciones aisladas:

| Archivo | Qué cubre |
|---|---|
| `test_agente_nodos.py` | 30 casos end-to-end del ciclo de decisión |
| `test_falsopositivo.py` | Activaciones que **no** deben disparar acciones |
| `test_diag_oversending.py` | Diagnóstico de envíos duplicados |

Un caso que vale la pena mencionar: dos pruebas fallaban de forma intermitente por envíos de más. El rastreo mostró que el defecto no estaba en el código sino en la **ambigüedad del español en los prompts de prueba** — el modelo interpretaba una instrucción como dos. La corrección fue en los casos de prueba, no en el agente.

---

## Componentes

| Módulo | Función |
|---|---|
| `main.py` | Punto de entrada; hilos de escucha, HUD y atajos |
| `orchestrator.py` | Ciclo declarar → confirmar → ejecutar |
| `ia_state.py` | Estado conversacional e historial |
| `stt.py` / `voz.py` | Transcripción y detección de palabra clave |
| `tts.py` | Síntesis de voz con respaldo automático |
| `kill_switch.py` | Corte de emergencia |
| `whatsapp.py` | Automatización de WhatsApp Web vía Selenium |
| `hud_window.py` / `hud_control.py` | Indicador visual de estado |
| `tools/` | Herramientas registrables del agente |

---

## Requisitos y ejecución

**Requisitos:** Windows 11, Python 3.11+, micrófono.

```bash
pip install -r requirements.txt
```

Descargar el modelo Vosk en español ([alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)) y colocarlo como `vosk-model-small-es-0.42/` en la raíz del proyecto (no versionado por tamaño).

Crear un `.env` con:

```
OPENAI_API_KEY=
CARTESIA_API_KEY=
YOUTUBE_API_KEY=
```

`CARTESIA_API_KEY` y `YOUTUBE_API_KEY` son opcionales — sin ellas el sistema recurre a Edge TTS y deshabilita la búsqueda en YouTube.

```bash
python main.py
```

---

## Estado

En desarrollo activo. Ver `ROADMAP.md`.

Pendiente: recordatorios y mensajes programados, endurecimiento adicional contra falsos positivos, y control de dispositivos domésticos.

---

## Autor

**Jefferson Carlos Sandoval Hernández**
Ingeniería en Sistemas Informáticos — Universidad de El Salvador
jeffsandoval016@gmail.com
