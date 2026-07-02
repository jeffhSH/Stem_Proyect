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
            "Envía archivos por WhatsApp. Un ítem por CONTACTO destinatario. "
            "SIEMPRE usa explorar_carpeta primero para obtener rutas absolutas. "
            "El campo 'archivos' contiene EXACTAMENTE los archivos que ese contacto debe "
            "recibir — nunca asumas que un archivo va a más contactos de los pedidos "
            "explícitamente. "
            "Si el usuario dijo 'A para Juan y B para María': "
            "[{contacto: Juan, archivos: [A]}, {contacto: María, archivos: [B]}] — un item por contacto. "
            "Si el usuario dijo 'A y B para Diana': "
            "[{contacto: Diana, archivos: [A, B]}] — un solo item con los dos archivos."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "envios": {
                    "type": "array",
                    "description": "Lista de envíos agrupados por contacto. Un item por contacto destinatario.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "contacto": {
                                "type": "string",
                                "description": "Nombre del contacto tal como aparece en la agenda.",
                            },
                            "archivos": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Rutas absolutas exactas de los archivos que este contacto debe "
                                    "recibir, obtenidas de explorar_carpeta."
                                ),
                            },
                        },
                        "required": ["contacto", "archivos"],
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
    envios_agrupados = args.get("envios", [])

    _arch_counts = Counter(
        archivo
        for e in envios_agrupados
        for archivo in e.get("archivos", [])
        if archivo
    )
    for _arch, _cnt in _arch_counts.items():
        if _cnt > 1:
            print(f"{_ts()}[ia] posible over-sending: '{Path(_arch).name}' asignado a {_cnt} contactos")

    pares = [
        {"contacto": e.get("contacto", ""), "archivo": archivo}
        for e in envios_agrupados
        for archivo in e.get("archivos", [])
    ]
    for p in pares:
        print(f"{_ts()}[ia] whatsapp archivo → {p['contacto']}: {p['archivo']}")

    ok = enviar_archivo_whatsapp(pares)
    if not ok:
        _hud_set_estado("hablando")
        hablar_edge("No pude enviar uno o más archivos.")
    return {"enviado": ok}
