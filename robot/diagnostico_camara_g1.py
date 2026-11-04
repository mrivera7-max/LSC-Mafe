"""Diagnóstico de la cámara del robot: lista lo que ve el PC2 y por qué no llegan imágenes.

    python -m robot.diagnostico_camara_g1
    python -m robot.diagnostico_camara_g1 --host 192.168.123.164 --usuario unitree

Solo lee (no cambia nada en el robot): cámaras USB y sus formatos, quién las tiene ocupadas y el log del
servidor de cámara. Pega la salida completa si hay que revisarla.
"""
import argparse

from robot.servidor_camara_g1 import ErrorCamaraG1, ServidorCamaraG1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.123.164")
    ap.add_argument("--usuario", default="unitree")
    a = ap.parse_args()
    srv = ServidorCamaraG1(a.host, a.usuario)
    comandos = [
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
