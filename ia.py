import json
import os
import queue as _stdlib_queue
import threading
import time

import ia_state
from ia_state import (
    DEBUG_TEXTO,
    _barge_in,
    _cancelado,
    _cancelar,
    _client,
    _hud_set_estado,
    _hud_set_tx,
    _interrumpir_tts,
    _ts,
    _tts_reproduciendo,
    _turno_id,
    toggle_modo_entrada,
)

import tools
from tools.context import ToolContext
from tools.filesystem import _ejecutar_con_verificacion, _get_escritorio, _pedir_accion_gpt

_MAX_INTENTOS_AGENTE = 3

_TOOLS_SYSTEM = (
    "Eres Stem, asistente de escritorio por voz en Windows 11. "
    "Usa responder_en_voz para preguntas o conversación. "
    "Usa ejecutar_accion para acciones en el PC. "
    "Usa explorar_carpeta para listar archivos antes de copiar/mover/abrir. "
    "Usa buscar_y_abrir_youtube para música o videos. "
    "Si el usuario menciona un archivo por nombre sin dar la ruta exacta, "
    "SIEMPRE usa explorar_carpeta primero para encontrarlo antes de ejecutar_accion. "
    "Cuando uses rutas de archivos en el código, usa siempre las rutas exactas "
    "que te devolvió explorar_carpeta. Usa barras dobles \\\\ o r-strings r'...' "
    "para rutas de Windows. "
    "Para envíos por WhatsApp, hacé una sola llamada a la tool correspondiente por petición. "
    "REGLA ANTI-COMBINACIONES: en enviar_archivo_whatsapp, cada item de 'envios' representa "
    "UN ÚNICO par (contacto, archivo) explícitamente pedido por el usuario. "
    "La cantidad de items debe ser IGUAL a la cantidad de asignaciones explícitas pedidas. "
    "Ejemplo CORRECTO: si el usuario dice 'envíale A.txt a Juan y B.csv a María', generá "
    "EXACTAMENTE [{contacto: Juan, archivo: A.txt}, {contacto: María, archivo: B.csv}]. "
    "NUNCA generes {contacto: Juan, archivo: B.csv} ni {contacto: María, archivo: A.txt} — "
    "eso no fue pedido. "
    "Si el usuario dice 'mándale A.txt y B.csv a Diana', SÍ generá dos items para Diana: "
    "[{contacto: Diana, archivo: A.txt}, {contacto: Diana, archivo: B.csv}] — "
    "eso SÍ fue pedido explícitamente. "
    "REGLA DE DEPENDENCIAS: antes de ejecutar una acción verificá si depende "
    "de otra aún no realizada (enviar depende de haber creado, leer depende de "
    "haber descomprimido). Si hay dependencia pendiente, resuélvela primero. "
    "REGLA DE TAREAS MÚLTIPLES: si el usuario pidió varias acciones, completar "
    "una (envío, creación, búsqueda, o cualquier otra tool) NO es señal de "
    "cierre — identificá TODAS las acciones pedidas y ejecutalas antes de "
    "responder_en_voz. Si el pedido era una sola acción, terminá al completarla. "
    "MODO CONVERSACIÓN: cuando el plan es solo responder_en_voz (sin acciones "
    "pendientes), sé proactivo, cálido y con iniciativa — mostrá interés genuino, "
    "hacé preguntas de seguimiento naturales y mantenné la conversación viva. No "
    "des respuestas cortas y neutras — eso suena a contestador automático. Si el "
    "usuario dice 'q tal' o 'cómo vas', respondé con energía y preguntá algo de "
    "vuelta. Si el usuario menciona una tarea concreta (crear, enviar, buscar algo), "
    "cambiá de modo naturalmente declarando un plan con acciones reales. "
    "CIERRE DE SESIÓN: cuando el usuario se despida ('chau', 'hasta luego', "
    "'gracias eso era todo', 'nos vemos', etc.), respondé con una despedida breve "
    "y natural usando responder_en_voz con cerrar_sesion=true. Nunca cierres "
    "abruptamente sin contestar. "
    "Siempre usa una tool, nunca respondas texto directo."
)

_TOOLS = tools.TOOL_SCHEMAS


from tts import hablar_edge, _reproducir_oracion  # noqa: E402
from stt import (  # noqa: E402
    _capturar_audio,
    _capturar_y_transcribir,
    _drenar_audio,
    _escuchar_confirmacion,
    _escuchar_confirmacion_debug,
    precargar_whisper,
    transcribir_whisper,
)
from orchestrator import Orchestrator  # noqa: E402


MAX_BLOQUEOS_GATEKEEPER = 2


def _quedan_pendientes(peticion_original: str, messages: list) -> bool:
    """Pregunta a GPT si la petición original quedó completamente satisfecha.
    Devuelve True si falta algo. Falla de forma segura (False en cualquier excepción)."""
    try:
        check_messages = [
            {
                "role": "system",
                "content": (
                    "Eres un verificador estricto. El usuario hizo esta petición:\n"
                    f"'{peticion_original}'\n\n"
                    "Basándote ÚNICAMENTE en las acciones ya ejecutadas en el historial, "
                    "¿quedó la petición COMPLETAMENTE satisfecha? "
                    "Responde SOLO 'SÍ' o 'NO'."
                ),
            }
        ] + [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=check_messages,
            max_tokens=5,
            temperature=0,
        )
        answer = resp.choices[0].message.content.strip().upper()
        return answer.startswith("NO")
    except Exception:
        return False


def _get_rutas_contexto() -> str:
    """Resuelve rutas reales del usuario via Registry (OneDrive-safe)."""
    try:
        import winreg  # noqa: PLC0415
        _key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        descargas = winreg.QueryValueEx(_key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
        documentos = winreg.QueryValueEx(_key, "Personal")[0]
    except Exception:
        descargas = os.path.join(os.path.expanduser("~"), "Downloads")
        documentos = os.path.join(os.path.expanduser("~"), "Documents")
    return (
        f"Rutas reales del sistema:\n"
        f"- Escritorio: {_get_escritorio()}\n"
        f"- Descargas: {descargas}\n"
        f"- Documentos: {documentos}\n"
        "Usa SIEMPRE estas rutas exactas como destino, nunca las inventes."
    )


def _escuchar_interrupcion(audio_q: _stdlib_queue.Queue, rec: object, mi_turno_id: int) -> None:
    """Escucha Vosk mientras TTS reproduce. Activa barge-in al detectar 2+ palabras."""
    while _turno_id[0] == mi_turno_id and not _barge_in.is_set():
        if not _tts_reproduciendo.is_set():
            time.sleep(0.05)
            continue
        try:
            data = audio_q.get(timeout=0.1)
        except _stdlib_queue.Empty:
            continue
        if rec.AcceptWaveform(data):
            texto_oido = json.loads(rec.Result()).get("text", "").strip()
        else:
            texto_oido = json.loads(rec.PartialResult()).get("partial", "").strip()
        if len(texto_oido.split()) >= 2:
            print(f"{_ts()}[ia] [INTERRUPCIÓN] barge-in detectado: '{texto_oido}'")
            _interrumpir_tts.set()
            _barge_in.set()
            return


def _ejecutar_turno(
    messages: list,
    texto: str,
    audio_q: _stdlib_queue.Queue,
    rec: object,
) -> str:
    """Loop interno de rondas GPT para un turno de la sesión.
    Modifica messages in-place. Devuelve 'continuar', 'cerrar', 'barge_in' o 'error'."""
    orchestrator: Orchestrator | None = None
    _bloqueos_gk = 0
    _tool_choice: object = {"type": "function", "function": {"name": "declarar_plan"}}
    MAX_RONDAS = 12

    _barge_in.clear()
    _turno_id[0] += 1
    threading.Thread(
        target=_escuchar_interrupcion,
        args=(audio_q, rec, _turno_id[0]),
        daemon=True,
    ).start()

    for ronda in range(MAX_RONDAS):
        if _cancelado():
            if orchestrator:
                orchestrator.reporte_final()
            return "error"

        if _barge_in.is_set():
            print(f"{_ts()}[ia] [INTERRUPCIÓN] turno abortado por barge-in")
            if orchestrator:
                orchestrator.reporte_final()
            return "barge_in"

        _hud_set_estado("procesando", ronda=ronda + 1, max_rondas=MAX_RONDAS)
        print(f"{_ts()}[ia] GPT-4o-mini ronda {ronda + 1}...")
        def _safe_msg_preview(m) -> dict:
            role    = m.get("role")    if isinstance(m, dict) else getattr(m, "role",    "?")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            return {"role": role, "content": str(content or "")[:80]}
        print(f"{_ts()}[diag] messages[-3:] = {[_safe_msg_preview(m) for m in messages[-3:]]}")
        try:
            resp = _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=_TOOLS,
                tool_choice=_tool_choice,
                max_tokens=1000,
                parallel_tool_calls=False,
            )
        except Exception as exc:
            print(f"{_ts()}[ia] error GPT: {exc}")
            _hud_set_estado("hablando")
            hablar_edge("Hubo un error al procesar tu solicitud.")
            return "error"

        if _cancelado():
            if orchestrator:
                orchestrator.reporte_final()
            return "error"

        tool_call = resp.choices[0].message.tool_calls[0]
        tool_name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            print(f"{_ts()}[ia] JSON truncado en tool_call ({tool_name}): {e}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"error": "respuesta truncada, reintentá con menos contenido"}, ensure_ascii=False),
            })
            continue
        print(f"{_ts()}[ia] tool: {tool_name}")

        messages.append(resp.choices[0].message)

        # ── FASE 1: declaración del plan ────────────────────────────────────
        if tool_name == "declarar_plan":
            plan = args.get("pasos", [])
            print(f"{_ts()}[orchestrator] plan recibido: {len(plan)} paso(s)")
            for p in plan:
                print(f"  {p.get('paso','?')}. [{p.get('accion','')}] {p.get('descripcion','')}")

            orchestrator = Orchestrator(plan, texto)

            if not orchestrator.confirmar_con_usuario(audio_q, rec):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps({"confirmado": False}, ensure_ascii=False),
                })
                _reproducir_oracion("Entendido, cancelado.")
                return "error"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(
                    {"confirmado": True, "pasos": len(plan)}, ensure_ascii=False
                ),
            })
            _tool_choice = "required"
            continue

        # Si GPT saltó declarar_plan (no debería ocurrir), crear orchestrator vacío
        if orchestrator is None:
            print(f"{_ts()}[orchestrator] advertencia: GPT saltó declarar_plan")
            orchestrator = Orchestrator([], texto)
            _tool_choice = "required"

        # ── FASE 3: ejecución vigilada ──────────────────────────────────────
        descripcion_corta = args.get("descripcion", tool_name)
        orchestrator.autorizar(tool_name, descripcion_corta)

        if tool_name == "responder_en_voz":
            respuesta = args.get("texto", "")
            cerrar = bool(args.get("cerrar_sesion", False))
            if _bloqueos_gk < MAX_BLOQUEOS_GATEKEEPER and _quedan_pendientes(texto, messages):
                _bloqueos_gk += 1
                print(
                    f"{_ts()}[ia] gatekeeper: cierre prematuro detectado "
                    f"(bloqueo {_bloqueos_gk}/{MAX_BLOQUEOS_GATEKEEPER})"
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(
                        {"pendiente": True, "instruccion": "Aún faltan acciones. Completá TODAS las acciones pedidas antes de responder_en_voz."},
                        ensure_ascii=False,
                    ),
                })
                messages.append({
                    "role": "user",
                    "content": "Faltan acciones por completar. Revisá la petición original y ejecutá las que faltan.",
                })
                continue
            orchestrator.reporte_final()
            print(f"{_ts()}[ia] respuesta: '{respuesta}' | cerrar={cerrar}")
            _hud_set_estado("hablando")
            hablar_edge(respuesta)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"entregado": True}, ensure_ascii=False),
            })
            return "cerrar" if cerrar else "continuar"

        handler = tools.TOOL_HANDLERS.get(tool_name)
        if handler is not None:
            ctx = ToolContext(audio_q=audio_q, rec=rec, orchestrator=orchestrator)
            resultado = handler(args, ctx)
            messages.append({
                "role":         "tool",
                "tool_call_id": tool_call.id,
                "content":      json.dumps(resultado, ensure_ascii=False),
            })
            continue

    if orchestrator:
        orchestrator.reporte_final()
    print(f"{_ts()}[ia] agotó rondas sin acción final")
    _hud_set_estado("hablando")
    hablar_edge("No pude completar la acción.")
    return "error"


def sesion_inteligente(audio_q: _stdlib_queue.Queue, rec: object) -> None:
    """Sesión conversacional continua: un solo historial acumulado por activación de Stem.
    GPT recibe el contexto completo en cada turno y decide naturalmente cuándo cerrar."""
    if _cancelado():
        return

    messages: list = [
        {"role": "system", "content": f"{_TOOLS_SYSTEM}\n\n{_get_rutas_contexto()}"},
    ]
    MAX_TURNOS = 20

    for turno in range(MAX_TURNOS):
        if _cancelado():
            break

        _hud_set_estado("procesando")
        print(f"{_ts()}[ia] [turno {turno + 1}/{MAX_TURNOS}] esperando input...")

        if ia_state.DEBUG_TEXTO:
            print(f"{_ts()}[ia] di tu pregunta (debug)...")
            try:
                texto = input("[DEBUG] escribe tu pregunta: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not texto:
                break
            _hud_set_tx(texto)
        else:
            print(f"{_ts()}[diag] inicio captura input turno {turno + 1}")
            print(f"{_ts()}[ia] di tu pregunta...")
            texto = _capturar_y_transcribir(audio_q, rec)
            if not texto:
                print(f"{_ts()}[ia] no se detectó texto — cerrando sesión.")
                break
            print(f"{_ts()}[ia] oído: '{texto}'")
            _hud_set_tx(texto)

        messages.append({"role": "user", "content": texto})

        resultado = _ejecutar_turno(messages, texto, audio_q, rec)

        if resultado in ("cerrar", "error"):
            break

    print(f"{_ts()}[ia] sesión finalizada.")


def activar_modo_agente(audio_q: _stdlib_queue.Queue, rec: object) -> None:
    """
    Modo agente: captura instrucción, genera código con GPT, pide confirmación,
    ejecuta y verifica. Con visión para corrección si falla.
    """
    _drenar_audio(audio_q)
    mostrar_prompt = True

    while True:
        if mostrar_prompt:
            _reproducir_oracion("Di la acción.")
            mostrar_prompt = False

        print(f"{_ts()}[agente] esperando instrucción...")
        audio_bytes = _capturar_audio(audio_q, rec)
        if not audio_bytes:
            _reproducir_oracion("No te escuché.")
            return

        print(f"{_ts()}[agente] transcribiendo...")
        texto = transcribir_whisper(audio_bytes)
        if not texto:
            _reproducir_oracion("No te escuché, intenta de nuevo.")
            mostrar_prompt = True
            continue

        print(f"{_ts()}[agente] texto: '{texto}'")
        print(f"{_ts()}[agente] generando código...")
        parsed = _pedir_accion_gpt(texto, _TOOLS_SYSTEM, _TOOLS)

        if parsed is None:
            _reproducir_oracion("No entendí, intenta de nuevo.")
            mostrar_prompt = True
            continue

        descripcion = parsed.get("descripcion", "algo")
        codigo      = parsed.get("codigo", "").strip()

        if not codigo:
            _reproducir_oracion("No pude generar el código, intenta de nuevo.")
            mostrar_prompt = True
            continue

        print(f"{_ts()}[agente] plan: {descripcion} | código: {codigo}")
        _ejecutar_con_verificacion(descripcion, codigo, audio_q, rec)
        return
