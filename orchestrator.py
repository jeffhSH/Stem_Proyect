import json
import queue as _stdlib_queue

import ia_state
from ia_state import _cancelado, _client, _ts
from kill_switch import _activar_kill_switch, _es_apagate
from tts import _hablar_stem
from stt import _es_confirmacion, _transcribir_respuesta

TOOLS_CONVERSACIONALES: frozenset[str] = frozenset({"responder_en_voz"})


def _requiere_confirmacion(plan: list[dict]) -> bool:
    """Devuelve False si todos los pasos son solo conversacionales (no necesitan confirmación)."""
    return not all(p.get("accion") in TOOLS_CONVERSACIONALES for p in plan)


def _replannear(peticion_actualizada: str) -> list[dict]:
    """Genera un plan revisado con GPT a partir de petición original + corrección del usuario."""
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres Stem. El usuario quiere modificar el plan de acciones. "
                        "Genera un plan revisado en formato JSON con esta estructura exacta: "
                        '[{"paso": 1, "accion": "nombre_tool", "descripcion": "descripción breve en español"}, ...]. '
                        "Las tools disponibles son: ejecutar_accion, enviar_whatsapp, "
                        "enviar_archivo_whatsapp, buscar_y_abrir_youtube, explorar_carpeta, responder_en_voz. "
                        "enviar_archivo_whatsapp es SOLO para enviar archivos por WhatsApp a un "
                        "contacto — nunca uses esta tool para mover, copiar o guardar archivos en "
                        "una carpeta local. Eso es ejecutar_accion con shutil.move/shutil.copy. "
                        "Respondé SOLO con el JSON, sin texto extra ni backticks."
                    ),
                },
                {"role": "user", "content": peticion_actualizada},
            ],
            max_tokens=300,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        # quitar posibles backticks de markdown si GPT los incluye
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        print(f"{_ts()}[orchestrator] _replannear error: {exc}")
        return []


def _humanizar_plan(pasos: list[dict]) -> str:
    """Convierte la lista de pasos del plan en una frase natural en español vía GPT.
    Fallback: lista numerada clásica si GPT falla."""
    fallback_lineas = [f"{p.get('paso', i+1)}. {p.get('descripcion', '')}" for i, p in enumerate(pasos)]
    fallback = "Haré lo siguiente: " + ", ".join(fallback_lineas) + ". ¿Procedo?"
    try:
        pasos_texto = "\n".join(
            f"{p.get('paso', i+1)}. [{p.get('accion', '')}] {p.get('descripcion', '')}"
            for i, p in enumerate(pasos)
        )
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres Stem, un asistente de voz personal. El usuario acaba de hacer una petición "
                        "y vas a ejecutar un plan de acciones. Convierte la siguiente lista de pasos técnicos "
                        "en UNA sola frase natural en español, en primera persona, que explique lo que vas a "
                        "hacer de forma conversacional y fluida — como si se lo explicaras a un amigo, no como "
                        "si leyeras un menú. No uses números, no uses corchetes, no menciones nombres técnicos "
                        "de herramientas (ejecutar_accion, enviar_whatsapp, etc.). Si hay varios pasos similares, "
                        "agrúpalos naturalmente. Termina con una pregunta de confirmación natural, "
                        "eligiendo UNA de estas variantes (varía, no uses siempre la misma): "
                        "'¿Procedo?', '¿Te parece si sigo?', '¿Avanzo con esto?', '¿Doy luz verde?', '¿Seguimos?'. "
                        "Máximo 2-3 oraciones."
                    ),
                },
                {"role": "user", "content": pasos_texto},
            ],
            max_tokens=120,
            temperature=0.4,
        )
        resultado = resp.choices[0].message.content.strip()
        if resultado:
            return resultado
    except Exception as exc:
        print(f"{_ts()}[orchestrator] _humanizar_plan error: {exc}")
    return fallback


def _correccion_tiene_sentido(plan_actual: list[dict], correccion: str) -> bool:
    """Pregunta a GPT (max_tokens=5) si la corrección del usuario tiene sentido para ajustar el plan."""
    try:
        pasos_txt = "; ".join(
            f"{p.get('paso', i+1)}. {p.get('descripcion', '')}"
            for i, p in enumerate(plan_actual)
        )
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un validador. El usuario tiene este plan: "
                        f"[{pasos_txt}]. "
                        "¿La corrección del usuario tiene sentido como modificación al plan? "
                        "Responde SOLO 'SÍ' o 'NO'."
                    ),
                },
                {"role": "user", "content": correccion},
            ],
            max_tokens=5,
            temperature=0,
        )
        answer = resp.choices[0].message.content.strip().upper()
        return answer.startswith("SÍ") or answer.startswith("SI")
    except Exception:
        return True  # en caso de error, asumir que tiene sentido y dejar pasar


class Orchestrator:
    def __init__(self, plan: list[dict], peticion_original: str, resumen_natural: str = "") -> None:
        self.plan = plan
        self.peticion_original = peticion_original
        self.paso_actual = 0
        self.desviaciones: list[str] = []
        self.resumen_natural = resumen_natural

    def confirmar_con_usuario(self, audio_q: _stdlib_queue.Queue, rec: object) -> bool:
        # BUG 1 — si el plan es solo conversacional, no hace falta confirmar
        if not _requiere_confirmacion(self.plan):
            return True

        MAX_REFINAMIENTOS = 3
        plan_context = self.peticion_original
        silencios_consecutivos = 0   # BUG 4
        confusiones_seguidas = 0     # MEJORA 6

        for intento in range(MAX_REFINAMIENTOS):
            if _cancelado():
                return False

            if intento == 0 and self.resumen_natural:
                print(f"{_ts()}[orchestrator] usando resumen_natural del plan (sin llamada extra a GPT)")
                resumen = self.resumen_natural
            else:
                resumen = _humanizar_plan(self.plan)
            _hablar_stem(resumen)

            if _cancelado():
                return False

            if ia_state.DEBUG_TEXTO:
                respuesta_texto = input("[DEBUG] respuesta (sí / no / corrección): ").strip()
            else:
                respuesta_texto = _transcribir_respuesta(audio_q, rec)

            if _es_apagate(respuesta_texto):
                _activar_kill_switch("confirmar_con_usuario")
                _hablar_stem("Apagándome.")
                return False

            print(f"{_ts()}[orchestrator] respuesta intento {intento + 1}: '{respuesta_texto}'")

            # BUG 4 — silencios consecutivos
            if not respuesta_texto:
                silencios_consecutivos += 1
                if silencios_consecutivos >= 2:
                    _hablar_stem("Cuando quieras me decís si procedo o si querés cambiar algo.")
                else:
                    _hablar_stem("No te escuché bien, ¿procedo o cambiás algo?")
                continue
            silencios_consecutivos = 0  # reset al recibir respuesta

            if _es_confirmacion(respuesta_texto):
                return True

            # Corrección en lenguaje natural — BUG 3: validar antes de replannear
            print(f"{_ts()}[orchestrator] corrección recibida: '{respuesta_texto}'")
            if not _correccion_tiene_sentido(self.plan, respuesta_texto):
                confusiones_seguidas += 1
                print(f"{_ts()}[orchestrator] corrección no reconocida (confusión #{confusiones_seguidas})")
                # MEJORA 6 — escalar mensaje según confusiones acumuladas
                if confusiones_seguidas >= 3:
                    _hablar_stem("Está bien, avísame cuando quieras que lo haga.")
                    return False
                if confusiones_seguidas >= 2:
                    _hablar_stem(
                        "No estoy entendiendo bien qué querés cambiar. "
                        "Podés decirme de nuevo con otras palabras, o decime 'cancela' para empezar de nuevo."
                    )
                else:
                    _hablar_stem("Perdona, no entendí bien. ¿Querés que proceda con el plan o cambiás algo?")
                continue
            confusiones_seguidas = 0  # reset si la corrección tiene sentido

            plan_context = f"{plan_context}. Corrección del usuario: {respuesta_texto}"
            nuevo_plan = _replannear(plan_context)
            if nuevo_plan:
                self.plan = nuevo_plan
                print(f"{_ts()}[orchestrator] plan revisado: {len(self.plan)} paso(s)")
                for p in self.plan:
                    print(f"  {p.get('paso', '?')}. [{p.get('accion', '')}] {p.get('descripcion', '')}")
            else:
                _hablar_stem("No pude ajustar el plan, ¿procedemos con el original?")

        # MEJORA 5 — mensaje natural al agotar intentos
        _hablar_stem("Está bien, avísame cuando quieras que lo haga.")
        return False

    def autorizar(self, tool_name: str, descripcion_corta: str) -> bool:
        # explorar_carpeta es un paso de soporte transparente — nunca cuenta como desviación
        if tool_name == "explorar_carpeta":
            return True

        if self.paso_actual >= len(self.plan):
            self._registrar_desviacion(
                f"paso extra no declarado: {tool_name} — {descripcion_corta}"
            )
            self.paso_actual += 1
            return True  # permisivo en v1

        paso_esperado = self.plan[self.paso_actual]
        if tool_name != paso_esperado["accion"]:
            self._registrar_desviacion(
                f"paso {self.paso_actual + 1}: esperaba '{paso_esperado['accion']}' "
                f"pero GPT llama '{tool_name}' ({descripcion_corta})"
            )
        self.paso_actual += 1
        return True

    def _registrar_desviacion(self, msg: str) -> None:
        print(f"{_ts()}[orchestrator] ⚠ desviación: {msg}")
        self.desviaciones.append(msg)

    def reporte_final(self) -> None:
        completados = self.paso_actual
        total = len(self.plan)
        if self.desviaciones:
            print(
                f"{_ts()}[orchestrator] plan: {completados}/{total} pasos | "
                f"{len(self.desviaciones)} desviación(es):"
            )
            for d in self.desviaciones:
                print(f"  - {d}")
        else:
            print(
                f"{_ts()}[orchestrator] plan completado sin desviaciones "
                f"({completados}/{total} pasos)"
            )
