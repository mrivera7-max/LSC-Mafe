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


class ServidorAudioG1(ServidorCamaraG1):
    """Reutiliza el SSH de ServidorCamaraG1; `puerto` es el del audio."""

    def __init__(self, host: str = HOST_DEFECTO, usuario: str = USUARIO_DEFECTO,
                 clave: Optional[str] = None, puerto: int = PUERTO_AUDIO):
        super().__init__(host, usuario, clave, puerto=puerto)

    def detectar(self) -> Tuple[str, str]:
        """(id, nombre) del micrófono conectado al robot."""
        return elegir_microfono(self._ssh("arecord -l 2>&1", timeout=15))

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
                              "pkill -9 -f '[l]sc_audio_server'; pkill -9 arecord; sleep 1\n"
                              f"setsid nohup python3 {SCRIPT_REMOTO} --port {self.puerto} --card {ident} "
                              f"> {LOG_REMOTO} 2>&1 < /dev/null &\n"
                              "sleep 0.5\n",
                      timeout=12)
        except ErrorCamaraG1 as e:
            if "a tiempo" not in str(e):
                raise
        fin = time.time() + espera_s
        while time.time() < fin:
            if self.puerto_abierto():
                fuente = ""
                try:
                    fuente = next((l for l in self._ssh(f"cat {LOG_REMOTO}").splitlines() if "FUENTE" in l), "")
                except ErrorCamaraG1:
                    pass
                paso(f"Audio del robot listo. {fuente.strip()}")
                return self.puerto
            time.sleep(1.0)
        cola = ""
        try:
            cola = self._ssh(f"tail -n 10 {LOG_REMOTO}")
        except ErrorCamaraG1:
            pass
        raise ErrorCamaraG1(f"El micrófono del robot no entregó audio en {espera_s:.0f} s.\n"
                            f"Log del robot ({LOG_REMOTO}):\n{cola.strip()}")

    def detener(self):
        try:
            self._ssh("pkill -9 -f '[l]sc_audio_server'; pkill -9 arecord; echo ok", timeout=10)
        except ErrorCamaraG1 as e:
            log.warning(f"No se pudo detener el audio del robot: {e}")
