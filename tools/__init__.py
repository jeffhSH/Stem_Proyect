"""Paquete de tools de Stem: cada módulo expone SCHEMA(S) (dict, formato
tool-calling de OpenAI) y handle(args, ctx) -> dict|list (misma firma para
todas). Este __init__ arma el registro que ia.py consume.

declarar_plan y responder_en_voz no tienen handle acá: su ejecución tiene
flujo especial (confirmación de plan, gatekeeper de cierre) y vive en
ia.py::_ejecutar_turno.
"""
from . import esperar_archivo, filesystem, plan, whatsapp_tools, youtube
from .context import ToolContext

TOOL_HANDLERS = {
    "ejecutar_accion":              filesystem.handle_ejecutar_accion,
    "explorar_carpeta":             filesystem.handle_explorar_carpeta,
    "buscar_y_abrir_youtube":       youtube.handle,
    "esperar_archivo_y_confirmar":  esperar_archivo.handle,
    "enviar_whatsapp":              whatsapp_tools.handle_enviar_whatsapp,
    "enviar_archivo_whatsapp":      whatsapp_tools.handle_enviar_archivo_whatsapp,
}

SCHEMA_DECLARAR_PLAN = plan.build_schema_declarar_plan(
    list(TOOL_HANDLERS.keys()) + ["responder_en_voz"]
)

TOOL_SCHEMAS = [
    SCHEMA_DECLARAR_PLAN,
    plan.SCHEMA_RESPONDER_EN_VOZ,
    filesystem.SCHEMA_EJECUTAR_ACCION,
    youtube.SCHEMA,
    filesystem.SCHEMA_EXPLORAR_CARPETA,
    esperar_archivo.SCHEMA,
    whatsapp_tools.SCHEMA_ENVIAR_WHATSAPP,
    whatsapp_tools.SCHEMA_ENVIAR_ARCHIVO_WHATSAPP,
]
