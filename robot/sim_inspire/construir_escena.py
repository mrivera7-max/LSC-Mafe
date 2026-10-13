"""Construye la escena de MuJoCo de SONIC con las manos Inspire RH56DFTP-2R.

Toma la escena original de SONIC (``scene_43dof.xml`` + ``g1_29dof_with_hand.xml``,
que trae las manos Dex3) y genera, JUNTO a ellas:

  * ``g1_29dof_inspire.xml``  - mismo robot, sin la geometria Dex3 y con las
    manos Inspire como cuerpos "mocap" que siguen a cada muneca.
  * ``scene_43dof_inspire.xml`` - la escena que carga el simulador.
  * ``meshes/inspire/*.STL``  - mallas de las manos.

Por que "mocap" y no hijas de la muneca: ``base_sim.py`` de SONIC asume que los
joints del robot estan ordenados (indice de joint - 1 = indice de actuador).
Si las articulaciones de los dedos Inspire se insertaran dentro del arbol de la
muneca, se desplazaria el indice de todo el brazo derecho. Con cuerpos mocap
al final del ``worldbody`` los joints Dex3/cuerpo conservan su orden y el
numero de actuadores no cambia.  El Dex3 sigue existiendo (sin geometria)
porque el despliegue de SONIC le sigue enviando comandos.

Uso:  python -m robot.sim_inspire.construir_escena --gr00t DIR_GR00T
"""
from __future__ import annotations

import argparse
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation as R

RAIZ = Path(__file__).resolve().parents[2]
ASSETS = RAIZ / "data" / "inspire_hand"
REL_G1 = Path("gear_sonic/data/robot_model/model_data/g1")

X_MANO = 0.0415   # donde empieza la palma respecto al sistema de la muneca (igual que la palma Dex3)
COLOR = "0.88 0.88 0.90 1"


# ------------------------------------------------------------------ URDF
def _vec(s):
    return np.array([float(v) for v in s.split()]) if s else np.zeros(3)


def leer_urdf(ruta: Path):
    raiz = ET.parse(ruta).getroot()
    links = {}
    for l in raiz.findall("link"):
        mallas = []
        for v in l.findall("visual"):
            m = v.find("geometry/mesh")
            if m is None:
                continue
            o = v.find("origin")
            mallas.append((Path(m.get("filename")).name,
                           _vec(o.get("xyz")) if o is not None else np.zeros(3),
                           _vec(o.get("rpy")) if o is not None else np.zeros(3)))
        links[l.get("name")] = mallas
    joints = []
    for j in raiz.findall("joint"):
        o = j.find("origin")
        lim = j.find("limit")
        mim = j.find("mimic")
        ax = j.find("axis")
        joints.append(dict(
            nombre=j.get("name"), tipo=j.get("type"),
            padre=j.find("parent").get("link"), hijo=j.find("child").get("link"),
            xyz=_vec(o.get("xyz")), rpy=_vec(o.get("rpy")),
            eje=_vec(ax.get("xyz")) if ax is not None else np.array([0, 0, 1.0]),
            rango=(float(lim.get("lower")), float(lim.get("upper"))) if lim is not None else (0, 0),
            mimic=(mim.get("joint"), float(mim.get("multiplier", 1))) if mim is not None else None))
    return links, joints


def _quat(rpy):  # URDF (rpy fijos) -> quaternion MuJoCo [w x y z]
    q = R.from_euler("xyz", rpy).as_quat()
    return [q[3], q[0], q[1], q[2]]


def _f(v):
    return " ".join(f"{x:.6g}" for x in v)


def construir_mano(lado: str, dir_meshes_rel: str = "inspire"):
    """Devuelve (lista de <mesh> para asset, <body> raiz mocap, lista de nombres de joint)."""
    L = lado.upper()
    pref = f"insp_{lado}_"
    links, joints = leer_urdf(ASSETS / f"inspire_hand_{'left' if lado == 'l' else 'right'}.urdf")
    hijos = {}
    for j in joints:
        hijos.setdefault(j["padre"], []).append(j)
    assets = []
    nombres_joint = []

    def geoms(cuerpo, link):
        for i, (f, xyz, rpy) in enumerate(links[link]):
            mn = f"{pref}{Path(f).stem}"
            if not any(a.get("name") == mn for a in assets):
                assets.append(ET.Element("mesh", name=mn, file=f"{dir_meshes_rel}/{f}"))
            ET.SubElement(cuerpo, "geom", type="mesh", mesh=mn, pos=_f(xyz), quat=_f(_quat(rpy)),
                          contype="0", conaffinity="0", group="1", density="0", rgba=COLOR)

    def rec(cuerpo, link):
        for j in hijos.get(link, []):
            b = ET.SubElement(cuerpo, "body", name=pref + j["hijo"], pos=_f(j["xyz"]), quat=_f(_quat(j["rpy"])))
            ET.SubElement(b, "inertial", pos="0 0 0", mass="1e-4", diaginertia="1e-9 1e-9 1e-9")
            if j["tipo"] != "fixed":
                nombre = pref + j["nombre"]
                ET.SubElement(b, "joint", name=nombre, type="hinge", axis=_f(j["eje"]),
                              range=_f(j["rango"]), damping="0.01", armature="1e-5", limited="true")
                nombres_joint.append(nombre)
            geoms(b, j["hijo"])
            rec(b, j["hijo"])

    base = f"{L}_hand_base_link"
    raiz = ET.Element("body", name=f"{pref}base", mocap="true", pos="0 0 -2")
    geoms(raiz, base)
    rec(raiz, base)
    return assets, raiz, nombres_joint


# ------------------------------------------------------------------ montaje en la muneca
def calcular_montaje(lado: str):
    """Rotacion mano->muneca: dedos hacia +X de la muneca y palma hacia +Y (derecha) / -Y (izquierda),
    igual que la mano Dex3 de SONIC.  Se obtiene de la cinematica del propio URDF."""
    import mujoco
    assets, raiz, nombres = construir_mano(lado)
    xml = ET.Element("mujoco")
    ET.SubElement(xml, "compiler", angle="radian", meshdir=str(ASSETS / "meshes"))
    a = ET.SubElement(xml, "asset")
    for m in assets:
        m.set("file", Path(m.get("file")).name)
        a.append(m)
    w = ET.SubElement(xml, "worldbody")
    raiz.set("mocap", "false")
    raiz.attrib.pop("mocap")
    raiz.set("pos", "0 0 0")
    w.append(raiz)
    modelo = mujoco.MjModel.from_xml_string(ET.tostring(xml, encoding="unicode"))
    datos = mujoco.MjData(modelo)
    pref = f"insp_{lado}_{lado.upper()}_"

    def pos(n):
        return datos.xpos[mujoco.mj_name2id(modelo, mujoco.mjtObj.mjOBJ_BODY, n)].copy()

    def poner(q):
        datos.qpos[:] = 0
        for dedo in ("index", "middle", "ring", "pinky"):
            for suf in ("proximal", "intermediate"):
                jid = mujoco.mj_name2id(modelo, mujoco.mjtObj.mjOBJ_JOINT, f"{pref}{dedo}_{suf}_joint")
                datos.qpos[modelo.jnt_qposadr[jid]] = q
        mujoco.mj_forward(modelo, datos)

    dedos = ("index", "middle", "ring", "pinky")
    poner(0.0)
    mcp = np.mean([pos(f"{pref}{d}_proximal") for d in dedos], axis=0)
    punta0 = np.mean([pos(f"{pref}{d}_intermediate") for d in dedos], axis=0)
    d_dedos = punta0 - mcp
    d_dedos /= np.linalg.norm(d_dedos)
    laterales = {d: pos(f"{pref}{d}_proximal") for d in dedos}
    poner(0.6)
    punta1 = np.mean([pos(f"{pref}{d}_intermediate") for d in dedos], axis=0)
    c = (punta1 - punta0)
    c -= d_dedos * (c @ d_dedos)
    c /= np.linalg.norm(c)
    # sistema de la mano (columnas) y el de destino en la muneca
    H = np.column_stack([d_dedos, c, np.cross(d_dedos, c)])
    s = 1.0 if lado == "r" else -1.0          # la palma mira a +Y (der) o -Y (izq) en Dex3
    W = np.column_stack([[1, 0, 0], [0, s, 0], np.cross([1, 0, 0], [0, s, 0])])
    Rm = W @ H.T
    # el indice debe quedar en +Z de la muneca (como en Dex3)
    z_ind = (Rm @ (laterales["index"] - laterales["pinky"]))[2]
    punto_mcp = Rm @ mcp
    # posicion: la base del dedo (MCP) sobre x = X_MANO + longitud palma; el origen de la mano en X_MANO
    p = np.array([X_MANO, 0.0, 0.0]) - Rm @ np.zeros(3)
    return Rm, p, z_ind, punto_mcp


# ------------------------------------------------------------------ escena
DEX3_MALLAS = ("hand_palm_link", "hand_thumb_", "hand_middle_", "hand_index_")


def _es_geom_dex3(g, padre_nombre):
    m = g.get("mesh", "")
    if any(k in m for k in DEX3_MALLAS):
        return True
    # caja de colision en thumb_1
    return g.get("type") == "box" and padre_nombre.startswith(("left_hand_", "right_hand_"))


def generar(gr00t: Path, forzar=False):
    g1 = gr00t / REL_G1
    origen = g1 / "g1_29dof_with_hand.xml"
    escena = g1 / "scene_43dof.xml"
    if not origen.exists() or not escena.exists():
        raise SystemExit(f"No encuentro la escena de SONIC en {g1}")
    arbol = ET.parse(origen)
    raiz = arbol.getroot()

    # 1) quitar la geometria Dex3 (los cuerpos/joints se quedan para no alterar los indices)
    quitadas = 0
    padres = {c: p for p in raiz.iter() for c in p}
    for cuerpo in raiz.iter("body"):
        n = cuerpo.get("name", "")
        en_muneca = n.endswith("wrist_yaw_link")
        if not (n.startswith(("left_hand_", "right_hand_")) or en_muneca):
            continue
        for g in list(cuerpo.findall("geom")):
            if en_muneca:
                if "hand_palm_link" in g.get("mesh", ""):
                    cuerpo.remove(g)
                    quitadas += 1
            elif _es_geom_dex3(g, n):
                cuerpo.remove(g)
                quitadas += 1

    # 2) mallas + manos Inspire (al final del worldbody)
    asset = raiz.find("asset")
    mundo = raiz.find("worldbody")
    custom = raiz.find("custom")
    if custom is None:
        custom = ET.SubElement(raiz, "custom")
    info = {}
    for lado in ("l", "r"):
        mallas, cuerpo, joints = construir_mano(lado)
        for m in mallas:
            asset.append(m)
        mundo.append(cuerpo)
        Rm, p, z_ind, _ = calcular_montaje(lado)
        q = R.from_matrix(Rm).as_quat()
        ET.SubElement(custom, "numeric", name=f"insp_{lado}_montaje",
                      data=_f([*p, q[3], q[0], q[1], q[2]]))
        info[lado] = (len(joints), z_ind)

    # 3) copiar mallas y guardar
    destino = g1 / "meshes" / "inspire"
    destino.mkdir(parents=True, exist_ok=True)
    for f in (ASSETS / "meshes").glob("*.STL"):
        shutil.copy2(f, destino / f.name)
    ET.indent(arbol)
    arbol.write(g1 / "g1_29dof_inspire.xml", encoding="unicode")
    txt = escena.read_text()
    if 'file="g1_29dof_with_hand.xml"' not in txt:
        raise SystemExit("scene_43dof.xml no incluye g1_29dof_with_hand.xml (version distinta de SONIC)")
    (g1 / "scene_43dof_inspire.xml").write_text(txt.replace('file="g1_29dof_with_hand.xml"',
                                                            'file="g1_29dof_inspire.xml"'))
    return g1 / "scene_43dof_inspire.xml", quitadas, info


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gr00t", required=True, help="carpeta GR00T-WholeBodyControl")
    a = ap.parse_args()
    esc, q, info = generar(Path(a.gr00t).expanduser())
    print(f"Escena creada: {esc}\n  geometrias Dex3 ocultas: {q}")
    for l, (n, z) in info.items():
        print(f"  mano {l}: {n} articulaciones Inspire, indice en {'+Z' if z > 0 else '-Z (REVISAR)'}")


if __name__ == "__main__":
    main()
