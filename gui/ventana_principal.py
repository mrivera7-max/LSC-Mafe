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
        self._t_ultima_seña = 0.0         # última vez que el reconocedor vio una seña (para poder repetirla)
        self._replicar = True             # replicar en el simulador / robot las señas detectadas
        self.estado_destino = None        # callable -> (texto, color); lo asigna app_unificada

        # Componentes del sistema (inicializados al arrancar)
        self._reconocedor = None
        self._robot = None
        self._cap = None
        self._gen_video = 0              # invalida el bucle de dibujo anterior al cambiar de cámara
        self._fuente_cam = None          # "usb" | "g1" | None (ninguna)

    # ── Arranque ──────────────────────────────────────────────────

    def ejecutar(self):
        """Crea la ventana y entra al bucle principal de Tk (modo autónomo)."""
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

        self._contenedor = self._raiz  # los widgets cuelgan de la raíz
        self._configurar_estilos()
        self._construir_ui()
        self._iniciar_sistemas()

        self._activa = True
        log.info("Ventana principal iniciada")
        self._raiz.mainloop()

    def montar_en(self, contenedor, raiz):
        """Construye la UI de la cámara dentro de un contenedor externo
        (una pestaña del Notebook), compartiendo la raíz de la app unificada.

        No crea su propio tk.Tk() ni mainloop: eso lo maneja AppUnificada.
        """
        self._raiz = raiz              # se usa para .after(...) desde los hilos
        self._contenedor = contenedor  # los widgets cuelgan de la pestaña
        self._configurar_estilos()
        self._construir_ui()
        self._iniciar_sistemas()
        self._activa = True
        log.info("Panel de cámara montado en pestaña")

    def cerrar(self):
        """Libera cámara, robot y reconocedor sin destruir la raíz.
        (La raíz la destruye AppUnificada.)"""
        self._activa = False
        self._detener_camara()
        if self._robot:
            try:
                self._robot.desconectar()
            except Exception:
                pass
        if self._reconocedor:
            try:
                self._reconocedor.detener()
            except Exception:
                pass

    def _al_cerrar(self):
        log.info("Cerrando LSC Bridge...")
        self.cerrar()
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

        if getattr(self, "_contenedor", self._raiz) is self._raiz:
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

        cont = getattr(self, "_contenedor", self._raiz)

        # Barra de herramientas
        self._barra = tk.Frame(cont, bg=c["acento"], height=48)
        self._barra.pack(fill="x", side="top")
        self._barra.pack_propagate(False)
        self._construir_barra()

        # Contenedor central
        central = ttk.Frame(cont)
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
        self._barra_estado = tk.Frame(cont, bg=c["acento"], height=28)
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

        self._btn_cam_pc = tk.Button(
            frame_btns, text="📷  Cámara PC",
            bg="#0ea5e9", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=6,
            cursor="hand2", command=lambda: self._activar_camara("usb"),
        )
        self._btn_cam_pc.pack(side="left", padx=4, pady=8)

        self._btn_cam_g1 = tk.Button(
            frame_btns, text="🤖  Cámara G1",
            bg=c["verde"], fg="#052e16", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=6,
            cursor="hand2", command=lambda: self._activar_camara("g1"),
        )
        self._btn_cam_g1.pack(side="left", padx=4, pady=8)

        # Conector antiguo del G1 (solo si la app NO se abrió con --sin-robot).
        # El G1 de SONIC (MuJoCo / real) se conecta desde la pestaña Voz / Texto → Seña.
        self._btn_robot = tk.Button(
            frame_btns, text="⚡  Conectar G1",
            bg=c["verde"], fg="#052e16", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=6,
            cursor="hand2", command=self._toggle_robot,
        )
        if getattr(self.config, "robot_activo", False):
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
                                    text="Pulsa «Cámara PC» o «Cámara G1» para empezar",
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

        tk.Label(self._barra_estado, text="|", bg=c["acento"], fg=c["texto_sec"]).pack(side="left")

        # Réplica de la seña detectada en el simulador (MuJoCo) o el robot real de SONIC
        self._lbl_status_sim = tk.Label(self._barra_estado, text="● Simulador: sin enlazar",
                                         bg=c["acento"], fg=c["rojo"], font=("Segoe UI", 8))
        self._lbl_status_sim.pack(side="left", padx=(12, 4))
        self._var_replicar = tk.BooleanVar(master=self._barra_estado, value=True)
        tk.Checkbutton(self._barra_estado, text="Replicar la seña detectada", variable=self._var_replicar,
                       command=lambda: setattr(self, "_replicar", bool(self._var_replicar.get())),
                       bg=c["acento"], fg=c["texto_sec"], selectcolor=c["acento"],
                       activebackground=c["acento"], activeforeground=c["texto"],
                       font=("Segoe UI", 8), cursor="hand2").pack(side="left")
        self._refrescar_estado_sim()

        # Versión a la derecha
        tk.Label(self._barra_estado, text="LSC Ing. María Fernanda Rivera Sanclemente | Colombia",
                 bg=c["acento"], fg=c["texto_sec"],
                 font=("Segoe UI", 8)).pack(side="right", padx=12)

    def _refrescar_estado_sim(self):
        """Cada segundo muestra si hay un simulador / robot de SONIC enlazado para replicar las señas."""
        try:
            texto, color = self.estado_destino() if self.estado_destino else ("Simulador: sin enlazar", self._colores["rojo"])
            self._lbl_status_sim.configure(text=f"● {texto}", fg=color)
            self._raiz.after(1000, self._refrescar_estado_sim)
        except tk.TclError:
            pass                                     # la ventana se cerró

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

    def _iniciar_camara(self, fuente: str = None) -> bool:
        """Abre la cámara ("usb" = de la PC, "g1" = del robot) y arranca el reconocimiento.
        True si quedó en marcha."""
        import cv2
        fuente = fuente or getattr(self.config, "camara_fuente", "usb")
        if fuente == "g1":
            from robot.camara_g1 import CamaraG1
            self._cap = CamaraG1(self.config.camara_g1_host, self.config.camara_g1_puerto,
                                 binocular=self.config.camara_g1_binocular)
            if not self._cap.isOpened():
                messagebox.showerror("Cámara del G1", self._cap.error or "No se pudo abrir la cámara del G1.")
                self._cap = None
                return False
            nombre_cam = f"del G1 ({self.config.camara_g1_host})"
        else:
            self._cap = cv2.VideoCapture(self.config.camara_idx)
            if not self._cap.isOpened():
                messagebox.showerror("Error de cámara",
                                     f"No se pudo abrir la cámara {self.config.camara_idx}.\n"
                                     "Verifica que esté conectada.")
                self._cap.release()
                self._cap = None
                return False
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.config.camara_ancho)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camara_alto)
            self._cap.set(cv2.CAP_PROP_FPS,          self.config.camara_fps)
            nombre_cam = str(self.config.camara_idx)

        if not self._reconocedor.iniciar():
            messagebox.showerror("Error de modelo",
                                 "No se pudo iniciar MediaPipe.\n"
                                 "Instala con: pip install mediapipe")
            self._cap.release()
            self._cap = None
            return False

        self._hilo_camara = threading.Thread(target=self._bucle_camara, daemon=True)
        self._hilo_camara.start()
        self._gen_video += 1
        self._actualizar_video(self._gen_video)
        log.info(f"Cámara {nombre_cam} iniciada")
        return True

    def _detener_camara(self):
        self._activa = False
        hilo = self._hilo_camara
        if hilo is not None and hilo.is_alive() and hilo is not threading.current_thread():
            hilo.join(timeout=3.0)      # que el hilo viejo termine antes de abrir otra cámara
        self._hilo_camara = None
        if self._cap:
            self._cap.release()
            self._cap = None
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

            ahora = time.time()
            if seña:
                self._t_ultima_seña = ahora
            elif self._ultima_seña is not None and ahora - self._t_ultima_seña > 1.5:
                self._ultima_seña = None      # sin seña por 1.5 s: la misma seña puede repetirse

            if seña and (self._ultima_seña is None or seña.nombre != self._ultima_seña.nombre):
                self._ultima_seña = seña
                self._total_señas += 1
                self._raiz.after(0, self._actualizar_seña, seña)
                # Primero el G1 vía SONIC (MuJoCo o real), si la pestaña Voz lo conectó
                hook = getattr(self, "ejecutar_seña_sonic", None)
                if hook is not None and self._replicar and hook(seña):
                    pass
                elif self._robot and self._robot.conectado:
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

    def _actualizar_video(self, gen=None):
        """Actualiza el label de video desde la cola de frames (hilo principal)."""
        if not self._activa or (gen is not None and gen != self._gen_video):
            return
        try:
            frame = self._cola_frames.get_nowait()
            self._mostrar_frame(frame)
        except queue.Empty:
            pass
        self._raiz.after(16, self._actualizar_video, gen)  # ~60 Hz

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

    def _estilo_botones_camara(self):
        c = self._colores
        nombres = {"usb": ("📷  Cámara PC", self._btn_cam_pc, "#0ea5e9", "white"),
                   "g1": ("🤖  Cámara G1", self._btn_cam_g1, c["verde"], "#052e16")}
        for fuente, (texto, boton, bg, fg) in nombres.items():
            if self._fuente_cam == fuente:
                boton.configure(text=f"■  Detener {texto.split('  ')[1]}", bg=c["rojo"], fg="white")
            else:
                boton.configure(text=texto, bg=bg, fg=fg)

    def _activar_camara(self, fuente: str):
        """Botones «Cámara PC» / «Cámara G1»: activan esa cámara; si ya es la activa, la detienen;
        si hay otra activa, la cambian."""
        c = self._colores
        if getattr(self, "_preparando_g1", False):
            return
        mismo = self._fuente_cam == fuente and self._reconocedor and self._reconocedor.activo
        if self._fuente_cam is not None or (self._reconocedor and self._reconocedor.activo):
            self._detener_camara()
            self._fuente_cam = None
            self._lbl_status_cam.configure(text="● Cámara: inactiva", fg=c["rojo"])
            self._lbl_video.configure(image="", text="Cámara detenida", fg="#555")
        if mismo:
            self._estilo_botones_camara()
            return
        if fuente == "g1" and getattr(self.config, "camara_g1_auto", True):
            self._preparar_g1_y_abrir()
            return
        self._abrir_fuente(fuente)

    def _preparar_g1_y_abrir(self):
        """Cámara G1: primero deja listo el servidor del robot por SSH (en un hilo) y luego abre."""
        if getattr(self, "_preparando_g1", False):
            return
        self._preparando_g1 = True
        for b in (self._btn_cam_pc, self._btn_cam_g1):
            b.configure(state="disabled")
        self._lbl_video.configure(image="", text="Preparando la cámara del robot…", fg="#555")

        def aviso(msg):
            self._contenedor.after(0, lambda: self._lbl_video.configure(image="", text=msg, fg="#555"))

        def tarea():
            from robot.servidor_camara_g1 import ServidorCamaraG1, ErrorCamaraG1
            cfg = self.config
            srv = ServidorCamaraG1(cfg.camara_g1_host, getattr(cfg, "camara_g1_usuario", "unitree"),
                                   getattr(cfg, "camara_g1_clave", None),
                                   nombre_camara=getattr(cfg, "camara_g1_nombre", "OBSBOT"))
            try:
                puerto = srv.preparar(aviso)
                error = None
            except ErrorCamaraG1 as e:
                puerto, error = None, str(e)
            except Exception as e:  # noqa: BLE001
                puerto, error = None, f"Error inesperado: {e}"
            self._contenedor.after(0, lambda: self._g1_preparado(puerto, error))

        threading.Thread(target=tarea, daemon=True).start()

    def _g1_preparado(self, puerto, error):
        self._preparando_g1 = False
        for b in (self._btn_cam_pc, self._btn_cam_g1):
            b.configure(state="normal")
        if error:
            messagebox.showerror("Cámara del G1", error)
            self._lbl_video.configure(image="", text="Cámara detenida", fg="#555")
            self._estilo_botones_camara()
            return
        self.config.camara_g1_puerto = puerto      # la externa sale por 55556, no por el 55555 de la cabeza
        self.config.camara_g1_binocular = False
        self._abrir_fuente("g1")

    def _abrir_fuente(self, fuente: str):
        c = self._colores
        etiqueta = {"usb": "PC", "g1": "G1"}[fuente]
        self._lbl_video.configure(image="", text=f"Abriendo cámara {etiqueta}…", fg="#555")
        self._lbl_video.update_idletasks()
        self._activa = True
        if not self._iniciar_camara(fuente):          # falló: la interfaz sigue en «inactiva»
            self._activa = False
            self._fuente_cam = None
            self._lbl_video.configure(image="", text="Cámara detenida", fg="#555")
            self._estilo_botones_camara()
            self._lbl_status_cam.configure(text="● Cámara: inactiva", fg=c["rojo"])
            return
        self._fuente_cam = fuente
        self._estilo_botones_camara()
        self._lbl_status_cam.configure(text=f"● Cámara {etiqueta}: activa", fg=c["verde"])

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