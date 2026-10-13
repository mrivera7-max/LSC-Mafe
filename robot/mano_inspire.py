"""
mano_inspire.py — mano Inspire RH56DFTP-2R del Unitree G1 (6 actuadores por mano).

Es el modelo de mano de referencia de toda la app. Cada mano se describe con 6 valores NORMALIZADOS
(1.0 = abierto, 0.0 = cerrado), en el orden de los actuadores de Unitree/Inspire:

    0 meñique   1 anular   2 medio   3 índice   4 pulgar-flexión   5 pulgar-rotación

Rangos articulares reales (URDF de unitreerobotics/xr_teleoperate, assets/inspire_hand):
    dedos 0..3          0 .. 1.7 rad   (1.7 = cerrado; la falange distal sigue a la proximal 1:1)
    pulgar-flexión 4    0 .. 0.5 rad   (el URDF suma 1.6x y 2.4x en las falanges siguientes)
    pulgar-rotación 5  -0.1 .. 1.3 rad (1.3 = pulgar cruzado sobre la palma)

Salidas:
  * Robot real con manos Inspire:  DDS  rt/inspire_hand/ctrl/{l,r}  (`inspire_hand_ctrl`, modo ángulo,
    `angle_set` = 6 enteros 0..1000; 1000 = abierto) — ver `SalidaInspireDDS`.
  * Simulador de SONIC: su MuJoCo trae manos Dex3 (7 articulaciones), no Inspire. `a_dex3` traduce los
    6 valores a las 7 articulaciones Dex3 (solo tiene pulgar, índice y medio: anular y meñique no se ven).
    Se envían en `left_hand_joints` / `right_hand_joints` del mensaje `planner` del deploy.

Las formas de la mano por seña salen de los datos del repositorio (`robot/extraer_formas_mano.py`).
"""

import json
import logging
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

import numpy as np

log = logging.getLogger("lsc_bridge.mano_inspire")

ORDEN = ("menique", "anular", "medio", "indice", "pulgar_flexion", "pulgar_rotacion")
N_ACTUADORES = 6

# (valor abierto, valor cerrado) en rad de cada actuador
RANGO_RAD = (
    (0.0, 1.7), (0.0, 1.7), (0.0, 1.7), (0.0, 1.7),
    (0.0, 0.5),
    (-0.1, 1.3),
)

RUTA_FORMAS = Path(__file__).resolve().parent.parent / "data" / "formas_mano_lsc.json"

# Formas básicas (respaldo si no existe data/formas_mano_lsc.json y para pruebas)
ABIERTA = (1.0, 1.0, 1.0, 1.0, 0.9, 0.4)
PUNO = (0.0, 0.0, 0.0, 0.0, 0.2, 0.5)
FORMAS_BASE: Dict[str, tuple] = {
    "Hola": (1.0, 1.0, 1.0, 1.0, 0.9, 0.45),
    "Gracias": (0.95, 1.0, 1.0, 0.95, 0.85, 0.4),
    "Si": (0.05, 0.0, 0.0, 0.25, 0.15, 0.55),
    "No": (0.4, 0.4, 0.45, 1.0, 0.25, 0.55),
    "Bien": (0.2, 0.2, 0.25, 0.2, 0.85, 1.0),
    "Mal": (0.05, 0.05, 0.1, 0.25, 0.85, 0.9),
    "Silencio": (0.25, 0.25, 0.3, 1.0, 0.8, 0.4),
    "REPOSO": (0.9, 0.9, 0.9, 0.9, 0.8, 0.4),
}


def _como6(v) -> np.ndarray:
    a = np.asarray(v, dtype=float).reshape(-1)
    if a.size != N_ACTUADORES:
        raise ValueError(f"Una mano necesita {N_ACTUADORES} valores, llegaron {a.size}")
    return np.clip(a, 0.0, 1.0)


def cargar_formas(ruta: Optional[Path] = None) -> Dict[str, np.ndarray]:
    """{seña: 6 valores 0..1}. Usa data/formas_mano_lsc.json (sacado de data/sequences); si falta,
    cae en FORMAS_BASE. Las señas que no estén en el archivo conservan su forma base."""
    formas = {k: _como6(v) for k, v in FORMAS_BASE.items()}
    ruta = Path(ruta) if ruta else RUTA_FORMAS
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        for sena, f in datos["formas"].items():
            formas[sena] = _como6([f[n] for n in ORDEN])
        log.info(f"Formas de mano cargadas de {ruta.name}: {', '.join(datos['formas'])}")
    except FileNotFoundError:
        log.warning(f"No existe {ruta}; se usan formas de mano por defecto "
                    "(genera el archivo con: python -m robot.extraer_formas_mano)")
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        log.warning(f"{ruta.name} no es válido ({e}); se usan formas de mano por defecto")
    return formas


# ── Conversiones ─────────────────────────────────────────────────────────

def normalizado_a_rad(v) -> np.ndarray:
    """6 valores 0..1 (1 = abierto) -> ángulos de los 6 actuadores en rad."""
    a = _como6(v)
    return np.array([lo + (1.0 - x) * (hi - lo) for x, (lo, hi) in zip(a, RANGO_RAD)])


def a_angle_set(v) -> list:
    """6 valores 0..1 -> `angle_set` de inspire_hand_ctrl (enteros 0..1000, 1000 = abierto)."""
    return [int(round(float(x) * 1000)) for x in _como6(v)]


# Rangos de las 7 articulaciones Dex3 (mano DERECHA; la izquierda es el espejo con signo cambiado)
# orden Dex3: thumb_0, thumb_1, thumb_2, middle_0, middle_1, index_0, index_1
_DEX3_DER_MIN = np.array([-1.0472, -1.0472, -1.7453, 0.0, 0.0, 0.0, 0.0])
_DEX3_DER_MAX = np.array([1.0472, 0.7243, 0.0, 1.5708, 1.7453, 1.5708, 1.7453])


def a_dex3(v, derecha: bool = True) -> np.ndarray:
    """6 valores Inspire (0..1) -> 7 articulaciones Dex3 (rad) de la mano derecha o izquierda.

    índice y medio: flexión proporcional al cierre. Pulgar: la rotación abre/cierra su separación
    lateral (thumb_0) y la flexión lo dobla (thumb_1/2). Anular y meñique no existen en Dex3.
    """
    meñique, anular, medio, indice, p_flex, p_rot = _como6(v)
    ci, cm = 1.0 - indice, 1.0 - medio
    u = float(np.clip((p_rot - 0.35) / 0.65, 0.0, 1.0))      # 0 = pegado a la mano, 1 = abierto hacia afuera
    f = float(p_flex)                                           # 1 = pulgar estirado, 0 = flexionado
    q = np.array([
        1.0472 * u,                  # thumb_0: separación lateral
        -1.0 * f,                    # thumb_1
        -1.7 + 1.2 * f,              # thumb_2
        1.5708 * cm, 1.7453 * cm,    # middle_0, middle_1
        1.5708 * ci, 1.7453 * ci,    # index_0, index_1
    ])
    q = np.clip(q, _DEX3_DER_MIN, _DEX3_DER_MAX)
    return q if derecha else -q


# ── Salida DDS a las manos Inspire reales ───────────────────────────────

class SalidaInspireDDS:
    """Publica en `rt/inspire_hand/ctrl/{l,r}` (requiere `pip install inspire_sdkpy` y el servicio
    de la mano en el PC2: DFX_inspire_service / inspire_g1). Se crea con `abrir()`."""

    TOPIC_IZQ = "rt/inspire_hand/ctrl/l"
    TOPIC_DER = "rt/inspire_hand/ctrl/r"

    def __init__(self, interfaz: str = "", dominio: int = 0):
        self.interfaz, self.dominio = interfaz, dominio
        self.error: Optional[str] = None
        self._pub_izq = self._pub_der = None
        self._defecto: Optional[Callable] = None

    def abrir(self) -> bool:
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelPublisher
            from inspire_sdkpy import inspire_dds
            import inspire_sdkpy.inspire_hand_defaut as por_defecto
        except ImportError as e:
            self.error = (f"Falta una librería para las manos Inspire ({e}). Instala: pip install inspire_sdkpy "
                          "(y unitree_sdk2py, que ya usa el resto de la app).")
            return False
        try:
            try:
                if self.interfaz:
                    ChannelFactoryInitialize(self.dominio, self.interfaz)
                else:
                    ChannelFactoryInitialize(self.dominio)
            except Exception as e:              # ya inicializado por otro módulo: no es un error
                log.debug(f"ChannelFactoryInitialize: {e}")
            self._pub_izq = ChannelPublisher(self.TOPIC_IZQ, inspire_dds.inspire_hand_ctrl)
            self._pub_izq.Init()
            self._pub_der = ChannelPublisher(self.TOPIC_DER, inspire_dds.inspire_hand_ctrl)
            self._pub_der.Init()
            self._defecto = por_defecto.get_inspire_hand_ctrl
        except Exception as e:
            self.error = f"No se pudo abrir DDS para las manos Inspire: {e}"
            return False
        self.error = None
        return True

    @property
    def abierta(self) -> bool:
        return self._pub_izq is not None

    def enviar(self, izq6: Sequence[float], der6: Sequence[float]):
        if not self.abierta:
            return
        for pub, v in ((self._pub_izq, izq6), (self._pub_der, der6)):
            msg = self._defecto()
            msg.angle_set = a_angle_set(v)
            msg.mode = 0b0001                    # modo ángulo
            pub.Write(msg)
