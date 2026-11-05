"""
Prepara desde la app el audio del micrófono conectado al PC2 del G1.

Por SSH:
  1. Lista las tarjetas de captura (`arecord -l`) y elige el micrófono USB (receptor del Hollyland,
     «Wireless microphone», o cualquier tarjeta USB que no sea la de Tegra/HDMI).
  2. Copia `remoto_audio_lsc.py` al robot y lo arranca (publica PCM 16 kHz mono por TCP).
  3. Espera a que el puerto responda.

Requiere el mismo acceso SSH sin contraseña que la cámara del robot.
"""

import logging
import os
import re
import socket
import time
from typing import Callable, List, Optional, Tuple

from robot.servidor_camara_g1 import (ErrorCamaraG1, HOST_DEFECTO, ServidorCamaraG1, USUARIO_DEFECTO)

log = logging.getLogger("robot.servidor_audio_g1")

PUERTO_AUDIO = 55557
PUERTO_UDP_AUDIO = 5601        # el robot envía el audio por UDP a este puerto de la PC (como mic_stream_g1.py)
SCRIPT_REMOTO = "~/lsc_audio_server.py"
LOG_REMOTO = "/tmp/lsc_audio.log"
_EXCLUIR = re.compile(r"tegra|ape\b|nvidia|hdmi|hda|dummy|loopback", re.IGNORECASE)
_PREFERIR = re.compile(r"wireless|hollyland|microphone|mic|usb", re.IGNORECASE)


def parsear_tarjetas(texto: str) -> List[Tuple[str, str]]:
    """Salida de `arecord -l` → [(id_tarjeta, nombre), ...] sin repetir tarjetas."""
    vistas, salida = set(), []
    for m in re.finditer(r"card\s+(\d+):\s*(\S+)\s*\[([^\]]*)\]", texto):
        _, ident, nombre = m.groups()
        if ident not in vistas:
            vistas.add(ident)
            salida.append((ident, nombre.strip()))
    return salida


def elegir_microfono(texto: str) -> Tuple[str, str]:
    """(id, nombre) del micrófono USB del robot. Lanza ErrorCamaraG1 si no hay ninguno."""
    tarjetas = parsear_tarjetas(texto)
    utiles = [t for t in tarjetas if not _EXCLUIR.search(t[0] + " " + t[1])]
    if utiles:
        preferidas = [t for t in utiles if _PREFERIR.search(t[0] + " " + t[1])]
        return (preferidas or utiles)[0]
    vistas = "; ".join(f"{i} [{n}]" for i, n in tarjetas) or "ninguna"
    raise ErrorCamaraG1("No encuentro un micrófono USB en el robot (tarjetas de captura: "
                        f"{vistas}). ¿El receptor del Hollyland está conectado al PC2?")


# ── Detección SIN nombres: tarjetas de captura cuyo origen es USB (sysfs), la más reciente primero ──
ENUMERAR_AUDIO = r"""
import glob, json, os, re, subprocess
def leer(p):
    try:
        return open(p).read().strip()
    except Exception:
        return ""
def udev(ruta):
    try:
        return subprocess.run(["udevadm", "info", "-q", "property", "-p", ruta],
                              capture_output=True, text=True, timeout=6).stdout
    except Exception:
        return ""
def dev_usb(real):
    d = real
    while d != "/" and not os.path.exists(os.path.join(d, "idVendor")):
        d = os.path.dirname(d)
    return d if d != "/" else ""
tarjetas = []
for sd in glob.glob("/sys/class/sound/card[0-9]*"):
    if not re.search(r"card\d+$", sd):
        continue
    n = int(re.search(r"(\d+)$", sd).group(1))
    real = os.path.realpath(sd)
    d = dev_usb(real)
    m = re.search(r"USEC_INITIALIZED=(\d+)", udev(sd))
    tarjetas.append({
        "n": n,
        "id": leer("/proc/asound/card%d/id" % n) or leer(sd + "/id"),
        "captura": bool(glob.glob("/proc/asound/card%d/pcm*c" % n)),
        "usb": os.path.basename(d) if d else "",
        "vidpid": (leer(d + "/idVendor") + ":" + leer(d + "/idProduct")) if d else "",
        "devnum": int(leer(d + "/devnum") or 0) if d else 0,
        "usec": int(m.group(1)) if m else 0,
        "info": leer(d + "/product") if d else leer(sd + "/id"),
    })
audio_usb = []
for ifc in glob.glob("/sys/bus/usb/devices/*:*"):
    if leer(ifc + "/bInterfaceClass") != "01":
        continue
    d = dev_usb(os.path.realpath(ifc))
    drv = os.path.basename(os.path.realpath(ifc + "/driver")) if os.path.exists(ifc + "/driver") else ""
    audio_usb.append({"usb": os.path.basename(d) if d else "", "vidpid": (leer(d + "/idVendor") + ":" + leer(d + "/idProduct")) if d else "",
                      "info": leer(d + "/product") if d else "", "driver": drv})
otros = []
for d in glob.glob("/sys/bus/usb/devices/*"):
    if ":" in os.path.basename(d) or not os.path.exists(d + "/idVendor"):
        continue
    if leer(d + "/bDeviceClass") == "09":
        continue                                  # hubs
    otros.append({"usb": os.path.basename(d), "vidpid": leer(d + "/idVendor") + ":" + leer(d + "/idProduct"),
                  "info": leer(d + "/product")})
print(json.dumps({"tarjetas": tarjetas, "audio_usb": audio_usb, "usb": otros}))
"""


def parsear_audio(texto: str) -> Optional[dict]:
    """Salida JSON de ENUMERAR_AUDIO → dict, o None si no hay JSON válido."""
    import json
    for linea in reversed(texto.strip().splitlines()):
        linea = linea.strip()
        if linea.startswith("{"):
            try:
                return json.loads(linea)
            except ValueError:
                return None
    return None


def elegir_captura_usb(datos: dict) -> List[Tuple[str, str]]:
    """Tarjetas de captura con origen USB, la conectada más recientemente primero: [(id, descripción)].

    No mira nombres: solo si la tarjeta cuelga de un dispositivo USB y tiene entrada de captura (las de
    Tegra/HDMI son de plataforma, no USB)."""
    t = [x for x in datos.get("tarjetas", []) if x.get("captura") and x.get("usb")]
    t.sort(key=lambda x: (-int(x.get("usec", 0)), -int(x.get("devnum", 0)), x.get("n", 0)))
    return [(x["id"], f"{x.get('info') or x['id']} (puerto USB {x['usb']})") for x in t]


def explicar_sin_microfono(datos: dict) -> str:
    """Mensaje con lo que SÍ ve el robot, para saber por qué no hay micrófono."""
    partes = []
    sin_driver = [a for a in datos.get("audio_usb", []) if not a.get("driver")]
    if sin_driver:
        partes.append("Hay dispositivos USB de audio sin driver cargado: "
                      + ", ".join(f"{a['vidpid']} puerto {a['usb']}" for a in sin_driver)
                      + " (prueba en el PC2: sudo modprobe snd-usb-audio).")
    elif datos.get("audio_usb"):
        partes.append("El USB de audio está reconocido pero sin entrada de captura: "
                      + ", ".join(f"{a['vidpid']} puerto {a['usb']}" for a in datos["audio_usb"]) + ".")
    else:
        partes.append("Ningún dispositivo USB del PC2 se presenta como audio.")
    otros = datos.get("usb", [])
    if otros:
        partes.append("USB conectados: " + "; ".join(f"{o['vidpid']} puerto {o['usb']}" for o in otros) + ".")
    return " ".join(partes)


class ServidorAudioG1(ServidorCamaraG1):
    """Reutiliza el SSH de ServidorCamaraG1; `puerto` es el del audio."""

    def __init__(self, host: str = HOST_DEFECTO, usuario: str = USUARIO_DEFECTO,
                 clave: Optional[str] = None, puerto: int = PUERTO_AUDIO):
        super().__init__(host, usuario, clave, puerto=puerto)

    def detectar(self) -> Tuple[str, str]:
        """(id, descripción) del micrófono conectado al robot: la tarjeta de captura USB más reciente."""
        datos = parsear_audio(self._ssh("python3 -", entrada=ENUMERAR_AUDIO, timeout=45))
        if datos is None:                              # no se pudo enumerar: respaldo por `arecord -l`
            return elegir_microfono(self._ssh("arecord -l 2>&1", timeout=15))
        tarjetas = elegir_captura_usb(datos)
        if not tarjetas:
            raise ErrorCamaraG1("No encuentro un micrófono USB en el robot. " + explicar_sin_microfono(datos))
        return tarjetas[0]

    def preparar(self, avisar: Optional[Callable[[str], None]] = None, espera_s: float = 25.0) -> int:
        def paso(msg: str):
            log.info(msg)
            if avisar:
                avisar(msg)

        paso("Buscando el micrófono conectado al robot…")
        ident, nombre = self.detectar()
        paso(f"Micrófono del robot: «{nombre}» (tarjeta {ident}).")
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remoto_audio_lsc.py")
        with open(ruta, encoding="utf-8") as f:
            codigo = f.read()
        self._ssh(f"cat > {SCRIPT_REMOTO}", entrada=codigo)
        paso("Arrancando el servidor de audio en el robot…")
        try:
            self._ssh("bash -s",
                      entrada="exec >/dev/null 2>&1\n"
                              "pkill -9 -f '[l]sc_audio_server'; pkill -9 arecord; pkill -9 parec; sleep 1\n"
                              f"setsid nohup python3 {SCRIPT_REMOTO} --port {self.puerto} --card {ident} "
                              f"--udp {self.ip_pc()}:{PUERTO_UDP_AUDIO} "
                              f"> {LOG_REMOTO} 2>&1 < /dev/null &\n"
                              "sleep 0.5\n",
                      timeout=12)
        except ErrorCamaraG1 as e:
            if "a tiempo" not in str(e):
                raise
        fin = time.time() + max(espera_s, 40.0)         # probar varias configuraciones de captura tarda
        while time.time() < fin:
            try:
                registro = self._ssh(f"cat {LOG_REMOTO} 2>/dev/null", timeout=10)
            except ErrorCamaraG1:
                registro = ""
            if "ERROR:" in registro:
                break
            if "audio enviado por UDP" in registro:
                fuente = next((l for l in registro.splitlines() if "FUENTE" in l), "")
                paso(f"Audio del robot listo (UDP → {self.ip_pc()}:{PUERTO_UDP_AUDIO}). {fuente.strip()}")
                return PUERTO_UDP_AUDIO
            time.sleep(1.5)
        cola = ""
        try:
            cola = self._ssh(f"tail -n 10 {LOG_REMOTO}")
        except ErrorCamaraG1:
            pass
        raise ErrorCamaraG1(f"El micrófono del robot no entregó audio en {espera_s:.0f} s.\n"
                            f"Log del robot ({LOG_REMOTO}):\n{cola.strip()}")

    def ip_pc(self) -> str:
        """IP de esta PC en la red del robot (la que usa para llegar a él)."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((self.host, 9))
            return s.getsockname()[0]
        except OSError:
            return "192.168.123.222"
        finally:
            s.close()

    def log_remoto(self, lineas: int = 12) -> str:
        """Últimas líneas del log del servidor de audio en el robot."""
        try:
            return self._ssh(f"tail -n {lineas} {LOG_REMOTO} 2>&1", timeout=10).strip()
        except ErrorCamaraG1 as e:
            return str(e)

    def detener(self):
        try:
            self._ssh("pkill -9 -f '[l]sc_audio_server'; pkill -9 arecord; pkill -9 parec; echo ok", timeout=10)
        except ErrorCamaraG1 as e:
            log.warning(f"No se pudo detener el audio del robot: {e}")
