"""
Módulo de integración con el robot Unitree G1.

Arquitectura:
    SeñaDetectada ──► MapeoSeñas ──► ComandoArticular ──► SDK G1

Referencia SDK:
    https://github.com/unitreerobotics/unitree_sdk2_python
    Puerto por defecto: 8080 en 192.168.123.161

Nota: Sin el robot físico, usar modo_simulacion=True para pruebas.
"""

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("lsc_bridge.robot")


class EstadoRobot(Enum):
    DESCONECTADO = "desconectado"
    CONECTANDO   = "conectando"
    CONECTADO    = "conectado"
    ERROR        = "error"
    EJECUTANDO   = "ejecutando"


@dataclass
class ComandoArticular:
    """Comando de postura para las articulaciones del G1."""
    # Ángulos en radianes para cada articulación
    hombro_izq_pitch: float = 0.0
    hombro_izq_roll:  float = 0.0
    codo_izq:         float = 0.0
    muñeca_izq_pitch: float = 0.0
    muñeca_izq_yaw:   float = 0.0

    hombro_der_pitch: float = 0.0
    hombro_der_roll:  float = 0.0
    codo_der:         float = 0.0
    muñeca_der_pitch: float = 0.0
    muñeca_der_yaw:   float = 0.0

    duracion: float = 1.5       # segundos para ejecutar el movimiento
    velocidad: float = 0.5      # factor de velocidad (0.0 - 1.0)
    seña_origen: str = ""

    def a_grados(self) -> dict:
        """Retorna los ángulos en grados para visualización."""
        return {
            "hombro_izq_pitch": math.degrees(self.hombro_izq_pitch),
            "hombro_izq_roll":  math.degrees(self.hombro_izq_roll),
            "codo_izq":         math.degrees(self.codo_izq),
            "muñeca_izq_pitch": math.degrees(self.muñeca_izq_pitch),
            "hombro_der_pitch": math.degrees(self.hombro_der_pitch),
            "hombro_der_roll":  math.degrees(self.hombro_der_roll),
            "codo_der":         math.degrees(self.codo_der),
            "muñeca_der_pitch": math.degrees(self.muñeca_der_pitch),
        }


# ── Mapeo de señas LSC a posturas del G1 ────────────────────────────────────
# Ángulos en radianes. Positivo = flexión/abducción según convenio Unitree.
# Ajustar estos valores según la calibración física del robot.

# ── Mapeo de senas LSC a posturas del G1 ────────────────────────────────────
# Angulos en radianes. Positivo = flexion/abduccion segun convenio Unitree.
# Ajustar estos valores segun la calibracion fisica del robot.
# IMPORTANTE: estos nombres deben coincidir EXACTAMENTE con las clases
# entrenadas en el modelo (ver utils/config.py -> clases_lsc), sin tildes.

POSTURAS_G1: dict[str, ComandoArticular] = {
    "Hola": ComandoArticular(
        hombro_der_pitch=-0.5, hombro_der_roll=0.3,
        codo_der=1.2, muñeca_der_pitch=0.2,
        duracion=1.0, seña_origen="Hola",
    ),
    "Gracias": ComandoArticular(
        hombro_der_pitch=-0.3, hombro_der_roll=0.1,
        codo_der=0.8, muñeca_der_pitch=-0.3,
        duracion=1.2, seña_origen="Gracias",
    ),
    "Si": ComandoArticular(
        hombro_der_pitch=-0.2, hombro_der_roll=0.1,
        codo_der=0.5, muñeca_der_pitch=0.5,
        duracion=0.6, seña_origen="Si",
    ),
    "No": ComandoArticular(
        hombro_der_pitch=-0.3, hombro_der_roll=0.2,
        codo_der=0.8, muñeca_der_yaw=0.6,
        duracion=0.6, seña_origen="No",
    ),
    "Bien": ComandoArticular(
        hombro_der_pitch=-0.4, hombro_der_roll=0.1,
        codo_der=1.3, muñeca_der_pitch=0.1,
        duracion=0.8, seña_origen="Bien",
    ),
    "Mal": ComandoArticular(
        hombro_der_pitch=-0.3, hombro_der_roll=-0.2,
        codo_der=0.9, muñeca_der_pitch=-0.4, muñeca_der_yaw=-0.3,
        duracion=0.8, seña_origen="Mal",
    ),
    "Silencio": ComandoArticular(
        hombro_der_pitch=-0.7, hombro_der_roll=0.0,
        codo_der=1.6, muñeca_der_pitch=0.0,
        duracion=1.0, seña_origen="Silencio",
    ),
    # Postura de reposo
    "REPOSO": ComandoArticular(duracion=1.0, seña_origen="REPOSO"),
}


class ConectorG1:
    """
    Conector principal al robot Unitree G1.

    Modos de operación:
        - "espejo":     el G1 replica la seña detectada
        - "respuesta":  el G1 responde con una postura predefinida
        - "traduccion": solo traduce, no mueve el robot
    """

    def __init__(self, config):
        self.config = config
        self._estado = EstadoRobot.DESCONECTADO
        self._sdk = None
        self._lock = threading.Lock()
        self._ultimo_comando: Optional[ComandoArticular] = None
        self._cola_comandos: list[ComandoArticular] = []
        self._hilo_ejecucion: Optional[threading.Thread] = None
        self._ejecutando = False
        self._telemetria: dict = {}

        # Callbacks opcionales
        self.on_estado_cambio = None      # fn(EstadoRobot)
        self.on_comando_enviado = None    # fn(ComandoArticular)
        self.on_error = None              # fn(str)

    # ── Conexión ─────────────────────────────────────────────────

    def conectar(self) -> bool:
        """
        Establece conexión con el G1 via SDK.
        Retorna True si la conexión fue exitosa.
        """
        if not self.config.robot_activo:
            log.info("Robot deshabilitado en config (modo demostración)")
            return False

        self._cambiar_estado(EstadoRobot.CONECTANDO)
        log.info(f"Conectando al Unitree G1 en {self.config.robot_ip}:{self.config.robot_puerto}...")

        try:
            self._sdk = self._intentar_importar_sdk()
            if self._sdk is None:
                raise ImportError("SDK no disponible")

            # Inicializar cliente SDK
            self._sdk.init(self.config.robot_ip, self.config.robot_puerto)
            self._sdk.set_control_mode("joint_position")
            self._cambiar_estado(EstadoRobot.CONECTADO)

            # Iniciar telemetría
            self._hilo_ejecucion = threading.Thread(
                target=self._bucle_telemetria, daemon=True
            )
            self._ejecutando = True
            self._hilo_ejecucion.start()

            log.info("✓ Unitree G1 conectado correctamente")
            return True

        except ImportError:
            log.warning(
                "unitree_sdk2py no instalado. Ejecutando en modo simulación.\n"
                "  Para instalar: pip install unitree_sdk2py\n"
                "  Documentación: https://github.com/unitreerobotics/unitree_sdk2_python"
            )
            self._cambiar_estado(EstadoRobot.CONECTADO)
            self._iniciar_simulacion()
            return True

        except ConnectionError as e:
            log.error(f"No se pudo conectar al G1: {e}")
            self._cambiar_estado(EstadoRobot.ERROR)
            if self.on_error:
                self.on_error(str(e))
            return False

        except Exception as e:
            log.error(f"Error inesperado al conectar: {e}")
            self._cambiar_estado(EstadoRobot.ERROR)
            return False

    def desconectar(self):
        """Desconecta el robot y lo lleva a postura de reposo."""
        if self._estado in (EstadoRobot.CONECTADO, EstadoRobot.EJECUTANDO):
            self.enviar_seña("REPOSO")
            time.sleep(1.5)

        self._ejecutando = False
        if self._sdk:
            try:
                self._sdk.close()
            except Exception:
                pass
        self._cambiar_estado(EstadoRobot.DESCONECTADO)
        log.info("Robot desconectado")

    # ── Envío de comandos ─────────────────────────────────────────

    def enviar_seña(self, nombre_seña: str) -> bool:
        """
        Envía el comando motor correspondiente a una seña LSC.

        Args:
            nombre_seña: Nombre de la seña (debe estar en POSTURAS_G1).

        Returns:
            True si el comando fue enviado correctamente.
        """
        if self._estado not in (EstadoRobot.CONECTADO, EstadoRobot.EJECUTANDO):
            log.warning("Robot no conectado — comando descartado")
            return False

        if self.config.robot_modo == "traduccion":
            return True  # Solo traducción, sin mover

        comando = POSTURAS_G1.get(nombre_seña)
        if comando is None:
            log.debug(f"Seña '{nombre_seña}' sin postura definida — usando REPOSO")
            comando = POSTURAS_G1["REPOSO"]

        return self._enviar_comando(comando)

    def _enviar_comando(self, cmd: ComandoArticular) -> bool:
        """Envía un ComandoArticular al SDK."""
        with self._lock:
            self._ultimo_comando = cmd
            self._cambiar_estado(EstadoRobot.EJECUTANDO)

        log.info(
            f"→ G1: {cmd.seña_origen} | "
            f"hombro_der={math.degrees(cmd.hombro_der_pitch):.0f}° "
            f"codo_der={math.degrees(cmd.codo_der):.0f}°"
        )

        try:
            if self._sdk:
                self._sdk.set_joint_positions({
                    "left_shoulder_pitch":  cmd.hombro_izq_pitch,
                    "left_shoulder_roll":   cmd.hombro_izq_roll,
                    "left_elbow":           cmd.codo_izq,
                    "left_wrist_pitch":     cmd.muñeca_izq_pitch,
                    "left_wrist_yaw":       cmd.muñeca_izq_yaw,
                    "right_shoulder_pitch": cmd.hombro_der_pitch,
                    "right_shoulder_roll":  cmd.hombro_der_roll,
                    "right_elbow":          cmd.codo_der,
                    "right_wrist_pitch":    cmd.muñeca_der_pitch,
                    "right_wrist_yaw":      cmd.muñeca_der_yaw,
                }, speed=cmd.velocidad * self.config.robot_velocidad_max)
            else:
                # Modo simulación: mostrar en log
                grados = cmd.a_grados()
                log.debug(f"[SIM] Postura: {grados}")

            if self.on_comando_enviado:
                self.on_comando_enviado(cmd)

            # Esperar duración del movimiento
            time.sleep(cmd.duracion)
            self._cambiar_estado(EstadoRobot.CONECTADO)
            return True

        except Exception as e:
            log.error(f"Error al enviar comando: {e}")
            self._cambiar_estado(EstadoRobot.ERROR)
            return False

    # ── Telemetría ────────────────────────────────────────────────

    def obtener_telemetria(self) -> dict:
        """Retorna los últimos datos de telemetría del robot."""
        return self._telemetria.copy()

    def _bucle_telemetria(self):
        """Hilo que consulta telemetría periódicamente."""
        while self._ejecutando:
            try:
                if self._sdk:
                    datos = self._sdk.get_state()
                    self._telemetria = {
                        "bateria":    datos.get("battery_level", 0),
                        "temperatura": datos.get("temperature", 0),
                        "voltaje":    datos.get("voltage", 0),
                        "corriente":  datos.get("current", 0),
                    }
                else:
                    # Telemetría simulada
                    self._telemetria = {
                        "bateria":    87 - int(time.time() % 10),
                        "temperatura": 35.2,
                        "voltaje":    22.1,
                        "corriente":  2.3,
                    }
            except Exception as e:
                log.debug(f"Error telemetría: {e}")
            time.sleep(2.0)

    def _iniciar_simulacion(self):
        """Inicia simulación de telemetría."""
        self._ejecutando = True
        self._hilo_ejecucion = threading.Thread(
            target=self._bucle_telemetria, daemon=True
        )
        self._hilo_ejecucion.start()
        log.info("Modo simulación del G1 activo")

    # ── Helpers ───────────────────────────────────────────────────

    def _cambiar_estado(self, nuevo: EstadoRobot):
        self._estado = nuevo
        if self.on_estado_cambio:
            self.on_estado_cambio(nuevo)

    def _intentar_importar_sdk(self):
        """Intenta importar el SDK de Unitree."""
        try:
            import unitree_sdk2py as sdk
            return sdk
        except ImportError:
            return None

    @property
    def estado(self) -> EstadoRobot:
        return self._estado

    @property
    def conectado(self) -> bool:
        return self._estado in (EstadoRobot.CONECTADO, EstadoRobot.EJECUTANDO)
