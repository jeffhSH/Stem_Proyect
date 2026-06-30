"""
Ventana HUD de Stem — proceso aparte (subprocess).
Overlay always-on-top, sin bordes de SO, esquina superior derecha.
Poll JSON de estado (hud_control) cada 80ms.

Limitación Tkinter: sin blur real ni transparencia por-pixel con texto nítido.
Se usa -alpha 0.94 (opacidad completa de ventana), no glassmorphism real como
en stem_hud_final.html. Para glassmorphism real sería necesario migrar a
PyQt/PySide — la lógica de estados y polling se reutiliza tal cual.

Animación del wordmark: S y M visibles de inmediato; T se inserta a 350ms,
E a 550ms (portado de stem_logo_animado.html). Implementado con .after()
escalonado cambiando el color de texto de BG→accent (Tkinter no tiene
CSS transitions — este es el equivalente más fiel posible).
"""
import json
import math
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

# ── Estado ──────────────────────────────────────────────────────────────────
STATE_FILE = Path(tempfile.gettempdir()) / "stem_hud_state.json"

# ── Colores (extraídos de stem_hud_final.html, tema oscuro) ─────────────────
BG      = "#0d1620"   # fondo ventana
C_IDLE  = "#7fa3b0"   # --text-dim (gris azulado)
C_PROC  = "#5fe3ff"   # --cyan
C_SPEK  = "#5fffb0"   # --green
C_TEXT  = "#e8f6fb"   # --text
C_DIM   = "#7fa3b0"   # --text-dim
C_TX_BG = "#060c14"   # fondo transcript (más oscuro que BG)

# E del wordmark es siempre cian (no cambia entre estados, per HTML reference)
C_E     = C_PROC

# ── Fuentes ──────────────────────────────────────────────────────────────────
F_MONO  = ("Consolas", 8)
F_MONO9 = ("Consolas", 9)
F_WM    = ("Segoe UI", 9, "bold")   # wordmark

# ── Dimensiones ──────────────────────────────────────────────────────────────
WIN_W   = 270   # ancho fijo de la ventana
MARGIN  = 18    # px desde los bordes de pantalla

# ── Constantes de animación ──────────────────────────────────────────────────
POLL_MS      = 80
TICK_MS      = 50      # ~20fps para animaciones
FLOAT_AMP    = 3.0     # px de amplitud
FLOAT_PERIOD = 4.5     # segundos por ciclo (--float 4.5s en HTML)
EQ_PERIOD    = 1.0     # segundos por ciclo del ecualizador
EQ_H_MAX     = 12      # px altura máxima
EQ_W         = 2       # px ancho de barra
EQ_GAP       = 2       # px espacio entre barras
# Fracciones de altura por barra (de .waveform span nth-child heights en HTML)
EQ_FRAC      = [0.40, 0.90, 0.60, 1.00, 0.50, 0.75]
# Desfases de fase (animation-delay / period, en fracción de ciclo)
EQ_PHASE_OFF = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]

# Wordmark animation timing (de stem_logo_animado.html)
WM_T_MS = 350   # ms → T aparece
WM_E_MS = 550   # ms → E aparece

DOT_SIZE     = 6   # px diámetro del status dot
DOT_PULSE_MS = 1.3 # segundos por ciclo de pulso


class HudWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root       = root
        self._t0        = time.monotonic()
        self._base_y    = MARGIN
        self._win_h     = 52
        self._state: dict = {"estado": "idle", "transcripcion": "", "ronda": 0, "max_rondas": 6}
        self._wm_t      = False   # T visible en wordmark
        self._wm_e      = False   # E visible en wordmark

        self._setup_window()
        self._build_ui()
        self._apply_noactivate()

        self.root.after(0,       self._wordmark_init)
        self.root.after(0,       self._poll)
        self.root.after(TICK_MS, self._tick)

    # ── Ventana ───────────────────────────────────────────────────────────────
    def _setup_window(self) -> None:
        r = self.root
        r.title("")
        r.overrideredirect(True)
        r.configure(bg=BG)
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.94)
        sw = r.winfo_screenwidth()
        r.geometry(f"{WIN_W}x{self._win_h}+{sw - WIN_W - MARGIN}+{MARGIN}")
        r.update_idletasks()

    def _apply_noactivate(self) -> None:
        """WS_EX_NOACTIVATE: evita que la ventana robe foco en Windows."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = self.root.winfo_id()
            GWL_EXSTYLE      = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            s = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, s | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            )
        except Exception:
            pass

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=14, pady=10)
        outer.pack(fill=tk.BOTH, expand=True)

        # Header: logo (izquierda) + status (derecha)
        hdr = tk.Frame(outer, bg=BG)
        hdr.pack(fill=tk.X)

        logo_f = tk.Frame(hdr, bg=BG)
        logo_f.pack(side=tk.LEFT)

        self._ic = tk.Canvas(logo_f, width=22, height=20, bg=BG, highlightthickness=0)
        self._ic.pack(side=tk.LEFT, padx=(0, 7))

        self._wmc = tk.Canvas(logo_f, width=40, height=18, bg=BG, highlightthickness=0)
        self._wmc.pack(side=tk.LEFT)

        status_f = tk.Frame(hdr, bg=BG)
        status_f.pack(side=tk.RIGHT, anchor=tk.CENTER)

        self._dot_cv = tk.Canvas(status_f, width=DOT_SIZE + 2, height=DOT_SIZE + 2,
                                  bg=BG, highlightthickness=0)
        self._dot_cv.pack(side=tk.LEFT, padx=(0, 5))

        self._status_lbl = tk.Label(status_f, text="EN ESPERA",
                                     fg=C_IDLE, bg=BG, font=F_MONO)
        self._status_lbl.pack(side=tk.LEFT)

        # Divider (1px, color cambia por estado)
        self._div = tk.Frame(outer, height=1, bg=C_PROC)

        # Info row: ecualizador + etiqueta de ronda/audio
        self._info_f = tk.Frame(outer, bg=BG)

        eq_total_w = (EQ_W + EQ_GAP) * len(EQ_FRAC) - EQ_GAP
        self._eq_cv = tk.Canvas(self._info_f, width=eq_total_w, height=EQ_H_MAX,
                                  bg=BG, highlightthickness=0)
        self._eq_cv.pack(side=tk.LEFT, padx=(0, 6), anchor=tk.CENTER)

        self._info_lbl = tk.Label(self._info_f, text="ronda 1/6",
                                   fg=C_DIM, bg=BG, font=F_MONO)
        self._info_lbl.pack(side=tk.LEFT, anchor=tk.CENTER)

        # Transcript (borde simulado con Frame externo de 1px)
        self._tx_border = tk.Frame(outer, bg=C_PROC, padx=1, pady=1)
        tx_inner = tk.Frame(self._tx_border, bg=C_TX_BG, padx=8, pady=6)
        tx_inner.pack(fill=tk.BOTH, expand=True)
        self._tx_lbl = tk.Label(tx_inner, text="",
                                 fg=C_TEXT, bg=C_TX_BG, font=F_MONO9,
                                 justify=tk.LEFT, wraplength=220, anchor="w")
        self._tx_lbl.pack(fill=tk.X, anchor="w")

        # Render inicial
        self._render_icon(C_IDLE)
        self._render_dot(C_IDLE, t=0.0)
        self._render_eq(C_IDLE, t=0.0)
        self._set_layout_idle()

    # ── Dibujo del ícono robot ────────────────────────────────────────────────
    def _render_icon(self, color: str) -> None:
        """Dibuja el robot (SVG viewBox 0 0 64 64 → 22px wide)."""
        c = self._ic
        c.delete("all")
        s  = 22 / 64
        lw = 1.5

        def L(x1, y1, x2, y2):
            c.create_line(x1*s, y1*s, x2*s, y2*s,
                          fill=color, width=lw, capstyle=tk.ROUND)

        def O(cx, cy, r):
            c.create_oval((cx-r)*s, (cy-r)*s, (cx+r)*s, (cy+r)*s,
                           fill=color, outline="")

        def R(x, y, w, h):
            c.create_rectangle(x*s, y*s, (x+w)*s, (y+h)*s,
                                outline=color, fill="", width=lw)

        L(20, 14, 16, 6)    # antena izquierda
        L(44, 14, 48, 6)    # antena derecha
        O(16, 6, 3)          # punto antena izq
        O(48, 6, 3)          # punto antena der
        R(10, 14, 44, 30)   # cuerpo
        O(25, 29, 4)         # ojo izquierdo
        O(39, 29, 4)         # ojo derecho

    # ── Status dot ────────────────────────────────────────────────────────────
    def _render_dot(self, color: str, *, t: float) -> None:
        c = self._dot_cv
        c.delete("all")
        estado = self._state.get("estado", "idle")
        if estado == "idle":
            r = DOT_SIZE // 2
        else:
            # pulso: radio oscila entre 2 y 3px
            pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t / DOT_PULSE_MS)
            r = int(2 + 1 * pulse)
        cx = (DOT_SIZE + 2) // 2
        cy = (DOT_SIZE + 2) // 2
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

    # ── Ecualizador ───────────────────────────────────────────────────────────
    def _render_eq(self, color: str, *, t: float) -> None:
        """6 barras animadas. scaleY: 0.4→1.0→0.4 por ciclo (igual que HTML)."""
        c = self._eq_cv
        c.delete("all")
        for i, (frac, phase_off) in enumerate(zip(EQ_FRAC, EQ_PHASE_OFF)):
            # scale(t) = 0.4 + 0.6 * (0.5 - 0.5*cos(2π*(t/period + phase_off)))
            phase  = 2 * math.pi * (t / EQ_PERIOD + phase_off)
            scale  = 0.4 + 0.3 * (1 - math.cos(phase))
            h      = max(2, int(EQ_H_MAX * frac * scale))
            x0     = i * (EQ_W + EQ_GAP)
            c.create_rectangle(x0, EQ_H_MAX - h, x0 + EQ_W, EQ_H_MAX,
                                fill=color, outline="")

    # ── Wordmark ──────────────────────────────────────────────────────────────
    def _render_wordmark(self) -> None:
        """Dibuja STEM en el canvas. T y E en BG (invisible) hasta que se revelan."""
        c = self._wmc
        c.delete("all")
        y = 9
        # Posiciones aproximadas para Segoe UI 9 Bold (~7px por carácter)
        c.create_text(1,  y, text="S", fill=C_TEXT,                     font=F_WM, anchor="w")
        c.create_text(9,  y, text="T", fill=(C_TEXT if self._wm_t else BG), font=F_WM, anchor="w")
        c.create_text(17, y, text="E", fill=(C_E    if self._wm_e else BG), font=F_WM, anchor="w")
        c.create_text(25, y, text="M", fill=C_TEXT,                     font=F_WM, anchor="w")

    def _wordmark_init(self) -> None:
        """Inicia la animación de entrada: S y M de inmediato, T a 350ms, E a 550ms."""
        self._wm_t = False
        self._wm_e = False
        self._render_wordmark()
        self.root.after(WM_T_MS, self._wordmark_show_t)
        self.root.after(WM_E_MS, self._wordmark_show_e)

    def _wordmark_show_t(self) -> None:
        self._wm_t = True
        self._render_wordmark()

    def _wordmark_show_e(self) -> None:
        self._wm_e = True
        self._render_wordmark()

    # ── Layouts ───────────────────────────────────────────────────────────────
    def _set_layout_idle(self) -> None:
        self._div.pack_forget()
        self._info_f.pack_forget()
        self._tx_border.pack_forget()

    def _set_layout_procesando(self, ronda: int, max_r: int, tx: str) -> None:
        self._div.pack(fill=tk.X, pady=(8, 0))
        self._info_f.pack(fill=tk.X, pady=(6, 0))
        self._info_lbl.config(text=f"ronda {ronda}/{max_r}")
        short = (tx[:58] + "…") if len(tx) > 58 else tx
        self._tx_lbl.config(text=f"> {short}")
        self._tx_border.config(bg=C_PROC)
        self._tx_border.pack(fill=tk.X, pady=(6, 0))

    def _set_layout_hablando(self) -> None:
        self._div.pack(fill=tk.X, pady=(8, 0))
        self._info_f.pack(fill=tk.X, pady=(6, 0))
        self._info_lbl.config(text="audio en curso")
        self._tx_border.pack_forget()

    # ── Aplicar estado ────────────────────────────────────────────────────────
    def _apply_state(self, data: dict) -> None:
        estado = data.get("estado", "idle")
        tx     = data.get("transcripcion", "")
        ronda  = data.get("ronda", 0)
        max_r  = data.get("max_rondas", 6)

        accent = self._accent_for(estado)
        self._render_icon(accent)
        self._render_wordmark()

        if estado == "idle":
            self._status_lbl.config(text="EN ESPERA", fg=C_IDLE)
            self._set_layout_idle()
        elif estado == "procesando":
            self._status_lbl.config(text="PROCESANDO", fg=C_PROC)
            self._div.config(bg=C_PROC)
            self._set_layout_procesando(ronda, max_r, tx)
        elif estado == "hablando":
            self._status_lbl.config(text="RESPONDIENDO", fg=C_SPEK)
            self._div.config(bg=C_SPEK)
            self._set_layout_hablando()

        self.root.update_idletasks()
        self._win_h = max(self.root.winfo_reqheight(), 50)

    @staticmethod
    def _accent_for(estado: str) -> str:
        return C_PROC if estado == "procesando" else (C_SPEK if estado == "hablando" else C_IDLE)

    # ── Poll ──────────────────────────────────────────────────────────────────
    def _poll(self) -> None:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if data != self._state:
                self._state = data
                self._apply_state(data)
        except Exception:
            pass
        self.root.after(POLL_MS, self._poll)

    # ── Tick de animación (~20fps) ────────────────────────────────────────────
    def _tick(self) -> None:
        t      = time.monotonic() - self._t0
        estado = self._state.get("estado", "idle")
        accent = self._accent_for(estado)

        # Ecualizador (solo visible en procesando/hablando)
        if estado in ("procesando", "hablando"):
            self._render_eq(accent, t=t)

        # Dot pulse
        self._render_dot(accent, t=t)

        # Flotación vertical
        float_y = FLOAT_AMP * math.sin(2 * math.pi * t / FLOAT_PERIOD)
        y       = int(self._base_y + float_y)
        sw      = self.root.winfo_screenwidth()
        self.root.wm_geometry(f"{WIN_W}x{self._win_h}+{sw - WIN_W - MARGIN}+{y}")

        self.root.after(TICK_MS, self._tick)


def main() -> None:
    root = tk.Tk()
    HudWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
