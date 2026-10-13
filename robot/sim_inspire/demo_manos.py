"""Prueba rapida SIN la app: mueve las manos Inspire del simulador abierto.

  1) Terminal del simulador:  python gear_sonic/scripts/run_sim_loop.py   (ya con el instalador aplicado)
  2) Esta:                    python -m robot.sim_inspire.demo_manos

Recorre: abierta -> puno -> indice -> puno con pulgar -> abierta (ambas manos).
"""
import json
import time

import zmq

FORMAS = {  # rad: menique, anular, medio, indice, pulgar-flexion, pulgar-rotacion
    "abierta (Hola/Gracias)": [0, 0, 0, 0, 0, 0],
    "puno (Si)": [1.7, 1.7, 1.7, 1.7, 0.4, 1.0],
    "indice (No/Silencio)": [1.7, 1.7, 1.7, 0, 0.4, 1.0],
    "puno + pulgar (Bien/Mal)": [1.7, 1.7, 1.7, 1.7, 0, 0],
}


def main():
    s = zmq.Context.instance().socket(zmq.PUB)
    s.bind("tcp://127.0.0.1:5601")
    time.sleep(1.0)
    for _ in range(2):
        for n, v in FORMAS.items():
            print("->", n)
            t0 = time.time()
            while time.time() - t0 < 2.0:
                s.send_string(json.dumps({"l": v, "r": v}))
                time.sleep(0.02)
    s.send_string(json.dumps({"l": [0] * 6, "r": [0] * 6}))


if __name__ == "__main__":
    main()
