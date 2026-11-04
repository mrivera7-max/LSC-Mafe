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
import os
import subprocess
import sys
import threading
import time

import cv2
import zmq


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _es_color(frame):
    return frame is not None and getattr(frame, "ndim", 0) == 3 and frame.shape[2] == 3


def configuraciones(w, h, fps):
    """Perfiles a probar: el pedido y otros más ligeros. Una RealSense en un USB 2 (o con cable USB 2)
    abre y entrega un fotograma pero se queda sin ancho de banda a 640x480 y 30 fps."""
    cfgs = [(w, h, fps)]
    for c in ((w, h, 15), (424, 240, 30), (424, 240, 15), (320, 240, 15)):
        if c not in cfgs:
            cfgs.append(c)
    return cfgs


def _abrir(nodo, fmt, w, h, fps):
    cap = cv2.VideoCapture(nodo, cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


def sondeo_hijo(nodo, w, h, fps):
    """Proceso hijo: sale con 0 (e imprime el formato) si el nodo entrega 3 fotogramas de color seguidos."""
    for fmt in ("MJPG", "YUYV"):       # webcams USB (OBSBOT): MJPG; RealSense: YUYV
        cap = _abrir(nodo, fmt, w, h, fps)
        if cap is None:
            print("no-abre")
            sys.exit(1)
        buenos = 0
        for _ in range(10):
            ok, fr = cap.read()
            if ok and _es_color(fr):
                buenos += 1
                if buenos >= 3:
                    print(fmt)
                    cap.release()
                    sys.exit(0)
        cap.release()
    sys.exit(1)


def probar_v4l2(nodo, w, h, fps, limite=6.0):
    """(cv2.VideoCapture, fps) que entrega imagen en color, o (None, fps).

    Cada perfil se prueba en un proceso aparte con tiempo máximo: OpenCV se queda 10 s bloqueado en cada
    lectura si la cámara no entrega y no se puede cancelar; matando el proceso la cámara queda libre."""
    for cw, ch, cf in configuraciones(w, h, fps):
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--sondeo", str(nodo), str(cw), str(ch), str(cf)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
        try:
            salida, _ = p.communicate(timeout=limite)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            log("video%d %dx%d@%d: sin imagen en %.0f s" % (nodo, cw, ch, cf, limite))
            time.sleep(0.5)
            continue
        if p.returncode != 0:
            log("video%d %dx%d@%d: %s" % (nodo, cw, ch, cf,
                                         "no abre" if "no-abre" in (salida or "") else "no entrega color"))
            if "no-abre" in (salida or ""):
                return None, fps            # el nodo no se puede abrir: no sirve probar otros perfiles
            continue
        fmt = (salida or "").split()[-1]
        cap = _abrir(nodo, fmt, cw, ch, cf)
        ok, fr = cap.read() if cap is not None else (False, None)
        if ok and _es_color(fr):
            log("video%d: %s %dx%d@%d" % (nodo, fmt, fr.shape[1], fr.shape[0], cf))
            return cap, cf
        if cap is not None:
            cap.release()
    return None, fps


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
    if len(sys.argv) == 6 and sys.argv[1] == "--sondeo":
        sondeo_hijo(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
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
        cap, fps_real = probar_v4l2(n, a.w, a.h, a.fps)
        if cap is not None:
            fuente = FuenteV4L2(cap, n)
            a.fps = fps_real
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

    ultimo = [time.time()]

    def vigilante():                       # si la cámara deja de entregar, salir con un error claro
        while True:
            time.sleep(2.0)
            if time.time() - ultimo[0] > 8.0:
                log("ERROR: la cámara dejó de entregar imagen (¿USB 2 o cable flojo?)")
                os._exit(3)
    threading.Thread(target=vigilante, daemon=True).start()

    periodo, enviados, t0 = 1.0 / max(a.fps, 1), 0, time.time()
    while True:
        ini = time.time()
        fr = fuente.leer()
        if fr is not None:
            ultimo[0] = time.time()
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
