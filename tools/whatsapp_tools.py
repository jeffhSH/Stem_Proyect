from collections import Counter
from pathlib import Path

from ia_state import _hud_set_estado, _ts
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
    envios_arch = args.get("envios", [])
    _arch_counts = Counter(e.get("archivo", "") for e in envios_arch if e.get("archivo"))
    for _arch, _cnt in _arch_counts.items():
        if _cnt > 1:
            print(f"{_ts()}[ia] posible over-sending: '{Path(_arch).name}' asignado a {_cnt} contactos")
    for e in envios_arch:
        print(f"{_ts()}[ia] whatsapp archivo → {e.get('contacto', '')}: {e.get('archivo', '')}")
    ok = enviar_archivo_whatsapp(envios_arch)
    if not ok:
        _hud_set_estado("hablando")
        hablar_edge("No pude enviar uno o más archivos.")
    return {"enviado": ok}
