#!/usr/bin/env python3
"""
remoto_audio_lsc.py — servidor de audio ligero que la app copia y ejecuta en el PC2 del robot.

Captura el micrófono USB conectado al robot (p. ej. el receptor del Hollyland, «Wireless microphone»)
con `arecord` y lo publica por TCP como PCM 16 kHz · mono · int16 little-endian, el mismo formato que
espera la app. Solo usa la biblioteca estándar (Python 3.6+, `audioop`) y `arecord`.

  python3 remoto_audio_lsc.py --port 55557 --card microphone

Prueba varias configuraciones de captura (plughw convierte formato/frecuencia) hasta que una entregue
datos, y cuenta los clientes: mientras no haya ninguno sigue capturando pero descarta el audio.
"""
import argparse
import os
import select
import socket
import subprocess
import sys
import threading
import time

FS_SALIDA = 16000

try:
    import audioop
except ImportError:                   # Python >= 3.13 ya no trae audioop: respaldo equivalente con array
    import array

    class audioop:                    # noqa: N801 — solo lo que usa este script
        @staticmethod
        def lin2lin(datos, ancho, nuevo):
            assert ancho == 3 and nuevo == 2
            return b"".join(datos[i + 1:i + 3] for i in range(0, len(datos) - 2, 3))

        @staticmethod
        def tomono(datos, ancho, f1, f2):
            a = array.array("h")
            a.frombytes(datos)
            return array.array("h", [int(a[i] * f1 + a[i + 1] * f2) for i in range(0, len(a) - 1, 2)]).tobytes()

        @staticmethod
        def rms(datos, ancho):
            a = array.array("h")
            a.frombytes(datos[:len(datos) // 2 * 2])
            return int((sum(v * v for v in a) / float(len(a) or 1)) ** 0.5)

        @staticmethod
        def ratecv(datos, ancho, canales, fs_in, fs_out, estado):
            a = array.array("h")
            a.frombytes(datos)
            pos = estado or 0.0
            paso = fs_in / float(fs_out)
            salida = array.array("h")
            while pos < len(a) - 1:
                i = int(pos)
                f = pos - i
                salida.append(int(a[i] * (1 - f) + a[i + 1] * f))
                pos += paso
            return salida.tobytes(), pos - len(a)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# (dispositivo, formato, frecuencia, canales, bytes por muestra)
def candidatos(card):
    return [
        ("plughw:CARD=%s,DEV=0" % card, "S16_LE", 16000, 1, 2),
        ("plughw:CARD=%s,DEV=0" % card, "S16_LE", 48000, 2, 2),
        ("hw:CARD=%s,DEV=0" % card, "S24_3LE", 48000, 2, 3),
        ("hw:CARD=%s,DEV=0" % card, "S16_LE", 48000, 2, 2),
        ("hw:CARD=%s,DEV=0" % card, "S16_LE", 44100, 1, 2),
    ]


def fuentes_pulse():
    """Fuentes de captura USB de PulseAudio (nombres «alsa_input.usb-…», sin monitores), la más nueva primero.

    Si PulseAudio (p. ej. el del escritorio) ya tiene abierta la tarjeta, `arecord` directo da «Device or
    resource busy»; entonces se captura a través de Pulse con `parec`."""
    try:
        txt = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True,
                             timeout=6).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    fuentes = []
    for linea in txt.splitlines():
        campos = linea.split("\t")
        if len(campos) >= 2 and "usb" in campos[1].lower() and not campos[1].endswith(".monitor"):
            fuentes.append((int(campos[0]) if campos[0].isdigit() else 0, campos[1]))
    return [n for _, n in sorted(fuentes, reverse=True)]


def candidatos_pulse():
    return [("pulse:%s" % n, "S16_LE", 16000, 1, 2) for n in fuentes_pulse()]


def abrir_arecord(cfg):
    dev, fmt, fs, ch, _ = cfg
    if dev.startswith("pulse:"):
        return subprocess.Popen(
            ["parec", "--device=%s" % dev[6:], "--format=s16le", "--rate=%d" % fs, "--channels=%d" % ch, "--raw"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return subprocess.Popen(
        ["arecord", "-q", "-D", dev, "-f", fmt, "-r", str(fs), "-c", str(ch), "-t", "raw"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def a_16k_mono(datos, cfg, estado):
    """Convierte lo que entrega arecord a int16 mono 16 kHz."""
    _, _, fs, ch, ancho = cfg
    if ancho == 3:
        datos = audioop.lin2lin(datos, 3, 2)
        ancho = 2
    if ch == 2:
        # canal con más energía (promediar anula la señal si un canal viene en contrafase o vacío)
        izq, der = audioop.tomono(datos, ancho, 1, 0), audioop.tomono(datos, ancho, 0, 1)
        datos = izq if audioop.rms(izq, ancho) >= audioop.rms(der, ancho) else der
    if fs != FS_SALIDA:
        datos, estado[0] = audioop.ratecv(datos, ancho, 1, fs, FS_SALIDA, estado[0])
    return datos


class Difusor:
    def __init__(self):
        self.clientes = []
        self.lock = threading.Lock()

    def agregar(self, c):
        with self.lock:
            self.clientes.append(c)

    def enviar(self, datos):
        with self.lock:
            vivos = []
            for c in self.clientes:
                try:
                    c.sendall(datos)
                    vivos.append(c)
                except OSError:
                    try:
                        c.close()
                    except OSError:
                        pass
            self.clientes = vivos
            return len(vivos)


class EmisorUDP:
    """Misma interfaz que Difusor, pero manda cada bloque como datagrama UDP a la PC (3200 B = 100 ms)."""

    def __init__(self, destino):
        ip, puerto = destino.rsplit(":", 1)
        self.destino = (ip, int(puerto))
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def enviar(self, datos):
        try:
            self.s.sendto(datos, self.destino)
        except OSError as e:
            log("UDP: %s" % e)
        return 1


def aceptar(srv, difusor):
    while True:
        c, addr = srv.accept()
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        difusor.agregar(c)
        log("cliente conectado: %s" % (addr[0],))


def elegir_captura(card):
    """Primera configuración que entrega datos en 3 s; devuelve (cfg, proceso) o (None, None)."""
    for cfg in candidatos(card) + candidatos_pulse():
        p = abrir_arecord(cfg)
        t0 = time.time()
        datos = b""
        fd = p.stdout.fileno()
        while time.time() - t0 < 3.0 and len(datos) < 4800 and p.poll() is None:
            if select.select([fd], [], [], 0.5)[0]:
                trozo = os.read(fd, 4800 - len(datos))
                if not trozo:
                    break
                datos += trozo
        if len(datos) >= 4800:
            log("FUENTE %s %s %d Hz %d canal(es)" % (cfg[0], cfg[1], cfg[2], cfg[3]))
            return cfg, p
        log("no sirve: %s %s %d Hz %d canal(es)" % (cfg[0], cfg[1], cfg[2], cfg[3]))
        try:
            p.kill()
        except OSError:
            pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=55557)
    ap.add_argument("--card", default="microphone", help="ID de la tarjeta (arecord -l: 'card N: ID [..]')")
    ap.add_argument("--udp", default="", metavar="IP:PUERTO",
                    help="en vez de servir por TCP, enviar el audio por UDP a la PC (como mic_stream_g1.py)")
    a = ap.parse_args()

    cfg, proc = elegir_captura(a.card)
    if cfg is None:
        log("ERROR: ninguna configuración entregó audio de la tarjeta '%s'" % a.card)
        sys.exit(2)

    if a.udp:
        difusor = EmisorUDP(a.udp)
        log("audio enviado por UDP a %s" % a.udp)
    else:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", a.port))
        srv.listen(4)
        log("audio publicado en :%d" % a.port)
        difusor = Difusor()
        threading.Thread(target=aceptar, args=(srv, difusor), daemon=True).start()

    ancho_in = cfg[4]
    bloque = cfg[2] * cfg[3] * ancho_in // 10          # 100 ms de audio de entrada
    estado = [None]
    bloques = 0
    while True:
        datos = proc.stdout.read(bloque)
        if not datos:
            log("arecord terminó; reabro la captura")
            time.sleep(1.0)
            proc = abrir_arecord(cfg)
            estado = [None]
            continue
        pcm = a_16k_mono(datos, cfg, estado)
        difusor.enviar(pcm)
        bloques += 1
        if bloques % 50 == 1:          # cada ~5 s: nivel del audio (para ver si el micrófono entrega señal)
            log("nivel RMS %d pico %d (bloques %d)" % (audioop.rms(pcm, 2), audioop.max(pcm, 2) if hasattr(audioop, "max") else 0, bloques))


if __name__ == "__main__":
    main()
