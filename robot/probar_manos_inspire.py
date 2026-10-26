"""Prueba de las manos Inspire por Modbus TCP, sin la app ni el robot.

    python -m robot.probar_manos_inspire              # las dos manos
    python -m robot.probar_manos_inspire --solo der   # solo la derecha (o izq)

Con las manos libres (nada que atrapar). Recorre: abierta, medio cerrada, puño, índice extendido, abierta.
Ctrl+C la deja abierta.
"""
import argparse
import time

from robot.inspire_modbus import IP_DER, IP_IZQ, PUERTO, SalidaInspireModbus

ABIERTA = [1, 1, 1, 1, 1, 1]
PASOS = [("abierta", ABIERTA), ("medio cerrada", [0.5, 0.5, 0.5, 0.5, 0.7, 0.6]),
         ("puño", [0.05, 0.0, 0.0, 0.2, 0.2, 0.55]), ("índice extendido", [0.3, 0.3, 0.35, 1.0, 0.25, 0.55]),
         ("abierta", ABIERTA)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo", choices=["der", "izq"])
    ap.add_argument("--espera", type=float, default=2.5, help="segundos por postura")
    a = ap.parse_args()
    print(f"Derecha {IP_DER}:{PUERTO} | Izquierda {IP_IZQ}:{PUERTO}")
    s = SalidaInspireModbus()
    if not s.abrir():
        raise SystemExit(f"✖ {s.error}")
    if s.error:
        print(f"⚠ {s.error}")
    time.sleep(0.3)
    print("Conexión:", s.estado())
    try:
        for nombre, v in PASOS:
            izq = v if a.solo != "der" else ABIERTA
            der = v if a.solo != "izq" else ABIERTA
            print(f"→ {nombre}")
            t0 = time.time()
            while time.time() - t0 < a.espera:
                s.enviar(izq, der)
                time.sleep(0.04)
    except KeyboardInterrupt:
        pass
    finally:
        for _ in range(10):
            s.enviar(ABIERTA, ABIERTA)
            time.sleep(0.04)
        time.sleep(0.3)
        s.cerrar()
        print("Manos abiertas. Fin.")


if __name__ == "__main__":
    main()
