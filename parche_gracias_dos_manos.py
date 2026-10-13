#!/usr/bin/env python3
"""«Gracias» con los dos brazos y las dos manos (abiertas, segun data/sequences) en robot/conector_sonic.py.
Repetible; deja copia robot/conector_sonic.py.bak_gracias."""
import shutil
import sys
from pathlib import Path

p = Path(__file__).resolve().parent / "robot" / "conector_sonic.py"
t = p.read_text(encoding="utf-8")
if "_espejo(" in t:
    sys.exit("Ya estaba aplicado.")

ANTES = '''    "Gracias": [
        ({22: -0.24, 23: -0.68, 24: 0.83, 25: -0.78, 26: 0.42, 27: -1.06, 28: -0.14}, 1.0),
        ({22: -0.28, 23: -0.04, 24: -0.10, 25: -0.72, 26: 0.12, 27: 1.09, 28: -0.08}, 1.0),
    ],
'''
NUEVO = '''    # ambas manos planas desde la barbilla hacia adelante (en data/sequences las dos manos aparecen abiertas)
    "Gracias": [
        ({**_GRACIAS_1, **_espejo(_GRACIAS_1)}, 1.0, _MANOS_GRACIAS),
        ({**_GRACIAS_2, **_espejo(_GRACIAS_2)}, 1.0, _MANOS_GRACIAS),
    ],
'''
DEFS = '''
# Brazo izquierdo (15..21) = espejo del derecho (22..28): roll y yaw cambian de signo.
_ESPEJO = {22: (15, 1), 23: (16, -1), 24: (17, -1), 25: (18, 1), 26: (19, -1), 27: (20, 1), 28: (21, -1)}


def _espejo(der: dict) -> dict:
    return {izq: signo * der[d] for d, (izq, signo) in _ESPEJO.items() if d in der}


_GRACIAS_1 = {22: -0.24, 23: -0.68, 24: 0.83, 25: -0.78, 26: 0.42, 27: -1.06, 28: -0.14}
_GRACIAS_2 = {22: -0.28, 23: -0.04, 24: -0.10, 25: -0.72, 26: 0.12, 27: 1.09, 28: -0.08}
_MANOS_GRACIAS = {"der": "Gracias", "izq": "Gracias"}
'''
ancla = "SEÑAS_SONIC = {"
if ANTES not in t or ancla not in t:
    sys.exit("El bloque «Gracias» de tu conector_sonic.py no coincide con el esperado; no se modifico nada.")
shutil.copy2(p, p.with_suffix(".py.bak_gracias"))
t = t.replace(ANTES, NUEVO).replace(ancla, DEFS.lstrip("\n") + "\n" + ancla, 1)
p.write_text(t, encoding="utf-8")
print("Gracias ahora usa los dos brazos y las dos manos.")
