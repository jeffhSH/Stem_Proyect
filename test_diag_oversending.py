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

# ── Patches ─────────────────────────────────────────────────────────────────
ia._escuchar_confirmacion_debug = lambda: "si"
ia.hablar_edge         = lambda texto, *a, **kw: print(f"   [TTS] {texto}")
ia._reproducir_oracion = lambda texto: print(f"   [TTS-oracion] {texto}")

_wa_calls: list[dict] = []

def _stub_enviar_whatsapp(envios: list[dict]) -> bool:
    for e in envios:
        _wa_calls.append({"type": "msg", "contacto": e.get("contacto", ""), "mensaje": e.get("mensaje", "")})
    return True

def _stub_enviar_archivo_whatsapp(envios: list[dict]) -> bool:
    for e in envios:
        contacto = e.get("contacto", "")
        for ruta in e.get("archivos", []):
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
                   f"envíaselos a {cons[0]} y {cons[1]} como archivos, "
                   f"y envíales también mensajes: a {cons[2]}: '{msgs[0]}' "
                   f"y a {cons[3]}: '{msgs[1]}'")
    else:
        txt = f"(5 nodos cat {cat} — no usado en este test)"
    return txt, nodos

# ── Casos de interés ─────────────────────────────────────────────────────────
CASOS = [15, 17, 21, 25, 27, 29]  # → tests 16, 18, 22, 26, 28, 30
_DUMMY_REC = type("DummyRec", (), {"Reset": lambda self: None})()
_DUMMY_Q   = queue.Queue()

print(f"\n{'='*70}")
print("DIAGNÓSTICO OVER-SENDING — 6 casos con JSON RAW de enviar_archivo_whatsapp")
print(f"{'='*70}\n")

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
    print(f"  RESULTADO: WA-files={len(wa_files)}, WA-msgs={len(wa_msgs)}, rondas={_rondas}")
    time.sleep(0.3)

print(f"\n{'='*70}\n")
