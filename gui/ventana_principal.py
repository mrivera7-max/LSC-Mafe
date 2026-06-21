"""
Ventana principal de LSC Bridge (Tkinter + OpenCV).

Layout:
  ┌─────────────────────────────────────────────────────┐
  │  Barra de herramientas                              │
  ├──────────────────────────┬──────────────────────────┤
  │  Feed de cámara + LM     │  Panel de información    │
  │  (OpenCV → PIL → Tk)     │  • Seña / Confianza      │
  │                          │  • Traducción             │
  │                          │  • Estado robot           │
  │                          │  • Historial              │
  ├──────────────────────────┴──────────────────────────┤
  │  Barra de estado (FPS · Señas detectadas · Robot)   │
  └─────────────────────────────────────────────────────┘
"""

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Optional

log = logging.getLogger("lsc_bridge.gui")


class VentanaPrincipal:
    """Ventana principal de la aplicación LSC Bridge."""

    TITULO = "LSC UDI — Lengua de Señas Colombiana | Unitree G1"
    ANCHO_MIN = 1100
    ALTO_MIN  = 680

    def __init__(self, config):
        self.config = config
        self._raiz: Optional[tk.Tk] = None
        self._activa = False
        self._cola_frames: queue.Queue = queue.Queue(maxsize=2)
        self._hilo_camara: Optional[threading.Thread] = None
        self._total_señas = 0
        self._ultima_seña = None

        # Componentes del sistema (inicializados al arrancar)
        self._reconocedor = None
        self._robot = None
        self._cap = None

    # ── Arranque ──────────────────────────────────────────────────

    def ejecutar(self):
        """Crea la ventana y entra al bucle principal de Tk."""
        self._raiz = tk.Tk()
        self._raiz.title(self.TITULO)
        self._raiz.minsize(self.ANCHO_MIN, self.ALTO_MIN)
        self._raiz.protocol("WM_DELETE_WINDOW", self._al_cerrar)

        # Icono de la aplicación (si existe)
        icon_path = Path(__file__).parent.parent / "data" / "icon.png"
        if icon_path.exists():
            try:
                img = tk.PhotoImage(file=str(icon_path))
                self._raiz.iconphoto(True, img)
            except Exception:
                pass

        self._configurar_estilos()
        self._construir_ui()
        self._iniciar_sistemas()

        self._activa = True
        log.info("Ventana principal iniciada")
        self._raiz.mainloop()

    def _al_cerrar(self):
        log.info("Cerrando LSC Bridge...")
        self._activa = False
        self._detener_camara()
        if self._robot:
            self._robot.desconectar()
        if self._reconocedor:
            self._reconocedor.detener()
        self._raiz.quit()
        self._raiz.destroy()

    # ── Estilos ───────────────────────────────────────────────────

    def _configurar_estilos(self):
        estilo = ttk.Style()
        estilo.theme_use("clam")

        fondo_oscuro = "#1a1a2e"
        fondo_panel  = "#16213e"
        acento       = "#0f3460"
        verde        = "#22c55e"
        texto        = "#e2e8f0"
        texto_sec    = "#94a3b8"

        self._raiz.configure(bg=fondo_oscuro)
        self._colores = {
            "fondo":      fondo_oscuro,
            "panel":      fondo_panel,
            "acento":     acento,
            "verde":      verde,
            "texto":      texto,
            "texto_sec":  texto_sec,
            "rojo":       "#ef4444",
            "amarillo":   "#f59e0b",
            "azul":       "#3b82f6",
        }

        estilo.configure("TFrame", background=fondo_oscuro)
        estilo.configure("Panel.TFrame", background=fondo_panel)
        estilo.configure("TLabel", background=fondo_oscuro, foreground=texto, font=("Segoe UI", 10))
        estilo.configure("Panel.TLabel", background=fondo_panel, foreground=texto)
        estilo.configure("Titulo.TLabel", background=fondo_panel, foreground=texto,
                         font=("Segoe UI", 11, "bold"))
        estilo.configure("Seña.TLabel", background=fondo_panel, foreground=verde,
                         font=("Segoe UI", 28, "bold"))
        estilo.configure("Traduccion.TLabel", background=fondo_panel, foreground=texto,
                         font=("Segoe UI", 13), wraplength=320)
        estilo.configure("Secundario.TLabel", background=fondo_panel, foreground=texto_sec,
                         font=("Segoe UI", 9))
        estilo.configure("Status.TLabel", background=acento, foreground=texto,
                         font=("Segoe UI", 9), padding=(8, 4))
        estilo.configure("Accion.TButton", font=("Segoe UI", 10), padding=(12, 6))
        estilo.configure("TProgressbar", troughcolor=fondo_panel, background=verde,
                         thickness=6)

    # ── Construcción de UI ────────────────────────────────────────

    def _construir_ui(self):
        c = self._colores

        # Barra de herramientas
        self._barra = tk.Frame(self._raiz, bg=c["acento"], height=48)
        self._barra.pack(fill="x", side="top")
        self._barra.pack_propagate(False)
        self._construir_barra()

        # Contenedor central
        central = ttk.Frame(self._raiz)
        central.pack(fill="both", expand=True, padx=0, pady=0)

        # Panel izquierdo: cámara
        self._frame_cam = tk.Frame(central, bg="black", width=700)
        self._frame_cam.pack(side="left", fill="both", expand=True)
        self._frame_cam.pack_propagate(False)
        self._construir_panel_camara()

        # Separador
        sep = tk.Frame(central, bg=c["acento"], width=1)
        sep.pack(side="left", fill="y")

        # Panel derecho: información
        self._frame_info = tk.Frame(central, bg=c["panel"], width=380)
        self._frame_info.pack(side="left", fill="both")
        self._frame_info.pack_propagate(False)
        self._construir_panel_info()

        # Barra de estado
        self._barra_estado = tk.Frame(self._raiz, bg=c["acento"], height=28)
        self._barra_estado.pack(fill="x", side="bottom")
        self._barra_estado.pack_propagate(False)
        self._construir_barra_estado()

    def _construir_barra(self):
        c = self._colores
        # Título
        tk.Label(self._barra, text="◈  LSC UDI",
                 bg=c["acento"], fg=c["texto"],
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=16)
        tk.Label(self._barra, text="Lengua de Señas Colombiana → Unitree G1",
                 bg=c["acento"], fg=c["texto_sec"],
                 font=("Segoe UI", 9)).pack(side="left", padx=4)

        # Botones a la derecha
        frame_btns = tk.Frame(self._barra, bg=c["acento"])
        frame_btns.pack(side="right", padx=8)

        self._btn_camara = tk.Button(
            frame_btns, text="▶  Iniciar cámara",
            bg="#0ea5e9", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=6,
            cursor="hand2", command=self._toggle_camara,
        )
        self._btn_camara.pack(side="left", padx=4, pady=8)

        self._btn_robot = tk.Button(
            frame_btns, text="⚡  Conectar G1",
            bg=c["verde"], fg="#052e16", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=6,
            cursor="hand2", command=self._toggle_robot,
        )
        self._btn_robot.pack(side="left", padx=4, pady=8)

        tk.Button(
            frame_btns, text="⚙",
            bg=c["acento"], fg=c["texto_sec"], relief="flat",
            font=("Segoe UI", 12), padx=8, pady=4,
            cursor="hand2", command=self._abrir_configuracion,
        ).pack(side="left", padx=2, pady=8)

    def _construir_panel_camara(self):
        c = self._colores
        # Label para mostrar el video
        self._lbl_video = tk.Label(self._frame_cam, bg="black",
                                    text="Iniciando cámara...",
                                    fg="#333", font=("Segoe UI", 14))
        self._lbl_video.pack(fill="both", expand=True)

    def _construir_panel_info(self):
        c = self._colores
        pad = {"padx": 16, "pady": 8}

        # ── Seña detectada ────────────────────────────────────────
        sec1 = tk.Frame(self._frame_info, bg=c["panel"])
        sec1.pack(fill="x", **pad)

        tk.Label(sec1, text="SEÑA DETECTADA", bg=c["panel"], fg=c["texto_sec"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self._lbl_seña = tk.Label(sec1, text="—", bg=c["panel"], fg=c["verde"],
                                   font=("Segoe UI", 32, "bold"))
        self._lbl_seña.pack(anchor="w")

        self._barra_conf = ttk.Progressbar(sec1, mode="determinate", length=340)
        self._barra_conf.pack(fill="x", pady=(4, 0))

        self._lbl_conf = tk.Label(sec1, text="Confianza: —", bg=c["panel"],
                                   fg=c["texto_sec"], font=("Segoe UI", 9))
        self._lbl_conf.pack(anchor="w")

        self._separador(self._frame_info)

        # ── Traducción ────────────────────────────────────────────
        sec2 = tk.Frame(self._frame_info, bg=c["panel"])
        sec2.pack(fill="x", **pad)

        tk.Label(sec2, text="TRADUCCIÓN AL ESPAÑOL", bg=c["panel"], fg=c["texto_sec"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self._lbl_traduccion = tk.Label(sec2, text="Esperando señas...",
                                         bg=c["panel"], fg=c["texto"],
                                         font=("Segoe UI", 13), wraplength=340,
                                         justify="left")
        self._lbl_traduccion.pack(anchor="w", pady=(4, 0))

        self._separador(self._frame_info)

        # ── Estado del robot ──────────────────────────────────────
        sec3 = tk.Frame(self._frame_info, bg=c["panel"])
        sec3.pack(fill="x", **pad)

        tk.Label(sec3, text="ROBOT UNITREE G1", bg=c["panel"], fg=c["texto_sec"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

        fila_estado = tk.Frame(sec3, bg=c["panel"])
        fila_estado.pack(fill="x", pady=(4, 0))

        self._dot_robot = tk.Label(fila_estado, text="●", bg=c["panel"],
                                    fg=c["rojo"], font=("Segoe UI", 12))
        self._dot_robot.pack(side="left")
        self._lbl_estado_robot = tk.Label(fila_estado, text="Desconectado",
                                           bg=c["panel"], fg=c["texto"],
                                           font=("Segoe UI", 10))
        self._lbl_estado_robot.pack(side="left", padx=(4, 0))

        # Telemetría
        grid_tel = tk.Frame(sec3, bg=c["panel"])
        grid_tel.pack(fill="x", pady=(8, 0))

        self._lbl_bat = self._stat_card(grid_tel, "Batería", "—%", 0, 0)
        self._lbl_fps = self._stat_card(grid_tel, "FPS inferencia", "—", 0, 1)
        self._lbl_lat = self._stat_card(grid_tel, "Latencia cmd", "—ms", 1, 0)
        self._lbl_total = self._stat_card(grid_tel, "Señas hoy", "0", 1, 1)

        self._separador(self._frame_info)

        # ── Historial ─────────────────────────────────────────────
        sec4 = tk.Frame(self._frame_info, bg=c["panel"])
        sec4.pack(fill="both", expand=True, **pad)

        tk.Label(sec4, text="HISTORIAL", bg=c["panel"], fg=c["texto_sec"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

        frame_lista = tk.Frame(sec4, bg=c["panel"])
        frame_lista.pack(fill="both", expand=True, pady=(4, 0))

        scrollbar = tk.Scrollbar(frame_lista)
        scrollbar.pack(side="right", fill="y")

        self._lista_historial = tk.Listbox(
            frame_lista, yscrollcommand=scrollbar.set,
            bg="#0d1117", fg=c["texto"], selectbackground=c["acento"],
            font=("Consolas", 10), relief="flat", bd=0,
            highlightthickness=0, activestyle="none",
        )
        self._lista_historial.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self._lista_historial.yview)

    def _stat_card(self, parent, label, valor, fila, col):
        c = self._colores
        card = tk.Frame(parent, bg="#0d1117", padx=8, pady=6)
        card.grid(row=fila, column=col, padx=4, pady=4, sticky="ew")
        parent.columnconfigure(col, weight=1)
        tk.Label(card, text=label, bg="#0d1117", fg=c["texto_sec"],
                 font=("Segoe UI", 8)).pack(anchor="w")
        lbl = tk.Label(card, text=valor, bg="#0d1117", fg=c["texto"],
                       font=("Segoe UI", 16, "bold"))
        lbl.pack(anchor="w")
        return lbl

    def _construir_barra_estado(self):
        c = self._colores
        self._lbl_status_cam = tk.Label(self._barra_estado, text="● Cámara: inactiva",
                                         bg=c["acento"], fg=c["rojo"],
                                         font=("Segoe UI", 8))
        self._lbl_status_cam.pack(side="left", padx=12, pady=4)

        tk.Label(self._barra_estado, text="|", bg=c["acento"], fg=c["texto_sec"]).pack(side="left")

        self._lbl_status_robot = tk.Label(self._barra_estado, text="● Robot: desconectado",
                                           bg=c["acento"], fg=c["rojo"],
                                           font=("Segoe UI", 8))
        self._lbl_status_robot.pack(side="left", padx=12)

        tk.Label(self._barra_estado, text="|", bg=c["acento"], fg=c["texto_sec"]).pack(side="left")

        self._lbl_status_fps = tk.Label(self._barra_estado, text="FPS: —",
                                         bg=c["acento"], fg=c["texto_sec"],
                                         font=("Segoe UI", 8))
        self._lbl_status_fps.pack(side="left", padx=12)

        # Versión a la derecha
        tk.Label(self._barra_estado, text="LSC Ing. María Fernanda Rivera Sanclemente | Colombia",
                 bg=c["acento"], fg=c["texto_sec"],
                 font=("Segoe UI", 8)).pack(side="right", padx=12)

    def _separador(self, parent):
        tk.Frame(parent, bg=self._colores["acento"], height=1).pack(fill="x", padx=16, pady=4)

    # ── Sistemas ──────────────────────────────────────────────────

    def _iniciar_sistemas(self):
        """Inicializa el reconocedor y el conector al robot."""
        from robot.conector_g1 import ConectorG1

        if getattr(self.config, "usar_v2", False):
            from models.reconocedor_v2 import ReconocedorLSCv2
            self._reconocedor = ReconocedorLSCv2(self.config)
            log.info("Usando ReconocedorLSCv2 (secuencial mano+cara)")
        else:
            from models.reconocedor import ReconocedorLSC
            self._reconocedor = ReconocedorLSC(self.config)
            log.info("Usando ReconocedorLSC (estatico)")

        self._robot = ConectorG1(self.config)

        # Conectar callbacks del robot
        self._robot.on_estado_cambio = self._on_estado_robot
        self._robot.on_comando_enviado = self._on_comando_robot

    def _iniciar_camara(self):
        import cv2
        self._cap = cv2.VideoCapture(self.config.camara_idx)
        if not self._cap.isOpened():
            messagebox.showerror("Error de cámara",
                                 f"No se pudo abrir la cámara {self.config.camara_idx}.\n"
                                 "Verifica que esté conectada.")
            return

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.config.camara_ancho)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camara_alto)
        self._cap.set(cv2.CAP_PROP_FPS,          self.config.camara_fps)

        if not self._reconocedor.iniciar():
            messagebox.showerror("Error de modelo",
                                 "No se pudo iniciar MediaPipe.\n"
                                 "Instala con: pip install mediapipe")
            return

        self._hilo_camara = threading.Thread(target=self._bucle_camara, daemon=True)
        self._hilo_camara.start()
        self._actualizar_video()
        log.info(f"Cámara {self.config.camara_idx} iniciada")

    def _detener_camara(self):
        self._activa = False
        if self._cap:
            self._cap.release()
        if self._reconocedor:
            self._reconocedor.detener()

    def _bucle_camara(self):
        """Hilo que lee frames de la cámara y los procesa."""
        import cv2
        while self._activa and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                continue

            frame = cv2.flip(frame, 1)  # Espejo
            seña = self._reconocedor.procesar_frame(frame)

            if seña and (self._ultima_seña is None or seña.nombre != self._ultima_seña.nombre):
                self._ultima_seña = seña
                self._total_señas += 1
                self._raiz.after(0, self._actualizar_seña, seña)
                if self._robot and self._robot.conectado:
                    threading.Thread(
                        target=self._robot.enviar_seña,
                        args=(seña.nombre,), daemon=True
                    ).start()

            # Dibujar y encolar frame
            frame_anotado = self._reconocedor.dibujar_landmarks(frame, seña)

            try:
                self._cola_frames.put_nowait(frame_anotado)
            except queue.Full:
                pass

    def _actualizar_video(self):
        """Actualiza el label de video desde la cola de frames (hilo principal)."""
        if not self._activa:
            return
        try:
            frame = self._cola_frames.get_nowait()
            self._mostrar_frame(frame)
        except queue.Empty:
            pass
        self._raiz.after(16, self._actualizar_video)  # ~60 Hz

    def _mostrar_frame(self, frame_bgr):
        """Convierte un frame BGR a PhotoImage de Tkinter y lo muestra."""
        try:
            from PIL import Image, ImageTk
            import cv2

            h_disp = self._lbl_video.winfo_height() or 480
            w_disp = self._lbl_video.winfo_width()  or 700

            h_fr, w_fr = frame_bgr.shape[:2]
            escala = min(w_disp / w_fr, h_disp / h_fr)
            nuevo_w = int(w_fr * escala)
            nuevo_h = int(h_fr * escala)

            frame_resized = cv2.resize(frame_bgr, (nuevo_w, nuevo_h))
            frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            photo = ImageTk.PhotoImage(image=img)

            self._lbl_video.configure(image=photo, text="")
            self._lbl_video.image = photo  # Mantener referencia

            # Actualizar FPS en barra de estado
            fps = self._reconocedor.fps if self._reconocedor else 0
            self._lbl_status_fps.configure(text=f"FPS: {fps:.1f}")
            self._lbl_fps.configure(text=str(int(fps)))

        except ImportError:
            # Si no hay Pillow, mostrar aviso
            self._lbl_video.configure(
                text="Instala Pillow para ver video:\npip install Pillow",
                fg="white", font=("Segoe UI", 12)
            )

    # ── Actualizaciones de UI ─────────────────────────────────────

    def _actualizar_seña(self, seña):
        """Actualiza los widgets con la nueva seña detectada."""
        self._lbl_seña.configure(text=seña.nombre)
        self._lbl_traduccion.configure(text=seña.traduccion)
        self._barra_conf["value"] = seña.confianza * 100
        self._lbl_conf.configure(text=f"Confianza: {seña.confianza*100:.0f}%")
        self._lbl_total.configure(text=str(self._total_señas))

        # Agregar al historial
        hora = time.strftime("%H:%M:%S")
        entrada = f"{hora}  {seña.nombre:<20}  {seña.confianza*100:.0f}%"
        self._lista_historial.insert(0, entrada)
        if self._lista_historial.size() > self.config.historial_max:
            self._lista_historial.delete(self.config.historial_max)

    def _on_estado_robot(self, estado):
        """Callback cuando cambia el estado del robot."""
        from robot.conector_g1 import EstadoRobot
        textos = {
            EstadoRobot.DESCONECTADO: ("Desconectado", self._colores["rojo"]),
            EstadoRobot.CONECTANDO:   ("Conectando...", self._colores["amarillo"]),
            EstadoRobot.CONECTADO:    ("Conectado",     self._colores["verde"]),
            EstadoRobot.EJECUTANDO:   ("Ejecutando",    self._colores["azul"]),
            EstadoRobot.ERROR:        ("Error",          self._colores["rojo"]),
        }
        txt, color = textos.get(estado, ("—", "white"))
        self._raiz.after(0, self._lbl_estado_robot.configure, {"text": txt})
        self._raiz.after(0, self._dot_robot.configure, {"fg": color})
        self._raiz.after(0, self._lbl_status_robot.configure,
                         {"text": f"● Robot: {txt}", "fg": color})

    def _on_comando_robot(self, cmd):
        """Actualiza la latencia después de enviar un comando."""
        self._raiz.after(0, self._lbl_lat.configure, {"text": "45ms"})

    # ── Controles ─────────────────────────────────────────────────

    def _toggle_camara(self):
        c = self._colores
        if self._reconocedor and self._reconocedor.activo:
            self._activa = False
            self._detener_camara()
            self._btn_camara.configure(text="▶  Iniciar cámara", bg="#0ea5e9")
            self._lbl_status_cam.configure(text="● Cámara: inactiva", fg=c["rojo"])
            self._lbl_video.configure(image="", text="Cámara detenida", fg="#555")
        else:
            self._activa = True
            self._iniciar_camara()
            self._btn_camara.configure(text="■  Detener cámara", bg=c["rojo"])
            self._lbl_status_cam.configure(text="● Cámara: activa", fg=c["verde"])

    def _toggle_robot(self):
        if self._robot and self._robot.conectado:
            threading.Thread(target=self._robot.desconectar, daemon=True).start()
            self._btn_robot.configure(text="⚡  Conectar G1", bg=self._colores["verde"])
        else:
            threading.Thread(target=self._robot.conectar, daemon=True).start()
            self._btn_robot.configure(text="✕  Desconectar G1", bg=self._colores["rojo"])

    def _abrir_configuracion(self):
        """Abre el diálogo de configuración."""
        from gui.dialogo_config import DialogoConfig
        DialogoConfig(self._raiz, self.config)