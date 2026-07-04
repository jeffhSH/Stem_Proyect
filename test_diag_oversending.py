"""
test_diag_oversending.py — diagnóstico de over-sending en 6 casos específicos.
Solo imprime el JSON RAW de enviar_archivo_whatsapp para determinar si el problema
es el modelo generando mal el JSON, o un bug de iteración en nuestro código.

Casos de interés (i=15, 17, 21, 25, 27, 29 → tests 16, 18, 22, 26, 28, 30).
"""
import os
import sys
import queue
import time
import json as _json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
os.environ["STEM_DEBUG_TEXTO"] = "1"

import ia       # noqa: E402
import whatsapp # noqa: E402

# ── Compat shim ──────────────────────────────────────────────────────────────
# decidir_y_actuar fue reemplazado por _ejecutar_turno cuando se introdujo el
# Orchestrator (ia.py ya no lo expone). Arma un messages[] de un solo turno y
# llama al loop actual para no tocar el resto del test. Mismo patrón que
# test_agente_nodos.py.
def _decidir_y_actuar_compat(texto, audio_q, rec):
    messages = [
        {"role": "system", "content": f"{ia._TOOLS_SYSTEM}\n\n{ia._get_rutas_contexto()}"},
        {"role": "user", "content": texto},
    ]
    return ia._ejecutar_turno(messages, texto, audio_q, rec)

ia.decidir_y_actuar = _decidir_y_actuar_compat

# Orchestrator.confirmar_con_usuario() usa input() en modo debug — auto-confirmar.
import builtins  # noqa: E402
builtins.input = lambda *a, **kw: "si"

# ── Patches ─────────────────────────────────────────────────────────────────
ia._escuchar_confirmacion_debug = lambda: "si"
ia.hablar_edge         = lambda texto, *a, **kw: print(f"   [TTS] {texto}")
ia._reproducir_oracion = lambda texto: print(f"   [TTS-oracion] {texto}")

# orchestrator.confirmar_con_usuario() llama _hablar_stem() (Cartesia real) para
# el "¿procedo?" — mockear para no reproducir audio real ~7-10s por cada acción.
import orchestrator as _orch  # noqa: E402
_orch._hablar_stem = lambda texto, *a, **kw: print(f"   [TTS-confirm] {texto}")

_wa_calls: list[dict] = []

def _stub_enviar_whatsapp(envios: list[dict]) -> bool:
    for e in envios:
        _wa_calls.append({"type": "msg", "contacto": e.get("contacto", ""), "mensaje": e.get("mensaje", "")})
    return True

def _stub_enviar_archivo_whatsapp(envios: list[dict]) -> bool:
    # Schema actual (tools/whatsapp_tools.py): un item por par (contacto, archivo) —
    # 'archivo' es un string, no una lista 'archivos'. El stub previo leía la clave
    # equivocada y por eso WA-files daba 0 siempre, incluso sin el AttributeError.
    for e in envios:
        contacto = e.get("contacto", "")
        ruta = e.get("archivo", "")
        if ruta:
            _wa_calls.append({"type": "file", "contacto": contacto, "ruta": ruta})
            print(f"   [WA-FILE] {contacto}: {ruta}")
    return True

whatsapp.enviar_whatsapp         = _stub_enviar_whatsapp
whatsapp.enviar_archivo_whatsapp = _stub_enviar_archivo_whatsapp

_orig_create = ia._client.chat.completions.create
_rondas = 0

def _mock_create(*args, **kwargs):
    global _rondas
    resp = _orig_create(*args, **kwargs)
    _rondas += 1
    return resp

ia._client.chat.completions.create = _mock_create

def _mock_ejecutar(descripcion, codigo, audio_q, rec, youtube_query=None):
    if youtube_query:
        print(f"   [YT-MOCK] {youtube_query}")
    else:
        print(f"   [EXEC-MOCK] {descripcion}")
    return True

ia._ejecutar_con_verificacion = _mock_ejecutar

# ── Vocabularios (idénticos a test_agente_nodos.py) ─────────────────────────
CONTACTOS  = ["Carlos","Ana","Luis","Sofia","Miguel","Elena","Pedro","Laura","Diego","Valeria","Rodrigo","Camila","Javier","Natalia","Andres"]
EXTENSIONES = [".txt",".csv",".json",".md",".log",".xml",".cfg",".ini"]
NOMBRES    = ["reporte","resumen","agenda","datos","config","notas","plan","inventario","estadisticas","log_dia","presupuesto","contrato","acuerdos","pendientes","borrador","ideas","tareas","cronograma","objetivo","esquema","informe","memo","prioridades","guia","listado","registro","apuntes","plantilla","propuesta","analisis","minutas","checklist","seguimiento","resultados","reporte_mes"]
CONTENIDOS = ["reunión programada para las 3pm","lista de compras: leche, pan, huevos","tareas pendientes del día de hoy","sin novedades por el momento","ventas del mes: 100 unidades","objetivos de la semana en curso","resumen de la llamada con el cliente","presupuesto estimado para el proyecto","pendientes urgentes antes del viernes","cinco consejos de productividad","notas de la reunión de equipo","cronograma de actividades del mes","errores detectados en el sistema","acciones correctivas identificadas","balance financiero del trimestre","indicadores clave de desempeño","agenda del próximo sprint","retrospectiva del proyecto anterior","decisiones tomadas en la sesión","riesgos identificados y mitigaciones"]
MENSAJES_WA = ["Recuerda la reunión de mañana","¿Puedes revisar el documento que te envié?","Todo listo para el viernes","Confirma si puedes asistir","Llego en veinte minutos","El proyecto fue aprobado","Necesito tu firma antes del lunes","Te paso el resumen por aquí","Avanza con el informe, ya tengo los datos","Confirmado para las 4pm"]
CARPETAS   = ["el escritorio","descargas","documentos"]
QUERIES_YT = ["música para concentrarse","meditación guiada 10 minutos","lo mejor del jazz 2024","tutoriales de Python avanzado","noticias tecnología hoy","lofi hip hop estudio","ejercicios de respiración","cocina italiana recetas"]

def _pick(pool, i, offset=0):    return pool[(i * 7 + offset) % len(pool)]
def _pick_n(pool, i, n, offset=0):
    seen, result = set(), []
    for step in range(len(pool)):
        c = pool[(i * 7 + offset + step) % len(pool)]
        if c not in seen:
            seen.add(c); result.append(c)
        if len(result) == n: break
    return result

def generar_peticion(i: int) -> tuple[str, int]:
    nodos = 5 if i < 15 else 6
    cat   = i % 4
    noms  = _pick_n(NOMBRES,     i, 5, 0)
    exts  = _pick_n(EXTENSIONES, i, 5, 1)
    dirs  = _pick_n(CARPETAS,    i, 3, 2)
    conts = _pick_n(CONTENIDOS,  i, 5, 3)
    cons  = _pick_n(CONTACTOS,   i, 5, 4)
    msgs  = _pick_n(MENSAJES_WA, i, 4, 5)
    yt    = _pick(QUERIES_YT,    i,    6)
    d0, d1, d2 = dirs[0], dirs[1 % 3], dirs[2 % 3]

    if nodos == 6:
        if cat == 0:
            txt = (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                   f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                   f"crea {noms[2]}{exts[2]} en {d2} con '{conts[2]}', "
                   f"envíaselo {noms[0]}{exts[0]} a {cons[0]} y "
                   f"{noms[1]}{exts[1]} a {cons[1]} como archivos por WhatsApp, "
                   f"y envíale a {cons[2]} el mensaje '{msgs[0]}'")
        elif cat == 1:
            txt = (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                   f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                   f"envíaselos a {cons[0]} y {cons[1]} como archivos por WhatsApp, "
                   f"crea {noms[2]}{exts[2]} en {d2} con '{conts[2]}', "
                   f"y envíale a {cons[2]} el mensaje '{msgs[0]}'")
        elif cat == 2:
            txt = (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                   f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                   f"crea {noms[2]}{exts[2]} en {d2} con '{conts[2]}', "
                   f"y envíales mensajes por WhatsApp: a {cons[0]}: '{msgs[0]}', "
                   f"a {cons[1]}: '{msgs[1]}' y a {cons[2]}: '{msgs[2]}'")
        else:
            txt = (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                   f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                   f"envíale {noms[0]}{exts[0]} a {cons[0]} y {noms[1]}{exts[1]} a {cons[1]} "
                   f"como archivos por WhatsApp, "
                   f"y envíales también mensajes: a {cons[2]}: '{msgs[0]}' "
                   f"y a {cons[3]}: '{msgs[1]}'")
    else:
        txt = f"(5 nodos cat {cat} — no usado en este test)"
    return txt, nodos

# ── Casos de interés ─────────────────────────────────────────────────────────
CASOS = [15, 17, 21, 25, 27, 29]  # → tests 16, 18, 22, 26, 28, 30
_DUMMY_REC = type("DummyRec", (), {"Reset": lambda self: None})()
_DUMMY_Q   = queue.Queue()

# Conteo esperado de archivos/mensajes por categoría (ver generar_peticion):
# cat 0 y 1: 2 archivos (a cons[0], cons[1]) + 1 mensaje (a cons[2])
# cat 2:     0 archivos + 3 mensajes (a cons[0], cons[1], cons[2])
# cat 3:     2 archivos (a cons[0], cons[1]) + 2 mensajes (a cons[2], cons[3])
_ESPERADO_POR_CAT = {0: (2, 1), 1: (2, 1), 2: (0, 3), 3: (2, 2)}

print(f"\n{'='*70}")
print("DIAGNÓSTICO OVER-SENDING — 6 casos con JSON RAW de enviar_archivo_whatsapp")
print(f"{'='*70}\n")

_ok_count = 0

for i in CASOS:
    texto, nodos_esp = generar_peticion(i)
    _wa_calls.clear()
    _rondas = 0

    print(f"\n[Test {i+1:02d}/30] (cat={i%4}, {nodos_esp} nodos)")
    print(f"  PROMPT: {texto}")
    print(f"  {'-'*66}")

    try:
        ia.decidir_y_actuar(texto, _DUMMY_Q, _DUMMY_REC)
    except Exception as exc:
        print(f"  ERROR: {exc}")

    wa_files = [c for c in _wa_calls if c["type"] == "file"]
    wa_msgs  = [c for c in _wa_calls if c["type"] == "msg"]
    esp_files, esp_msgs = _ESPERADO_POR_CAT[i % 4]
    over_sending = len(wa_files) > esp_files or len(wa_msgs) > esp_msgs
    ok = len(wa_files) == esp_files and len(wa_msgs) == esp_msgs
    if ok:
        _ok_count += 1
    veredicto = "OK" if ok else ("OVER-SENDING" if over_sending else "FALTAN ENVIOS")
    print(f"  RESULTADO: WA-files={len(wa_files)}, WA-msgs={len(wa_msgs)}, rondas={_rondas} "
          f"(esperado: files={esp_files}, msgs={esp_msgs})")
    print(f"  VEREDICTO: {veredicto}")
    time.sleep(0.3)

print(f"\n{'='*70}")
print(f"RESUMEN: {_ok_count}/{len(CASOS)} sin over-sending ni envíos faltantes")
print(f"{'='*70}\n")
