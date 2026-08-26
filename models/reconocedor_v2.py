"""
Reconocedor LSC v2 en tiempo real — usa ventana deslizante de frames
para capturar movimiento y posición relativa a la cara.

Reemplaza a models/reconocedor.py para el flujo con secuencias.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from models.extractor_v2 import ExtractorSecuencial, VENTANA_FRAMES

log = logging.getLogger("lsc_bridge.reconocimiento_v2")

try:
    import joblib
    JOBLIB_OK = True
except ImportError:
    JOBLIB_OK = False


@dataclass
class SenaDetectadaV2:
    nombre: str
    traduccion: str
    confianza: float
    timestamp: float = field(default_factory=time.time)
    fps: float = 0.0

    def __str__(self):
        return f"{self.nombre} ({self.confianza*100:.0f}%)"


TRADUCCIONES_LSC = {
    "Hola": "Hola",
    "Gracias": "Gracias",
    "Si": "Sí / Afirmativo",
    "No": "No / Negativo",
    "Bien": "Bien / Estoy bien",
    "Mal": "Mal / No estoy bien",
    "Silencio": "Silencio / Guarda silencio",
}


class ReconocedorLSCv2:
    """
    Reconocedor basado en ventana deslizante de frames.
    Cada VENTANA_FRAMES frames, agrega la secuencia y clasifica.
    """

    def __init__(self, config):
        self.config = config
        self._extractor = ExtractorSecuencial()
        self._clasificador = None
        self._clases = []
        self._activo = False

        self._buffer_frames = deque(maxlen=VENTANA_FRAMES)
        self._t_inicio = time.time()

        self._fps_historia = []
        self._fps_actual = 0.0
        self._ultimo_frame_t = 0.0

        self._ultima_sena_emitida = None
        self._frames_desde_ultima_emision = 0
        self._cooldown_frames = 15  # evitar re-emitir la misma seña muy seguido

        # Suavizado por votación: acumula las últimas N predicciones y emite
        # solo cuando hay consenso. Filtra el ruido de detección frame a frame.
        self._historial_pred = deque(maxlen=7)   # últimas 7 predicciones (seña, conf)
        self._min_votos = 4                       # nº mínimo que deben coincidir
        self._min_conf_voto = 0.55                # confianza mínima para contar un voto

    def iniciar(self) -> bool:
        if not self._extractor.iniciar():
            return False
        self._cargar_clasificador()
        self._activo = True
        log.info("ReconocedorLSCv2 iniciado")
        return True

    def detener(self):
        self._extractor.detener()
        self._activo = False
        log.info("ReconocedorLSCv2 detenido")

    def procesar_frame(self, frame_bgr: np.ndarray) -> Optional[SenaDetectadaV2]:
        if not self._activo:
            return None

        ahora = time.time()
        if self._ultimo_frame_t > 0:
            delta = ahora - self._ultimo_frame_t
            if delta > 0:
                self._fps_historia.append(1.0 / delta)
                if len(self._fps_historia) > 30:
                    self._fps_historia.pop(0)
                self._fps_actual = float(np.mean(self._fps_historia))
        self._ultimo_frame_t = ahora

        timestamp_ms = int((ahora - self._t_inicio) * 1000)
        features_frame = self._extractor.procesar_frame(frame_bgr, timestamp_ms)

        if features_frame is None or not features_frame.manos_presentes:
            self._frames_desde_ultima_emision += 1
            return None

        self._buffer_frames.append(features_frame)
        self._frames_desde_ultima_emision += 1

        if len(self._buffer_frames) < VENTANA_FRAMES:
            return None  # esperar a llenar la ventana

        # Clasificar la ventana actual y acumular en el historial de votación
        vector = self._extractor.agregar_secuencia(list(self._buffer_frames))
        nombre, confianza = self._clasificar(vector)

        # Solo cuenta como voto si supera una confianza mínima (más laxa que el
        # umbral final). Predicciones muy dudosas no ensucian la votación.
        if confianza >= self._min_conf_voto:
            self._historial_pred.append((nombre, confianza))

        # ¿Ya pasó el cooldown desde la última emisión?
        if self._frames_desde_ultima_emision < self._cooldown_frames:
            return None

        # Contar votos por seña en la ventana de historial
        if len(self._historial_pred) < self._min_votos:
            return None

        votos = {}
        conf_acum = {}
        for n, c in self._historial_pred:
            votos[n] = votos.get(n, 0) + 1
            conf_acum[n] = conf_acum.get(n, 0.0) + c

        # Seña más votada
        nombre_ganador = max(votos, key=votos.get)
        n_votos = votos[nombre_ganador]
        conf_media = conf_acum[nombre_ganador] / n_votos

        # Emitir solo si hay consenso suficiente Y la confianza media supera el umbral
        if n_votos < self._min_votos:
            return None
        if conf_media < self.config.umbral_confianza:
            return None

        self._ultima_sena_emitida = nombre_ganador
        self._frames_desde_ultima_emision = 0
        self._historial_pred.clear()  # reiniciar votación tras emitir

        return SenaDetectadaV2(
            nombre=nombre_ganador,
            traduccion=TRADUCCIONES_LSC.get(nombre_ganador, nombre_ganador),
            confianza=conf_media,
            fps=round(self._fps_actual, 1),
        )

    def dibujar_landmarks(self, frame: np.ndarray, sena: Optional[SenaDetectadaV2] = None) -> np.ndarray:
        """Dibuja indicadores básicos (sin landmarks detallados por simplicidad en v2)."""
        import cv2
        h, w = frame.shape[:2]

        # Indicador de buffer
        progreso = len(self._buffer_frames) / VENTANA_FRAMES
        cv2.rectangle(frame, (10, h - 20), (10 + int(200 * progreso), h - 10), (0, 200, 255), -1)
        cv2.rectangle(frame, (10, h - 20), (210, h - 10), (255, 255, 255), 1)

        if sena:
            texto = f"{sena.nombre}  {sena.confianza*100:.0f}%"
            cv2.rectangle(frame, (10, 10), (len(texto)*16 + 20, 58), (0, 0, 0), -1)
            cv2.putText(frame, texto, (15, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 220, 100), 2, cv2.LINE_AA)
            cv2.putText(frame, sena.traduccion, (15, 72),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)

        if self.config.gui_mostrar_fps:
            cv2.putText(frame, f"FPS: {self._fps_actual:.1f}",
                        (w - 120, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (100, 100, 255), 1, cv2.LINE_AA)

        return frame

    def _clasificar(self, vector: np.ndarray) -> tuple:
        if self._clasificador is None:
            return "Desconocida", 0.0
        try:
            proba = self._clasificador.predict_proba([vector])[0]
            idx = int(np.argmax(proba))
            nombre = self._clases[idx] if idx < len(self._clases) else "Desconocida"
            return nombre, float(proba[idx])
        except Exception as e:
            log.debug(f"Error clasificando: {e}")
            return "Desconocida", 0.0

    def _cargar_clasificador(self):
        raiz_proyecto = Path(__file__).resolve().parent.parent
        ruta_config = getattr(self.config, "modelo_pesos_v2", "data/lsc_model_v2.pkl")
        ruta = Path(ruta_config)
        if not ruta.is_absolute():
            ruta = raiz_proyecto / ruta
        if not ruta.exists():
            log.warning(f"Modelo v2 no encontrado en '{ruta}'.")
            return
        if not JOBLIB_OK:
            return
        try:
            self._clasificador = joblib.load(ruta)
            clases_path = ruta.with_suffix(".clases.txt")
            if clases_path.exists():
                self._clases = clases_path.read_text(encoding="utf-8").strip().split("\n")
            log.info(f"Clasificador LSC v2 cargado desde '{ruta}' — clases: {self._clases}")
        except Exception as e:
            log.error(f"Error al cargar clasificador v2: {e}")

    @property
    def fps(self) -> float:
        return round(self._fps_actual, 1)

    @property
    def activo(self) -> bool:
        return self._activo