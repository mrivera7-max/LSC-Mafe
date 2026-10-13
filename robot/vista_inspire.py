"""
vista_inspire.py — muestra la mano Inspire RH56DFTP-2R haciendo la forma de cada seña LSC.

Sirve para comprobar, con el modelo 3D real de la mano, que los 6 valores por seña
(`data/formas_mano_lsc.json`, calculados con los datos del repositorio) dan la forma esperada
antes de mandarlos al robot. No necesita el simulador de SONIC.

Modelo 3D: el URDF + mallas de Unitree (repositorio unitreerobotics/xr_teleoperate, carpeta
assets/inspire_hand, ~12 MB). Una sola vez:

    git clone --depth 1 https://github.com/unitreerobotics/xr_teleoperate ~/xr_teleoperate

Uso (desde la raíz del proyecto, con el entorno virtual activado; requiere `pip install mujoco`):

    python -m robot.vista_inspire --png manos.png            # imagen con las 7 señas
    python -m robot.vista_inspire --visor                     # ventana 3D que recorre las señas
    python -m robot.vista_inspire --png manos.png --assets /ruta/a/assets/inspire_hand
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np

from robot.mano_inspire import cargar_formas, normalizado_a_rad

ASSETS_RELATIVO = Path("assets") / "inspire_hand"
BUSCAR_EN = (
    Path.home() / "xr_teleoperate",
    Path.home() / "unitreerobotics" / "xr_teleoperate",
    Path.cwd() / "xr_teleoperate",
    Path.cwd().parent / "xr_teleoperate",
)
SENAS = ("Hola", "Gracias", "Si", "No", "Bien", "Mal", "Silencio")


def buscar_assets(explicito: Optional[str] = None) -> Path:
    candidatos = [Path(explicito).expanduser()] if explicito else []
    if os.environ.get("INSPIRE_ASSETS"):
        candidatos.append(Path(os.environ["INSPIRE_ASSETS"]).expanduser())
    candidatos += [b / ASSETS_RELATIVO for b in BUSCAR_EN]
    for c in candidatos:
        if (c / "inspire_hand_right.urdf").exists() and (c / "meshes").is_dir():
            return c
    raise FileNotFoundError(
        "No encuentro el modelo 3D de la mano Inspire (inspire_hand_right.urdf + meshes/).\n"
        "Descárgalo con:  git clone --depth 1 https://github.com/unitreerobotics/xr_teleoperate ~/xr_teleoperate\n"
        "o indica la carpeta con --assets <ruta>/assets/inspire_hand")


def cargar_modelo(assets: Path, lado: str = "right"):
    import mujoco
    urdf = assets / f"inspire_hand_{lado}.urdf"
    texto = urdf.read_text(encoding="utf-8")
    # el URDF ya trae meshdir="./meshes" Y rutas "./meshes/..."; se deja solo meshdir (absoluto)
    texto = texto.replace('meshdir="./meshes"', f'meshdir="{assets / "meshes"}"').replace('filename="./meshes/', 'filename="')
    with tempfile.NamedTemporaryFile("w", suffix=".urdf", delete=False, encoding="utf-8") as f:
        f.write(texto)
        tmp = f.name
    try:
        modelo = mujoco.MjModel.from_xml_path(tmp)
    finally:
        os.unlink(tmp)
    # el URDF trae ejes y esferas de referencia (no son mallas de la mano): se ocultan
    for g in range(modelo.ngeom):
        if modelo.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            modelo.geom_rgba[g, 3] = 0.0
        else:
            modelo.geom_rgba[g] = (0.62, 0.66, 0.74, 1.0)
    modelo.vis.headlight.ambient[:] = 0.3
    modelo.vis.headlight.diffuse[:] = 0.7
    return modelo


def poner_forma(modelo, datos, f6, lado: str = "R"):
    """Coloca los 12 joints del URDF a partir de los 6 valores normalizados (1 = abierto).
    Las falanges 'mimic' siguen a su actuador: dedos 1:1; pulgar 1.6x y 2.4x del pitch."""
    import mujoco
    q = normalizado_a_rad(f6)         # meñique, anular, medio, índice, pulgar-flexión(pitch), pulgar-rotación(yaw)
    p = f"{lado}_"
    valores = {
        p + "pinky_proximal_joint": q[0], p + "pinky_intermediate_joint": q[0],
        p + "ring_proximal_joint": q[1], p + "ring_intermediate_joint": q[1],
        p + "middle_proximal_joint": q[2], p + "middle_intermediate_joint": q[2],
        p + "index_proximal_joint": q[3], p + "index_intermediate_joint": q[3],
        p + "thumb_proximal_pitch_joint": q[4],
        p + "thumb_intermediate_joint": 1.6 * q[4], p + "thumb_distal_joint": 2.4 * q[4],
        p + "thumb_proximal_yaw_joint": q[5],
    }
    for nombre, v in valores.items():
        j = modelo.joint(nombre)
        lo, hi = modelo.jnt_range[j.id]
        datos.qpos[modelo.jnt_qposadr[j.id]] = float(np.clip(v, lo, hi))
    mujoco.mj_forward(modelo, datos)


def _camara(modelo, datos):
    import mujoco
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.lookat[:] = [0.0, -0.09, 0.0]
    cam.distance, cam.azimuth, cam.elevation = 0.33, 150, -25
    return cam


def renderizar_png(assets: Path, ruta: str, senas=SENAS, formas=None, tam: int = 400) -> str:
    import mujoco
    from PIL import Image, ImageDraw
    formas = formas or cargar_formas()
    modelo = cargar_modelo(assets)
    datos = mujoco.MjData(modelo)
    r = mujoco.Renderer(modelo, tam, tam)
    cam = _camara(modelo, datos)
    imagenes = []
    for s in senas:
        poner_forma(modelo, datos, formas[s])
        r.update_scene(datos, camera=cam)
        im = Image.fromarray(r.render().copy())
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, tam, 22), fill=(20, 20, 30))
        d.text((6, 5), f"{s}   " + " ".join(f"{x:.1f}" for x in formas[s]), fill=(255, 255, 255))
        imagenes.append(im)
    columnas = 4
    filas = (len(imagenes) + columnas - 1) // columnas
    lienzo = Image.new("RGB", (columnas * tam, filas * tam), (30, 30, 40))
    for i, im in enumerate(imagenes):
        lienzo.paste(im, ((i % columnas) * tam, (i // columnas) * tam))
    lienzo.save(ruta)
    r.close()
    return ruta


def visor(assets: Path, senas=SENAS, formas=None, segundos: float = 2.2):
    import mujoco
    import mujoco.viewer
    formas = formas or cargar_formas()
    modelo = cargar_modelo(assets)
    datos = mujoco.MjData(modelo)
    print("Visor de la mano Inspire. Cierra la ventana para salir.")
    with mujoco.viewer.launch_passive(modelo, datos) as v:
        v.cam.lookat[:] = [0.0, -0.10, 0.0]
        v.cam.distance, v.cam.azimuth, v.cam.elevation = 0.45, 150, -25
        i, ultimo, actual = 0, 0.0, np.array(formas["REPOSO"])
        destino = actual.copy()
        while v.is_running():
            ahora = time.time()
            if ahora - ultimo > segundos:
                nombre = senas[i % len(senas)]
                destino = np.array(formas[nombre])
                print(f"→ {nombre}: " + " ".join(f"{x:.2f}" for x in destino))
                i, ultimo = i + 1, ahora
            actual += 0.12 * (destino - actual)
            poner_forma(modelo, datos, actual)
            v.sync()
            time.sleep(0.02)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", help="carpeta assets/inspire_hand de xr_teleoperate")
    ap.add_argument("--png", help="guarda una imagen con la mano en cada seña")
    ap.add_argument("--visor", action="store_true", help="ventana 3D que recorre las señas")
    ap.add_argument("senas", nargs="*", help="señas a mostrar (por defecto las 7)")
    a = ap.parse_args(argv)
    if a.png and not a.visor and not os.environ.get("DISPLAY"):
        os.environ.setdefault("MUJOCO_GL", "egl")       # sin pantalla: render fuera de pantalla (debe fijarse antes de importar mujoco)
    try:
        import mujoco  # noqa: F401
    except ImportError:
        print("Falta mujoco:  pip install mujoco")
        return 2
    try:
        assets = buscar_assets(a.assets)
    except FileNotFoundError as e:
        print(e)
        return 2
    senas = tuple(a.senas) or SENAS
    if a.png:
        print("Imagen guardada en", renderizar_png(assets, a.png, senas))
    if a.visor or not a.png:
        visor(assets, senas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
