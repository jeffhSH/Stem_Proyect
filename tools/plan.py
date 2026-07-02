"""Schemas de declarar_plan y responder_en_voz.

Sin handle(): su ejecución tiene flujo especial (confirmación de plan,
gatekeeper de cierre de sesión) y queda en ia.py::_ejecutar_turno.
"""


def build_schema_declarar_plan(acciones: list[str]) -> dict:
    """Arma el schema de declarar_plan con el enum de 'accion' calculado
    dinámicamente (nombres de tools registradas + responder_en_voz)."""
    return {
        "type": "function",
        "function": {
            "name": "declarar_plan",
            "description": (
                "SIEMPRE llama esta tool PRIMERO, antes de cualquier otra acción. "
                "Declara el plan completo de acciones que vas a ejecutar para cumplir "
                "la petición del usuario. No ejecutes nada todavía — solo declara el plan "
                "para que el usuario lo confirme."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pasos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "paso": {
                                    "type": "integer",
                                    "description": "Número de paso, empezando en 1",
                                },
                                "accion": {
                                    "type": "string",
                                    "enum": acciones,
                                    "description": "Nombre de la tool que se usará para este paso.",
                                },
                                "descripcion": {
                                    "type": "string",
                                    "description": (
                                        "Descripción breve en español de qué hace este paso "
                                        "(ej: 'Crear ensayo_luna.txt en Documentos', "
                                        "'Enviar ensayo_luna.txt a mamá por WhatsApp')"
                                    ),
                                },
                            },
                            "required": ["paso", "accion", "descripcion"],
                        },
                    },
                },
                "required": ["pasos"],
            },
        },
    }


SCHEMA_RESPONDER_EN_VOZ = {
    "type": "function",
    "function": {
        "name": "responder_en_voz",
        "description": "Responde una pregunta o conversación en voz al usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {
                    "type": "string",
                    "description": "Respuesta en español, máximo 2 oraciones.",
                },
                "cerrar_sesion": {
                    "type": "boolean",
                    "description": "true solo cuando el usuario se despide y la sesión debe cerrarse.",
                },
            },
            "required": ["texto"],
        },
    },
}
