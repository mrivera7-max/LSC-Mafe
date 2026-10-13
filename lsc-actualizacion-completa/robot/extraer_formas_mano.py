"""
extraer_formas_mano.py — forma de la mano (6 actuadores de la Inspire RH56DFTP-2R) por seña,
calculada a partir de los datos reales del repositorio (data/sequences/<Seña>/*.npy).

Cada .npy es el vector agregado de ExtractorSecuencial (342 valores, models/extractor_v2.py):
    [0:63] landmarks medios mano «Right»   [63:126] mano «Left»   (21 puntos x 3, relativos a la muñeca)
Como las muestras se grabaron con la imagen en espejo, la mano que hace la seña puede quedar en
cualquiera de las dos posiciones; se usa la que esté presente en más muestras de cada seña.

De los 21 puntos de MediaPipe se obtiene cuánto se flexiona cada dedo y la posición del pulgar, y se
convierte a la escala de la mano Inspire: 1.0 = abierto, 0.0 = cerrado.

    python -m robot.extraer_formas_mano            # escribe data/formas_mano_lsc.json
    python -m robot.extraer_formas_mano --ver      # además imprime la tabla
"""

import argparse
import glob
import json
import os
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
RUTA_SECUENCIAS = RAIZ / "data" / "sequences"
RUTA_SALIDA = RAIZ / "data" / "formas_mano_lsc.json"

# Orden de los 6 actuadores de la Inspire RH56DFTP-2R (Unitree: ids 0..5)
ORDEN = ("menique", "anular", "medio", "indice", "pulgar_flexion", "pulgar_rotacion")
# MediaPipe: (MCP, PIP, DIP, punta) de cada dedo
REALCE = 1.4          # factor de contraste de la apertura de los dedos
DEDOS = {"indice": (5, 6, 7, 8), "medio": (9, 10, 11, 12), "anular": (13, 14, 15, 16), "menique": (17, 18, 19, 20)}


def _ang(a, b) -> float:
    a = a / (np.linalg.norm(a) + 1e-9)
    b = b / (np.linalg.norm(b) + 1e-9)
    return float(np.arccos(np.clip(a @ b, -1.0, 1.0)))


def medidas_mano(vec63: np.ndarray) -> dict:
    """Flexión total de cada dedo (rad) y datos del pulgar a partir de 21 puntos (63 valores)."""
    P = np.asarray(vec63, float).reshape(21, 3)
    m = {}
    for nombre, (mcp, pip, dip, pta) in DEDOS.items():
        m[nombre] = (_ang(P[mcp] - P[0], P[pip] - P[mcp]) + _ang(P[pip] - P[mcp], P[dip] - P[pip])
                     + _ang(P[dip] - P[pip], P[pta] - P[dip]))
    m["pulgar_flex"] = _ang(P[2] - P[1], P[3] - P[2]) + _ang(P[3] - P[2], P[4] - P[3])
    m["pulgar_sep"] = float(np.linalg.norm(P[4] - P[5]))      # punta del pulgar a la base del índice
    return m


def _lado_con_mano(matriz: np.ndarray) -> int:
    """0 ó 63: posición del vector donde hay mano en más muestras."""
    pres0 = (np.abs(matriz[:, 0:63]).sum(1) > 1e-6).sum()
    pres1 = (np.abs(matriz[:, 63:126]).sum(1) > 1e-6).sum()
    return 0 if pres0 >= pres1 else 63


def calcular(ruta_secuencias: Path = RUTA_SECUENCIAS) -> dict:
    clases = sorted(p.name for p in Path(ruta_secuencias).iterdir() if p.is_dir())
    if not clases:
        raise FileNotFoundError(f"No hay señas en {ruta_secuencias}")
    medidas, info = {}, {}
    for c in clases:
        archivos = sorted(glob.glob(os.path.join(ruta_secuencias, c, "*.npy")))
        M = np.array([np.load(f) for f in archivos])
        off = _lado_con_mano(M)
        ok = np.abs(M[:, off:off + 63]).sum(1) > 1e-6
        medidas[c] = [medidas_mano(v[off:off + 63]) for v in M[ok]]
        mov = np.linalg.norm(M[ok][:, 150 + off + 24:150 + off + 27], axis=1) if ok.any() else np.zeros(1)
        info[c] = dict(muestras=int(ok.sum()), total=int(len(M)), posicion_vector=off,
                       movimiento_punta_indice=float(np.median(mov)))
    # Escala común (todas las señas y dedos): abierto = percentil 5, cerrado = percentil 95
    flex = np.array([r[d] for c in clases for r in medidas[c] for d in DEDOS])
    t_abierto, t_cerrado = float(np.percentile(flex, 5)), float(np.percentile(flex, 95))
    sep = np.array([r["pulgar_sep"] for c in clases for r in medidas[c]])
    s_cerca, s_lejos = float(np.percentile(sep, 5)), float(np.percentile(sep, 95))
    pf = np.array([r["pulgar_flex"] for c in clases for r in medidas[c]])
    f_abierto, f_cerrado = float(np.percentile(pf, 5)), float(np.percentile(pf, 95))

    formas = {}
    for c in clases:
        r = medidas[c]
        if not r:
            continue
        med = {k: float(np.median([x[k] for x in r])) for k in r[0]}
        cierre = lambda t: float(np.clip((t - t_abierto) / (t_cerrado - t_abierto), 0, 1))
        # MediaPipe subestima el cierre de un puño; se acentúa el contraste alrededor de 0.5
        nitido = lambda a: float(np.clip((a - 0.5) * REALCE + 0.5, 0, 1))
        sep_n = float(np.clip((med["pulgar_sep"] - s_cerca) / (s_lejos - s_cerca), 0, 1))
        flex_n = float(np.clip((med["pulgar_flex"] - f_abierto) / (f_cerrado - f_abierto), 0, 1))
        formas[c] = {
            "menique": round(nitido(1 - cierre(med["menique"])), 2),
            "anular": round(nitido(1 - cierre(med["anular"])), 2),
            "medio": round(nitido(1 - cierre(med["medio"])), 2),
            "indice": round(nitido(1 - cierre(med["indice"])), 2),
            "pulgar_flexion": round(1 - flex_n, 2),
            # 1.0 = pulgar abierto hacia afuera; ~0.35 pegado a la mano; <0.35 cruzado sobre la palma
            "pulgar_rotacion": round(0.35 + 0.65 * sep_n, 2),
        }
    return dict(orden=list(ORDEN), escala="1.0 = abierto, 0.0 = cerrado", formas=formas,
                referencia=dict(flexion_abierta_rad=t_abierto, flexion_cerrada_rad=t_cerrado),
                fuente={c: info[c] for c in clases})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--secuencias", default=str(RUTA_SECUENCIAS))
    ap.add_argument("--salida", default=str(RUTA_SALIDA))
    ap.add_argument("--ver", action="store_true")
    a = ap.parse_args()
    res = calcular(Path(a.secuencias))
    Path(a.salida).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Formas de mano escritas en {a.salida}")
    if a.ver:
        print(f"{'seña':10s} " + " ".join(f"{n[:9]:>9s}" for n in ORDEN) + "   muestras")
        for c, f in res["formas"].items():
            print(f"{c:10s} " + " ".join(f"{f[n]:9.2f}" for n in ORDEN) + f"   {res['fuente'][c]['muestras']}/{res['fuente'][c]['total']}")


if __name__ == "__main__":
    main()
