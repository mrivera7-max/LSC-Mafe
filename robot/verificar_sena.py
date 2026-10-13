"""
Verifica cada seña SIN abrir MuJoCo: dónde queda la mano derecha en cada fotograma, hacia dónde
apuntan los dedos y el pulgar, qué forma tiene la mano (Inspire RH56DFTP-2R, de los datos del
repositorio) y si todos los joints respetan sus límites.

    python -m robot.verificar_sena                 # todas las señas
    python -m robot.verificar_sena Hola Mal
    python -m robot.verificar_sena Hola --png hola.png     # dibujo frontal/lateral (requiere matplotlib)

Referencias (respecto a la pelvis, G1 de pie): hombro z≈0.29 · boca z≈0.36 · cabeza z≈0.43–0.50.
Devuelve 0 si todo está en orden, 1 si algún joint sale de sus límites, 2 si falta el modelo.
"""

import argparse
import os
import sys

import numpy as np

from robot.cinematica_g1 import CinematicaG1
from robot.conector_sonic import LIMITES_MJ, POSTURA_BASE_MJ, SEÑAS_SONIC
from robot.lanzador_sonic import DIR_DEFECTO
from robot.mano_inspire import ORDEN, cargar_formas

Z_HOMBRO, Z_BOCA, Z_CABEZA = 0.29, 0.36, 0.45


def zona(z: float) -> str:
    if z >= Z_CABEZA - 0.04:
        return "a la altura de la cabeza"
    if z >= Z_BOCA - 0.05:
        return "a la altura de la boca"
    if z >= Z_HOMBRO - 0.06:
        return "a la altura del hombro"
    if z >= 0.10:
        return "a la altura del pecho"
    return "baja (cintura o menos)"


def direccion(v) -> str:
    """Texto corto para un vector (x adelante, y izquierda, z arriba)."""
    v = np.asarray(v, float)
    partes = []
    if abs(v[2]) >= 0.5:
        partes.append("hacia arriba" if v[2] > 0 else "hacia abajo")
    if v[0] >= 0.5:
        partes.append("hacia adelante")
    elif v[0] <= -0.5:
        partes.append("hacia atrás")
    if v[1] >= 0.5:
        partes.append("hacia la izquierda")
    elif v[1] <= -0.5:
        partes.append("hacia la derecha")
    return " y ".join(partes) or "en diagonal"


def describir_mano(f6) -> str:
    """Descripción en palabras de los 6 valores de la mano (1 = abierto)."""
    d = dict(zip(ORDEN, f6))
    abiertos = [n for n in ("indice", "medio", "anular", "menique") if d[n] >= 0.6]
    nombres = {"indice": "índice", "medio": "medio", "anular": "anular", "menique": "meñique"}
    if len(abiertos) == 4:
        base = "mano abierta (4 dedos extendidos)"
    elif not abiertos:
        base = "puño"
    else:
        base = "extendidos: " + ", ".join(nombres[n] for n in abiertos) + "; el resto cerrado"
    pulgar = "pulgar pegado" if d["pulgar_rotacion"] < 0.45 else ("pulgar abierto" if d["pulgar_rotacion"] > 0.8 else "pulgar a medias")
    if d["pulgar_flexion"] < 0.4:
        pulgar = "pulgar doblado"
    return f"{base}, {pulgar}"


def fotogramas(nombre: str):
    estado = dict(POSTURA_BASE_MJ)
    for fot in SEÑAS_SONIC[nombre]:
        estado.update(fot[0])
        yield dict(estado), fot[1]


def fuera_de_limites(q: dict) -> list:
    return [(k, v, LIMITES_MJ[k]) for k, v in q.items() if k in LIMITES_MJ and not LIMITES_MJ[k][0] - 1e-6 <= v <= LIMITES_MJ[k][1] + 1e-6]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("senas", nargs="*", help="nombres de señas (por defecto todas)")
    ap.add_argument("--gr00t-dir", default=os.environ.get("GR00T_DIR") or DIR_DEFECTO)
    ap.add_argument("--xml", help="ruta al g1_29dof_old_freebase.xml (si no, se busca en --gr00t-dir)")
    ap.add_argument("--png", help="guarda un dibujo de la postura final de la seña indicada")
    a = ap.parse_args(argv)
    try:
        cin = CinematicaG1(a.xml, a.gr00t_dir)
    except FileNotFoundError as e:
        print(e)
        return 2
    nombres = a.senas or list(SEÑAS_SONIC)
    for n in nombres:
        if n not in SEÑAS_SONIC:
            print(f"No hay seña «{n}». Disponibles: {', '.join(SEÑAS_SONIC)}")
            return 2
    formas = cargar_formas()
    problemas = 0
    ultimo = None
    for n in nombres:
        print(f"\n== {n}   ·   dedos: {describir_mano(formas[n]) if n in formas else '(sin datos)'}")
        if n in formas:
            print("   valores de la mano (1=abierto): " + " ".join(f"{k}={v:.2f}" for k, v in zip(ORDEN, formas[n])))
        for i, (q, dur) in enumerate(fotogramas(n)):
            m = cin.mano_derecha(q)
            x, y, z = m["mano"]
            malos = fuera_de_limites(q)
            problemas += len(malos)
            print(f"  fotograma {i:02d} ({dur:.2f} s)  mano x={x:+.2f} y={y:+.2f} z={z:+.2f}  → {zona(z)}"
                  f"\n      dedos {direccion(m['dedos'])} · pulgar {direccion(m['pulgar'])}"
                  f" · codo={q[25]:+.2f} muñeca(r/p/y)={q[26]:+.2f}/{q[27]:+.2f}/{q[28]:+.2f}")
            for k, v, (lo, hi) in malos:
                print(f"      ⚠ joint {k} = {v:+.2f} fuera de [{lo:+.2f}, {hi:+.2f}]")
            ultimo = (n, q)
    if a.png and ultimo:
        _dibujar(cin, ultimo[1], ultimo[0], a.png)
        print(f"\nDibujo guardado en {a.png}")
    print("\n" + ("Todos los joints dentro de sus límites." if not problemas else f"{problemas} joint(s) fuera de límites."))
    return 1 if problemas else 0


def _dibujar(cin: CinematicaG1, q: dict, titulo: str, ruta: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    m = cin.mano_derecha(q)
    fig, ejes = plt.subplots(1, 2, figsize=(7, 4))
    for ax, (ix, nombre) in zip(ejes, ((1, "frontal (y→, z↑)"), (0, "lateral (x→ adelante, z↑)"))):
        ax.add_patch(plt.Circle((0, 0.44), 0.09, fc="#fde68a", ec="#b45309"))
        ax.add_patch(plt.Rectangle((-0.16 if ix == 1 else -0.08, 0), 0.32 if ix == 1 else 0.16, 0.30,
                                   fc="#e5e7eb", ec="#9ca3af"))
        pts = [m[k] for k in ("hombro", "codo", "muneca", "mano")]
        ax.plot([p[ix] for p in pts], [p[2] for p in pts], "-o", c="#2563eb", lw=3)
        mano = m["mano"]
        ax.annotate("", xy=(mano[ix] + 0.1 * m["pulgar"][ix], mano[2] + 0.1 * m["pulgar"][2]), xytext=(mano[ix], mano[2]),
                    arrowprops=dict(arrowstyle="->", color="#dc2626"))
        ax.axhline(Z_BOCA, ls=":", c="#f59e0b", lw=0.8)
        ax.set_aspect("equal"); ax.set_ylim(-0.3, 0.65); ax.grid(alpha=.3); ax.set_title(f"{titulo} — {nombre}", fontsize=9)
    plt.tight_layout(); plt.savefig(ruta, dpi=140)


if __name__ == "__main__":
    sys.exit(main())
