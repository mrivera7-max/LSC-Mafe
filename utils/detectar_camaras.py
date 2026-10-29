"""
detectar_camaras.py — encuentra qué cámaras USB hay conectadas a esta PC y cuáles entregan imagen.

No se fía del número ni del nombre: una cámara como la Intel RealSense crea varios /dev/video*
(profundidad, infrarrojo, color, metadatos) y solo el de color sirve. Se listan los nodos desde
/sys/class/video4linux, se descartan los que no son de captura de color y se PRUEBA cada candidato
leyendo un fotograma.

    python -m utils.detectar_camaras          # lista y prueba
"""

import glob
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("utils.detectar_camaras")

# Nodos que nunca sirven como cámara de color (según el final del nombre; la RealSense a veces los nombra igual
# y entonces los distinguen los formatos de v4l2-ctl)
_NO_COLOR = re.compile(r"(depth|infrared|\bir\b|metadata)\s*\d*\s*$", re.IGNORECASE)
_FMT_COLOR = {"YUYV", "MJPG", "RGB3", "BGR3", "NV12", "UYVY", "YU12", "H264"}
_FMT_NO_COLOR = {"Z16", "GREY", "Y8I", "Y12I", "Y16", "Y8", "Y10", "Y12", "BA81", "BYR2"}
SYS_V4L = "/sys/class/video4linux"
_INTERNOS = re.compile(r"tegra|platform:|vi-output|nvhost", re.IGNORECASE)


@dataclass
class CamaraPC:
    indice: int
    ruta: str
    nombre: str
    forma: Optional[tuple] = None        # (alto, ancho) del fotograma de prueba
    funciona: bool = False


def listar_nodos() -> List[CamaraPC]:
    """Nodos /dev/videoN que pueden ser de captura de color, el principal de cada cámara primero."""
    nodos = []
    for ruta_sys in sorted(glob.glob(os.path.join(SYS_V4L, "video*")), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        n = int(re.sub(r"\D", "", os.path.basename(ruta_sys)))
        try:
            nombre = open(os.path.join(ruta_sys, "name"), encoding="utf-8", errors="replace").read().strip()
        except OSError:
            continue
        try:
            sub = int(open(os.path.join(ruta_sys, "index")).read().strip())   # 0 = nodo de captura principal
        except (OSError, ValueError):
            sub = 0
        if sub != 0 or _NO_COLOR.search(nombre) or _INTERNOS.search(nombre):
            continue
        nodos.append(CamaraPC(n, f"/dev/video{n}", nombre))
    return nodos


def formatos(indice: int) -> set:
    """Formatos de píxel que ofrece /dev/videoN (v4l2-ctl); vacío si v4l2-ctl no está o no responde."""
    import shutil
    import subprocess
    if shutil.which("v4l2-ctl") is None:
        return set()
    try:
        out = subprocess.run(["v4l2-ctl", "-d", f"/dev/video{indice}", "--list-formats"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {c.strip() for c in re.findall(r"'([A-Za-z0-9 ]{4})'", out)}


def es_de_color(indice: int) -> Optional[bool]:
    """True/False según los formatos del nodo; None si no se pudo saber."""
    f = formatos(indice)
    if not f:
        return None
    return bool(f & _FMT_COLOR) and not (f & _FMT_NO_COLOR)


def _parece_gris(frame) -> bool:
    """Un nodo de infrarrojo/profundidad entrega GREY que OpenCV convierte a 3 canales iguales."""
    import numpy as np
    m = frame[::8, ::8].astype(np.int16)
    return float(np.mean(np.abs(m[..., 0] - m[..., 1])) + np.mean(np.abs(m[..., 1] - m[..., 2]))) < 1.0


def probar(indice: int, timeout: float = 4.0) -> Optional[tuple]:
    """Abre la cámara `indice` y lee un fotograma en color; devuelve (alto, ancho) o None.
    Se hace en un hilo para que una cámara colgada no bloquee la app."""
    resultado = [None]

    def trabajo():
        try:
            import cv2
            cap = cv2.VideoCapture(indice, cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else cv2.VideoCapture(indice)
            if not cap.isOpened():
                return
            try:
                for _ in range(8):                    # las primeras lecturas suelen venir vacías
                    ok, frame = cap.read()
                    if ok and frame is not None and getattr(frame, "ndim", 0) == 3 and frame.shape[2] == 3:
                        resultado[0] = frame.shape[:2]
                        return
            finally:
                cap.release()
        except Exception as e:  # noqa: BLE001
            log.debug(f"Prueba de /dev/video{indice}: {e}")

    hilo = threading.Thread(target=trabajo, daemon=True)
    hilo.start()
    hilo.join(timeout)
    return resultado[0]


def detectar(preferida: Optional[int] = None, probar_todas: bool = False) -> List[CamaraPC]:
    """Cámaras candidatas probadas. Si `preferida` es un índice válido se prueba primero.
    Se detiene en la primera que funcione salvo `probar_todas`."""
    cand = listar_nodos()
    if preferida is not None:
        cand.sort(key=lambda c: 0 if c.indice == preferida else 1)
        if not any(c.indice == preferida for c in cand):
            cand.insert(0, CamaraPC(preferida, f"/dev/video{preferida}", "(indicada por el usuario)"))
    # Primero los nodos que por formato son de color; los dudosos (v4l2-ctl no disponible) después
    estado = {c.indice: es_de_color(c.indice) for c in cand}
    cand = [c for c in cand if estado[c.indice] is not False]
    cand.sort(key=lambda c: 0 if estado[c.indice] else 1)
    salida = []
    for c in cand:
        c.forma = probar(c.indice)
        c.funciona = c.forma is not None
        salida.append(c)
        if c.funciona and not probar_todas:
            break
    return salida


def elegir_camara(indice_config: int):
    """(índice a usar, texto para el registro) o (None, motivo). Usa el índice pedido si entrega
    imagen; si no, la primera cámara conectada que la entregue."""
    cams = detectar(indice_config)
    for c in cams:
        if c.funciona:
            if c.indice == indice_config:
                return c.indice, f"{c.nombre} (/dev/video{c.indice})"
            return c.indice, (f"el índice {indice_config} no entrega imagen; se usa {c.nombre} "
                              f"(/dev/video{c.indice})")
    vistos = ", ".join(f"{c.ruta} «{c.nombre}»" for c in listar_nodos()) or "ninguna cámara de color"
    return None, f"ninguna cámara entrega imagen (nodos de color vistos: {vistos})"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    nodos = listar_nodos()
    print("Nodos de color candidatos:", ", ".join(f"{n.ruta} «{n.nombre}»" for n in nodos) or "ninguno")
    for c in detectar(probar_todas=True):
        print(f"  {c.ruta:12s} {c.nombre:45s} {'OK ' + str(c.forma) if c.funciona else 'sin imagen'}")
