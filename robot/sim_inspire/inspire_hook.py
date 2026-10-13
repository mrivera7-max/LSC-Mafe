"""Manos Inspire RH56DFTP-2R dentro del simulador MuJoCo de SONIC.

Este archivo se copia a ``gear_sonic/utils/mujoco_sim/inspire_hook.py`` (lo hace
``instalar_manos_inspire_sim.py``) y ``base_sim.py`` lo llama en cada paso:

    ManosInspire.crear(sim)   -> al terminar de cargar la escena
    manos.paso(sim)           -> al inicio de cada ``sim_step``

* Las bases de las manos son cuerpos *mocap*: en cada paso se colocan sobre la
  muneca (``*_wrist_yaw_link``) con el montaje guardado en la escena.
* Los 6 valores de cada mano (rad, orden Inspire: menique, anular, medio,
  indice, pulgar-flexion, pulgar-rotacion) llegan por ZeroMQ (SUB a
  ``tcp://127.0.0.1:5601``) desde la aplicacion LSC.  Los 12 joints (con los
  "mimic" del URDF) se escriben cinematicamente: no hay un segundo simulador.
"""
from __future__ import annotations

import json
import os

import numpy as np

try:
    import mujoco
except ImportError:  # pragma: no cover
    mujoco = None

PUERTO = int(os.environ.get("LSC_INSPIRE_PUERTO", "5601"))
ORDEN = ("pinky_proximal", "ring_proximal", "middle_proximal", "index_proximal",
         "thumb_proximal_pitch", "thumb_proximal_yaw")
# joint dependiente -> (joint maestro, multiplicador)  (del URDF)
MIMIC = {"pinky_intermediate": ("pinky_proximal", 1.0), "ring_intermediate": ("ring_proximal", 1.0),
         "middle_intermediate": ("middle_proximal", 1.0), "index_intermediate": ("index_proximal", 1.0),
         "thumb_intermediate": ("thumb_proximal_pitch", 1.6), "thumb_distal": ("thumb_proximal_pitch", 2.4)}
SUAVIZADO = 0.35   # 0..1, fraccion que se acerca al objetivo en cada paso


def _q_mul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1*w2 - x1*x2 - y1*y2 - z1*z2, w1*x2 + x1*w2 + y1*z2 - z1*y2,
                     w1*y2 - x1*z2 + y1*w2 + z1*x2, w1*z2 + x1*y2 - y1*x2 + z1*w2])


class _Mano:
    def __init__(self, m, lado):
        L = lado.upper()
        self.lado = lado
        nom = lambda n: f"insp_{lado}_{L}_{n}_joint"
        jid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, nom(n))
        self.act = []          # (adr qpos, lim_inf, lim_sup) de los 6 maestros
        for n in ORDEN:
            j = jid(n)
            if j < 0:
                raise RuntimeError(f"falta el joint {nom(n)} en la escena")
            self.act.append(j)
        self.dep = []          # (adr qpos dependiente, indice maestro, mult)
        for n, (mae, mult) in MIMIC.items():
            self.dep.append((jid(n), ORDEN.index(mae), mult))
        self.rango = np.array([m.jnt_range[j] for j in self.act])
        self.q = np.zeros(6)
        self.obj = np.zeros(6)
        self.base = m.body(f"insp_{lado}_base")
        self.mocap = self.base.mocapid[0]
        self.muneca = m.body(f"{'left' if lado == 'l' else 'right'}_wrist_yaw_link").id
        mont = m.numeric(f"insp_{lado}_montaje").data
        self.p_mont = np.array(mont[:3])
        self.q_mont = np.array(mont[3:7])
        self.m = m

    def escribir(self, d):
        m = self.m
        self.q += SUAVIZADO * (np.clip(self.obj, self.rango[:, 0], self.rango[:, 1]) - self.q)
        for i, j in enumerate(self.act):
            d.qpos[m.jnt_qposadr[j]] = self.q[i]
            d.qvel[m.jnt_dofadr[j]] = 0.0
        for j, mae, mult in self.dep:
            lo, hi = m.jnt_range[j]
            d.qpos[m.jnt_qposadr[j]] = float(np.clip(self.q[mae] * mult, lo, hi))
            d.qvel[m.jnt_dofadr[j]] = 0.0

    def seguir_muneca(self, d):
        p = d.xpos[self.muneca]
        q = d.xquat[self.muneca]
        if np.linalg.norm(q) < 0.5:      # aun no se ha calculado la cinematica
            return
        Rw = np.zeros(9)
        mujoco.mju_quat2Mat(Rw, q)
        d.mocap_pos[self.mocap] = p + Rw.reshape(3, 3) @ self.p_mont
        qm = _q_mul(q, self.q_mont)
        d.mocap_quat[self.mocap] = qm / np.linalg.norm(qm)


class ManosInspire:
    @classmethod
    def crear(cls, sim):
        """Devuelve la instancia o None si la escena no trae manos Inspire."""
        try:
            m = sim.mj_model
            if mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "insp_r_base") < 0:
                return None
            o = cls(sim)
            print("[inspire] Manos Inspire RH56DFTP-2R activas en el simulador "
                  f"(escuchando tcp://127.0.0.1:{PUERTO})", flush=True)
            return o
        except Exception as e:  # nunca romper el simulador por las manos
            print(f"[inspire] manos desactivadas: {e}", flush=True)
            return None

    def __init__(self, sim):
        self.manos = {"l": _Mano(sim.mj_model, "l"), "r": _Mano(sim.mj_model, "r")}
        self.sock = None
        self._conectar()
        self._n = 0

    def _conectar(self):
        try:
            import zmq
            ctx = zmq.Context.instance()
            s = ctx.socket(zmq.SUB)
            s.setsockopt(zmq.CONFLATE, 1)
            s.setsockopt_string(zmq.SUBSCRIBE, "")
            s.connect(f"tcp://127.0.0.1:{PUERTO}")
            self.sock = s
        except Exception as e:
            print(f"[inspire] sin canal ZMQ ({e}); las manos quedaran abiertas", flush=True)

    def _leer(self):
        if self.sock is None:
            return
        import zmq
        try:
            msg = self.sock.recv(zmq.NOBLOCK)
        except zmq.Again:
            return
        try:
            j = json.loads(msg.decode())
            for lado in ("l", "r"):
                v = j.get(lado)
                if v is not None and len(v) == 6:
                    self.manos[lado].obj = np.asarray(v, dtype=float)
        except Exception:
            pass

    def paso(self, sim):
        d = sim.mj_data
        self._n += 1
        if self._n % 4 == 0:     # el simulador corre a 200 Hz; basta leer a 50 Hz
            self._leer()
        for h in self.manos.values():
            h.escribir(d)
            h.seguir_muneca(d)
