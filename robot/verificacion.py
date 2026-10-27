"""
Verificación de arranque: comprueba que todo lo necesario para trabajar con el G1 está listo
(red, SSH, cámara del robot, simulador/deploy, voz...) y deja preparado lo que se pueda
(servidor de cámara del robot). Cada comprobación devuelve un `Resultado` que la interfaz
muestra en la pestaña «Conexión».
"""

import glob
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

from robot.servidor_camara_g1 import ErrorCamaraG1, ServidorCamaraG1

log = logging.getLogger("robot.verificacion")

OK, AVISO, ERROR, EN_CURSO, PENDIENTE, OMITIDO = "ok", "aviso", "error", "en_curso", "pendiente", "omitido"
PATRONES_PROCESOS = ("run_sim_loop.py", "g1_deploy_onnx_ref")


@dataclass
class Resultado:
    clave: str
    titulo: str
    estado: str = PENDIENTE
    detalle: str = ""
    solucion: str = ""
    accion: Optional[str] = None      # "ssh" | "procesos": botón de arreglo en la interfaz


class Verificador:
    def __init__(self, config, lanzador, usar_camara_robot: bool = True):
        self.config = config
        self.lanzador = lanzador
        self.host = getattr(config, "camara_g1_host", "192.168.123.164")
        self.usuario = getattr(config, "camara_g1_usuario", "unitree")
        self.clave = getattr(config, "camara_g1_clave", None)
        self.nombre_cam = getattr(config, "camara_g1_nombre", "OBSBOT")
        self.usar_camara_robot = usar_camara_robot
        self._ssh_ok = False
        self._red_ok = False
        self.pasos = [
            ("red", "Robot en la red", self._red),
            ("ssh", "Acceso SSH al robot", self._ssh),
            ("interfaz", "Cable al robot (interfaz del deploy)", self._interfaz),
            ("sonic", "Carpeta GR00T / deploy SONIC", self._sonic),
            ("procesos", "Procesos antiguos", self._procesos),
            ("camara_robot", "Cámara del robot (servidor)", self._camara_robot),
            ("mic_robot", "Micrófono del robot (USB en el PC2)", self._mic_robot),
            ("camara_pc", "Cámara de la PC", self._camara_pc),
            ("internet", "Internet (reconocimiento de voz)", self._internet),
            ("mic_pc", "Micrófono de la PC", self._mic_pc),
            ("xdotool", "Colocar la ventana de MuJoCo", self._xdotool),
        ]

    # ── Ejecución ────────────────────────────────────────────────────
    def titulos(self) -> List[Resultado]:
        return [Resultado(c, t) for c, t, _ in self.pasos]

    def ejecutar(self, al_cambiar: Callable[[Resultado], None],
                 al_avisar: Optional[Callable[[str, str], None]] = None) -> List[Resultado]:
        """Corre todas las comprobaciones en orden; llama a `al_cambiar` al empezar y al terminar cada una."""
        self._ssh_ok = self._red_ok = False
        resultados = []
        for clave, titulo, fn in self.pasos:
            al_cambiar(Resultado(clave, titulo, EN_CURSO))
            try:
                if clave == "camara_robot" and al_avisar:
                    r = fn(lambda msg, c=clave, t=titulo: al_avisar(c, msg))
                else:
                    r = fn()
            except Exception as e:  # noqa: BLE001
                log.exception("Fallo la verificación %s", clave)
                r = Resultado(clave, titulo, ERROR, f"Error inesperado: {e}")
            r.clave, r.titulo = clave, titulo
            resultados.append(r)
            al_cambiar(r)
        return resultados

    @staticmethod
    def resumen(resultados: List[Resultado]) -> str:
        if any(r.estado == ERROR for r in resultados):
            return ERROR
        if any(r.estado == AVISO for r in resultados):
            return AVISO
        return OK

    # ── Comprobaciones ───────────────────────────────────────────────
    def _srv(self) -> ServidorCamaraG1:
        return ServidorCamaraG1(self.host, self.usuario, self.clave, nombre_camara=self.nombre_cam)

    def _red(self) -> Resultado:
        try:
            with socket.create_connection((self.host, 22), timeout=3):
                self._red_ok = True
                return Resultado("", "", OK, f"{self.host} responde")
        except OSError as e:
            return Resultado("", "", ERROR, f"{self.host} no responde ({e.__class__.__name__})",
                             "Enciende el robot y conecta el cable Ethernet a la PC (red 192.168.123.x).")

    def _ssh(self) -> Resultado:
        if not self._red_ok:
            return Resultado("", "", OMITIDO, "Depende de «Robot en la red»")
        try:
            self._srv()._ssh("echo ok", timeout=12)
            self._ssh_ok = True
            return Resultado("", "", OK, f"{self.usuario}@{self.host} sin contraseña")
        except ErrorCamaraG1 as e:
            if "contraseña" in str(e):
                return Resultado("", "", ERROR, "El robot pide contraseña por SSH",
                                 "Pulsa «Configurar acceso SSH» y escribe la contraseña del robot una sola vez.", "ssh")
            return Resultado("", "", ERROR, str(e).splitlines()[0])

    def _interfaz(self) -> Resultado:
        iface = self.lanzador.interfaz
        if shutil.which("ip") is None:
            return Resultado("", "", AVISO, "No se pudo comprobar (falta el comando ip)")
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", iface], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            return Resultado("", "", AVISO, f"La interfaz {iface} no existe o no tiene IP",
                             "Solo afecta al robot real. Revisa el cable o indica otra con --iface.")
        ips = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", r.stdout)
        if not any(i.startswith("192.168.123.") for i in ips):
            return Resultado("", "", AVISO, f"{iface} tiene {', '.join(ips)}, no 192.168.123.x",
                             "El deploy real necesita la IP 192.168.123.x en esa interfaz.")
        ruta = subprocess.run(["ip", "route", "get", self.host], capture_output=True, text=True).stdout
        m = re.search(r"dev (\S+)", ruta)
        if m and m.group(1) != iface:
            return Resultado("", "", AVISO, f"{iface} OK, pero el robot se alcanza por {m.group(1)}",
                             f"Probablemente el Wi-Fi comparte la subred. El deploy real usa {iface}.")
        return Resultado("", "", OK, f"{iface} → {', '.join(ips)}")

    def _sonic(self) -> Resultado:
        msg = self.lanzador.validar()
        if msg:
            return Resultado("", "", ERROR, msg.split(".")[0], "Indica la carpeta con --gr00t-dir o GR00T_DIR.")
        try:
            self.lanzador.comando_sim()
        except RuntimeError as e:
            return Resultado("", "", ERROR, str(e))
        return Resultado("", "", OK, str(self.lanzador.directorio))

    def _procesos(self) -> Resultado:
        vivos = []
        for patron in PATRONES_PROCESOS:
            r = subprocess.run(["pgrep", "-f", patron], capture_output=True, text=True)
            pids = [p for p in r.stdout.split() if p and int(p) != os.getpid()]
            if pids:
                vivos.append(patron)
        if vivos:
            return Resultado("", "", AVISO, "Siguen en marcha: " + ", ".join(vivos),
                             "Ciérralos para no tener ventanas/deploys duplicados.", "procesos")
        return Resultado("", "", OK, "Ninguno")

    def _camara_robot(self, avisar: Optional[Callable[[str], None]] = None) -> Resultado:
        if not self.usar_camara_robot or not getattr(self.config, "camara_g1_auto", True):
            return Resultado("", "", OMITIDO, "Preparación automática desactivada")
        if not self._ssh_ok:
            return Resultado("", "", OMITIDO, "Depende del acceso SSH")
        try:
            puerto = self._srv().preparar(avisar)
        except ErrorCamaraG1 as e:
            return Resultado("", "", ERROR, str(e).splitlines()[0], str(e))
        return Resultado("", "", OK, f"RealSense desactivada, cámara externa publicando en :{puerto}")

    def _mic_robot(self) -> Resultado:
        """Solo detecta; el audio se arranca al pulsar «Conectar robot real» (o con --mic robot)."""
        if not self._ssh_ok:
            return Resultado("", "", OMITIDO, "Depende del acceso SSH")
        from robot.servidor_audio_g1 import ServidorAudioG1
        try:
            _, nombre = ServidorAudioG1(self.host, self.usuario, self.clave).detectar()
        except ErrorCamaraG1 as e:
            return Resultado("", "", AVISO, str(e).splitlines()[0],
                             "Conecta el receptor del micrófono (Hollyland) a un USB del PC2, o usa el micrófono de la PC.")
        return Resultado("", "", OK, f"«{nombre}» conectado al robot; se usará al conectar el robot real")

    def _camara_pc(self) -> Resultado:
        idx = getattr(self.config, "camara_idx", 0)
        if os.path.exists(f"/dev/video{idx}"):
            return Resultado("", "", OK, f"/dev/video{idx}")
        hay = sorted(glob.glob("/dev/video*"))
        return Resultado("", "", AVISO, f"No existe /dev/video{idx}" + (f" (hay: {' '.join(hay[:4])})" if hay else ""),
                         "Conecta la webcam o elige otra con --camara N.")

    def _internet(self) -> Resultado:
        try:
            with socket.create_connection(("www.google.com", 443), timeout=3):
                return Resultado("", "", OK, "Conexión a Google disponible")
        except OSError:
            return Resultado("", "", AVISO, "Sin internet: la voz no se reconocerá",
                             "El reconocimiento de voz usa Google. El texto y la cámara siguen funcionando.")

    def _mic_pc(self) -> Resultado:
        try:
            import speech_recognition as sr
            nombres = sr.Microphone.list_microphone_names()
        except ImportError:
            return Resultado("", "", AVISO, "Falta SpeechRecognition/pyaudio",
                             "pip install SpeechRecognition pyaudio")
        except Exception as e:  # noqa: BLE001
            return Resultado("", "", AVISO, f"No se pudo listar micrófonos: {e}")
        if not nombres:
            return Resultado("", "", AVISO, "No hay micrófonos en la PC", "Conecta uno o usa el texto.")
        return Resultado("", "", OK, f"{len(nombres)} dispositivo(s) de audio")

    def _xdotool(self) -> Resultado:
        if getattr(self.lanzador, "posicion", None) is None:
            return Resultado("", "", OMITIDO, "Posición de ventana desactivada")
        if shutil.which("xdotool") or shutil.which("wmctrl"):
            return Resultado("", "", OK, "xdotool/wmctrl disponible")
        return Resultado("", "", AVISO, "No hay xdotool ni wmctrl",
                         "sudo apt install xdotool (solo para colocar la ventana de MuJoCo).")


def cerrar_procesos_antiguos() -> str:
    """Termina MuJoCo y el deploy que hayan quedado de otra sesión."""
    cerrados = []
    for patron in PATRONES_PROCESOS:
        if subprocess.run(["pkill", "-f", patron], capture_output=True).returncode == 0:
            cerrados.append(patron)
    return ", ".join(cerrados) or "nada"
