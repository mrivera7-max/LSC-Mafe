#!/usr/bin/env python3
"""
remoto_camara_lsc.py — servidor de cámara ligero que la app copia y ejecuta en el PC2 del robot.

Sustituye a teleimager-server cuando se usa una RealSense: teleimager prueba TODOS los /dev/video* al
arrancar (los nodos de profundidad/infrarrojo no entregan imagen y se quedan esperando) y puede tardar
minutos o no abrir el puerto. Este script solo prueba los nodos que se le indican y publica con el mismo
protocolo que lee la app (ZMQ PUB, un JPEG crudo por mensaje).

  python3 remoto_camara_lsc.py --port 55556 --nodes 10,4

Prueba cada nodo en orden (OpenCV/V4L2, YUYV); si ninguno entrega imagen, prueba pyrealsense2.
Solo necesita cv2 y zmq (los que ya usa teleimager). Compatible con Python 3.6+.
"""
import argparse
import sys
import threading
import time

import cv2
import zmq


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _es_color(frame):
    return frame is not None and getattr(frame, "ndim", 0) == 3 and frame.shape[2] == 3


def probar_v4l2(nodo, w, h, fps, limite=7.0):
    """Devuelve un cv2.VideoCapture que ya entrega imagen en color, o None."""
    res = [None]

    def trabajo():
        cap = cv2.VideoCapture(nodo, cv2.CAP_V4L2)
        if not cap.isOpened():
            log("video%d: no abre" % nodo)
            return
        for fmt in ("MJPG", "YUYV"):       # webcams USB (OBSBOT): MJPG; RealSense: YUYV
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS, fps)
            for _ in range(8):
                ok, fr = cap.read()
                if ok and _es_color(fr):
                    log("video%d: %s %dx%d" % (nodo, fmt, fr.shape[1], fr.shape[0]))
                    res[0] = cap
                    return
        log("video%d: abre pero no entrega imagen de color" % nodo)
        cap.release()

    hilo = threading.Thread(target=trabajo, daemon=True)
    hilo.start()
    hilo.join(limite)
    if hilo.is_alive():
        log("video%d: sin respuesta en %.0f s" % (nodo, limite))
    return res[0]


class FuenteV4L2:
    def __init__(self, cap, nodo):
        self.cap, self.nombre = cap, "v4l2 /dev/video%d" % nodo

    def leer(self):
        ok, fr = self.cap.read()
        return fr if ok and _es_color(fr) else None


class FuenteRealSense:
    def __init__(self, pipe, serie):
        self.pipe, self.nombre = pipe, "pyrealsense2 %s" % serie

    def leer(self):
        import numpy as np
        try:
            cuadros = self.pipe.wait_for_frames(3000)
        except RuntimeError:
            return None
        c = cuadros.get_color_frame()
        return np.asanyarray(c.get_data()) if c else None


def probar_pyrealsense(w, h, fps):
    try:
        import pyrealsense2 as rs
    except ImportError:
        log("pyrealsense2 no está instalado: se omite")
        return None
    ctx = rs.context()
    series = [d.get_info(rs.camera_info.serial_number) for d in ctx.query_devices()]
    log("pyrealsense2 ve %d cámara(s): %s" % (len(series), series))
    for serie in reversed(series):          # la última conectada suele ser la externa
        pipe = rs.pipeline(ctx)
        cfg = rs.config()
        cfg.enable_device(serie)
        cfg.enable_stream(rs.stream.color, w, h, rs.format.bgr8, fps)
        try:
            pipe.start(cfg)
            fuente = FuenteRealSense(pipe, serie)
            if fuente.leer() is not None:
                return fuente
            pipe.stop()
        except Exception as e:  # noqa: BLE001
            log("RealSense %s: %s" % (serie, e))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=55556)
    ap.add_argument("--nodes", default="", help="nodos /dev/videoN a probar, en orden de preferencia")
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--h", type=int, default=480)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--calidad", type=int, default=80)
    a = ap.parse_args()

    fuente = None
    for n in [int(x) for x in a.nodes.split(",") if x.strip()]:
        cap = probar_v4l2(n, a.w, a.h, a.fps)
        if cap is not None:
            fuente = FuenteV4L2(cap, n)
            break
    if fuente is None:
        fuente = probar_pyrealsense(a.w, a.h, a.fps)
    if fuente is None:
        log("ERROR: ningún nodo entregó imagen (probados: %s)" % (a.nodes or "ninguno"))
        sys.exit(2)
    log("FUENTE %s" % fuente.nombre)

    sock = zmq.Context.instance().socket(zmq.PUB)
    sock.setsockopt(zmq.SNDHWM, 2)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind("tcp://*:%d" % a.port)
    log("Publicando en el puerto %d" % a.port)

    periodo, enviados, t0 = 1.0 / max(a.fps, 1), 0, time.time()
    while True:
        ini = time.time()
        fr = fuente.leer()
        if fr is not None:
            ok, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, a.calidad])
            if ok:
                sock.send(buf.tobytes())
                enviados += 1
                if enviados % 300 == 0:
                    log("%d frames (%.1f fps)" % (enviados, enviados / (time.time() - t0)))
        resto = periodo - (time.time() - ini)
        if resto > 0:
            time.sleep(resto)


if __name__ == "__main__":
    main()
