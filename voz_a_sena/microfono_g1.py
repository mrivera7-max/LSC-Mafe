"""
microfono_g1.py — micrófono del Unitree G1 como fuente de voz.

El G1 transmite el audio de su arreglo de micrófonos (lo origina el servicio de voz
del PC1) por UDP multicast en la red del robot:

    grupo 239.168.123.161 · puerto 5555 · PCM 16 kHz · mono · int16 little-endian
    (paquetes de ~5120 bytes = 160 ms)

Cualquier equipo de la red 192.168.123.x puede unirse al grupo; no hay que instalar nada en
el robot. Este módulo se une al grupo por la interfaz cableada, detecta una frase por energía
(VAD simple) y devuelve el PCM para que SpeechRecognition lo convierta en texto.

Referencias del protocolo (comprobado en proyectos de terceros, no en documentación oficial):
github.com/ja-avos/g1-audio-driver y github.com/bohcstams/unitree_converse.
"""

import logging
import os
import socket
import struct
import time
from typing import Optional

import numpy as np

log = logging.getLogger("voz_a_sena.microfono_g1")

GRUPO_G1 = "239.168.123.161"
PUERTO_G1 = 5555
FS = 16000                 # Hz
BYTES_MUESTRA = 2          # int16
TRAMA_MS = 20              # resolución del VAD
IP_ROBOT_PC2 = "192.168.123.164"


class MicrofonoG1:
    """Captura frases del micrófono del G1 por multicast UDP.

    grupo=None  recibe unicast en el puerto (para pruebas con un emisor local).
    iface_ip    IP local de la interfaz cableada del robot. Si es None se usa la variable
                de entorno G1_LOCAL_IP o se deduce de la ruta hacia el PC2 del robot.
    """

    def __init__(self, grupo: Optional[str] = GRUPO_G1, puerto: int = PUERTO_G1,
                 iface_ip: Optional[str] = None):
        self.grupo = grupo
        self.puerto = puerto
        self.iface_ip = iface_ip or os.environ.get("G1_LOCAL_IP")
        self.error: Optional[str] = None
        self._sock: Optional[socket.socket] = None

    # ── Socket ───────────────────────────────────────────────────────
    def _ip_local(self) -> str:
        """IP de origen que usa esta PC para llegar al robot (la de la interfaz cableada)."""
        if self.iface_ip:
            return self.iface_ip
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((IP_ROBOT_PC2, 9))
            return s.getsockname()[0]
        finally:
            s.close()

    @staticmethod
    def ips_locales() -> list:
        """Todas las IPv4 locales (sin loopback), p. ej. cable del robot y Wi-Fi."""
        import subprocess
        ips = []
        try:
            out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True,
                                 text=True, timeout=3).stdout
            for linea in out.splitlines():
                ip = linea.split()[3].split("/")[0]
                if not ip.startswith("127."):
                    ips.append(ip)
        except Exception:
            pass
        return ips

    def abrir(self) -> bool:
        if self._sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            s.bind(("", self.puerto))
            if self.grupo:
                if self.iface_ip:
                    ips = [self.iface_ip]
                else:
                    # Sin IP indicada: unirse por TODAS las interfaces (la del cable del robot
                    # puede no ser la de la ruta por defecto, p. ej. si hay Wi-Fi).
                    ips = list(dict.fromkeys([self._ip_local()] + self.ips_locales()))
                unidas = []
                for ip in ips:
                    try:
                        mreq = struct.pack("4s4s", socket.inet_aton(self.grupo),
                                           socket.inet_aton(ip))
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                        unidas.append(ip)
                    except OSError as e:
                        log.debug(f"Multicast: no se pudo unir por {ip}: {e}")
                if not unidas:
                    raise OSError(f"no se pudo unir al grupo {self.grupo} por ninguna interfaz {ips}")
                log.info(f"Micrófono G1: unido a {self.grupo}:{self.puerto} por {unidas}")
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            self._sock = s
            self.error = None
            return True
        except OSError as e:
            self.error = f"No se pudo abrir el audio del G1 ({self.grupo or 'unicast'}:{self.puerto}): {e}"
            log.error(self.error)
            return False

    def cerrar(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _vaciar(self):
        """Descarta el audio viejo acumulado en el buffer del socket."""
        self._sock.setblocking(False)
        try:
            while True:
                self._sock.recv(65535)
        except (BlockingIOError, OSError):
            pass
        finally:
            self._sock.setblocking(True)

    # ── Diagnóstico ──────────────────────────────────────────────────
    def probar(self, segundos: float = 3.0) -> dict:
        """Escucha `segundos` y resume: paquetes/s, bytes y nivel RMS (int16)."""
        if not self.abrir():
            return {"ok": False, "error": self.error}
        self._vaciar()
        n = nbytes = 0
        rms = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < segundos:
            self._sock.settimeout(max(0.05, segundos - (time.monotonic() - t0)))
            try:
                d = self._sock.recv(65535)
            except socket.timeout:
                break
            n += 1
            nbytes += len(d)
            x = np.frombuffer(d[: len(d) // 2 * 2], dtype="<i2").astype(np.float64)
            if x.size:
                rms.append(float(np.sqrt(np.mean(x * x))))
        dur = nbytes / (FS * BYTES_MUESTRA)
        return {"ok": n > 0, "paquetes": n, "segundos_audio": dur,
                "rms_medio": float(np.mean(rms)) if rms else 0.0,
                "rms_max": float(np.max(rms)) if rms else 0.0,
                "error": None if n else
                f"No llegan paquetes a {self.grupo or 'unicast'}:{self.puerto} en {segundos:.0f} s"}

    # ── Captura de una frase ─────────────────────────────────────────
    def capturar_frase(self, timeout_espera: float = 5.0, limite_frase: float = 8.0,
                       silencio_fin: float = 1.2, umbral_rms: Optional[float] = None,
                       preroll: float = 0.5) -> Optional[bytes]:
        """Espera a que alguien hable y devuelve el PCM (int16 LE, 16 kHz, mono) de la frase.

        Devuelve None si no llega audio o nadie habla en `timeout_espera` s; self.error
        explica cuál de los dos casos fue. El umbral se adapta al ruido de fondo medido en
        los primeros 0.5 s (3x el ruido, nunca menos de 250 de RMS).
        """
        if not self.abrir():
            return None
        self._vaciar()
        trama = FS * BYTES_MUESTRA * TRAMA_MS // 1000          # bytes por trama de 20 ms
        pre_max = int(preroll * 1000 / TRAMA_MS)
        resto = b""
        previas, frase = [], []
        ruido = []
        umbral = umbral_rms
        umbral_fin = umbral_rms * 0.5 if umbral_rms else None   # histéresis: terminar con un umbral más bajo
        hablando = False
        silencio = 0.0
        t_ini = time.monotonic()
        t_voz = None
        recibio = False

        while True:
            ahora = time.monotonic()
            if not hablando and ahora - t_ini > timeout_espera:
                self.error = ("Tiempo de espera agotado, no se detectó voz" if recibio else
                              f"No llegan paquetes de audio a {self.grupo or 'unicast'}:{self.puerto}")
                return None
            if hablando and ahora - t_voz > limite_frase:
                break
            self._sock.settimeout(0.5)
            try:
                d = self._sock.recv(65535)
            except socket.timeout:
                continue
            recibio = True
            buf = resto + d
            n = len(buf) // trama * trama
            resto = buf[n:]
            for i in range(0, n, trama):
                f = buf[i:i + trama]
                x = np.frombuffer(f, dtype="<i2").astype(np.float64)
                rms = float(np.sqrt(np.mean(x * x)))
                if umbral is None:
                    ruido.append(rms)
                    if len(ruido) * TRAMA_MS >= 500:
                        mediana = float(np.median(ruido))
                        umbral = max(250.0, 2.5 * mediana)
                        umbral_fin = max(150.0, 1.4 * mediana)
                        log.info(f"Micrófono G1: ruido {mediana:.0f} -> inicio {umbral:.0f}, fin {umbral_fin:.0f}")
                    previas = (previas + [f])[-pre_max:]
                    continue
                if not hablando:
                    previas = (previas + [f])[-pre_max:]
                    if rms > umbral:
                        hablando, t_voz, silencio = True, time.monotonic(), 0.0
                        frase = list(previas)
                    continue
                frase.append(f)
                silencio = 0.0 if rms > umbral_fin else silencio + TRAMA_MS / 1000
                if silencio >= silencio_fin:
                    return b"".join(frase)
        return b"".join(frase)
