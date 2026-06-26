import ctypes
import json
import queue
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
import vosk

MODEL_PATH     = "vosk-model-small-es-0.42"
SAMPLE_RATE    = 16000
CHUNK          = 4000
WAKE_WORDS     = ["stem", "estén", "stein", "steam", "stand", "steve", "están", "sten", "esteam"]
TIMEOUT_ACTIVO = 6.0


def _cargar_modelo(model_path: str) -> vosk.Model:
    path = Path(model_path)
    if not path.exists():
        print(f"Error: modelo no encontrado en '{model_path}'")
        print("Descarga desde:  https://alphacephei.com/vosk/models")
        print("Recomendado:     vosk-model-small-es-0.42  (~40 MB)")
        sys.exit(1)
    vosk.SetLogLevel(-1)
    print(f"Cargando modelo '{path.name}'...")
    return vosk.Model(str(path))


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
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK,
            dtype="int16",
            channels=1,
        ) as stream:
            while True:
                data, _overflow = stream.read(CHUNK)

                if rec.AcceptWaveform(bytes(data)):
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
    Al detectar 'stem' pasa a modo activo y espera TIMEOUT_ACTIVO segundos
    para recibir un comando; si no llega, vuelve a dormir.
    """
    from comandos import texto_a_comando

    model = _cargar_modelo(model_path or MODEL_PATH)
    rec   = vosk.KaldiRecognizer(model, SAMPLE_RATE)

    audio_q: queue.Queue = queue.Queue()

    def _callback(indata, frames, time_info, status):
        audio_q.put(bytes(indata))

    dormido  = True
    t_activo = 0.0

    print(f"En espera de {WAKE_WORDS}... Ctrl+C para salir.\n")

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK,
            dtype="int16",
            channels=1,
            callback=_callback,
        ):
            while True:
                if not dormido and time.time() - t_activo > TIMEOUT_ACTIVO:
                    print("[stem] tiempo agotado, volviendo a dormir...")
                    dormido = True
                    rec.Reset()

                data = audio_q.get()

                # --- Amplificación 30% + clip para evitar overflow int16 ---
                arr = np.frombuffer(data, dtype=np.int16)
                arr = np.clip(arr.astype(np.float32) * 1.3, -32768, 32767).astype(np.int16)
                data = arr.tobytes()

                # --- DIAGNÓSTICO: nivel RMS del chunk ---
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

                # Estado dormido: reacciona a cualquier variante fonológica del wake word
                if dormido:
                    if any(w in texto for w in WAKE_WORDS):
                        print(f"[stem] activado — di un comando ({int(TIMEOUT_ACTIVO)} s)...")
                        dormido  = False
                        t_activo = time.time()
                        rec.Reset()
                    continue

                # Estado activo: espera resultado final para procesar el comando
                if not es_final:
                    continue

                print(f"[vosk]: {texto}")

                if "apagar sistema" in texto:
                    if _esperar_confirmacion(audio_q, rec):
                        subprocess.run(["shutdown", "/s", "/t", "0"])
                    dormido = True
                    continue
                if "apagar" in texto:
                    ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
                    dormido = True
                    continue
                if "bloquear" in texto:
                    ctypes.windll.user32.LockWorkStation()
                    dormido = True
                    continue
                if "reiniciar" in texto:
                    subprocess.run(["shutdown", "/r", "/t", "0"])
                    dormido = True
                    continue

                cmd = texto_a_comando(texto)
                if cmd:
                    print(f"[oído]: {texto}")
                    if on_accion(cmd):
                        break
                    dormido = True
                    rec.Reset()

    except KeyboardInterrupt:
        print("\nDeteniendo detector.")
