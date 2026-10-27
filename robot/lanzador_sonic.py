"""
Abre desde la app la simulación MuJoCo y/o el deploy SONIC en terminales visibles.

Equivale a las Terminales 1 y 2 de docs/sonic_mujoco.md y docs/g1_real.md:

  simulación : run_sim_loop.py (MuJoCo)  +  ./deploy.sh --input-type zmq_manager sim
  robot real : ./deploy.sh --input-type zmq_manager <interfaz>      (sin MuJoCo)

Las terminales quedan abiertas para que se vean los avisos y se pueda pulsar O (paro).
La salida del deploy se registra con `script` para saber cuándo aparece «Init done».
"""

import fcntl
import logging
import os
import pty
import re
import signal
import struct
import termios
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

log = logging.getLogger("robot.lanzador_sonic")

DIR_DEFECTO = "/home/udirobotika/DURVVIN/SIM2SIM/blea/GR00T-WholeBodyControl"
INTERFAZ_DEFECTO = "enp131s0"
ESPERA_DEPLOY_SIM_S = 8          # el deploy necesita que MuJoCo ya publique LowState
DIR_LOGS = Path.home() / ".cache" / "lsc-mafe"
_INIT_DONE = re.compile(r"init\s*done", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class _Proceso:
    """Un proceso hijo con su propio pseudo-terminal: se lee su salida y se le pueden enviar teclas
    (Enter, O, Ctrl+C), igual que en una terminal."""

    MAX = 200_000

    def __init__(self, nombre: str, comando: str, ruta_log: Optional[Path]):
        self.nombre = nombre
        self.texto = ""
        self._lock = threading.Lock()
        self._log = open(ruta_log, "ab", buffering=0) if ruta_log else None
        maestro, esclavo = pty.openpty()
        fcntl.ioctl(esclavo, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))

        def hijo():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)       # terminal de control: Ctrl+C llega al deploy

        self.proc = subprocess.Popen(["bash", "-lc", comando], stdin=esclavo, stdout=esclavo,
                                     stderr=esclavo, preexec_fn=hijo, close_fds=True)
        os.close(esclavo)
        self._maestro = maestro
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        while True:
            try:
                datos = os.read(self._maestro, 4096)
            except OSError:
                break
            if not datos:
                break
            if self._log:
                self._log.write(datos)
            with self._lock:
                self.texto = (self.texto + datos.decode(errors="replace"))[-self.MAX:]
        try:
            os.close(self._maestro)
        except OSError:
            pass

    @property
    def vivo(self) -> bool:
        return self.proc.poll() is None

    def leer(self) -> str:
        with self._lock:
            return self.texto

    def enviar(self, datos: bytes) -> bool:
        if not self.vivo:
            return False
        try:
            os.write(self._maestro, datos)
            return True
        except OSError:
            return False

    def terminar(self, espera: float = 4.0):
        """Ctrl+C (como en la terminal); si no responde, SIGTERM y luego SIGKILL al grupo."""
        if not self.vivo:
            return
        self.enviar(b"\x03")
        for sig, t in ((None, espera), (signal.SIGTERM, 3.0), (signal.SIGKILL, 2.0)):
            if sig is not None:
                try:
                    os.killpg(self.proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                self.proc.wait(timeout=t)
                return
            except subprocess.TimeoutExpired:
                continue


class LanzadorSonic:
    def __init__(self, directorio: Optional[str] = None, interfaz: Optional[str] = None,
                 posicion: Optional[tuple] = (0, 0), tamano: Optional[tuple] = None,
                 integrado: bool = True):
        """posicion (x, y) y tamano (ancho, alto) de la ventana de MuJoCo; None = no tocarla.

        integrado=True: los procesos corren dentro de la app (sin terminales; se ven y se
        controlan desde la consola de la app). False: abre terminales externas como antes.
        """
        self.integrado = integrado
        self._procs: Dict[str, "_Proceso"] = {}
        self.posicion = posicion
        self.tamano = tamano
        self.directorio = Path(directorio or os.environ.get("GR00T_DIR") or DIR_DEFECTO).expanduser()
        self.interfaz = interfaz or INTERFAZ_DEFECTO
        self.error: Optional[str] = None

    # ── Comprobaciones y comandos ────────────────────────────────────
    def validar(self) -> Optional[str]:
        """None si todo está; si no, el motivo (con la opción para corregirlo)."""
        d = self.directorio
        if not (d / "gear_sonic_deploy" / "deploy.sh").exists():
            return (f"No encuentro gear_sonic_deploy/deploy.sh en {d}. "
                    "Indica la carpeta con --gr00t-dir o la variable GR00T_DIR.")
        return None

    def _venv(self) -> Optional[Path]:
        pref = self.directorio / ".venv_teleop"
        if (pref / "bin" / "activate").exists():
            return pref
        for c in sorted(self.directorio.glob(".venv*")):
            if (c / "bin" / "activate").exists():
                return c
        return None

    def comando_sim(self) -> str:
        venv = self._venv()
        if venv is None:
            raise RuntimeError(f"No hay entorno .venv* en {self.directorio} (el de gear_sonic_teleop).")
        return (f"cd {shlex.quote(str(self.directorio))} && "
                f"source {shlex.quote(str(venv / 'bin' / 'activate'))} && "
                f"{self._forzar_x11()}python gear_sonic/scripts/run_sim_loop.py")

    @staticmethod
    def _forzar_x11() -> str:
        """Con Wayland nativo la ventana de MuJoCo (GLFW) sale sin barra de título, centrada y sin poder
        moverla ni colocarla con xdotool. Quitando WAYLAND_DISPLAY abre por XWayland (con bordes).
        Desactivar con LSC_MUJOCO_WAYLAND=1."""
        if os.environ.get("LSC_MUJOCO_WAYLAND") == "1":
            return ""
        return '[ -n "$DISPLAY" ] && unset WAYLAND_DISPLAY && export XDG_SESSION_TYPE=x11 GDK_BACKEND=x11; '


    def comando_deploy(self, real: bool) -> str:
        destino = self.interfaz if real else "sim"
        previo = "" if real else f"echo 'Esperando {ESPERA_DEPLOY_SIM_S} s a que MuJoCo abra...'; sleep {ESPERA_DEPLOY_SIM_S}; "
        return (previo + f"cd {shlex.quote(str(self.directorio / 'gear_sonic_deploy'))} && "
                "source scripts/setup_env.sh && "
                f"./deploy.sh --input-type zmq_manager {shlex.quote(destino)}")

    # ── Terminales ───────────────────────────────────────────────────
    @staticmethod
    def _argv_terminal(titulo: str, cmd: str) -> Optional[List[str]]:
        bash = ["bash", "-lc", cmd]
        if shutil.which("gnome-terminal"):
            return ["gnome-terminal", "--title", titulo, "--"] + bash
        if shutil.which("x-terminal-emulator"):
            return ["x-terminal-emulator", "-e"] + bash
        if shutil.which("konsole"):
            return ["konsole", "-p", f"tabtitle={titulo}", "-e"] + bash
        if shutil.which("xfce4-terminal"):
            return ["xfce4-terminal", "--title", titulo, "-x"] + bash
        if shutil.which("xterm"):
            return ["xterm", "-T", titulo, "-e"] + bash
        return None

    def ruta_log(self, nombre: str) -> Path:
        return DIR_LOGS / f"{nombre}.log"

    def abrir_terminal(self, titulo: str, comando: str, log_nombre: Optional[str] = None) -> bool:
        """Abre una terminal que ejecuta `comando` y se queda abierta al terminar."""
        interior = comando
        if log_nombre and shutil.which("script"):
            DIR_LOGS.mkdir(parents=True, exist_ok=True)
            ruta = self.ruta_log(log_nombre)
            ruta.write_text("")
            # `script` conserva el terminal (pty): los avisos y la tecla O siguen funcionando
            interior = f"script -qf -c {shlex.quote(comando)} {shlex.quote(str(ruta))}"
        completo = f"{interior}; echo; echo '[proceso terminado — cierra esta ventana]'; exec bash"
        argv = self._argv_terminal(titulo, completo)
        if argv is None:
            self.error = "No hay emulador de terminal (gnome-terminal, xterm, konsole...)."
            return False
        try:
            subprocess.Popen(argv, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError as e:
            self.error = f"No se pudo abrir la terminal: {e}"
            return False

    # ── Modo integrado (sin terminales) ──────────────────────────────
    NOMBRES = {"sim": "MuJoCo", "deploy_sim": "Deploy (simulación)", "deploy_real": "Deploy (robot real)"}

    def _arrancar(self, clave: str, comando: str, con_log: bool = True) -> bool:
        previo = self._procs.get(clave)
        if previo is not None and previo.vivo:
            previo.terminar()
        DIR_LOGS.mkdir(parents=True, exist_ok=True)
        ruta = self.ruta_log(clave if clave != "sim" else "mujoco")
        ruta.write_text("")
        try:
            self._procs[clave] = _Proceso(clave, comando, ruta if con_log else None)
            return True
        except OSError as e:
            self.error = f"No se pudo iniciar {self.NOMBRES.get(clave, clave)}: {e}"
            return False

    def salida(self, clave: str) -> str:
        p = self._procs.get(clave)
        return _ANSI.sub("", p.leer()).replace("\r\n", "\n").replace("\r", "\n") if p else ""

    def proceso_vivo(self, clave: str) -> bool:
        p = self._procs.get(clave)
        return bool(p and p.vivo)

    def claves_activas(self) -> List[str]:
        return [k for k, p in self._procs.items() if p.vivo]

    def enviar(self, clave: str, texto: str) -> bool:
        """Envía teclas al proceso (texto tal cual; '\\n' = Enter, '\\x03' = Ctrl+C)."""
        p = self._procs.get(clave)
        return bool(p and p.enviar(texto.encode()))

    def cerrar_todo(self) -> str:
        """Cierra los procesos que lanzó la app (Ctrl+C) y limpia restos."""
        cerrados = []
        for clave, p in list(self._procs.items()):
            if p.vivo:
                p.terminar()
                cerrados.append(self.NOMBRES.get(clave, clave))
        self._procs.clear()
        return ", ".join(cerrados)

    def _lanzar_integrado(self, real: bool) -> bool:
        if not real:
            if not self._arrancar("sim", self.comando_sim()):
                return False
            if self.posicion is not None:
                threading.Thread(target=self.posicionar_ventana, daemon=True).start()
        return self._arrancar("deploy_real" if real else "deploy_sim", self.comando_deploy(real))

    def lanzar(self, real: bool) -> bool:
        """Simulación: MuJoCo + deploy sim. Real: solo deploy real."""
        self.error = None
        msg = self.validar()
        if msg:
            self.error = msg
            return False
        try:
            if self.integrado:
                return self._lanzar_integrado(real)
            if not real:
                if not self.abrir_terminal("LSC · Simulador (T1)", self.comando_sim()):
                    return False
                if self.posicion is not None:
                    threading.Thread(target=self.posicionar_ventana, daemon=True).start()
            return self.abrir_terminal(
                "LSC · Deploy SONIC " + ("REAL" if real else "sim") + " (T2)",
                self.comando_deploy(real), log_nombre="deploy_real" if real else "deploy_sim")
        except RuntimeError as e:
            self.error = str(e)
            return False

    def comandos_manuales(self, real: bool) -> str:
        """Texto con los comandos, por si no se puede abrir terminal."""
        partes = []
        try:
            if not real:
                partes.append("Terminal 1:  " + self.comando_sim())
        except RuntimeError as e:
            partes.append(f"Terminal 1: {e}")
        partes.append("Terminal 2:  " + self.comando_deploy(real))
        return "\n".join(partes)

    def deploy_listo(self, real: bool) -> bool:
        """True si el log del deploy ya muestra «Init done»."""
        ruta = self.ruta_log("deploy_real" if real else "deploy_sim")
        try:
            texto = _ANSI.sub("", ruta.read_text(errors="ignore"))
        except OSError:
            return False
        return bool(_INIT_DONE.search(texto))

    def hay_log(self, real: bool) -> bool:
        return self.integrado or shutil.which("script") is not None

    def cerrar_simulacion(self) -> str:
        """Termina MuJoCo y el deploy de la simulación. Devuelve qué proceso se cerró."""
        cerrados = []
        for clave in ("sim", "deploy_sim"):          # los que lanzó la app: Ctrl+C como en la terminal
            p = self._procs.pop(clave, None)
            if p is not None and p.vivo:
                p.terminar()
                cerrados.append(self.NOMBRES[clave])
        for etiqueta, patron in (("MuJoCo", "run_sim_loop.py"),
                                 ("deploy", "g1_deploy_onnx_ref"),
                                 ("deploy.sh", "gear_sonic_deploy/deploy.sh|\\./deploy.sh --input-type zmq_manager sim")):
            r = subprocess.run(["pkill", "-f", patron], capture_output=True)
            if r.returncode == 0:
                cerrados.append(etiqueta)
        return ", ".join(dict.fromkeys(cerrados)) if cerrados else "nada (ya estaba cerrada)"

    def cerrar_deploy_real(self) -> str:
        """Detiene el deploy del robot real con Ctrl+C (la forma documentada de terminarlo)."""
        p = self._procs.pop("deploy_real", None)
        if p is not None and p.vivo:
            p.terminar()
            return "deploy real"
        return ""

    def posicionar_ventana(self, esperar: float = 60.0) -> bool:
        """Mueve (y opcionalmente redimensiona) la ventana de MuJoCo cuando aparece (X11).

        Usa xdotool o, si no está, wmctrl. Con Wayland puro no funciona: arrastra la ventana a mano.
        """
        x, y = self.posicion
        w, h = self.tamano if self.tamano else (None, None)
        xdotool, wmctrl = shutil.which("xdotool"), shutil.which("wmctrl")
        if not (xdotool or wmctrl):
            log.warning("Para colocar la ventana de MuJoCo instala xdotool: sudo apt install xdotool")
            return False
        fin = time.time() + esperar
        while time.time() < fin:
            time.sleep(1.0)
            try:
                if xdotool:
                    r = subprocess.run([xdotool, "search", "--name", "^MuJoCo"],
                                       capture_output=True, text=True, timeout=5)
                    ids = r.stdout.split()
                    if not ids:
                        continue
                    for wid in ids:
                        subprocess.run([xdotool, "windowmove", wid, str(x), str(y)], timeout=5)
                        if w and h:
                            subprocess.run([xdotool, "windowsize", wid, str(w), str(h)], timeout=5)
                    return True
                geom = f"0,{x},{y},{w or -1},{h or -1}"
                r = subprocess.run([wmctrl, "-r", "MuJoCo", "-e", geom], capture_output=True, timeout=5)
                if r.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                return False
        log.warning("No encontré la ventana de MuJoCo para colocarla.")
        return False
