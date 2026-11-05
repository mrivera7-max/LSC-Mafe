"""Diagnóstico del micrófono del robot: qué ve el PC2 y por qué no aparece una tarjeta de captura USB.

    python -m robot.diagnostico_audio_g1
    python -m robot.diagnostico_audio_g1 --host 192.168.123.164 --usuario unitree

Solo lee (no cambia nada en el robot). Pega la salida completa si hay que revisarla.
"""
import argparse
import json

NIVEL_AUDIO = r"""
import audioop, subprocess, sys
card = sys.argv[1]
subprocess.run("pkill -9 -f '[l]sc_audio_server'; pkill -9 arecord; pkill -9 parec; sleep 1", shell=True)
print("--- procesos que usan el audio (fuser/ps) ---")
print(subprocess.run("(fuser -v /dev/snd/* 2>&1 || true) | head -20; "
                     "ps aux | grep -E 'pulse|pipewire|wireplumber|arecord|aplay|audio|alsa|voice|speech' | grep -v grep | cut -c1-150",
                     shell=True, capture_output=True, text=True).stdout or "(ninguno)")
print(subprocess.run("pactl list short sources 2>&1 | head", shell=True, capture_output=True, text=True).stdout)
print("Habla fuerte cerca del micrófono durante estas pruebas (3 s por configuración).")
fuentes = [l.split("\t")[1] for l in subprocess.run("pactl list short sources 2>/dev/null", shell=True,
           capture_output=True, text=True).stdout.splitlines() if "\t" in l and "usb" in l.lower() and ".monitor" not in l]
for f in fuentes:
    r = subprocess.run("timeout 3 parec --device=%s --format=s16le --rate=16000 --channels=1 --raw 2>&1" % f,
                       shell=True, capture_output=True)
    d = r.stdout
    if len(d) < 1000:
        print("PulseAudio %s: sin datos (%s)" % (f[:50], d.decode(errors="replace").strip()[:80]))
    else:
        print("PulseAudio %s 16 kHz 1 ch: RMS %d (pico %d)" % (f[:50], audioop.rms(d, 2), audioop.max(d, 2)))
print(subprocess.run("amixer -c %s scontents 2>&1 | head -30" % card, shell=True, capture_output=True, text=True).stdout)
for dev, fmt, fs, ch, ancho in [
        ("plughw:CARD=%s,DEV=0" % card, "S16_LE", 16000, 1, 2),
        ("plughw:CARD=%s,DEV=0" % card, "S16_LE", 48000, 2, 2),
        ("hw:CARD=%s,DEV=0" % card, "S24_3LE", 48000, 2, 3),
        ("hw:CARD=%s,DEV=0" % card, "S16_LE", 48000, 2, 2)]:
    r = subprocess.run(["arecord", "-q", "-d", "3", "-D", dev, "-f", fmt, "-r", str(fs), "-c", str(ch), "-t", "raw"],
                       capture_output=True)
    d = r.stdout
    if len(d) < 1000:
        print("%-30s %s %d Hz %d ch: sin datos (%s)" % (dev, fmt, fs, ch, r.stderr.decode(errors="replace").strip()[:80]))
        continue
    if ancho == 3:
        d = audioop.lin2lin(d, 3, 2); ancho = 2
    if ch == 2:
        i, dd = audioop.tomono(d, 2, 1, 0), audioop.tomono(d, 2, 0, 1)
        print("%-30s %s %d Hz 2 ch: RMS izq %d (pico %d) | der %d (pico %d) | promedio %d" % (
            dev, fmt, fs, audioop.rms(i, 2), audioop.max(i, 2), audioop.rms(dd, 2), audioop.max(dd, 2),
            audioop.rms(audioop.tomono(d, 2, .5, .5), 2)))
    else:
        print("%-30s %s %d Hz 1 ch: RMS %d (pico %d)" % (dev, fmt, fs, audioop.rms(d, 2), audioop.max(d, 2)))
"""

VIGILAR_USB = r"""
import glob, os, subprocess, time
def leer(p):
    try:
        return open(p).read().strip()
    except Exception:
        return ""
def foto():
    r = {}
    for d in glob.glob("/sys/bus/usb/devices/*"):
        b = os.path.basename(d)
        if ":" in b or not os.path.exists(d + "/idVendor"):
            continue
        r[b] = leer(d + "/idVendor") + ":" + leer(d + "/idProduct") + " " + leer(d + "/product")
    return r
a, t0, ev = foto(), time.time(), []
while time.time() - t0 < SEGUNDOS:
    time.sleep(1)
    b = foto()
    for k in b:
        if k not in a:
            ev.append("%4.0fs  CONECTADO    puerto %s  %s" % (time.time() - t0, k, b[k]))
    for k in a:
        if k not in b:
            ev.append("%4.0fs  DESCONECTADO puerto %s  %s" % (time.time() - t0, k, a[k]))
    a = b
print("\n".join(ev) or "(no cambió ningún dispositivo USB en este tiempo)")
print("--- dmesg (últimas líneas) ---")
print(subprocess.run("dmesg 2>/dev/null | tail -n 15", shell=True, capture_output=True, text=True).stdout)
"""

from robot.servidor_audio_g1 import (ENUMERAR_AUDIO, elegir_captura_usb, explicar_sin_microfono,
                                     parsear_audio)
from robot.servidor_camara_g1 import ErrorCamaraG1, ServidorCamaraG1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.123.164")
    ap.add_argument("--usuario", default="unitree")
    ap.add_argument("--nivel", action="store_true",
                    help="mide el volumen que entrega el micrófono del robot (habla fuerte durante la prueba)")
    ap.add_argument("--vigilar", type=int, nargs="?", const=40, metavar="SEG",
                    help="vigila los USB del robot SEG segundos (def. 40): desconecta y vuelve a conectar el receptor")
    a = ap.parse_args()
    srv = ServidorCamaraG1(a.host, a.usuario)
    if a.nivel:
        try:
            datos = parsear_audio(srv._ssh("python3 -", entrada=ENUMERAR_AUDIO, timeout=60))
            cand = elegir_captura_usb(datos or {})
            if not cand:
                print("No hay micrófono USB en el robot (ver sin --nivel).")
                return
            print(f"Micrófono: {cand[0][1]} (tarjeta {cand[0][0]}). HABLA FUERTE AHORA…")
            print(srv._ssh(f"python3 - {cand[0][0]}", entrada=NIVEL_AUDIO, timeout=90))
        except ErrorCamaraG1 as e:
            print(f"ERROR: {e}")
        return
    if a.vigilar:
        print(f"Vigilando los USB del robot {a.vigilar} s: desconecta el receptor del micrófono y vuelve a "
              "conectarlo AHORA (prueba también otro puerto)…")
        try:
            print(srv._ssh("python3 -", entrada=VIGILAR_USB.replace("SEGUNDOS", str(a.vigilar)),
                           timeout=a.vigilar + 30))
        except ErrorCamaraG1 as e:
            print(f"ERROR: {e}")
        return
    try:
        print("##### Tarjetas de sonido y dispositivos USB (sysfs, sin nombres)")
        datos = parsear_audio(srv._ssh("python3 -", entrada=ENUMERAR_AUDIO, timeout=60))
        if datos is None:
            print("(no se pudo enumerar)")
        else:
            print(json.dumps(datos, indent=1, ensure_ascii=False))
            cand = elegir_captura_usb(datos)
            print("\nMicrófonos USB (el primero es el que usa la app):", cand or "ninguno")
            if not cand:
                print(explicar_sin_microfono(datos))
        for titulo, cmd in [
            ("arecord -l", "arecord -l 2>&1"),
            ("lsusb", "lsusb 2>&1"),
            ("Módulo de audio USB cargado", "lsmod | grep -i snd_usb || echo '(snd_usb_audio NO está cargado)'"),
            ("dmesg (últimas líneas de USB/audio)", "dmesg 2>/dev/null | grep -i -E 'usb|audio|snd' | tail -n 25 "
                                                    "|| echo '(sin permiso para dmesg)'"),
        ]:
            print(f"\n##### {titulo}")
            print(srv._ssh(cmd, timeout=40).rstrip())
    except ErrorCamaraG1 as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
