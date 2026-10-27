"""
Diagnóstico del micrófono.

    python -m voz_a_sena.probar_microfono g1 [--ip 192.168.123.X]   # micrófono del robot
    python -m voz_a_sena.probar_microfono pc                         # micrófono de la PC
Añade --reconocer para además transcribir una frase (necesita internet).
"""
import argparse
import sys


def probar_g1(ip, reconocer):
    from voz_a_sena.microfono_g1 import MicrofonoG1
    mic = MicrofonoG1(iface_ip=ip)
    try:
        mic.abrir()
    except Exception as e:
        print(f"[ERROR] No se pudo abrir el multicast del G1: {e}")
        return 1
    print("Escuchando 3 s el audio del G1 (habla cerca del robot)...")
    r = mic.probar(3.0)
    print(r)
    if r.get("paquetes", 0) == 0:
        print("[FALLO] No llega audio. Revisa: cable/Ethernet a la red 192.168.123.x, "
              "IP de la PC en esa red (usa --ip), firewall (UDP 5555) y que el servicio de "
              "audio del robot esté activo.")
        mic.cerrar()
        return 2
    print("[OK] Llega audio del G1.")
    if reconocer:
        from voz_a_sena.reconocimiento_voz import ReconocedorVoz
        print("Di una frase...")
        res = ReconocedorVoz(fuente="g1", iface_ip=ip).escuchar()
        print(res)
        import os
        if os.path.exists("/tmp/lsc_ultima_frase_g1.wav"):
            print("Frase guardada: aplay /tmp/lsc_ultima_frase_g1.wav")
    mic.cerrar()
    return 0


def escanear(segundos=6.0):
    """Escucha el puerto 5555 en todas las interfaces y dice quién envía qué (sin unirse a grupos)."""
    import socket, time, struct
    from voz_a_sena.microfono_g1 import MicrofonoG1, GRUPO_G1, PUERTO_G1
    print("IPs locales:", MicrofonoG1.ips_locales())
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", PUERTO_G1))
    for ip in MicrofonoG1.ips_locales():
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         struct.pack("4s4s", socket.inet_aton(GRUPO_G1), socket.inet_aton(ip)))
        except OSError:
            pass
    s.settimeout(0.5)
    vistos, t0 = {}, time.time()
    print(f"Escaneando UDP {PUERTO_G1} durante {segundos:.0f} s (habla cerca del robot)...")
    while time.time() - t0 < segundos:
        try:
            d, a = s.recvfrom(65535)
            vistos[a[0]] = vistos.get(a[0], 0) + 1
            vistos.setdefault("tam", len(d))
        except socket.timeout:
            pass
    vistos_ips = {k: v for k, v in vistos.items() if k != "tam"}
    print("Emisores:", vistos_ips or "NINGUNO", f"(tamaño paquete: {vistos.get('tam')})" if vistos_ips else "")
    return 0 if vistos_ips else 2


def niveles(segundos=1.5):
    """Mide el nivel de CADA entrada de audio: el que sube al hablar es el micrófono real."""
    try:
        import audioop
        import pyaudio
    except ImportError as e:
        print(f"[ERROR] Falta {e.name}: pip install pyaudio (y sudo apt install portaudio19-dev)")
        return 1
    pa = pyaudio.PyAudio()
    print(f"HABLA FUERTE durante toda la prueba ({segundos:.1f} s por dispositivo)...\n")
    mejor = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) < 1:
            continue
        rate = int(info.get("defaultSampleRate", 16000))
        try:
            fl = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                         input_device_index=i, frames_per_buffer=1024)
            pico = 0
            for _ in range(int(rate / 1024 * segundos)):
                pico = max(pico, audioop.rms(fl.read(1024, exception_on_overflow=False), 2))
            fl.close()
            print(f"  [{i:2d}] nivel {pico:6d}  {info['name']}")
            if mejor is None or pico > mejor[1]:
                mejor = (i, pico, info["name"])
        except Exception as e:  # noqa: BLE001
            print(f"  [{i:2d}] no abre ({str(e)[:40]})  {info['name']}")
    pa.terminate()
    if mejor and mejor[1] > 300:
        print(f"\n→ Usa el índice {mejor[0]}:  python app_unificada.py ... --mic-indice {mejor[0]}   ({mejor[2]})")
        return 0
    print("\n[FALLO] Ningún dispositivo captó voz (nivel > 300). Revisa el cable, que no esté silenciado "
          "(pavucontrol → Entrada) y el volumen de entrada.")
    return 2


def probar_pc(reconocer):
    try:
        import speech_recognition as sr
    except ImportError:
        print("[ERROR] Falta SpeechRecognition: pip install SpeechRecognition")
        return 1
    try:
        nombres = sr.Microphone.list_microphone_names()
    except Exception as e:
        print(f"[ERROR] PyAudio/PortAudio no funciona: {e}\n"
              "  Ubuntu: sudo apt install portaudio19-dev && pip install pyaudio")
        return 1
    print("Micrófonos detectados:")
    for i, n in enumerate(nombres):
        print(f"  [{i}] {n}")
    if not nombres:
        print("[FALLO] No hay micrófonos de entrada.")
        return 2
    if reconocer:
        from voz_a_sena.reconocimiento_voz import ReconocedorVoz
        print("Di una frase...")
        print(ReconocedorVoz(fuente="pc").escuchar())
    return 0


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("fuente", choices=["g1", "pc", "escanear", "niveles"])
    ap.add_argument("--ip", default=None, help="IP local en la red del robot")
    ap.add_argument("--reconocer", action="store_true")
    a = ap.parse_args()
    if a.fuente == "escanear":
        sys.exit(escanear())
    if a.fuente == "niveles":
        sys.exit(niveles())
    sys.exit(probar_g1(a.ip, a.reconocer) if a.fuente == "g1" else probar_pc(a.reconocer))


if __name__ == "__main__":
    main()
