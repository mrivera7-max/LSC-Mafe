"""Envia al simulador (SONIC/MuJoCo) la forma de las manos Inspire RH56DFTP-2R.

Publica por ZeroMQ (PUB, ``tcp://127.0.0.1:5601``) el JSON
``{"l": [6 rad], "r": [6 rad]}`` en el orden Inspire (menique, anular, medio,
indice, pulgar-flexion, pulgar-rotacion).  El simulador (``inspire_hook.py``)
se suscribe y mueve los 12 joints de cada mano.  No abre ninguna ventana ni
proceso extra: es un hilo dentro de la app.

Se engancha solo a ``ConectorSonic`` (ver ultima linea de conector_sonic.py)
y lee ``conector.forma_manos_actual()`` a ~30 Hz.
"""
from __future__ import annotations

import json
import os
import threading
import time

import numpy as np

PUERTO = int(os.environ.get("LSC_INSPIRE_PUERTO", "5601"))
HZ = 30.0
# Solo se usa si forma_manos_actual() devolviera 12 valores sueltos.
ORDEN_12 = os.environ.get("LSC_INSPIRE_ORDEN", "rl").lower()
# (rad abierto, rad cerrado) de cada actuador (igual que RANGO_RAD de mano_inspire.py)
ABIERTO = np.array([0.0, 0.0, 0.0, 0.0, 0.0, -0.1])
CERRADO = np.array([1.7, 1.7, 1.7, 1.7, 0.5, 1.3])


def _a_rad(v6):
    """6 valores normalizados (1 = abierto, 0 = cerrado) -> rad de los actuadores Inspire."""
    v = np.clip(np.asarray(v6, dtype=float).reshape(-1)[:6], 0.0, 1.0)
    return ABIERTO + (1.0 - v) * (CERRADO - ABIERTO)


def _separar(h):
    """forma_manos_actual() -> (derecha6, izquierda6) o None.
    Acepta {"der": [...], "izq": [...]} (ConectorSonic) o 12 valores [der, izq]."""
    if isinstance(h, dict):
        d = next((h[k] for k in ("der", "derecha", "r", "R") if k in h), None)
        i = next((h[k] for k in ("izq", "izquierda", "l", "L") if k in h), None)
        return (d, i) if d is not None and i is not None else None
    a = np.asarray(h, dtype=float).reshape(-1)
    if a.size == 12:
        return (a[:6], a[6:]) if ORDEN_12 == "rl" else (a[6:], a[:6])
    return None


class PublicadorInspire:
    def __init__(self, conector, puerto=PUERTO, hz=HZ):
        import zmq
        self.c = conector
        self.ctx = zmq.Context.instance()
        self.sock = self.ctx.socket(zmq.PUB)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.setsockopt(zmq.SNDHWM, 2)
        try:
            self.sock.bind(f"tcp://127.0.0.1:{puerto}")
        except Exception as e:
            print(f"[inspire] no pude abrir el puerto {puerto}: {e}")
            raise
        self.dt = 1.0 / hz
        self._parar = threading.Event()
        self._fallos = 0
        self.ultimo = None
        threading.Thread(target=self._bucle, daemon=True, name="inspire-sim").start()

    def _bucle(self):
        while not self._parar.is_set():
            t0 = time.time()
            try:
                h = self.c.forma_manos_actual()
                par = _separar(h)
                if par is not None:
                    d, i = par
                    msg = {"l": _a_rad(i).tolist(), "r": _a_rad(d).tolist()}
                    self.ultimo = msg
                    self.sock.send_string(json.dumps(msg), flags=1)  # NOBLOCK
                self._fallos = 0
            except Exception as e:
                self._fallos += 1
                if self._fallos == 3:
                    print(f"[inspire] no puedo leer forma_manos_actual(): {e}")
            time.sleep(max(0.0, self.dt - (time.time() - t0)))

    def cerrar(self):
        self._parar.set()
        try:
            self.sock.close(0)
        except Exception:
            pass


def instalar(clase):
    """Hace que cada ``ConectorSonic`` creado publique sus manos hacia el simulador."""
    if getattr(clase, "_inspire_sim_instalado", False):
        return
    original = clase.__init__

    def __init__(self, *a, **k):
        original(self, *a, **k)
        try:
            self._pub_inspire = PublicadorInspire(self)
        except Exception as e:
            print(f"[inspire] sin enlace con el simulador: {e}")

    clase.__init__ = __init__
    clase._inspire_sim_instalado = True
