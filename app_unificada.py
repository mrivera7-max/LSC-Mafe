"""
LSC UDI — Ventana unificada (cámara + voz/texto en una sola aplicación)
========================================================================
Reúne los dos sistemas en una sola ventana con pestañas:

  ┌─────────────────────────────────────────────────┐
  │  [ Cámara → Seña ]   [ Voz/Texto → Seña ]        │  <- pestañas
  ├─────────────────────────────────────────────────┤
  │                                                   │
  │   (contenido de la pestaña activa)                │
  │                                                   │
  └─────────────────────────────────────────────────┘

El avatar 3D (Three.js/WebGL) sigue abriéndose en el navegador: Tkinter
no puede renderizar WebGL dentro de una ventana. La pestaña de voz lo
lanza igual que antes, mediante el servidor HTTP/WebSocket.

Uso:
    python app_unificada.py             # ambas pestañas, con robot
    python app_unificada.py --sin-robot # sin conectar el G1
    python app_unificada.py --v2        # reconocedor v2 (recomendado)
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import configurar_logger
from utils.config import Configuracion
from gui.ventana_principal import VentanaPrincipal
from voz_a_sena.gui_principal import VentanaVozASena

log = logging.getLogger("lsc_udi.app")


class AppUnificada:
    """Contenedor único: una ventana raíz, un Notebook, dos sistemas."""

    TITULO = "LSC UDI — Lengua de Señas Colombiana | Unitree G1"
    ANCHO_MIN = 1100
    ALTO_MIN = 700

    def __init__(self, config, sonic_opciones=None, verificar_al_inicio: bool = True):
        self.verificar_al_inicio = verificar_al_inicio
        self.config = config
        self.sonic_opciones = sonic_opciones or {}
        self._raiz = None
        self.panel_camara = None   # instancia de VentanaPrincipal
        self.panel_voz = None      # instancia de VentanaVozASena

    def ejecutar(self):
        self._raiz = tk.Tk()
        self._raiz.title(self.TITULO)
        self._raiz.minsize(self.ANCHO_MIN, self.ALTO_MIN)
        self._raiz.configure(bg="#1a1a2e")
        self._raiz.protocol("WM_DELETE_WINDOW", self._al_cerrar)

        # ── Notebook (pestañas) ───────────────────────────────────
        estilo = ttk.Style()
        try:
            estilo.theme_use("clam")
        except tk.TclError:
            pass
        estilo.configure("TNotebook", background="#1a1a2e", borderwidth=0)
        estilo.configure("TNotebook.Tab", padding=(20, 10),
                         font=("Segoe UI", 10, "bold"))
        estilo.map("TNotebook.Tab",
                   background=[("selected", "#0f3460"), ("!selected", "#16213e")],
                   foreground=[("selected", "#e2e8f0"), ("!selected", "#94a3b8")])

        notebook = ttk.Notebook(self._raiz)
        notebook.pack(fill="both", expand=True)

        tab_con0 = tk.Frame(notebook, bg="#1a1a2e")
        notebook.add(tab_con0, text="  🔌  Conexión  ")
        tab_cam = ttk.Frame(notebook)
        tab_voz = ttk.Frame(notebook)
        notebook.add(tab_cam, text="  📷  Cámara → Seña  ")
        notebook.add(tab_voz, text="  🎤  Voz / Texto → Seña  ")
        tab_con = tk.Frame(notebook, bg="#0d1117")
        notebook.add(tab_con, text="  🖥  Consola  ")
        tab_datos = tk.Frame(notebook, bg="#1a1a2e")
        notebook.add(tab_datos, text="  📁  Datos  ")

        # ── Montar cada sistema dentro de su pestaña ──────────────
        # Cada clase construye su UI sobre el frame que le pasamos,
        # en vez de crear su propio tk.Tk(). Comparten la misma raíz.
        self.panel_camara = VentanaPrincipal(self.config)
        self.panel_camara.montar_en(tab_cam, self._raiz)

        self.panel_voz = VentanaVozASena(**self.sonic_opciones)
        self.panel_voz.montar_en(tab_voz, self._raiz)
        self.panel_voz.montar_consola(tab_con, lambda: notebook.select(tab_con))
        self.panel_voz.robot = self.panel_camara._robot  # compartir el conector
        # La seña que detecta la cámara también la ejecuta el G1 de SONIC (MuJoCo o real)
        self.panel_camara.ejecutar_seña_sonic = self.panel_voz.ejecutar_desde_camara
        self.panel_camara.estado_destino = self.panel_voz.estado_replica

        # ── Datos: capturar señas con la cámara, o cargar capturas, y reentrenar
        from gui.panel_datos import PanelDatos
        self.panel_datos = PanelDatos(tab_datos, self.config, self._raiz,
                                      antes_de_capturar=self.panel_camara._detener_camara,
                                      tras_entrenar=self._recargar_modelo)
        self.panel_datos.pack(fill="both", expand=True)
        # «Enseñar» en Voz/Texto lleva a esta pestaña con el nombre de la seña ya puesto
        self.panel_voz.abrir_datos = lambda nombre=None: (self.panel_datos.preparar(nombre or ""),
                                                          notebook.select(tab_datos))

        # ── Verificación de arranque: deja todo listo y lo muestra en la pestaña «Conexión»
        from gui.panel_conexion import PanelConexion
        from robot.verificacion import Verificador
        self.panel_conexion = PanelConexion(
            tab_con0, Verificador(self.config, self.panel_voz.lanzador), self._raiz)
        self.panel_conexion.pack(fill="both", expand=True)
        if self.verificar_al_inicio:
            self._raiz.after(400, self.panel_conexion.verificar)
        else:
            notebook.select(tab_cam)

        log.info("Ventana unificada iniciada (5 pestañas: conexión, cámara, voz, consola y datos)")
        self._raiz.mainloop()

    def _recargar_modelo(self, clases) -> str:
        """Tras reentrenar: la cámara usa el modelo nuevo y el texto entiende las señas nuevas, sin reiniciar."""
        partes = []
        rec = getattr(self.panel_camara, "_reconocedor", None)
        if rec is not None and hasattr(rec, "_cargar_clasificador"):
            rec._cargar_clasificador()
            partes.append("cámara recargada")
        trad = getattr(getattr(self.panel_voz, "servidor", None), "traductor", None)
        if trad is not None:
            trad.registrar_señas(clases)
            partes.append("texto actualizado")
        if not partes:
            return "Reinicia la app para usar el modelo nuevo."
        return "(" + ", ".join(partes) + ")"

    def _al_cerrar(self):
        log.info("Cerrando LSC UDI...")
        try:
            if getattr(self, "panel_datos", None):
                self.panel_datos.cerrar()
        except Exception as e:
            log.warning(f"Error al cerrar el capturador: {e}")
        # Cerrar limpiamente ambos sistemas
        try:
            if self.panel_camara:
                self.panel_camara.cerrar()
        except Exception as e:
            log.warning(f"Error al cerrar cámara: {e}")
        try:
            if self.panel_voz:
                self.panel_voz.cerrar()
                self.panel_voz.cerrar_procesos()
        except Exception as e:
            log.warning(f"Error al cerrar voz: {e}")
        self._raiz.quit()
        self._raiz.destroy()


def parsear_args():
    p = argparse.ArgumentParser(description="LSC UDI — aplicación unificada")
    p.add_argument("--sin-robot", action="store_true",
                   help="Ejecutar sin conectar al robot G1")
    p.add_argument("--simulacion", action="store_true",
                   help="Forzar simulación aunque el robot esté conectado (equivale a --sin-robot)")
    p.add_argument("--estatico", action="store_true",
                   help="Usar el reconocedor estático en vez del v2 (por defecto se usa v2)")
    p.add_argument("--v2", action="store_true",
                   help="(ya es el valor por defecto) reconocedor v2 secuencial mano+cara")
    p.add_argument("--camara", type=int, default=0, help="Índice de cámara")
    p.add_argument("--camara-g1", nargs="?", const="192.168.123.164", metavar="IP",
                   help="IP del PC2 del G1 (teleimager-server) para el botón «Cámara G1» (default 192.168.123.164)")
    p.add_argument("--camara-g1-puerto", type=int, default=None, metavar="N",
                   help="Puerto ZMQ de la cámara en teleimager (default 55555; una cámara adicional suele ser 55556)")
    p.add_argument("--sin-verificar", action="store_true",
                   help="no verificar conexiones al abrir (abre directo en la pestaña de cámara)")
    p.add_argument("--terminales", action="store_true",
                   help="abrir MuJoCo y el deploy en terminales externas (por defecto corren dentro de la app)")
    p.add_argument("--sin-auto-g1", action="store_true",
                   help="«Cámara G1» NO prepara el robot por SSH (teleimager-server ya corre a mano)")
    p.add_argument("--g1-usuario", default=None, metavar="USR", help="usuario SSH del PC2 del G1 (defecto: el de Ajustes, unitree)")
    p.add_argument("--g1-clave", default=None, metavar="CLAVE",
                   help="clave SSH del PC2 (necesita sshpass; mejor usar ssh-copy-id una vez)")
    p.add_argument("--g1-camara-nombre", default=None, metavar="TXT",
                   help="texto que identifica la cámara externa en v4l2-ctl (defecto OBSBOT)")
    p.add_argument("--camara-g1-mono", action="store_true",
                   help="Forzar imagen de un solo lente (no recortar). Por defecto se detecta solo")
    p.add_argument("--sonic-real", action="store_true",
                   help="El deploy SONIC mueve el G1 FÍSICO (no MuJoCo): pide confirmación de seguridad, "
                        "limita amplitud/velocidad y habilita el botón PARAR (ver docs/g1_real.md)")
    p.add_argument("--sonic-escala", type=float, default=None, metavar="0-1",
                   help="Fracción de la amplitud de las señas (default 0.6 en real, 1.0 en simulación)")
    p.add_argument("--sonic-tiempo", type=float, default=None, metavar="X",
                   help="Factor de duración de cada tramo; >1 = más lento (default 1.5 en real, 1.0 en sim)")
    p.add_argument("--sonic-vel-max", type=float, default=None, metavar="RAD/S",
                   help="Tope de velocidad articular en rad/s (default 1.2 en real, sin tope en sim)")
    p.add_argument("--manos", choices=["auto", "dex3", "inspire", "ambas", "ninguna"], default=None,
                   help="dedos de la mano Inspire RH56DFTP-2R: auto = simulador -> manos Dex3 de MuJoCo, "
                        "real -> Inspire por DDS (rt/inspire_hand/ctrl); ninguna = sin mover los dedos")
    p.add_argument("--sin-auto-enlace", action="store_true",
                   help="no enlazar solo con el simulador cuando la cámara detecta una seña")
    p.add_argument("--sonic-confianza", type=float, default=None, metavar="0-1",
                   help="Confianza mínima de la seña detectada por cámara para que el G1 la ejecute (default 0.8)")
    p.add_argument("--mic", choices=["auto", "pc", "g1", "robot"], default=None,
                   help="Micrófono de voz: robot = el micrófono USB conectado al PC2 del robot (se detecta y arranca "
                        "por SSH), g1 = multicast del G1, pc = esta PC, "
                        "auto = el del robot al usar el robot real (con respaldo a la PC), PC en simulación")
    p.add_argument("--mic-indice", type=int, default=None, metavar="N",
                   help="índice del micrófono de la PC (ver: python -m voz_a_sena.probar_microfono niveles)")
    p.add_argument("--mic-g1-ip", default=None, metavar="IP",
                   help="IP de esta PC en la red del robot (192.168.123.x) para recibir el audio")
    p.add_argument("--gr00t-dir", default=None, metavar="RUTA",
                   help="Carpeta GR00T-WholeBodyControl (para abrir MuJoCo/deploy desde la app). "
                        "También vale la variable GR00T_DIR")
    p.add_argument("--iface", default=None, metavar="NOMBRE",
                   help="Interfaz de red cableada del robot para el deploy real (defecto enp131s0)")
    p.add_argument("--mujoco-pos", default="0,0", metavar="X,Y",
                   help="Posición de la ventana de MuJoCo en pantalla (defecto 0,0 = esquina "
                        "superior izquierda; 'no' la deja donde el sistema la ponga). Requiere xdotool")
    p.add_argument("--mujoco-tam", default=None, metavar="ANCHOxALTO",
                   help="Tamaño de la ventana de MuJoCo, p. ej. 960x600")
    p.add_argument("--config", type=str, default="config.json")
    p.add_argument("--log-nivel", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   default="INFO")
    return p.parse_args()


def _par(texto, sep, nombre):
    """'a,b' o 'axb' -> (a, b); None / 'no' -> None."""
    if texto is None or texto.strip().lower() in ("no", "ninguno", ""):
        return None
    try:
        a, b = texto.lower().split(sep)
        return int(a), int(b)
    except ValueError:
        raise SystemExit(f"{nombre}: formato inválido ({texto!r}), esperaba dos números separados por «{sep}»")


def robot_alcanzable(host: str, puerto: int = 22, espera: float = 2.0) -> bool:
    """True si el PC2 del G1 responde por red (SSH)."""
    import socket
    try:
        with socket.create_connection((host, puerto), timeout=espera):
            return True
    except OSError:
        return False


def main():
    args = parsear_args()
    configurar_logger(nivel=args.log_nivel)
    log.info("=" * 60)
    log.info("  LSC UDI — Aplicación unificada (cámara + voz)")
    log.info("=" * 60)

    config = Configuracion(args.config)
    config.camara_idx = args.camara
    if args.camara_g1:
        config.camara_g1_host = args.camara_g1
    # Puerto y modo mono valen para el botón «Cámara G1» aunque no se pase --camara-g1
    if args.camara_g1_puerto:
        config.camara_g1_puerto = args.camara_g1_puerto
    if args.camara_g1_mono:
        config.camara_g1_binocular = False
    config.camara_g1_auto = not args.sin_auto_g1
    # Las opciones de línea de comandos mandan; si no se dan, valen los Ajustes guardados en config.json
    if args.g1_usuario:
        config.camara_g1_usuario = args.g1_usuario
    if args.g1_clave:
        config.camara_g1_clave = args.g1_clave
    if args.g1_camara_nombre:
        config.camara_g1_nombre = args.g1_camara_nombre
    # Modo automático: sin flags, la app detecta si el robot está en la red y elige real o simulación
    if args.simulacion:
        args.sin_robot = True
    host_g1 = getattr(config, "camara_g1_host", None) or "192.168.123.164"
    if not args.sin_robot and not args.sonic_real:
        if robot_alcanzable(host_g1):
            args.sonic_real = True
            log.info(f"Robot detectado en {host_g1}: modo REAL")
        else:
            args.sin_robot = True
            log.info(f"Robot no responde en {host_g1}: modo SIMULACIÓN")
    config.robot_activo = not args.sin_robot
    config.usar_v2 = False if args.estatico else (True if args.v2 else config.usar_v2)

    def de(arg, atributo, solo_real=False):
        """Valor dado al lanzar, o el de Ajustes (los de movimiento solo con el robot real)."""
        if arg is not None:
            return arg
        if solo_real and not args.sonic_real:
            return None
        return getattr(config, atributo, None)

    escala = de(args.sonic_escala, "sonic_escala", True)
    tiempo = de(args.sonic_tiempo, "sonic_tiempo", True)
    vel_max = de(args.sonic_vel_max, "sonic_vel_max", True)
    if escala is not None and not 0.0 < escala <= 1.0:
        raise SystemExit("La amplitud (--sonic-escala o Ajustes) debe estar en (0, 1]")
    if args.sonic_real and tiempo is not None and tiempo < 1.0:
        raise SystemExit("El factor de tiempo (--sonic-tiempo o Ajustes) no puede ser < 1 con --sonic-real")
    mic = de(args.mic, "mic_fuente") or "auto"
    mic_indice = args.mic_indice if args.mic_indice is not None else (
        config.mic_indice if getattr(config, "mic_indice", -1) >= 0 else None)
    sonic_opciones = dict(modo_real=args.sonic_real, escala=escala, factor_tiempo=tiempo, vel_max=vel_max,
                          confianza_min=de(args.sonic_confianza, "sonic_confianza") or 0.8,
                          manos=de(args.manos, "sonic_manos") or "auto",
                          auto_enlazar_camara=not args.sin_auto_enlace)
    sonic_opciones.update(mic=mic, mic_iface_ip=args.mic_g1_ip,
                          robot_host=getattr(config, "camara_g1_host", None) or "192.168.123.164",
                          robot_usuario=getattr(config, "camara_g1_usuario", None) or "unitree",
                          robot_clave=getattr(config, "camara_g1_clave", None),
                          gr00t_dir=args.gr00t_dir, interfaz=args.iface or (getattr(config, "sonic_iface", "") or None),
                          mujoco_pos=_par(args.mujoco_pos, ",", "--mujoco-pos"),
                          mujoco_tam=_par(args.mujoco_tam, "x", "--mujoco-tam"),
                          integrado=not args.terminales)
    if mic_indice is not None:
        os.environ["LSC_MIC_IDX"] = str(mic_indice)
    log.info(f"Micrófono de voz: {mic} (auto = el del robot con robot real, PC con simulación)")
    app = AppUnificada(config, sonic_opciones, verificar_al_inicio=not args.sin_verificar)
    app.ejecutar()


if __name__ == "__main__":
    main()
