"""
Ventana HUD de Stem — proceso aparte (subprocess).
Overlay always-on-top, sin bordes de SO, esquina superior derecha.
Poll JSON de estado (hud_control) cada 80ms.

Tema: light (rgba(255,255,255,0.68) simulado via -alpha; sin blur real —
Tkinter no soporta backdrop-filter. Para glassmorphism real migrar a PyQt/PySide).
"""
import json
import math
import random
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

import hud_control

# ── Estado ──────────────────────────────────────────────────────────────────
STATE_FILE = Path(tempfile.gettempdir()) / "stem_hud_state.json"

# ── Colores tema light (de stem_hud_final.html .hud.light) ──────────────────
BG       = "#f0f8fc"    # aproxima rgba(255,255,255,0.68); blur via -alpha
C_BORDER = "#1ea7d6"    # rgba(30,167,214,0.35) → solid tkinter
C_IDLE   = "#9aabb0"    # .hud.light.idle .status-dot
C_PROC   = "#1ea7d6"    # --light-cyan
C_SPEK   = "#28b482"    # .hud.light.speak .status-dot
C_TEXT   = "#16313d"    # .hud.light .logo color
C_DIM    = "#5b7884"    # .hud.light .hud-label color
C_TX_BG  = "#ffffff"    # .hud.light .hud-transcript background
C_TX_FG  = "#1c3640"    # .hud.light .hud-transcript color
C_E      = C_PROC       # E del wordmark siempre cian

# ── Fuentes (+20% sobre valores originales) ──────────────────────────────────
F_MONO   = ("Consolas", 10)          # era 8  → round(8*1.2)=10
F_MONO11 = ("Consolas", 11)          # era 9  → round(9*1.2)=11
F_WM     = ("Segoe UI", 11, "bold")  # era 9  → round(9*1.2)=11

# ── Dimensiones (+20%) ───────────────────────────────────────────────────────
WIN_W  = 324   # era 270 → 270*1.2=324
MARGIN = 22    # era 18  → round(18*1.2)=22

# ── Animación ────────────────────────────────────────────────────────────────
POLL_MS           = 80
TICK_MS           = 16    # ~60fps (era 50ms ~20fps)
FLOAT_AMP         = 3.0
FLOAT_PERIOD      = 4.5
EQ_H_MAX          = 14    # era 12 → round(12*1.2)=14
EQ_W              = 2
EQ_GAP            = 2
EQ_LERP           = 0.12  # factor de interpolación hacia target por tick
EQ_RETARGET_TICKS = 8     # retargetear cada 8 ticks (~128ms a 16ms/tick)
EQ_FRAC           = [0.40, 0.90, 0.60, 1.00, 0.50, 0.75]

WM_T_MS    = 350
WM_E_MS    = 550
DOT_SIZE   = 7    # era 6 → round(6*1.2)=7
BRACKET_SZ = 12   # px, canvas de esquina (decorativo)


def _interpolar_color(c1: str, c2: str, t: float) -> str:
    """Interpola entre dos colores hex #rrggbb con t ∈ [0.0, 1.0]."""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = max(0, min(255, int(r1 + (r2 - r1) * t)))
    g = max(0, min(255, int(g1 + (g2 - g1) * t)))
    b = max(0, min(255, int(b1 + (b2 - b1) * t)))
    return f"#{r:02x}{g:02x}{b:02x}"


class HudWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root           = root
        self._t0            = time.monotonic()
        self._base_y        = MARGIN
        self._win_h         = 62   # era 52 → round(52*1.2)=62
        self._state: dict   = {"estado": "idle", "transcripcion": "", "ronda": 0, "max_rondas": 6}
        self._pregunta_id: str | None = None
        self._wm_t          = False
        self._wm_e          = False
        self._eq_heights    = list(EQ_FRAC)
        self._eq_targets    = [random.uniform(0.3, 1.0) for _ in range(len(EQ_FRAC))]
        self._eq_tick_count = 0

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
        r.configure(bg=C_BORDER)   # color de borde visible en gaps
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.92)
        sw = r.winfo_screenwidth()
        r.geometry(f"{WIN_W}x{self._win_h}+{sw - WIN_W - MARGIN}+{MARGIN}")
        r.update_idletasks()

    def _apply_noactivate(self) -> None:
        """WS_EX_NOACTIVATE: evita que la ventana robe foco en Windows."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd             = self.root.winfo_id()
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
        # Marco de 1px que simula el border CSS del .hud.light
        border_f = tk.Frame(self.root, bg=C_BORDER, padx=1, pady=1)
        border_f.pack(fill=tk.BOTH, expand=True)

        outer = tk.Frame(border_f, bg=BG, padx=17, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        # Header: logo (izquierda) + status + close (derecha)
        hdr = tk.Frame(outer, bg=BG)
        hdr.pack(fill=tk.X)

        # Botón × — pack RIGHT primero → queda en el extremo derecho
        self._close_btn = tk.Label(hdr, text="×", fg=C_DIM, bg=BG,
                                    font=("Segoe UI", 13), cursor="hand2")
        self._close_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._close_btn.bind("<Button-1>", lambda e: self.root.destroy())
        self._close_btn.bind("<Enter>",    lambda e: self._close_btn.config(fg="#d04040"))
        self._close_btn.bind("<Leave>",    lambda e: self._close_btn.config(fg=C_DIM))

        logo_f = tk.Frame(hdr, bg=BG)
        logo_f.pack(side=tk.LEFT)

        self._ic = tk.Canvas(logo_f, width=26, height=24, bg=BG, highlightthickness=0)
        self._ic.pack(side=tk.LEFT, padx=(0, 8))

        self._wmc = tk.Canvas(logo_f, width=48, height=22, bg=BG, highlightthickness=0)
        self._wmc.pack(side=tk.LEFT)

        status_f = tk.Frame(hdr, bg=BG)
        status_f.pack(side=tk.RIGHT, anchor=tk.CENTER)

        self._dot_cv = tk.Canvas(status_f, width=DOT_SIZE + 2, height=DOT_SIZE + 2,
                                  bg=BG, highlightthickness=0)
        self._dot_cv.pack(side=tk.LEFT, padx=(0, 6))

        self._status_lbl = tk.Label(status_f, text="EN ESPERA",
                                     fg=C_IDLE, bg=BG, font=F_MONO)
        self._status_lbl.pack(side=tk.LEFT)

        # Divider (1px, color según estado)
        self._div = tk.Frame(outer, height=1, bg=C_PROC)

        # Info row: ecualizador + etiqueta ronda/audio
        self._info_f = tk.Frame(outer, bg=BG)

        eq_total_w = (EQ_W + EQ_GAP) * len(EQ_FRAC) - EQ_GAP
        self._eq_cv = tk.Canvas(self._info_f, width=eq_total_w, height=EQ_H_MAX,
                                  bg=BG, highlightthickness=0)
        self._eq_cv.pack(side=tk.LEFT, padx=(0, 7), anchor=tk.CENTER)

        self._info_lbl = tk.Label(self._info_f, text="ronda 1/6",
                                   fg=C_DIM, bg=BG, font=F_MONO)
        self._info_lbl.pack(side=tk.LEFT, anchor=tk.CENTER)

        # Transcript editable (borde simulado + tk.Text)
        self._tx_border = tk.Frame(outer, bg=C_PROC, padx=1, pady=1)
        tx_inner = tk.Frame(self._tx_border, bg=C_TX_BG, padx=10, pady=7)
        tx_inner.pack(fill=tk.BOTH, expand=True)
        self._tx_txt = tk.Text(
            tx_inner,
            height=3,
            state="normal",
            cursor="xterm",
            wrap=tk.WORD,
            fg=C_TX_FG,
            bg=C_TX_BG,
            insertbackground=C_PROC,   # cursor de texto cian
            font=F_MONO11,
            relief=tk.FLAT,
            bd=0,
            selectbackground="#dde8f4",
        )
        self._tx_txt.pack(fill=tk.X)

        # Corner brackets decorativos (esquinas opuestas, estilo .hud::before/.hud::after)
        self._brk_tl = tk.Canvas(self.root, width=BRACKET_SZ, height=BRACKET_SZ,
                                   bg=BG, highlightthickness=0)
        self._brk_tl.place(x=0, y=0)
        self._brk_br = tk.Canvas(self.root, width=BRACKET_SZ, height=BRACKET_SZ,
                                   bg=BG, highlightthickness=0)
        self._brk_br.place(relx=1.0, rely=1.0, x=-BRACKET_SZ, y=-BRACKET_SZ)

        # Frame de botones de confirmación (oculto por defecto)
        self._btn_frame = tk.Frame(outer, bg=BG)
        self._btn1 = tk.Button(
            self._btn_frame, text="", bg=C_PROC, fg="#ffffff",
            font=F_MONO, relief=tk.FLAT, bd=0, padx=10, pady=4,
            activebackground="#1686aa", activeforeground="#ffffff", cursor="hand2",
        )
        self._btn2 = tk.Button(
            self._btn_frame, text="", bg=BG, fg=C_DIM,
            font=F_MONO, relief=tk.FLAT, bd=1, padx=10, pady=4,
            activebackground="#e0ecf4", activeforeground=C_TEXT, cursor="hand2",
        )
        self._btn1.pack(side=tk.LEFT, padx=(0, 8))
        self._btn2.pack(side=tk.LEFT)

        # Render inicial
        self._render_icon(C_IDLE)
        self._render_dot(C_IDLE)
        self._render_eq(C_IDLE)
        self._render_brackets(C_IDLE)
        self._set_layout_idle()

    # ── Bracket corners ───────────────────────────────────────────────────────
    def _render_brackets(self, color: str) -> None:
        """Líneas L en esquina superior-izquierda e inferior-derecha."""
        n = BRACKET_SZ - 1
        for cv, lines in [
            (self._brk_tl, [(0, 0, 8, 0), (0, 0, 0, 8)]),
            (self._brk_br, [(n - 8, n, n, n), (n, n - 8, n, n)]),
        ]:
            cv.delete("all")
            for x1, y1, x2, y2 in lines:
                cv.create_line(x1, y1, x2, y2, fill=color, width=1.5)

    # ── Ícono robot ───────────────────────────────────────────────────────────
    def _render_icon(self, color: str) -> None:
        """SVG viewBox 0 0 64 64 → 26px wide (+20% sobre 22px original)."""
        c  = self._ic
        c.delete("all")
        s  = 26 / 64
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

        L(20, 14, 16, 6)
        L(44, 14, 48, 6)
        O(16, 6, 3)
        O(48, 6, 3)
        R(10, 14, 44, 30)
        O(25, 29, 4)
        O(39, 29, 4)

    # ── Status dot (pulso por interpolación de color, seno continuo) ──────────
    def _render_dot(self, accent: str) -> None:
        c  = self._dot_cv
        c.delete("all")
        cx = (DOT_SIZE + 2) // 2
        cy = (DOT_SIZE + 2) // 2
        r  = DOT_SIZE // 2
        estado = self._state.get("estado", "idle")
        if estado == "idle":
            color = C_IDLE
        else:
            intensidad = (1.0 + math.sin(time.monotonic() * 4.0)) / 2.0
            color = _interpolar_color(BG, accent, intensidad)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

    # ── Ecualizador (alturas interpoladas hacia targets aleatorios) ───────────
    def _render_eq(self, color: str) -> None:
        c = self._eq_cv
        c.delete("all")
        for i, h_frac in enumerate(self._eq_heights):
            h  = max(2, int(EQ_H_MAX * h_frac))
            x0 = i * (EQ_W + EQ_GAP)
            c.create_rectangle(x0, EQ_H_MAX - h, x0 + EQ_W, EQ_H_MAX,
                                fill=color, outline="")

    # ── Wordmark ──────────────────────────────────────────────────────────────
    def _render_wordmark(self) -> None:
        c = self._wmc
        c.delete("all")
        y = 11
        c.create_text(1,  y, text="S", fill=C_TEXT,                       font=F_WM, anchor="w")
        c.create_text(11, y, text="T", fill=(C_TEXT if self._wm_t else BG), font=F_WM, anchor="w")
        c.create_text(21, y, text="E", fill=(C_E    if self._wm_e else BG), font=F_WM, anchor="w")
        c.create_text(31, y, text="M", fill=C_TEXT,                       font=F_WM, anchor="w")

    def _wordmark_init(self) -> None:
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
        self._btn_frame.pack_forget()

    def _set_layout_procesando(self, ronda: int, max_r: int, tx: str) -> None:
        self._div.pack(fill=tk.X, pady=(8, 0))
        self._info_f.pack(fill=tk.X, pady=(6, 0))
        self._info_lbl.config(text=f"ronda {ronda}/{max_r}")
        self._tx_border.config(bg=C_PROC)
        self._tx_border.pack(fill=tk.X, pady=(6, 0))
        self._btn_frame.pack_forget()
        # No sobreescribir si el usuario está editando el widget
        if self.root.focus_get() is not self._tx_txt:
            short = (tx[:70] + "…") if len(tx) > 70 else tx
            self._tx_txt.delete("1.0", tk.END)
            self._tx_txt.insert(tk.END, f"> {short}")

    def _set_layout_hablando(self) -> None:
        self._div.pack(fill=tk.X, pady=(8, 0))
        self._info_f.pack(fill=tk.X, pady=(6, 0))
        self._info_lbl.config(text="audio en curso")
        self._tx_border.pack_forget()
        self._btn_frame.pack_forget()

    def _set_layout_pregunta(self, texto: str, opciones: dict, pregunta_id: str) -> None:
        self._div.pack(fill=tk.X, pady=(8, 0))
        self._info_f.pack(fill=tk.X, pady=(6, 0))
        self._info_lbl.config(text="esperando respuesta")
        self._tx_border.config(bg=C_PROC)
        self._tx_border.pack(fill=tk.X, pady=(6, 0))
        self._tx_txt.delete("1.0", tk.END)
        self._tx_txt.insert(tk.END, texto)

        label1 = f"1 · {opciones.get('1', 'Sí')}"
        label2 = f"2 · {opciones.get('2', 'No')}"
        self._btn1.config(text=label1, command=lambda: self._responder(pregunta_id, "1"))
        self._btn2.config(text=label2, command=lambda: self._responder(pregunta_id, "2"))
        self._btn_frame.pack(fill=tk.X, pady=(8, 0))

        # Bind teclas 1/2 (puede no funcionar si WS_EX_NOACTIVATE impide foco)
        self._pregunta_id = pregunta_id
        self.root.bind("<Key-1>", lambda e: self._responder(pregunta_id, "1"))
        self.root.bind("<Key-2>", lambda e: self._responder(pregunta_id, "2"))

    def _ocultar_botones(self) -> None:
        self._btn_frame.pack_forget()
        self._pregunta_id = None
        self.root.unbind("<Key-1>")
        self.root.unbind("<Key-2>")

    def _responder(self, pregunta_id: str, respuesta: str) -> None:
        hud_control.responder_pregunta_hud(pregunta_id, respuesta)
        self._ocultar_botones()

    # ── Aplicar estado ────────────────────────────────────────────────────────
    def _apply_state(self, data: dict) -> None:
        pregunta = data.get("pregunta")
        estado   = data.get("estado", "idle")
        tx       = data.get("transcripcion", "")
        ronda    = data.get("ronda", 0)
        max_r    = data.get("max_rondas", 6)

        accent = self._accent_for("procesando" if pregunta else estado)
        self._render_icon(accent)
        self._render_wordmark()
        self._render_brackets(accent)

        if pregunta:
            self._status_lbl.config(text="CONFIRMACIÓN", fg=C_PROC)
            self._div.config(bg=C_PROC)
            pid = pregunta.get("id", "")
            if pid != self._pregunta_id:
                self._set_layout_pregunta(
                    pregunta.get("texto", ""),
                    pregunta.get("opciones", {"1": "Sí", "2": "No"}),
                    pid,
                )
        elif self._pregunta_id is not None:
            self._ocultar_botones()
            # volver al estado normal
            if estado == "idle":
                self._status_lbl.config(text="EN ESPERA", fg=C_IDLE)
                self._set_layout_idle()
            elif estado == "procesando":
                self._status_lbl.config(text="PROCESANDO", fg=C_PROC)
                self._div.config(bg=C_PROC)
                self._set_layout_procesando(ronda, max_r, tx)
        elif estado == "idle":
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
        self._win_h = max(self.root.winfo_reqheight(), 62)

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

    # ── Tick de animación (~60fps) ────────────────────────────────────────────
    def _tick(self) -> None:
        t      = time.monotonic() - self._t0
        estado = self._state.get("estado", "idle")
        accent = self._accent_for(estado)

        # Ecualizador: lerp suave hacia targets aleatorios (solo activo en procesando/hablando)
        if estado in ("procesando", "hablando"):
            self._eq_tick_count += 1
            if self._eq_tick_count >= EQ_RETARGET_TICKS:
                self._eq_tick_count = 0
                self._eq_targets = [random.uniform(0.25, 1.0) for _ in range(len(EQ_FRAC))]
            for i in range(len(EQ_FRAC)):
                self._eq_heights[i] += (self._eq_targets[i] - self._eq_heights[i]) * EQ_LERP
            self._render_eq(accent)

        # Dot: pulso por interpolación de color (seno continuo, sin saltos de radio)
        self._render_dot(accent)

        # Flotación vertical: función seno a ~60fps (movimiento continuo sin acumulación)
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
