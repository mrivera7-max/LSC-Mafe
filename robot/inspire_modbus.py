"""Manos Inspire RH56DFTP-2R por Modbus TCP, sin DDS ni inspire_sdkpy.

La mano FTP tiene IP propia (derecha 192.168.123.211, izquierda 192.168.123.210 de fábrica) y habla
Modbus TCP por el puerto 6000. El driver oficial (inspire_hand_ws) hace lo mismo: escribe los 6
ángulos objetivo en el registro 1486 (0..1000, 1000 = abierta). Aquí se hace directamente desde la app,
con sockets, así que no hace falta instalar cyclonedds, PyQt5 ni el driver.

Interfaz idéntica a `mano_inspire.SalidaInspireDDS` (abrir / abierta / enviar / error), así el
`ConectorSonic` no cambia. Cada mano va en su propio hilo: si una está apagada o sin cable la otra
sigue funcionando, y la que falta se reintenta sola cada 2 s.

IP por variable de entorno (opcional): LSC_MANO_IP_DER, LSC_MANO_IP_IZQ, LSC_MANO_PUERTO.
"""
from __future__ import annotations

import logging
import os
import socket
import struct
import threading
import time
from typing import Optional, Sequence

from robot.mano_inspire import a_angle_set

log = logging.getLogger("lsc_bridge.inspire_modbus")

IP_DER = os.environ.get("LSC_MANO_IP_DER", "192.168.123.211")
IP_IZQ = os.environ.get("LSC_MANO_IP_IZQ", "192.168.123.210")
PUERTO = int(os.environ.get("LSC_MANO_PUERTO", "6000"))
UNIDAD = 1                 # device_id de las manos (el driver oficial usa 1 en las dos)

REG_ANGLE_SET = 1486       # 6 registros: meñique, anular, medio, índice, pulgar-flexión, pulgar-rotación
REG_CLEAR_ERROR = 1004     # escribir 1 limpia el error (como el driver oficial)
REENVIO_S = 0.5            # si no cambia la consigna, se repite cada tanto (la mano sigue conectada)


def trama_escribir_registros(tid: int, unidad: int, inicio: int, valores: Sequence[int]) -> bytes:
    """Modbus TCP, función 0x10 (escribir varios registros)."""
    n = len(valores)
    return (struct.pack(">HHHBBHHB", tid & 0xFFFF, 0, 7 + 2 * n, unidad, 0x10, inicio, n, 2 * n)
            + struct.pack(">" + "H" * n, *[int(v) & 0xFFFF for v in valores]))


def trama_escribir_registro(tid: int, unidad: int, direccion: int, valor: int) -> bytes:
    """Modbus TCP, función 0x06 (escribir un registro)."""
    return struct.pack(">HHHBBHH", tid & 0xFFFF, 0, 6, unidad, 0x06, direccion, int(valor) & 0xFFFF)


class _Mano:
    def __init__(self, nombre: str, ip: str, puerto: int):
        self.nombre, self.ip, self.puerto = nombre, ip, puerto
        self.sock: Optional[socket.socket] = None
        self.conectada = False
        self.ultimo_error: Optional[str] = None
        self._objetivo: Optional[list] = None
        self._enviado: Optional[list] = None
        self._t_envio = 0.0
        self._tid = 0
        self._ev = threading.Event()
        self._parar = threading.Event()
        self._hilo: Optional[threading.Thread] = None

    # -- conexión -------------------------------------------------------
    def conectar(self, timeout: float = 1.5) -> bool:
        self.cerrar_socket()
        try:
            s = socket.create_connection((self.ip, self.puerto), timeout=timeout)
            s.settimeout(0.5)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = s
            self._pedir(trama_escribir_registro(self._siguiente(), UNIDAD, REG_CLEAR_ERROR, 1))
            self.conectada, self.ultimo_error = True, None
            self._enviado = None                       # reenvía la consigna al reconectar
            log.info(f"Mano {self.nombre} conectada ({self.ip}:{self.puerto})")
            return True
        except OSError as e:
            self.ultimo_error = f"{self.ip}:{self.puerto} {e}"
            self.cerrar_socket()
            return False

    def cerrar_socket(self):
        self.conectada = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _siguiente(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid

    def _pedir(self, trama: bytes):
        """Envía y lee la respuesta (cabecera 7 + PDU). Lanza OSError si falla o la mano devuelve excepción."""
        self.sock.sendall(trama)
        cab = self._leer(7)
        longitud = struct.unpack(">H", cab[4:6])[0]
        pdu = self._leer(longitud - 1)
        if pdu and pdu[0] & 0x80:
            raise OSError(f"excepción Modbus {pdu[1] if len(pdu) > 1 else '?'}")

    def _leer(self, n: int) -> bytes:
        datos = b""
        while len(datos) < n:
            trozo = self.sock.recv(n - len(datos))
            if not trozo:
                raise OSError("conexión cerrada por la mano")
            datos += trozo
        return datos

    # -- hilo -----------------------------------------------------------
    def arrancar(self):
        self._hilo = threading.Thread(target=self._bucle, name=f"inspire-{self.nombre}", daemon=True)
        self._hilo.start()

    def poner(self, angulos: list):
        self._objetivo = angulos
        self._ev.set()

    def _bucle(self):
        proximo_intento = 0.0
        while not self._parar.is_set():
            self._ev.wait(0.2)
            self._ev.clear()
            if self._parar.is_set():
                break
            if not self.conectada:
                if time.monotonic() >= proximo_intento:
                    if not self.conectar():
                        proximo_intento = time.monotonic() + 2.0
                continue
            obj = self._objetivo
            if obj is None:
                continue
            ahora = time.monotonic()
            if obj == self._enviado and ahora - self._t_envio < REENVIO_S:
                continue
            try:
                self._pedir(trama_escribir_registros(self._siguiente(), UNIDAD, REG_ANGLE_SET, obj))
                self._enviado, self._t_envio = list(obj), ahora
            except OSError as e:
                self.ultimo_error = f"{self.ip}:{self.puerto} {e}"
                log.warning(f"Mano {self.nombre}: {self.ultimo_error}; se reintenta")
                self.cerrar_socket()
                proximo_intento = time.monotonic() + 1.0

    def parar(self):
        self._parar.set()
        self._ev.set()
        if self._hilo is not None:
            self._hilo.join(timeout=1.0)
        self.cerrar_socket()


class SalidaInspireModbus:
    """Misma interfaz que SalidaInspireDDS."""

    def __init__(self, interfaz: str = "", ip_izq: str = IP_IZQ, ip_der: str = IP_DER, puerto: int = PUERTO):
        # `interfaz` se acepta por compatibilidad; Modbus TCP usa la ruta normal hacia 192.168.123.x
        self.error: Optional[str] = None
        self._izq = _Mano("izquierda", ip_izq, puerto)
        self._der = _Mano("derecha", ip_der, puerto)
        self._manos = (self._izq, self._der)
        self._abierta = False

    def abrir(self) -> bool:
        ok = [m.conectar() for m in self._manos]
        for m in self._manos:
            m.arrancar()                  # la que no conectó se sigue reintentando sola
        self._abierta = True
        faltan = [f"{m.nombre} ({m.ultimo_error})" for m, c in zip(self._manos, ok) if not c]
        if not any(ok):
            self.error = "No hay conexión con ninguna mano Inspire: " + "; ".join(faltan)
            log.warning(self.error)
            self.cerrar()
            return False
        self.error = ("Mano sin conexión por ahora (se reintenta): " + "; ".join(faltan)) if faltan else None
        if self.error:
            log.warning(self.error)
        return True

    @property
    def abierta(self) -> bool:
        return self._abierta

    def estado(self) -> dict:
        return {m.nombre: m.conectada for m in self._manos}

    def enviar(self, izq6: Sequence[float], der6: Sequence[float]):
        """6 valores 0..1 por mano (1 = abierta). No bloquea ni lanza excepciones."""
        if not self._abierta:
            return
        try:
            self._izq.poner(a_angle_set(izq6))
            self._der.poner(a_angle_set(der6))
        except Exception as e:                    # valores mal formados: no tumbar el bucle de señas
            log.warning(f"Manos Inspire: {e}")

    def cerrar(self):
        self._abierta = False
        for m in self._manos:
            m.parar()
