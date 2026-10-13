"""
conector_sonic.py — LSC UDI -> política SONIC en MuJoCo (Unitree G1, 29 GDL).

Sustituye al visor 3D del navegador en la pestaña Voz/Texto. Las señas se
ejecutan en el mismo entorno del runbook sim2sim (GR00T-WholeBodyControl),
pero SIN Pico 4: este módulo hace de publicador ZMQ y el deploy ONNX lo lee
con `--input-type zmq_manager`.

    App LSC ──ZMQ :5556──▶ deploy.sh (política SONIC, ONNX) ──▶ run_sim_loop.py (MuJoCo)

Qué se envía
------------
Topic `command`  {start, stop, planner}: arranca el control y elige modo planner.
Topic `planner`  {mode=IDLE, movement, facing, speed, height,
                  upper_body_position[17], upper_body_velocity[17]}.
  El planner mantiene al G1 de pie (IDLE) y los 17 GDL de torso y brazos siguen
  las posiciones que enviamos. Las piernas las controla la política.

El deploy considera obsoleto un mensaje `planner` de más de ~100 ms, así que
un hilo lo reenvía continuamente (50 Hz) con la postura actual.

Formato de mensaje (idéntico a gear_sonic_deploy/.../tests/test_zmq_manager.py):
    topic + cabecera JSON rellena con NUL hasta 1280 bytes + datos little-endian.

Orden de los 17 valores (IsaacLab restringido al tren superior, según
policy_parameters.hpp: upper_body_joint_isaaclab_order_in_mujoco_index):
    waist yaw/roll/pitch, hombro pitch L/R, hombro roll L/R, hombro yaw L/R,
    codo L/R, muñeca roll L/R, muñeca pitch L/R, muñeca yaw L/R.
"""

import json
import logging
import math
import threading
import time
from typing import Dict, Optional

import numpy as np

try:
    import zmq
except ImportError:  # se avisa al llamar a conectar()
    zmq = None

from robot.conector_g1 import POSTURAS_G1, ComandoArticular
from robot.mano_inspire import SalidaInspireDDS, a_dex3, cargar_formas

log = logging.getLogger("lsc_bridge.conector_sonic")

# ── Protocolo ZMQ del deploy ─────────────────────────────────────────────
HEADER_SIZE = 1280
TOPIC_COMMAND = b"command"
TOPIC_PLANNER = b"planner"
MODO_IDLE = 0                      # LocomotionMode::IDLE: el G1 se queda de pie

_DTYPES = {"u8": "u1", "i32": "<i4", "f32": "<f4"}

# ── Articulaciones ───────────────────────────────────────────────────────
# Índice MuJoCo de cada uno de los 17 valores de upper_body_position.
UB17_A_MJ = (12, 13, 14, 15, 22, 16, 23, 17, 24, 18, 25, 19, 26, 20, 27, 21, 28)
MJ_A_UB17 = {mj: i for i, mj in enumerate(UB17_A_MJ)}

# Postura de pie por defecto de la política (default_angles en policy_parameters.hpp).
# Es la postura de REPOSO: con ella no hay salto al empezar ni al terminar.
POSTURA_BASE_MJ = {
    12: 0.0, 13: 0.0, 14: 0.0,
    15: 0.2, 16: 0.2, 17: 0.0, 18: 0.6, 19: 0.0, 20: 0.0, 21: 0.0,
    22: 0.2, 23: -0.2, 24: 0.0, 25: 0.6, 26: 0.0, 27: 0.0, 28: 0.0,
}

# Límites articulares (g1_29dof_old_freebase.xml, el modelo de scene_29dof_freebase.xml).
LIMITES_MJ = {
    12: (-2.618, 2.618), 13: (-0.52, 0.52), 14: (-0.52, 0.52),
    15: (-3.0892, 2.6704), 16: (-1.5882, 2.2515), 17: (-2.618, 2.618),
    18: (-1.0472, 2.0944), 19: (-1.97222, 1.97222),
    20: (-1.61443, 1.61443), 21: (-1.61443, 1.61443),
    22: (-3.0892, 2.6704), 23: (-2.2515, 1.5882), 24: (-2.618, 2.618),
    25: (-1.0472, 2.0944), 26: (-1.97222, 1.97222),
    27: (-1.61443, 1.61443), 28: (-1.61443, 1.61443),
}

# Campo de ComandoArticular -> (índice MuJoCo, signo). Mismos signos verificados
# en robot_sim/reproducir_sena.py (el brazo derecho es espejo en roll y yaw).
CAMPOS_A_MJ = {
    "hombro_izq_pitch": (15, +1), "hombro_izq_roll": (16, +1), "codo_izq": (18, +1),
    "muñeca_izq_pitch": (20, +1), "muñeca_izq_yaw": (21, +1),
    "hombro_der_pitch": (22, +1), "hombro_der_roll": (23, -1), "codo_der": (25, +1),
    "muñeca_der_pitch": (27, +1), "muñeca_der_yaw": (28, -1),
}


# ── Señas expresivas para SONIC ──────────────────────────────────────────
# Las posturas de POSTURAS_G1 son estáticas y de poca amplitud (se ajustaron con
# el robot colgado en unitree_mujoco). La política SONIC atenúa los movimientos
# pequeños y rápidos, así que aquí cada seña es una secuencia de fotogramas clave
# con más amplitud, más tiempo y, cuando la seña lo pide, oscilación
# (saludo, sí, no). Valores absolutos por índice MuJoCo (brazo derecho 22..28):
#   22 hombro pitch (− = adelante/arriba)   23 hombro roll (− = separa del cuerpo)
#   24 hombro yaw   25 codo (0 = antebrazo horizontal; + BAJA el antebrazo,
#                            − lo SUBE hacia la cara; rango −1.05..2.09)  26 muñeca roll
#   27 muñeca pitch 28 muñeca yaw
# Cada fotograma: ({índice: rad}, duración_s). El resto de joints conserva su valor.

def _osc(mj: int, centro: float, amp: float, n: int, dur: float = 0.35, extra=None):
    """n oscilaciones (ida y vuelta) de un joint alrededor de `centro`."""
    extra = extra or {}
    kf = []
    for _ in range(n):
        kf.append(({mj: centro + amp, **extra}, dur))
        kf.append(({mj: centro - amp, **extra}, dur))
    return kf


# Poses calculadas con cinemática inversa sobre el MJCF del G1 y comprobadas con
# robot/verificar_sena.py (posición de la mano y del pulgar respecto a la pelvis).
#
# Mano (Inspire RH56DFTP-2R): cada fotograma puede llevar un tercer elemento
#   {"der": <seña o 6 valores 0..1>, "izq": ...}   (1 = abierto, 0 = cerrado; ver robot/mano_inspire.py)
# Si se omite, la mano derecha toma la forma de la propia seña (data/formas_mano_lsc.json, sacada de
# los datos del repositorio) desde el primer fotograma y la izquierda queda en reposo.
_HOLA = {22: -0.52, 23: -0.73, 24: -0.67, 25: -0.58, 26: -0.74, 27: 0.04, 28: -0.21}

SEÑAS_SONIC = {
    # mano abierta junto a la cabeza, palma al frente; saluda moviendo la muñeca de lado a lado
    "Hola": [(_HOLA, 1.1)] + _osc(27, 0.04, 0.55, 3, 0.32) + [({27: 0.04}, 0.3)],
    # mano plana desde la barbilla hacia adelante
    "Gracias": [
        ({22: -0.24, 23: -0.68, 24: 0.83, 25: -0.78, 26: 0.42, 27: -1.06, 28: -0.14}, 1.0),
        ({22: -0.28, 23: -0.04, 24: -0.10, 25: -0.72, 26: 0.12, 27: 1.09, 28: -0.08}, 1.0),
    ],
    # puño frente al pecho que asiente (cabeceo de la muñeca)
    "Si": [({22: -0.08, 23: -0.08, 24: 0.0, 25: -0.58, 26: 0.11, 27: 0.75, 28: -0.08}, 0.9)]
          + _osc(27, 0.75, 0.4, 2, 0.3) + [({27: 0.75}, 0.3)],
    # índice arriba que se mueve de lado a lado
    "No": [({22: -0.71, 23: 0.05, 24: -0.73, 25: 0.07, 26: -0.44, 27: -1.13, 28: 0.01}, 1.0)]
          + _osc(26, -0.44, 0.45, 3, 0.28) + [({26: -0.44}, 0.3)],
    # puño con el pulgar arriba: del pecho hacia adelante
    "Bien": [
        ({22: 0.37, 23: -0.08, 24: 0.41, 25: -0.57, 26: -0.14, 27: -0.10, 28: -0.29}, 0.9),
        ({22: -0.37, 23: -0.11, 24: -0.06, 25: -0.51, 26: 0.11, 27: 0.58, 28: -0.09}, 0.9),
    ],
    # puño que gira hasta apuntar el pulgar hacia abajo (el brazo se abre porque el G1 no tiene pronación)
    "Mal": [
        ({22: -0.05, 23: -0.02, 24: 0.11, 25: -0.97, 26: 0.04, 27: 1.00, 28: -0.16}, 0.9),
        ({22: -0.20, 23: -1.26, 24: 0.54, 25: -0.47, 26: -1.60, 27: 0.01, 28: 0.21}, 1.2),
    ],
    # índice sobre los labios, quieto
    "Silencio": [({22: -0.36, 23: -0.13, 24: 1.05, 25: -0.64, 26: 0.32, 27: -0.93, 28: -0.11}, 1.1),
                 ({}, 1.0)],
}


def comando_a_objetivos_mj(cmd: ComandoArticular) -> Dict[int, float]:
    """ComandoArticular -> {índice MuJoCo: ángulo en rad}.

    Un campo en 0.0 significa "la seña no lo define" y se deja en la postura
    de pie de la política (así, una seña de mano derecha no estira el brazo izquierdo).
    """
    objetivos = {}
    for campo, (mj, signo) in CAMPOS_A_MJ.items():
        valor = getattr(cmd, campo)
        objetivos[mj] = POSTURA_BASE_MJ[mj] if valor == 0.0 else signo * valor
    return objetivos


# ── Empaquetado de mensajes ──────────────────────────────────────────────

def _empaquetar(topic: bytes, campos) -> bytes:
    """campos: lista de (nombre, dtype 'u8'|'i32'|'f32', ndarray-like)."""
    arreglos = [(n, d, np.asarray(a, dtype=_DTYPES[d])) for n, d, a in campos]
    cabecera = {
        "v": 1,
        "endian": "le",
        "count": 1,
        "fields": [{"name": n, "dtype": d, "shape": list(a.shape)} for n, d, a in arreglos],
    }
    cabecera_json = json.dumps(cabecera).encode("utf-8")
    if len(cabecera_json) > HEADER_SIZE:
        raise ValueError(f"Cabecera ZMQ demasiado grande ({len(cabecera_json)} > {HEADER_SIZE})")
    cabecera_bytes = cabecera_json + b"\x00" * (HEADER_SIZE - len(cabecera_json))
    datos = b"".join(a.tobytes() for _, _, a in arreglos)
    return topic + cabecera_bytes + datos


def empaquetar_comando(start: bool, stop: bool, planner: bool) -> bytes:
    return _empaquetar(TOPIC_COMMAND, [
        ("start", "u8", [1 if start else 0]),
        ("stop", "u8", [1 if stop else 0]),
        ("planner", "u8", [1 if planner else 0]),
    ])


def empaquetar_planner(upper_body_position, upper_body_velocity=None,
                       mode: int = MODO_IDLE, left_hand=None, right_hand=None) -> bytes:
    campos = [
        ("mode", "i32", [mode]),
        ("movement", "f32", [0.0, 0.0, 0.0]),
        ("facing", "f32", [1.0, 0.0, 0.0]),
        ("speed", "f32", [-1.0]),
        ("height", "f32", [-1.0]),
        ("upper_body_position", "f32", np.asarray(upper_body_position).reshape(17)),
    ]
    if upper_body_velocity is not None:
        campos.append(("upper_body_velocity", "f32", np.asarray(upper_body_velocity).reshape(17)))
    # manos Dex3 (7 articulaciones c/u); el deploy las aplica a rt/dex3/{left,right}/cmd
    if left_hand is not None:
        campos.append(("left_hand_joints", "f32", np.asarray(left_hand).reshape(7)))
    if right_hand is not None:
        campos.append(("right_hand_joints", "f32", np.asarray(right_hand).reshape(7)))
    return _empaquetar(TOPIC_PLANNER, campos)


# ── Conector ─────────────────────────────────────────────────────────────

class ConectorSonic:
    """Publica señas LSC hacia el deploy de SONIC (MuJoCo, 29 GDL).

    Misma idea que ConectorG1: `enviar_seña("Hola")` bloquea lo que dura el
    movimiento, así que se llama desde un hilo (la GUI ya lo hace).
    """

    def __init__(self, host: str = "*", puerto: int = 5556, hz: float = 50.0,
                 real: bool = False, escala: Optional[float] = None,
                 factor_tiempo: Optional[float] = None,
                 vel_max: Optional[float] = None, manos: str = "auto",
                 interfaz_dds: str = ""):
        """
        real           True cuando el deploy mueve el G1 físico (no MuJoCo).
        escala         0..1: fracción de la amplitud de cada seña respecto a la postura
                       de pie (1.0 = amplitud completa). Por defecto 0.6 en real, 1.0 en sim.
        factor_tiempo  multiplica la duración de cada tramo (>1 = más lento).
                       Por defecto 1.5 en real, 1.0 en sim.
        vel_max        tope de velocidad articular en rad/s (alarga el tramo si hace falta).
                       Por defecto 1.2 en real, sin tope en sim.
        manos          dónde se mueven los dedos (mano Inspire RH56DFTP-2R, 6 valores por mano):
                         "auto"     simulador -> "dex3";  robot real -> "inspire"
                         "dex3"     en el planner de SONIC, como manos Dex3 de 7 articulaciones (MuJoCo)
                         "inspire"  por DDS rt/inspire_hand/ctrl/{l,r} (manos Inspire físicas)
                         "ambas"    las dos salidas;   "ninguna"  no se mueven los dedos
        interfaz_dds   interfaz de red para DDS de las manos Inspire (p. ej. "enp131s0"); vacío = por defecto.
        """
        self.host = host
        self.puerto = puerto
        self.hz = hz
        self.error: Optional[str] = None
        self.real = real
        self.escala = float(escala if escala is not None else (0.6 if real else 1.0))
        self.factor_tiempo = float(factor_tiempo if factor_tiempo is not None
                                   else (1.5 if real else 1.0))
        self.vel_max = vel_max if vel_max is not None else (1.2 if real else None)
        self.detenido = False   # True tras parada_emergencia(): no se acepta más movimiento
        self.aviso_manos: Optional[str] = None   # motivo si las manos Inspire no se pudieron abrir
        if manos == "auto":
            manos = "inspire" if real else "dex3"
        if manos not in ("dex3", "inspire", "ambas", "ninguna"):
            raise ValueError(f"manos debe ser auto|dex3|inspire|ambas|ninguna, no {manos!r}")
        self.manos = manos
        self.interfaz_dds = interfaz_dds
        self.formas = cargar_formas()                 # {seña: 6 valores 0..1}
        self.salida_inspire: Optional[SalidaInspireDDS] = None
        if not 0.0 < self.escala <= 1.0:
            raise ValueError("escala debe estar en (0, 1]")
        if self.factor_tiempo < 1.0 and real:
            raise ValueError("en modo real factor_tiempo no puede ser < 1")

        self._ctx = None
        self._pub = None
        self._lock_pub = threading.Lock()
        self._activo = threading.Event()
        self._hilo: Optional[threading.Thread] = None

        # Trayectoria del tren superior (17 GDL, orden UB17)
        self._lock_tray = threading.Lock()
        base = np.array([POSTURA_BASE_MJ[mj] for mj in UB17_A_MJ], dtype=np.float64)
        self._q_from = base.copy()
        self._q_to = base.copy()
        self._t0 = time.monotonic()
        self._dur = 1.0
        # Manos: 12 valores normalizados (6 derecha + 6 izquierda), 1 = abierta
        reposo_mano = self.formas["REPOSO"]
        self._h_from = np.concatenate([reposo_mano, reposo_mano])
        self._h_to = self._h_from.copy()

    # ── Conexión ─────────────────────────────────────────────────────
    def conectar(self) -> bool:
        """Abre el publicador ZMQ y empieza a transmitir la postura de pie."""
        if zmq is None:
            self.error = "pyzmq no instalado (pip install pyzmq)"
            log.error(self.error)
            return False
        if self.conectado:
            return True
        try:
            self._ctx = zmq.Context()
            self._pub = self._ctx.socket(zmq.PUB)
            self._pub.setsockopt(zmq.LINGER, 0)
            self._pub.bind(f"tcp://{self.host}:{self.puerto}")
        except zmq.ZMQError as e:
            self.error = f"No se pudo abrir el puerto {self.puerto}: {e}"
            log.error(self.error)
            self._liberar()
            return False

        if self.manos in ("inspire", "ambas"):
            self.salida_inspire = SalidaInspireDDS(self.interfaz_dds)
            if self.salida_inspire.abrir():
                log.info("Manos Inspire: publicando en rt/inspire_hand/ctrl/{l,r}")
            else:
                self.aviso_manos = self.salida_inspire.error
                log.warning(self.aviso_manos)
                self.salida_inspire = None

        time.sleep(0.5)  # da tiempo a que el deploy (subscriptor) se enganche
        self.error = None
        self._activo.set()
        self._hilo = threading.Thread(target=self._bucle, name="sonic-pub", daemon=True)
        self._hilo.start()
        log.info(f"Publicador SONIC en tcp://{self.host}:{self.puerto} ({self.hz:.0f} Hz)")
        return True

    def iniciar_control(self, reintentos: int = 3):
        """Pide al deploy que arranque el control en modo planner.

        Es idempotente: si el control ya estaba arrancado no pasa nada. Se
        repite porque ZMQ PUB/SUB puede perder el primer mensaje.
        """
        for _ in range(reintentos):
            self._enviar(empaquetar_comando(start=True, stop=False, planner=True))
            time.sleep(0.3)
        log.info("Comando de inicio enviado al deploy SONIC (modo planner)")

    def parada_emergencia(self):
        """Envía `stop` al deploy (equivale a pulsar O en su terminal)."""
        self.detenido = True
        # Congela la consigna en la postura actual y reenvía `stop` varias veces
        # (ZMQ PUB/SUB puede perder un mensaje).
        with self._lock_tray:
            q, _ = self._calcular(time.monotonic())
            self._h_from = self._h_to = self._manos_en(time.monotonic()).copy()
            self._q_from = self._q_to = q.copy()
            self._dur = 1.0
            self._t0 = time.monotonic()
        for _ in range(5):
            self._enviar(empaquetar_comando(start=False, stop=True, planner=True))
            time.sleep(0.05)
        log.warning("Parada de emergencia enviada al deploy SONIC")

    def cerrar(self):
        """Vuelve a la postura de pie y cierra el publicador."""
        if self.conectado:
            try:
                self.reposo(1.0)
            except Exception:
                pass
        self._activo.clear()
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
        self._liberar()
        log.info("Publicador SONIC cerrado")

    @property
    def conectado(self) -> bool:
        return self._pub is not None and self._activo.is_set()

    # ── Movimiento ───────────────────────────────────────────────────
    def enviar_seña(self, nombre_seña: str, pausa: float = 0.4) -> bool:
        """Ejecuta una seña LSC (POSTURAS_G1). Bloquea duración + pausa."""
        if not self.conectado:
            log.warning("SONIC no conectado — seña descartada")
            return False
        if self.detenido:
            log.warning("Parada de emergencia activa — seña descartada")
            return False
        if nombre_seña == "REPOSO":
            return self.reposo(POSTURAS_G1["REPOSO"].duracion)
        cmd = POSTURAS_G1.get(nombre_seña)
        if cmd is None and nombre_seña not in SEÑAS_SONIC:
            log.warning(f"Seña '{nombre_seña}' sin postura definida — se omite")
            return False
        secuencia = SEÑAS_SONIC.get(nombre_seña)
        forma = self.formas.get(nombre_seña)
        if secuencia is not None:
            dur_total = sum(f[1] for f in secuencia)
            log.info(f"→ SONIC: {nombre_seña} ({len(secuencia)} fotogramas, {dur_total:.1f} s"
                     f"{', con dedos' if forma is not None and self.manos != 'ninguna' else ''})")
            for i, fotograma in enumerate(secuencia):
                objetivos, dur = fotograma[0], fotograma[1]
                manos = fotograma[2] if len(fotograma) > 2 else ({"der": nombre_seña} if i == 0 else None)
                self.ir_a(objetivos, dur, manos=manos)
        else:
            log.info(f"→ SONIC: {nombre_seña} ({cmd.duracion:.1f} s)")
            self.ir_a(comando_a_objetivos_mj(cmd), cmd.duracion,
                      manos={"der": nombre_seña} if forma is not None else None)
        time.sleep(pausa)
        return True

    def reposo(self, duracion: float = 1.5) -> bool:
        """Brazos y torso a la postura de pie de la política."""
        self.ir_a(dict(POSTURA_BASE_MJ), duracion, manos={"der": "REPOSO", "izq": "REPOSO"})
        return True

    def ir_a(self, objetivos_mj: Dict[int, float], duracion: float, esperar: bool = True,
             manos: Optional[dict] = None):
        """Lleva los joints indicados (índice MuJoCo) a su ángulo con perfil coseno.

        Los joints no indicados conservan su consigna. Los índices de piernas
        se ignoran: los controla la política. En modo real se aplican `escala`
        (menos amplitud), `factor_tiempo` (más lento) y `vel_max` (tope rad/s).
        `manos` = {"der": <seña o 6 valores>, "izq": ...} lleva los dedos a esa forma en el mismo tiempo;
        lo que no se indique conserva su forma. No tiene efecto con manos="ninguna".
        """
        if self.detenido:
            return
        with self._lock_tray:
            q_ini, _ = self._calcular(time.monotonic())
            q_fin = q_ini.copy()
            for mj, valor in objetivos_mj.items():
                i = MJ_A_UB17.get(mj)
                if i is None:
                    continue
                if self.escala != 1.0:
                    base = POSTURA_BASE_MJ[mj]
                    valor = base + self.escala * (valor - base)
                lo, hi = LIMITES_MJ[mj]
                if not lo <= valor <= hi:
                    log.warning(f"Joint {mj}: {valor:.2f} rad fuera de límites, se recorta")
                q_fin[i] = min(max(valor, lo), hi)
            dur = max(float(duracion) * self.factor_tiempo, 0.05)
            if self.vel_max:
                # el perfil coseno alcanza pico = (pi/2) * delta / dur
                pico = 0.5 * math.pi * float(np.max(np.abs(q_fin - q_ini))) / dur
                if pico > self.vel_max:
                    dur *= pico / self.vel_max
            h_ini = self._manos_en(time.monotonic()).copy()
            h_fin = h_ini.copy()
            if manos and self.manos != "ninguna":
                for lado, desplazamiento in (("der", 0), ("izq", 6)):
                    forma = manos.get(lado)
                    if forma is None:
                        continue
                    if isinstance(forma, str):
                        if forma not in self.formas:
                            log.warning(f"Forma de mano «{forma}» desconocida; se deja la mano como está")
                            continue
                        forma = self.formas[forma]
                    h_fin[desplazamiento:desplazamiento + 6] = np.clip(np.asarray(forma, float), 0.0, 1.0)
            self._q_from, self._q_to = q_ini, q_fin
            self._h_from, self._h_to = h_ini, h_fin
            self._dur = dur
            self._t0 = time.monotonic()
        if esperar:
            time.sleep(dur)

    def postura_actual(self) -> Dict[int, float]:
        """Consigna actual {índice MuJoCo: rad} del tren superior."""
        with self._lock_tray:
            q, _ = self._calcular(time.monotonic())
        return {mj: float(q[i]) for i, mj in enumerate(UB17_A_MJ)}

    def forma_manos_actual(self) -> Dict[str, list]:
        """Forma actual de los dedos: {"der": 6 valores, "izq": 6 valores} (1 = abierto)."""
        with self._lock_tray:
            h = self._manos_en(time.monotonic())
        return {"der": [round(float(x), 3) for x in h[:6]], "izq": [round(float(x), 3) for x in h[6:]]}

    # ── Interno ──────────────────────────────────────────────────────
    def _manos_en(self, t: float) -> np.ndarray:
        """12 valores de las manos (der 0..5, izq 6..11) en el instante t. Requiere _lock_tray."""
        s = min(max((t - self._t0) / self._dur, 0.0), 1.0)
        return self._h_from + 0.5 * (1.0 - math.cos(math.pi * s)) * (self._h_to - self._h_from)

    def _calcular(self, t: float):
        """(posición, velocidad) de los 17 GDL en el instante t. Requiere _lock_tray."""
        s = min(max((t - self._t0) / self._dur, 0.0), 1.0)
        delta = self._q_to - self._q_from
        q = self._q_from + 0.5 * (1.0 - math.cos(math.pi * s)) * delta
        if s < 1.0:
            dq = (0.5 * math.pi * math.sin(math.pi * s) / self._dur) * delta
        else:
            dq = np.zeros(17)
        return q, dq

    def _enviar(self, mensaje: bytes):
        if self._pub is None:
            return
        with self._lock_pub:
            try:
                self._pub.send(mensaje, zmq.NOBLOCK)
            except zmq.ZMQError as e:
                log.debug(f"ZMQ send: {e}")

    def _bucle(self):
        dt = 1.0 / self.hz
        siguiente = time.perf_counter()
        ciclo = 0
        en_planner = self.manos in ("dex3", "ambas")
        while self._activo.is_set():
            with self._lock_tray:
                q, dq = self._calcular(time.monotonic())
                h = self._manos_en(time.monotonic()).copy()
            if en_planner:
                self._enviar(empaquetar_planner(q, dq, right_hand=a_dex3(h[:6], True),
                                                left_hand=a_dex3(h[6:], False)))
            else:
                self._enviar(empaquetar_planner(q, dq))
            if self.salida_inspire is not None and ciclo % 2 == 0:      # ~25 Hz a las manos físicas
                try:
                    self.salida_inspire.enviar(h[6:], h[:6])
                except Exception as e:
                    log.warning(f"Manos Inspire: {e}")
                    self.salida_inspire = None
            ciclo += 1
            siguiente += dt
            time.sleep(max(0.0, siguiente - time.perf_counter()))

    def _liberar(self):
        with self._lock_pub:
            if self._pub is not None:
                self._pub.close(0)
                self._pub = None
            if self._ctx is not None:
                self._ctx.term()
                self._ctx = None
