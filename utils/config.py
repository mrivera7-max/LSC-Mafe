"""
Gestión de configuración del sistema LSC Bridge.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("lsc_bridge.config")


class Configuracion:
    """Configuración central del sistema."""

    def __init__(self, ruta: str = "config.json"):
        # Cámara
        self.camara_idx = 0
        self.camara_ancho = 1280
        self.camara_alto = 720
        self.camara_fps = 30

        # Modelo
        self.modelo_tipo = "mediapipe"
        self.umbral_confianza = 0.75
        self.num_manos = 2
        self.modelo_pesos = "data/lsc_model.pkl"
        # IMPORTANTE: este orden debe coincidir con el orden alfabetico
        # de las carpetas en data/signs/ (asi se entreno el modelo)
        self.clases_lsc = [
            "Bien", "Gracias", "Hola", "Mal", "No", "Silencio", "Si",
        ]

        # Robot Unitree G1
        self.robot_activo = True
        self.robot_ip = "192.168.123.161"
        self.robot_puerto = 8080
        self.robot_modo = "espejo"
        self.robot_timeout = 5.0
        self.robot_velocidad_max = 0.5
        self.joints_brazo_izq = [
            "left_shoulder_pitch", "left_shoulder_roll",
            "left_elbow", "left_wrist_pitch", "left_wrist_yaw",
        ]
        self.joints_brazo_der = [
            "right_shoulder_pitch", "right_shoulder_roll",
            "right_elbow", "right_wrist_pitch", "right_wrist_yaw",
        ]

        # GUI
        self.gui_tema = "claro"
        self.gui_idioma = "es"
        self.gui_mostrar_landmarks = True
        self.gui_mostrar_fps = True

        # General
        self.log_nivel = "INFO"
        self.historial_max = 50
        self.guardar_capturas = False
        self.directorio_capturas = "data/capturas"

        self._ruta_archivo = ruta
        self._cargar(ruta)

    def _cargar(self, ruta: str):
        path = Path(ruta)
        if not path.exists():
            log.info(f"Config no encontrada en {ruta}, usando valores por defecto")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                datos = json.load(f)
            for clave, valor in datos.items():
                if hasattr(self, clave) and not clave.startswith("_"):
                    setattr(self, clave, valor)
            log.info(f"Configuracion cargada desde {ruta}")
        except json.JSONDecodeError as e:
            log.error(f"Error al leer config.json: {e}")

    def guardar(self):
        datos = {
            k: v for k, v in self.__dict__.items()
            if not k.startswith("_")
        }
        with open(self._ruta_archivo, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
        log.info(f"Configuracion guardada en {self._ruta_archivo}")