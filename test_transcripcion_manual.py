"""
Script manual (NO forma parte de la suite de pytest) para comparar a oído la
transcripción de Whisper antes y después de CAMBIO B (normalización por pico)
y CAMBIO C (initial_prompt de dominio).

Grabá las 5 frases de prueba cuando el script lo pida. Para cada una se
imprime el texto "ANTES" (pipeline previo: amplificación 2.5x con clip, sin
initial_prompt) contra "DESPUÉS" (pipeline actual: normalización por pico +
initial_prompt de dominio).

Uso:
    python test_transcripcion_manual.py
"""
import queue

import numpy as np
import sounddevice as sd
import vosk

from stt import _capturar_audio, _construir_initial_prompt, _get_modelo, transcribir_whisper
from voz import CHUNK, MODEL_PATH, SAMPLE_RATE

FRASES_PRUEBA = [
    "Frase 1: (elegí vos qué decir)",
    "Frase 2: (elegí vos qué decir)",
    "Frase 3: (elegí vos qué decir)",
    "Frase 4: (elegí vos qué decir)",
    "Frase 5: (elegí vos qué decir)",
]


def _transcribir_antes(audio_bytes: bytes) -> str:
    """Réplica del pipeline previo a CAMBIO B/C: amplificación 2.5x con clip, sin initial_prompt."""
    arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    arr = np.clip(arr * 2.5, -1.0, 1.0)
    arr = np.ascontiguousarray(arr)
    segments, _ = _get_modelo().transcribe(
        arr,
        language="es",
        beam_size=1,
        best_of=1,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=300, threshold=0.3),
        condition_on_previous_text=False,
        word_timestamps=False,
    )
    return " ".join(seg.text for seg in segments).strip()


def main() -> None:
    print(f"initial_prompt actual: '{_construir_initial_prompt()}'\n")
    print("Cargando modelo Vosk...")
    model = vosk.Model(MODEL_PATH)
    rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)

    audio_q: queue.Queue = queue.Queue()

    def _callback(indata, frames, time_info, status):
        audio_q.put(bytes(indata))

    print("Precargando Whisper...")
    _get_modelo()

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK,
        dtype="int16",
        channels=1,
        callback=_callback,
    ):
        for i, frase in enumerate(FRASES_PRUEBA, start=1):
            input(f"\n[{i}/5] Sugerencia: {frase}\nPresioná Enter y hablá (corta a los 900ms de silencio)...")
            while not audio_q.empty():
                audio_q.get_nowait()
            audio_bytes = _capturar_audio(audio_q, rec)
            if not audio_bytes:
                print("  (sin audio capturado, saltando)")
                continue

            antes = _transcribir_antes(audio_bytes)
            despues = transcribir_whisper(audio_bytes)
            print(f"  ANTES   : '{antes}'")
            print(f"  DESPUÉS : '{despues}'")


if __name__ == "__main__":
    main()
