"""
Generador de secuencias de posturas para el robot G1 a partir de una
secuencia de senas LSC.

Reutiliza el diccionario POSTURAS_G1 ya definido en robot/conector_g1.py
del proyecto original (reconocimiento), para que ambos proyectos compartan
la misma definicion de "como se ve" cada sena en el robot.
"""

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger("voz_a_sena.generador_posturas")

try:
    from robot.conector_g1 import POSTURAS_G1, ComandoArticular
    POSTURAS_OK = True
except ImportError as e:
    log.error(f"No se pudo importar POSTURAS_G1 desde robot/conector_g1.py: {e}")
    POSTURAS_OK = False
    POSTURAS_G1 = {}


@dataclass
class PasoSecuencia:
    """Un paso dentro de la secuencia completa de senas a ejecutar."""
    nombre_sena: str
    comando: "object"  # ComandoArticular
    indice: int
    total: int


@dataclass
class SecuenciaCompleta:
    texto_original: str
    pasos: List[PasoSecuencia] = field(default_factory=list)

    @property
    def duracion_total_segundos(self) -> float:
        return sum(p.comando.duracion for p in self.pasos)

    def __len__(self):
        return len(self.pasos)


class GeneradorPosturas:
    """
    Convierte una lista de nombres de senas en una secuencia ejecutable
    de comandos articulares, lista para enviar al robot G1 o al visor 3D.
    """

    def __init__(self):
        if not POSTURAS_OK:
            log.warning(
                "POSTURAS_G1 no disponible. Verifica que robot/conector_g1.py "
                "exista y sea importable."
            )

    def generar(self, secuencia_señas: List[str], texto_original: str = "") -> SecuenciaCompleta:
        """
        Genera la secuencia completa de comandos articulares.

        Args:
            secuencia_señas: Lista de nombres de senas, ej. ["Hola", "Gracias"]
            texto_original: El texto que origino esta secuencia (para registro).

        Returns:
            SecuenciaCompleta con un PasoSecuencia por cada sena.
        """
        pasos = []
        total = len(secuencia_señas)

        for idx, nombre_sena in enumerate(secuencia_señas):
            comando = POSTURAS_G1.get(nombre_sena)
            if comando is None:
                log.warning(f"No hay postura definida para '{nombre_sena}', se omite")
                continue
            pasos.append(PasoSecuencia(
                nombre_sena=nombre_sena,
                comando=comando,
                indice=idx,
                total=total,
            ))

        secuencia = SecuenciaCompleta(texto_original=texto_original, pasos=pasos)
        log.info(
            f"Secuencia generada: {len(pasos)} pasos, "
            f"duracion total {secuencia.duracion_total_segundos:.1f}s"
        )
        return secuencia

    def a_dict_serializable(self, secuencia: SecuenciaCompleta) -> dict:
        """
        Convierte la secuencia a un diccionario JSON-serializable,
        listo para enviar por WebSocket al visor 3D.
        """
        return {
            "texto_original": secuencia.texto_original,
            "duracion_total": secuencia.duracion_total_segundos,
            "pasos": [
                {
                    "sena": p.nombre_sena,
                    "indice": p.indice,
                    "total": p.total,
                    "duracion": p.comando.duracion,
                    "articulaciones": {
                        "hombro_izq_pitch": p.comando.hombro_izq_pitch,
                        "hombro_izq_roll": p.comando.hombro_izq_roll,
                        "codo_izq": p.comando.codo_izq,
                        "muñeca_izq_pitch": p.comando.muñeca_izq_pitch,
                        "muñeca_izq_yaw": p.comando.muñeca_izq_yaw,
                        "hombro_der_pitch": p.comando.hombro_der_pitch,
                        "hombro_der_roll": p.comando.hombro_der_roll,
                        "codo_der": p.comando.codo_der,
                        "muñeca_der_pitch": p.comando.muñeca_der_pitch,
                        "muñeca_der_yaw": p.comando.muñeca_der_yaw,
                    },
                }
                for p in secuencia.pasos
            ],
        }
