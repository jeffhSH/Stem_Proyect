import json
from collections import Counter

from ia_state import _client, _hud_set_estado, _ts
from tts import hablar_edge

SCHEMA_ENVIAR_WHATSAPP = {
    "type": "function",
    "function": {
        "name": "enviar_whatsapp",
        "description": "Envía mensajes de WhatsApp a uno o varios contactos en una sola llamada. Cada item de 'envios' tiene su propio contacto y mensaje (pueden ser iguales o distintos por persona).",
        "parameters": {
            "type": "object",
            "properties": {
                "envios": {
                    "type": "array",
                    "description": "Lista de envíos. Un item por destinatario.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "contacto": {
                                "type": "string",
                                "description": "Nombre del contacto tal como aparece en la agenda.",
                            },
                            "mensaje": {
                                "type": "string",
                                "description": "Texto del mensaje para este contacto.",
                            },
                        },
                        "required": ["contacto", "mensaje"],
                    },
                },
            },
            "required": ["envios"],
        },
    },
}

SCHEMA_ENVIAR_ARCHIVO_WHATSAPP = {
    "type": "function",
    "function": {
        "name": "enviar_archivo_whatsapp",
        "description": (
            "Envía archivos por WhatsApp. Un ítem por par exacto (contacto, archivo). "
            "SIEMPRE usa explorar_carpeta primero para obtener rutas absolutas. "
            "Cada item representa UNA asignación explícita del usuario: un archivo para un contacto. "
            "Si el usuario dijo 'A para Juan y B para María': "
            "[{contacto: Juan, archivo: A}, {contacto: María, archivo: B}] — 2 items, no 4. "
            "Si el usuario dijo 'A y B para Diana': "
            "[{contacto: Diana, archivo: A}, {contacto: Diana, archivo: B}] — 2 items, correcto."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "envios": {
                    "type": "array",
                    "description": "Lista de pares exactos (contacto, archivo). Un item por envío explícito.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "contacto": {
                                "type": "string",
                                "description": "Nombre del contacto tal como aparece en la agenda.",
                            },
                            "archivo": {
                                "type": "string",
                                "description": "Ruta absoluta exacta del archivo, obtenida de explorar_carpeta.",
                            },
                        },
                        "required": ["contacto", "archivo"],
                    },
                },
            },
            "required": ["envios"],
        },
    },
}


def _verificar_asignaciones(peticion: str, envios: list[dict]) -> list[dict]:
    """Verifica cada par (contacto, archivo) contra la petición original del usuario
    con una llamada GPT-4o-mini enfocada, sin historial. Fail-safe: ante cualquier
    fallo de parseo, o si el verificador vacía una lista que no lo estaba, conserva
    la lista ORIGINAL — nunca se pierden envíos por un fallo del verificador."""
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Sos un verificador de asignaciones. El usuario hizo esta petición: '{peticion}'. "
                        f"Se generó esta lista de envíos (contacto, archivo): {json.dumps(envios, ensure_ascii=False)}. "
                        "Verificá cada par contra lo que el usuario pidió LITERALMENTE. Si un archivo está "
                        "asignado a un contacto que el usuario NO mencionó para ese archivo, eliminá ese par. "
                        "Si todos los pares son correctos, devolvé la lista igual. Respondé SOLO con el JSON "
                        "del array corregido, sin texto extra ni backticks."
                    ),
                },
            ],
            max_tokens=300,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        corregidos = json.loads(raw)
        if not isinstance(corregidos, list) or (not corregidos and envios):
            raise ValueError("verificador devolvió una lista inválida o vacía")
    except Exception as exc:
        print(f"{_ts()}[wa] verificador falló, se usa lista original: {exc}")
        return envios

    eliminados = [e for e in envios if e not in corregidos]
    if eliminados:
        for e in eliminados:
            print(f"{_ts()}[wa] verificador eliminó par: {e.get('contacto', '')} ← {e.get('archivo', '')}")
    else:
        print(f"{_ts()}[wa] verificador: todos los pares correctos")
    return corregidos


def handle_enviar_whatsapp(args: dict, ctx) -> dict:
    from whatsapp import enviar_whatsapp  # noqa: PLC0415
    envios = args.get("envios", [])
    for e in envios:
        print(f"{_ts()}[ia] whatsapp → {e.get('contacto')}: {e.get('mensaje')}")
    ok = enviar_whatsapp(envios)
    if not ok:
        _hud_set_estado("hablando")
        hablar_edge("No pude enviar uno o más mensajes.")
    return {"enviado": ok}


def handle_enviar_archivo_whatsapp(args: dict, ctx) -> dict:
    from whatsapp import enviar_archivo_whatsapp  # noqa: PLC0415
    envios = args.get("envios", [])

    _arch_counts = Counter(e.get("archivo", "") for e in envios if e.get("archivo"))
    _hay_alerta = False
    for archivo, count in _arch_counts.items():
        if count > 1:
            _hay_alerta = True
            print(f"{_ts()}[ia] posible over-sending: {archivo} asignado a {count} contactos")

    if _hay_alerta and getattr(ctx, "peticion_original", ""):
        envios = _verificar_asignaciones(ctx.peticion_original, envios)

    for e in envios:
        print(f"{_ts()}[ia] whatsapp archivo → {e.get('contacto', '')}: {e.get('archivo', '')}")

    ok = enviar_archivo_whatsapp(envios)
    if not ok:
        _hud_set_estado("hablando")
        hablar_edge("No pude enviar uno o más archivos.")
    return {"enviado": ok}
