"""
test_falsopositivo.py — dos baterías:
  A) Los 6 casos de over-sending originales (tests 16,18,22,26,28,30) → deben generar
     EXACTAMENTE los pares explícitos (2 items, no 4).
  B) 5 casos de "falso over-sending" legítimo → GPT debe enviar múltiples items por
     contacto CUANDO el usuario realmente lo pidió, y el log de alerta puede dispararse
     sin bloquear nada.
"""
import os, sys, queue, time, json as _json
from pathlib import Path
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
os.environ["STEM_DEBUG_TEXTO"] = "1"

import ia, whatsapp

ia._escuchar_confirmacion_debug = lambda: "si"
ia.hablar_edge         = lambda texto, *a, **kw: print(f"   [TTS] {texto}")
ia._reproducir_oracion = lambda texto: print(f"   [TTS-oracion] {texto}")

_wa_calls: list[dict] = []
_oversend_alerts: list[str] = []

def _stub_wa_msg(envios):
    for e in envios:
        _wa_calls.append({"type":"msg","contacto":e.get("contacto",""),"msg":e.get("mensaje","")})
        print(f"   [WA-MSG] {e.get('contacto')}: {e.get('mensaje','')}")
    return True

def _stub_wa_file(envios):
    for e in envios:
        _wa_calls.append({"type":"file","contacto":e.get("contacto",""),"archivo":e.get("archivo","")})
        print(f"   [WA-FILE] {e.get('contacto')}: {e.get('archivo','')}")
    return True

whatsapp.enviar_whatsapp         = _stub_wa_msg
whatsapp.enviar_archivo_whatsapp = _stub_wa_file

_orig_create = ia._client.chat.completions.create
_rondas_g = 0
def _mock_create(*a, **kw):
    global _rondas_g
    resp = _orig_create(*a, **kw)
    _rondas_g += 1
    return resp
ia._client.chat.completions.create = _mock_create

def _mock_exec(descripcion="", codigo="", audio_q=None, rec=None, youtube_query=None):
    if youtube_query: print(f"   [YT] {youtube_query}")
    else:             print(f"   [EXEC] {descripcion}")
    return True
ia._ejecutar_con_verificacion = _mock_exec

# ── Interceptar el log de alerta de ia.py ───────────────────────────────────
# La alerta se imprime con print() directo en ia.py — la capturamos en el output.

_DUMMY_REC = type("DummyRec", (), {"Reset": lambda self: None})()
_DUMMY_Q   = queue.Queue()

def _run(label, texto, nodos_esperados, archivos_esperados, msgs_esperados):
    _wa_calls.clear()
    global _rondas_g; _rondas_g = 0
    print(f"\n{'─'*70}")
    print(f"  {label}")
    print(f"  PROMPT: {texto}")
    print(f"  ESPERADO: {archivos_esperados} archivo(s), {msgs_esperados} msg(s)")
    print()
    try:
        ia.decidir_y_actuar(texto, _DUMMY_Q, _DUMMY_REC)
    except Exception as exc:
        print(f"  ERROR: {exc}")
    files = [c for c in _wa_calls if c["type"]=="file"]
    msgs  = [c for c in _wa_calls if c["type"]=="msg"]

    # Detección de over-sending en el resultado
    arch_cnt = Counter(c["archivo"] for c in files)
    over = {a:n for a,n in arch_cnt.items() if n>1}
    ok_arch = len(files) == archivos_esperados
    ok_msgs = len(msgs)  == msgs_esperados
    veredicto = "✓ OK" if (ok_arch and ok_msgs) else f"✗ FALLO (archivos={len(files)} esperados={archivos_esperados}, msgs={len(msgs)} esperados={msgs_esperados})"
    print(f"\n  RESULTADO: {len(files)} archivo(s), {len(msgs)} msg(s), rondas={_rondas_g}")
    if over:
        print(f"  OVER-SENDING detectado: {', '.join(f'{Path(a).name}×{n}' for a,n in over.items())}")
    print(f"  VEREDICTO: {veredicto}")
    time.sleep(0.3)
    return ok_arch and ok_msgs

# ── VOCABULARIOS (igual que test_agente_nodos) ───────────────────────────────
CONTACTOS  = ["Carlos","Ana","Luis","Sofia","Miguel","Elena","Pedro","Laura","Diego","Valeria","Rodrigo","Camila","Javier","Natalia","Andres"]
EXTENSIONES= [".txt",".csv",".json",".md",".log",".xml",".cfg",".ini"]
NOMBRES    = ["reporte","resumen","agenda","datos","config","notas","plan","inventario","estadisticas","log_dia","presupuesto","contrato","acuerdos","pendientes","borrador","ideas","tareas","cronograma","objetivo","esquema","informe","memo","prioridades","guia","listado","registro","apuntes","plantilla","propuesta","analisis","minutas","checklist","seguimiento","resultados","reporte_mes"]
CONTENIDOS = ["reunión programada para las 3pm","lista de compras: leche, pan, huevos","tareas pendientes del día de hoy","sin novedades por el momento","ventas del mes: 100 unidades","objetivos de la semana en curso","resumen de la llamada con el cliente","presupuesto estimado para el proyecto","pendientes urgentes antes del viernes","cinco consejos de productividad","notas de la reunión de equipo","cronograma de actividades del mes","errores detectados en el sistema","acciones correctivas identificadas","balance financiero del trimestre","indicadores clave de desempeño","agenda del próximo sprint","retrospectiva del proyecto anterior","decisiones tomadas en la sesión","riesgos identificados y mitigaciones"]
MENSAJES_WA= ["Recuerda la reunión de mañana","¿Puedes revisar el documento que te envié?","Todo listo para el viernes","Confirma si puedes asistir","Llego en veinte minutos","El proyecto fue aprobado","Necesito tu firma antes del lunes","Te paso el resumen por aquí","Avanza con el informe, ya tengo los datos","Confirmado para las 4pm"]
CARPETAS   = ["el escritorio","descargas","documentos"]
QUERIES_YT = ["música para concentrarse","meditación guiada 10 minutos","lo mejor del jazz 2024","tutoriales de Python avanzado","noticias tecnología hoy","lofi hip hop estudio","ejercicios de respiración","cocina italiana recetas"]

def _p(pool,i,off=0): return pool[(i*7+off)%len(pool)]
def _pn(pool,i,n,off=0):
    seen,res=[],[]
    for s in range(len(pool)):
        c=pool[(i*7+off+s)%len(pool)]
        if c not in seen: seen.append(c); res.append(c)
        if len(res)==n: break
    return res

def _prompt_original(i):
    noms=_pn(NOMBRES,i,5,0); exts=_pn(EXTENSIONES,i,5,1)
    dirs=_pn(CARPETAS,i,3,2); cons=_pn(CONTACTOS,i,5,4)
    msgs=_pn(MENSAJES_WA,i,4,5); conts=_pn(CONTENIDOS,i,5,3)
    d0,d1,d2=dirs[0],dirs[1%3],dirs[2%3]
    cat=i%4
    if cat==0:
        return (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                f"crea {noms[2]}{exts[2]} en {d2} con '{conts[2]}', "
                f"envíaselo {noms[0]}{exts[0]} a {cons[0]} y "
                f"{noms[1]}{exts[1]} a {cons[1]} como archivos por WhatsApp, "
                f"y envíale a {cons[2]} el mensaje '{msgs[0]}'")
    elif cat==1:
        return (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                f"envíaselos a {cons[0]} y {cons[1]} como archivos por WhatsApp, "
                f"crea {noms[2]}{exts[2]} en {d2} con '{conts[2]}', "
                f"y envíale a {cons[2]} el mensaje '{msgs[0]}'")
    elif cat==2:
        return (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                f"crea {noms[2]}{exts[2]} en {d2} con '{conts[2]}', "
                f"y envíales mensajes por WhatsApp: a {cons[0]}: '{msgs[0]}', "
                f"a {cons[1]}: '{msgs[1]}' y a {cons[2]}: '{msgs[2]}'")
    else:
        return (f"Crea {noms[0]}{exts[0]} en {d0} con '{conts[0]}', "
                f"crea {noms[1]}{exts[1]} en {d1} con '{conts[1]}', "
                f"envíaselos a {cons[0]} y {cons[1]} como archivos, "
                f"y envíales también mensajes: a {cons[2]}: '{msgs[0]}' "
                f"y a {cons[3]}: '{msgs[1]}'")


print(f"\n{'='*70}")
print("BATERÍA A — 6 CASOS ORIGINALES (deben generar pares exactos, no cross-product)")
print(f"{'='*70}")

resultados_a = []
# Test 16 (i=15, cat=3): envíaselos a Miguel y Elena → 2 archivos distintos
resultados_a.append(_run("Test 16 (cat=3) — 2 contactos, 2 archivos distintos",
    _prompt_original(15), 6, archivos_esperados=2, msgs_esperados=2))

# Test 18 (i=17, cat=1): envíaselos a Sofia y Miguel → 2 archivos distintos
resultados_a.append(_run("Test 18 (cat=1) — 2 contactos, 2 archivos distintos",
    _prompt_original(17), 6, archivos_esperados=2, msgs_esperados=1))

# Test 22 (i=21, cat=1)
resultados_a.append(_run("Test 22 (cat=1) — 2 contactos, 2 archivos distintos",
    _prompt_original(21), 6, archivos_esperados=2, msgs_esperados=1))

# Test 26 (i=25, cat=1)
resultados_a.append(_run("Test 26 (cat=1) — 2 contactos, 2 archivos distintos",
    _prompt_original(25), 6, archivos_esperados=2, msgs_esperados=1))

# Test 28 (i=27, cat=3)
resultados_a.append(_run("Test 28 (cat=3) — 2 contactos, 2 archivos distintos",
    _prompt_original(27), 6, archivos_esperados=2, msgs_esperados=2))

# Test 30 (i=29, cat=1)
resultados_a.append(_run("Test 30 (cat=1) — 2 contactos, 2 archivos distintos",
    _prompt_original(29), 6, archivos_esperados=2, msgs_esperados=1))

ok_a = sum(resultados_a)
print(f"\n  Batería A: {ok_a}/6 correctos")


print(f"\n{'='*70}")
print("BATERÍA B — FALSOS POSITIVOS LEGÍTIMOS (múltiples envíos por persona)")
print(f"{'='*70}")

resultados_b = []

# B1: misma persona recibe 1 archivo + 1 mensaje (distintas tools)
resultados_b.append(_run(
    "B1 — Diana: 1 archivo + 1 mensaje (distintas tools)",
    "Crea reporte.txt en descargas con 'datos del trimestre', "
    "envíaselo a Diana por WhatsApp, "
    "y también envíale a Diana el mensaje '¿Puedes revisarlo?'",
    3, archivos_esperados=1, msgs_esperados=1))

# B2: misma persona recibe 2 archivos (explícitamente pedido)
resultados_b.append(_run(
    "B2 — Diana: 2 archivos distintos explícitos",
    "Crea reporte.txt en descargas con 'resumen ejecutivo', "
    "crea notas.csv en documentos con 'indicadores clave', "
    "y envíaselos los dos a Diana por WhatsApp",
    4, archivos_esperados=2, msgs_esperados=0))

# B3: 2 personas reciben 2 archivos CADA UNA (cross-product EXPLÍCITO)
resultados_b.append(_run(
    "B3 — Diana y Alicia reciben AMBOS archivos (pedido explícito)",
    "Crea reporte.txt en descargas con 'resumen ejecutivo', "
    "crea notas.csv en documentos con 'indicadores'. "
    "Envíale a Diana reporte.txt y notas.csv. "
    "También envíale a Alicia reporte.txt y notas.csv.",
    6, archivos_esperados=4, msgs_esperados=0))

# B4: archivo a una persona, mensaje a otra (sin cruce)
resultados_b.append(_run(
    "B4 — archivo a Diana, mensaje a Alicia (sin cruce)",
    "Crea resumen.txt en descargas con 'puntos clave', "
    "mándaselo a Diana por WhatsApp, "
    "y mándale a Alicia el mensaje 'Ya está listo'",
    3, archivos_esperados=1, msgs_esperados=1))

# B5: 1 archivo a cada uno de 3 contactos (3 pares distintos)
resultados_b.append(_run(
    "B5 — 1 archivo distinto a cada uno de 3 contactos",
    "Crea plan.txt en descargas con 'agenda del sprint', "
    "crea resumen.csv en documentos con 'métricas', "
    "crea notas.md en el escritorio con 'pendientes'. "
    "Envíale plan.txt a Carlos, resumen.csv a Ana y notas.md a Luis por WhatsApp.",
    6, archivos_esperados=3, msgs_esperados=0))

ok_b = sum(resultados_b)
print(f"\n  Batería B: {ok_b}/5 correctos")

print(f"\n{'='*70}")
print(f"RESUMEN: Batería A {ok_a}/6  |  Batería B {ok_b}/5")
print(f"{'='*70}\n")
