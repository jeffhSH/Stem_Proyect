import json
import os
import subprocess
import urllib.parse
import urllib.request

from ia_state import _cancelado, _ts
from tts import _reproducir_oracion

_BRAVE = r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe'

SCHEMA = {
    "type": "function",
    "function": {
        "name": "buscar_y_abrir_youtube",
        "description": "Busca un video en YouTube y abre el más relevante en Brave. Usar cuando el usuario pida reproducir música, ver un video o buscar algo en YouTube.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término de búsqueda, ej: 'Hungry Eyes', 'lofi hip hop', 'Luis Miguel'",
                }
            },
            "required": ["query"],
        },
    },
}


def _buscar_youtube(query: str) -> str | None:
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return None
    try:
        params = urllib.parse.urlencode({
            "part": "snippet",
            "q": query,
            "maxResults": 1,
            "type": "video",
            "key": api_key,
        })
        with urllib.request.urlopen(
            f"https://www.googleapis.com/youtube/v3/search?{params}", timeout=5
        ) as r:
            data = json.loads(r.read())
        video_id = data["items"][0]["id"]["videoId"]
        return f"https://www.youtube.com/watch?v={video_id}"
    except Exception as exc:
        print(f"{_ts()}[youtube] error: {exc}")
        return None


def _abrir_en_brave(youtube_query: str) -> bool:
    """Busca en YouTube y abre el resultado (o el buscador, si no encontró nada) en Brave."""
    if _cancelado():
        return False
    url = _buscar_youtube(youtube_query)
    if url:
        print(f"{_ts()}[youtube] abriendo: {url}")
        subprocess.Popen([_BRAVE, url])
    else:
        fallback = f"https://www.youtube.com/results?search_query={urllib.parse.quote(youtube_query)}"
        subprocess.Popen([_BRAVE, fallback])
        _reproducir_oracion("No encontré el video exacto, abriendo búsqueda.")
    return True


def handle(args: dict, ctx) -> dict:
    query = args.get("query", "")
    print(f"{_ts()}[ia] youtube: '{query}'")
    exito = _abrir_en_brave(query)
    return {"exito": exito}
