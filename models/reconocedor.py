"""
Motor de reconocimiento LSC — compatible con MediaPipe 0.10+
Usa HandLandmarker (API Tasks).
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("lsc_bridge.reconocimiento")

MEDIAPIPE_OK = False
try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.tasks.python.components.containers import landmark as mp_landmark
    MEDIAPIPE_OK = True
except ImportError as e:
    log.warning(f"MediaPipe no disponible: {e}")

try:
    import joblib
    JOBLIB_OK = True
except ImportError:
    JOBLIB_OK = False


@dataclass
class Landmark:
    x: float
    y: float
    z: float


@dataclass
class SenaDetectada:
    nombre: str
    traduccion: str
    confianza: float
    landmarks_izq: list = field(default_factory=list)
    landmarks_der: list = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    fps: float = 0.0

    def __str__(self):
        return f"{self.nombre} ({self.confianza*100:.0f}%)"


TRADUCCIONES_LSC = {
    "Hola":          "Hola",
    "Gracias":       "Gracias",
    "Ayuda":         "Ayuda / Auxilio",
    "Sí":            "Sí / Afirmativo",
    "Si":            "Sí / Afirmativo",
    "No":            "No / Negativo",
    "Por favor":     "Por favor",
    "Como estas":    "¿Cómo estás?",
    "Bien":          "Bien / Estoy bien",
    "Mal":           "Mal / No estoy bien",
    "Silencio":      "Silencio / Guarda silencio",
    "Buenos dias":   "Buenos días",
    "Espera":        "Espera / Un momento",
    "Entiendo":      "Entiendo",
    "No entiendo":   "No entiendo",
    "Agua":          "Agua",
    "Comida":        "Comida / Tengo hambre",
    "Emergencia":    "Emergencia",
    "Paz":           "Paz / Victoria",
    "Te quiero":     "Te quiero",
}


# Ruta por defecto al modelo .task
MODELO_TASK_DEFAULT = "data/hand_landmarker.task"


class ReconocedorLSC:

    def __init__(self, config):
        self.config = config
        self._activo = False
        self._detector = None
        self._clasificador = None
        self._clases = config.clases_lsc
        self._ultimo_frame = 0.0
        self._fps_actual = 0.0
        self._fps_historia = []
        self._ventana_pred = []
        self._tam_ventana = 3
        # Guardamos los ultimos landmarks para dibujar
        self._lm_der = []
        self._lm_izq = []
        self._conexiones = None

    # ── Ciclo de vida ────────────────────────────────────────────

    def iniciar(self) -> bool:
        if not MEDIAPIPE_OK:
            log.error("MediaPipe no disponible")
            return False

        # Buscar el modelo .task
        ruta_task = Path(MODELO_TASK_DEFAULT)
        if not ruta_task.exists():
            # Buscar en otras ubicaciones comunes
            alternativas = [
                Path("hand_landmarker.task"),
                Path("data") / "hand_landmarker.task",
            ]
            for alt in alternativas:
                if alt.exists():
                    ruta_task = alt
                    break
            else:
                log.error(
                    f"No se encontro el modelo en '{MODELO_TASK_DEFAULT}'.\n"
                    "Descargalo desde:\n"
                    "https://storage.googleapis.com/mediapipe-models/"
                    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task\n"
                    "y guardalo en la carpeta 'data/' del proyecto."
                )
                return False

        log.info(f"Cargando modelo desde: {ruta_task}")

        try:
            BaseOptions = mp_python.BaseOptions
            HandLandmarker = mp_vision.HandLandmarker
            HandLandmarkerOptions = mp_vision.HandLandmarkerOptions
            VisionRunningMode = mp_vision.RunningMode

            options = HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(ruta_task)),
                running_mode=VisionRunningMode.VIDEO,
                num_hands=self.config.num_manos,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._detector = HandLandmarker.create_from_options(options)
            log.info("MediaPipe HandLandmarker (0.10+) iniciado correctamente")
            self._cargar_clasificador()
            self._activo = True
            return True

        except Exception as e:
            log.error(f"Error iniciando HandLandmarker: {e}")
            return False

    def detener(self):
        if self._detector:
            try:
                self._detector.close()
            except Exception:
                pass
        self._activo = False
        log.info("Reconocedor LSC detenido")

    # ── Procesamiento ─────────────────────────────────────────────

    def procesar_frame(self, frame_bgr: np.ndarray) -> Optional[SenaDetectada]:
        if not self._activo or self._detector is None:
            return None

        # FPS
        ahora = time.time()
        if self._ultimo_frame > 0:
            delta = ahora - self._ultimo_frame
            fps_inst = 1.0 / delta if delta > 0 else 0
            self._fps_historia.append(fps_inst)
            if len(self._fps_historia) > 30:
                self._fps_historia.pop(0)
            self._fps_actual = float(np.mean(self._fps_historia))
        self._ultimo_frame = ahora

        try:
            # Convertir frame a MediaPipe Image
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            timestamp_ms = int(ahora * 1000)

            resultado = self._detector.detect_for_video(mp_image, timestamp_ms)
        except Exception as e:
            log.debug(f"Error en deteccion: {e}")
            return None

        # Limpiar landmarks guardados
        self._lm_der = []
        self._lm_izq = []

        if not resultado.hand_landmarks:
            return None

        landmarks_izq, landmarks_der = [], []

        for idx, mano_lm in enumerate(resultado.hand_landmarks):
            # handedness: "Left" o "Right" (desde la perspectiva de la camara)
            if idx < len(resultado.handedness):
                lado = resultado.handedness[idx][0].category_name
            else:
                lado = "Right"

            puntos = [Landmark(lm.x, lm.y, lm.z) for lm in mano_lm]

            if lado == "Left":
                landmarks_izq = puntos
                self._lm_izq = [(lm.x, lm.y) for lm in mano_lm]
            else:
                landmarks_der = puntos
                self._lm_der = [(lm.x, lm.y) for lm in mano_lm]

        return self._clasificar_y_suavizar(landmarks_izq, landmarks_der)

    def dibujar_landmarks(self, frame: np.ndarray, sena: Optional[SenaDetectada] = None) -> np.ndarray:
        h, w = frame.shape[:2]

        # Dibujar conexiones de la mano manualmente
        CONEXIONES = [
            (0,1),(1,2),(2,3),(3,4),         # pulgar
            (0,5),(5,6),(6,7),(7,8),           # indice
            (0,9),(9,10),(10,11),(11,12),       # corazon
            (0,13),(13,14),(14,15),(15,16),     # anular
            (0,17),(17,18),(18,19),(19,20),     # menique
            (5,9),(9,13),(13,17),              # palma
        ]

        for lm_lista, color_punto, color_linea in [
            (self._lm_der, (0, 200, 255), (0, 150, 200)),
            (self._lm_izq, (255, 100, 0), (200, 80, 0)),
        ]:
            if not lm_lista:
                continue
            # Dibujar lineas
            for a, b in CONEXIONES:
                if a < len(lm_lista) and b < len(lm_lista):
                    x1, y1 = int(lm_lista[a][0] * w), int(lm_lista[a][1] * h)
                    x2, y2 = int(lm_lista[b][0] * w), int(lm_lista[b][1] * h)
                    cv2.line(frame, (x1, y1), (x2, y2), color_linea, 2, cv2.LINE_AA)
            # Dibujar puntos
            for x_n, y_n in lm_lista:
                cx, cy = int(x_n * w), int(y_n * h)
                cv2.circle(frame, (cx, cy), 5, color_punto, -1, cv2.LINE_AA)
                cv2.circle(frame, (cx, cy), 5, (255, 255, 255), 1, cv2.LINE_AA)

        # Etiqueta de sena
        if sena:
            texto = f"{sena.nombre}  {sena.confianza*100:.0f}%"
            ancho_rect = len(texto) * 16 + 20
            cv2.rectangle(frame, (10, 10), (ancho_rect, 58), (0, 0, 0), -1)
            cv2.putText(frame, texto, (15, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 220, 100), 2, cv2.LINE_AA)
            cv2.putText(frame, sena.traduccion, (15, 72),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)

        # FPS
        if self.config.gui_mostrar_fps:
            cv2.putText(frame, f"FPS: {self._fps_actual:.1f}",
                        (w - 120, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (100, 100, 255), 1, cv2.LINE_AA)

        return frame

    # ── Features y clasificacion ──────────────────────────────────

    def _clasificar_y_suavizar(self, landmarks_izq, landmarks_der):
        features = self._extraer_features(landmarks_izq, landmarks_der)
        nombre, confianza = self._clasificar(features)

        log.debug(f"Prediccion: {nombre} ({confianza*100:.1f}%) | umbral: {self.config.umbral_confianza*100:.0f}%")

        if confianza < self.config.umbral_confianza:
            return None

        self._ventana_pred.append(nombre)
        if len(self._ventana_pred) > self._tam_ventana:
            self._ventana_pred.pop(0)

        if len(self._ventana_pred) < self._tam_ventana:
            return None

        if not all(p == nombre for p in self._ventana_pred):
            return None

        return SenaDetectada(
            nombre=nombre,
            traduccion=TRADUCCIONES_LSC.get(nombre, nombre),
            confianza=confianza,
            landmarks_izq=landmarks_izq,
            landmarks_der=landmarks_der,
            fps=round(self._fps_actual, 1),
        )

    def _extraer_features(self, landmarks_izq, landmarks_der) -> np.ndarray:
        def normalizar(puntos):
            if not puntos:
                return np.zeros(63)
            arr = np.array([[p.x, p.y, p.z] for p in puntos])
            arr -= arr[0]
            escala = np.linalg.norm(arr[9]) + 1e-7
            arr /= escala
            return arr.flatten()
        return np.concatenate([normalizar(landmarks_der), normalizar(landmarks_izq)])

    def _clasificar(self, features: np.ndarray) -> tuple:
        if self._clasificador is not None:
            try:
                proba = self._clasificador.predict_proba([features])[0]
                idx = int(np.argmax(proba))
                return self._clases[idx] if idx < len(self._clases) else "Desconocida", float(proba[idx])
            except Exception as e:
                log.debug(f"Error clasificacion: {e}")
        return self._clasificar_heuristico(features)

    def _clasificar_heuristico(self, features: np.ndarray) -> tuple:
        mano = features[:63].reshape(21, 3)
        tips = mano[[4, 8, 12, 16, 20]]
        mcps = mano[[2, 5,  9, 13, 17]]
        ext  = tips[:, 1] < mcps[:, 1]
        n    = int(np.sum(ext))

        if n == 0:                                        return "No",       0.80
        if n == 5:                                        return "Hola",     0.82
        if ext[1] and not any(ext[[0,2,3,4]]):            return "Si",       0.78
        if ext[1] and ext[2] and not any(ext[[0,3,4]]):   return "Paz",      0.75
        if ext[0] and ext[4] and not any(ext[[1,2,3]]):   return "Te quiero",0.77
        if ext[0] and not any(ext[[1,2,3,4]]):            return "Bien",     0.72
        return "Gracias", 0.70

    def _cargar_clasificador(self):
        ruta = Path(self.config.modelo_pesos)
        if not ruta.exists():
            log.warning(f"Modelo ML no encontrado en '{ruta}'. Usando heuristicas.")
            return
        if not JOBLIB_OK:
            return
        try:
            self._clasificador = joblib.load(ruta)
            log.info(f"Clasificador LSC cargado desde '{ruta}'")
        except Exception as e:
            log.error(f"Error al cargar clasificador: {e}")

    @property
    def fps(self) -> float:
        return round(self._fps_actual, 1)

    @property
    def activo(self) -> bool:
        return self._activo