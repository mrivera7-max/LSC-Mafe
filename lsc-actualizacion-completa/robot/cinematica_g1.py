"""
cinematica_g1.py — cinemática directa del G1 de 29 GDL a partir del MJCF del simulador.

Sirve para comprobar dónde queda la mano en cada fotograma de una seña SIN abrir MuJoCo.
Solo usa numpy y la librería estándar. Lee el mismo modelo que la simulación:
    <GR00T>/gear_sonic/data/robots/g1/g1_29dof_old_freebase.xml
Todas las posiciones se dan en metros en el marco de la pelvis (x adelante, y izquierda, z arriba).
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional

import numpy as np

XML_RELATIVO = Path("gear_sonic/data/robots/g1/g1_29dof_old_freebase.xml")

# Índice MuJoCo (qpos sin la base flotante) -> nombre de la articulación
NOMBRES_17 = {
    12: "waist_yaw_joint", 13: "waist_roll_joint", 14: "waist_pitch_joint",
    15: "left_shoulder_pitch_joint", 16: "left_shoulder_roll_joint", 17: "left_shoulder_yaw_joint",
    18: "left_elbow_joint", 19: "left_wrist_roll_joint", 20: "left_wrist_pitch_joint",
    21: "left_wrist_yaw_joint",
    22: "right_shoulder_pitch_joint", 23: "right_shoulder_roll_joint", 24: "right_shoulder_yaw_joint",
    25: "right_elbow_joint", 26: "right_wrist_roll_joint", 27: "right_wrist_pitch_joint",
    28: "right_wrist_yaw_joint",
}
LARGO_MANO = 0.11            # de la muñeca (wrist_yaw_link) al centro de la mano, a lo largo de su eje x


def _quat_a_R(q) -> np.ndarray:
    w, x, y, z = np.asarray(q, float) / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _rot(eje, ang) -> np.ndarray:
    a = np.asarray(eje, float)
    a = a / np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K


def _vec(texto: Optional[str], defecto: str) -> np.ndarray:
    return np.array([float(v) for v in (texto or defecto).split()])


class CinematicaG1:
    def __init__(self, xml: Optional[str] = None, directorio_gr00t: Optional[str] = None):
        ruta = Path(xml) if xml else Path(directorio_gr00t or ".").expanduser() / XML_RELATIVO
        if not ruta.exists():
            raise FileNotFoundError(f"No encuentro el modelo del G1: {ruta}")
        raiz = ET.parse(ruta).getroot()
        self._arbol = self._leer(raiz.find("worldbody").find("body"))

    def _leer(self, b) -> dict:
        return dict(
            nombre=b.get("name"), pos=_vec(b.get("pos"), "0 0 0"), quat=_vec(b.get("quat"), "1 0 0 0"),
            juntas=[(j.get("name"), _vec(j.get("axis"), "0 0 1")) for j in b.findall("joint")
                    if j.get("type") != "free"],
            hijos=[self._leer(k) for k in b.findall("body")])

    def posiciones(self, qpos_mj: Dict[int, float]) -> Dict[str, tuple]:
        """{cuerpo: (posición, matriz de rotación)} para una postura {índice MuJoCo: rad}."""
        q = {NOMBRES_17[k]: v for k, v in qpos_mj.items() if k in NOMBRES_17}
        salida = {}

        def rec(n, Rp, pp, raiz=False):
            R = Rp @ _quat_a_R(n["quat"])
            p = pp + (np.zeros(3) if raiz else Rp @ n["pos"])
            for nombre, eje in n["juntas"]:
                R = R @ _rot(eje, q.get(nombre, 0.0))
            salida[n["nombre"]] = (p, R)
            for k in n["hijos"]:
                rec(k, R, p)

        rec(self._arbol, np.eye(3), np.zeros(3), raiz=True)
        return salida

    def mano_derecha(self, qpos_mj: Dict[int, float]) -> dict:
        """Hombro, codo, muñeca, centro de la mano (der.) y direcciones de dedos, palma y pulgar."""
        r = self.posiciones(qpos_mj)
        pm, Rm = r["right_wrist_yaw_link"]
        return dict(hombro=r["right_shoulder_pitch_link"][0], codo=r["right_elbow_link"][0],
                    muneca=pm, mano=pm + Rm[:, 0] * LARGO_MANO, dedos=Rm[:, 0], palma=Rm[:, 1],
                    pulgar=np.cross(Rm[:, 0], Rm[:, 1]))
