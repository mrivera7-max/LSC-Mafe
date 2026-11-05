"""Diagnóstico de la cámara del robot: lista lo que ve el PC2 y por qué no llegan imágenes.

    python -m robot.diagnostico_camara_g1
    python -m robot.diagnostico_camara_g1 --host 192.168.123.164 --usuario unitree

Solo lee (no cambia nada en el robot): cámaras USB y sus formatos, quién las tiene ocupadas y el log del
servidor de cámara. Pega la salida completa si hay que revisarla.
"""
import argparse

from robot.servidor_camara_g1 import ENUMERAR_USB, ErrorCamaraG1, ServidorCamaraG1, candidatas_usb, parsear_camaras_usb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.123.164")
    ap.add_argument("--usuario", default="unitree")
    a = ap.parse_args()
    srv = ServidorCamaraG1(a.host, a.usuario)
    comandos = [
        ("Nodos de vídeo USB (sysfs, sin nombres)", None),
        ("Dispositivos USB de Intel/cámaras", "lsusb | grep -i -E 'intel|realsense|camera|obsbot|webcam' || echo '(ninguno)'"),
        ("Cámaras (v4l2-ctl --list-devices)", "timeout 40 v4l2-ctl --list-devices 2>&1"),
        ("Formatos por nodo", "for d in /dev/video*; do echo \"== $d\"; v4l2-ctl -d $d --list-formats 2>/dev/null | grep -E \"\\[[0-9]+\\]\" ; done"),
        ("Procesos que usan cámaras", "(fuser -v /dev/video* 2>&1 || true) | head -30; ps aux | grep -E 'teleimager|lsc_camara|realsense' | grep -v grep"),
        ("Log del servidor ligero (/tmp/lsc_camara.log)", "tail -n 30 /tmp/lsc_camara.log 2>&1"),
        ("¿Puerto 55556 abierto en el robot?", "(ss -ltnp 2>/dev/null | grep -E '5555[56]') || echo '(cerrado)'"),
    ]
    for titulo, cmd in comandos:
        print(f"\n##### {titulo}")
        try:
            if cmd is None:
                camaras = parsear_camaras_usb(srv._ssh("python3 -", entrada=ENUMERAR_USB, timeout=60))
                for c in camaras:
                    print(f"  {c!r}  color={'sí' if c.es_color else 'no'}  [{c.info}]")
                print("  Orden en que la app las prueba (la primera = la conectada más reciente):")
                for i, (usb, nodos, datos) in enumerate(candidatas_usb(camaras), 1):
                    print(f"   {i}. puerto USB {usb}  nodos {nodos}  {datos[0].velocidad or '?'} Mb/s")
                if not camaras:
                    print("  (el robot no tiene nodos de vídeo USB)")
                continue
            print(srv._ssh(cmd, timeout=60).rstrip())
        except ErrorCamaraG1 as e:
            print(f"ERROR: {e}")
            if "SSH" in str(e):
                break
    print("\n##### Desde esta PC")
    for puerto in (55555, 55556):
        srv.puerto = puerto
        print(f"puerto {puerto}:", "abierto" if srv.puerto_abierto() else "cerrado")


if __name__ == "__main__":
    main()
