import ctypes
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import samplerate as _samplerate
import sounddevice as sd
import vosk

MODEL_PATH     = "vosk-model-small-es-0.42"
SAMPLE_RATE    = 16000
CHUNK          = 4000
WAKE_WORDS     = ["stem", "estén", "stein", "steam", "stand", "steve", "están", "sten", "esteam", "stern"]
TIMEOUT_ACTIVO = 6.0


def _get_native_rate(device=None) -> int:
    """Detecta el sample rate nativo del dispositivo de entrada."""
    try:
        info = sd.query_devices(device=device, kind="input")
        return int(info["default_sample_rate"])
    except Exception:
        return 48000

_NATIVE_RATE    = _get_native_rate()
_RESAMPLER      = _samplerate.Resampler("sinc_fastest", channels=1)
_RESAMPLE_RATIO = SAMPLE_RATE / _NATIVE_RATE
_INPUT_STREAM_KWARGS = {
    "samplerate": _NATIVE_RATE,
    "blocksize":  8000,
    "latency":    "high",
    "dtype":      "int16",
    "channels":   1,
}


def _resample_to_vosk(indata: bytes) -> bytes:
    """Convierte audio de _NATIVE_RATE a SAMPLE_RATE (16000 Hz) para Vosk."""
    if _NATIVE_RATE == SAMPLE_RATE:
        return indata
    pcm       = np.frombuffer(indata, dtype="int16").astype("float32") / 32767.0
    resampled = _RESAMPLER.process(pcm, _RESAMPLE_RATIO)
    return (resampled * 32767).astype("int16").tobytes()

# Modelo cargado (accesible por training.py sin recarga)
_modelo_activo: vosk.Model | None = None
# Señal de pausa durante el modo entrenamiento
_entrenando           = threading.Event()
# Se activa mientras un comando está ejecutándose (para no dormir antes de que termine)
_comando_ejecutandose = threading.Event()


def _apagar_pantalla() -> None:
    """
    Envía WM_SYSCOMMAND / SC_MONITORPOWER en un hilo daemon.
    SendMessageW con HWND_BROADCAST espera respuesta de TODAS las ventanas del
    sistema; si alguna está colgada puede bloquear minutos. Corriendo en un
    hilo daemon el bucle de audio sigue funcionando sin importar cuánto tarde.
    """
    threading.Thread(
        target=lambda: ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2),
        daemon=True,
    ).start()


def _drenar_audio(q: queue.Queue) -> None:
    """Descarta todo el audio acumulado en la cola para evitar re-procesar la utterance anterior."""
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def pausar() -> None:
    """Pausa el bucle de reconocimiento. Llamar antes del modo entrenamiento."""
    _entrenando.set()


def reanudar() -> None:
    """Reanuda el bucle de reconocimiento. Llamar al salir del modo entrenamiento."""
    _entrenando.clear()


def _cargar_modelo(model_path: str) -> vosk.Model:
    global _modelo_activo
    path = Path(model_path)
    if not path.exists():
        print(f"Error: modelo no encontrado en '{model_path}'")
        print("Descarga desde:  https://alphacephei.com/vosk/models")
        print("Recomendado:     vosk-model-small-es-0.42  (~40 MB)")
        sys.exit(1)
    vosk.SetLogLevel(-1)
    print(f"Cargando modelo '{path.name}'...")
    _modelo_activo = vosk.Model(str(path))
    return _modelo_activo


def escuchar(
    on_texto:   Callable[[str], bool],
    on_parcial: Callable[[str], tuple[bool, bool]],
    model_path: str = MODEL_PATH,
) -> None:
    """
    Escucha el micrófono continuamente con Vosk.

    on_texto(text)   — resultado final; retorna True para detener el bucle.
    on_parcial(text) — resultado parcial; retorna (coincidió, debe_salir).
    """
    model = _cargar_modelo(model_path)
    rec   = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    print("Escuchando... Ctrl+C para salir.\n")

    try:
        with sd.RawInputStream(**_INPUT_STREAM_KWARGS) as stream:
            while True:
                data, _overflow = stream.read(CHUNK)

                if rec.AcceptWaveform(_resample_to_vosk(bytes(data))):
                    texto = json.loads(rec.Result()).get("text", "").strip()
                    if texto:
                        print(f"[oído]: {texto}")
                        if on_texto(texto):
                            break
                else:
                    texto_p = json.loads(rec.PartialResult()).get("partial", "").strip()
                    if texto_p:
                        coincidio, debe_salir = on_parcial(texto_p)
                        if coincidio:
                            rec.Reset()
                        if debe_salir:
                            break

    except KeyboardInterrupt:
        print("\nDeteniendo detector.")


def _esperar_confirmacion(audio_q: queue.Queue, rec: vosk.KaldiRecognizer) -> bool:
    """Espera 'confirmar' por voz durante 6 s usando el stream activo."""
    print("[stem] di 'confirmar' para proceder (6 s)...")
    t_fin = time.time() + 6.0
    rec.Reset()
    while time.time() < t_fin:
        try:
            data = audio_q.get(timeout=max(0.0, t_fin - time.time()))
        except queue.Empty:
            break

        if rec.AcceptWaveform(data):
            texto = json.loads(rec.Result()).get("text", "").lower().strip()
        else:
            texto = json.loads(rec.PartialResult()).get("partial", "").lower().strip()

        if "confirmar" in texto:
            rec.Reset()
            return True

    print("[stem] confirmación no recibida, cancelando.")
    rec.Reset()
    return False


def escuchar_wake_word(
    on_accion: Callable[[str], bool],
    model_path: str | None = None,
) -> None:
    """
    Vosk escucha continuamente.
    Dormido: rec_dormido genérico detecta wake word por substring en WAKE_WORDS.
    Activo:  rec_activo con gramática restringida a VARIANTES reduce falsos positivos (Fix 1).
    """
    from comandos import texto_a_comando, VARIANTES, NUMEROS_ES  # noqa: PLC0415
    from macros import VARIANTES_MEDIOS                          # noqa: PLC0415

    model = _cargar_modelo(model_path or MODEL_PATH)

    # Fix 1: gramática restringida — variantes de comandos + macros de medios + números + confirmación
    _gram_frases  = sorted(
        {v for vset in VARIANTES.values()       for v in vset} |
        {v for vset in VARIANTES_MEDIOS.values() for v in vset}
    )
    _gram_numeros = list(NUMEROS_ES.keys())
    _gram_json    = json.dumps(_gram_frases + _gram_numeros + ["confirmar", "[unk]"])

    # Fix 1: dos reconocedores
    rec_dormido = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    rec_activo  = vosk.KaldiRecognizer(model, SAMPLE_RATE, _gram_json)

    rec = rec_dormido   # empieza en estado dormido

    audio_q: queue.Queue = queue.Queue()

    def _callback(indata, frames, time_info, status):
        audio_q.put(_resample_to_vosk(bytes(indata)))

    dormido         = True
    t_activo        = 0.0
    _era_entrenando = False

    # ── Tecla | como activador en paralelo al wake word por voz ──────────────
    from pynput import keyboard as _kb  # noqa: PLC0415

    _tecla_activa = threading.Event()

    def _on_press(key):
        try:
            if getattr(key, "char", None) == "|" and not _tecla_activa.is_set():
                _tecla_activa.set()
        except Exception:
            pass

    def _on_release(key):
        try:
            if getattr(key, "char", None) == "|":
                _tecla_activa.clear()
        except Exception:
            pass

    _kb_listener = _kb.Listener(on_press=_on_press, on_release=_on_release)
    _kb_listener.daemon = True
    _kb_listener.start()

    print(f"En espera de {WAKE_WORDS} o tecla | ... Ctrl+C para salir.\n")
    print("Presioná T para entrar al modo entrenamiento.\n")

    try:
        with sd.RawInputStream(**_INPUT_STREAM_KWARGS, callback=_callback):
            while True:
                # Activación/extensión de ventana por tecla |
                if _tecla_activa.is_set():
                    if dormido:
                        dormido  = False
                        t_activo = time.time()
                        rec = rec_activo   # Fix 1: gramática restringida al activar
                        rec.Reset()
                        print(f"[stem] activado por | — di un comando ({int(TIMEOUT_ACTIVO)} s)...")
                    else:
                        t_activo = time.time()   # extiende mientras se mantiene presionada

                if not dormido and time.time() - t_activo > TIMEOUT_ACTIVO:
                    if _comando_ejecutandose.is_set():
                        t_activo = time.time()   # espera a que el comando termine antes de dormir
                    else:
                        print("[stem] tiempo agotado, volviendo a dormir...")
                        dormido = True
                        rec = rec_dormido   # Fix 1: restaurar recognizer genérico
                        rec.Reset()

                data = audio_q.get()

                # Descarta audio mientras está en modo entrenamiento
                if _entrenando.is_set():
                    _era_entrenando = True
                    continue

                # Al volver del modo entrenamiento: reset para evitar artefactos
                if _era_entrenando:
                    _era_entrenando = False
                    dormido = True
                    rec = rec_dormido   # Fix 1
                    rec.Reset()

                # Amplificación 50% con clip para evitar overflow int16
                arr  = np.frombuffer(data, dtype=np.int16)
                arr  = np.clip(arr.astype(np.float32) * 1.5, -32768, 32767).astype(np.int16)
                data = arr.tobytes()

                # Diagnóstico: RMS del chunk
                rms = np.sqrt(np.mean(arr.astype(np.float32) ** 2)) / 32768.0
                print(f"[rms]: {rms:.4f}")

                es_final = rec.AcceptWaveform(data)

                if es_final:
                    texto = json.loads(rec.Result()).get("text", "").lower().strip()
                    print(f"[vosk final]: '{texto}'")
                else:
                    texto = json.loads(rec.PartialResult()).get("partial", "").lower().strip()
                    if texto:
                        print(f"[vosk parcial]: '{texto}'")

                if not texto:
                    continue

                # Fix A: detección de wake word por substring (lista WAKE_WORDS)
                wake = next((w for w in WAKE_WORDS if w in texto), None)

                if dormido:
                    if not wake:
                        continue
                    rec.Reset()

                    # Fix B: quitar wake word y TODAS sus variantes del texto
                    # para que texto_a_comando reciba solo la parte del comando
                    resto = texto
                    for ww in WAKE_WORDS:
                        resto = resto.replace(ww, " ")
                    resto = " ".join(resto.split())   # colapsa espacios múltiples

                    if resto and es_final:
                        print(f"[stem] '{wake}' + '{resto}' — procesando al instante")
                        texto = resto
                        # cae al bloque de comandos (dormido sigue True)
                    else:
                        print(f"[stem] activado — di un comando ({int(TIMEOUT_ACTIVO)} s)...")
                        dormido  = False
                        t_activo = time.time()
                        rec = rec_activo   # Fix 1: gramática restringida en modo activo
                        rec.Reset()
                        continue

                else:
                    # Estado activo (gramática restringida): solo resultados finales
                    if not es_final:
                        continue
                    print(f"[vosk]: {texto}")

                if "apagar sistema" in texto:
                    if _esperar_confirmacion(audio_q, rec):
                        subprocess.run(["shutdown", "/s", "/t", "0"])
                    _drenar_audio(audio_q)
                    dormido = True
                    rec = rec_dormido   # Fix 1
                    rec.Reset()
                    continue
                if "apagar" in texto:
                    _apagar_pantalla()
                    _drenar_audio(audio_q)
                    dormido = True
                    rec = rec_dormido   # Fix 1
                    rec.Reset()
                    continue
                if "bloquear" in texto:
                    ctypes.windll.user32.LockWorkStation()
                    _drenar_audio(audio_q)
                    dormido = True
                    rec = rec_dormido   # Fix 1
                    rec.Reset()
                    continue
                if "reiniciar" in texto:
                    subprocess.run(["shutdown", "/r", "/t", "0"])
                    _drenar_audio(audio_q)
                    dormido = True
                    rec = rec_dormido   # Fix 1
                    rec.Reset()
                    continue

                cmd = texto_a_comando(texto)
                if cmd:
                    print(f"[oído]: {texto}")
                    if cmd == "apagar_pantalla":
                        _apagar_pantalla()
                        _drenar_audio(audio_q)
                        dormido = True
                        rec = rec_dormido   # Fix 1
                        rec.Reset()
                    else:
                        _comando_ejecutandose.set()
                        if on_accion(cmd):
                            _comando_ejecutandose.clear()
                            break
                        _comando_ejecutandose.clear()
                        _drenar_audio(audio_q)
                        dormido = True
                        rec = rec_dormido   # Fix 1
                        rec.Reset()

    except KeyboardInterrupt:
        print("\nDeteniendo detector.")
    finally:
        _kb_listener.stop()
