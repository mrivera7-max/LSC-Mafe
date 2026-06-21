"""
Captura de secuencias de video para señas dinámicas LSC v2.

Cada muestra es una secuencia corta (≈0.7s, ~20 frames) en vez de una sola foto.
Esto permite capturar movimiento (Si) y posición relativa a la cara (Silencio).

Uso:
    python models/capturar_secuencias.py
    python models/capturar_secuencias.py --sena Si

Controles:
    ESPACIO  - iniciar grabación de una secuencia (0.7s automáticos)
    N        - siguiente seña
    R        - reiniciar contador de la seña actual
    Q        - salir
"""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.extractor_v2 import ExtractorSecuencial, VENTANA_FRAMES

SEÑAS = [
    "Hola", "Gracias", "Si", "No", "Bien", "Mal", "Silencio",
]
MUESTRAS_POR_SEÑA = 60  # menos que en captura estática porque cada una es una secuencia completa
DIR_SALIDA = Path("data/sequences")
DURACION_SEGUNDOS = 0.7

CONEXIONES = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),
    (0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),
    (5,9),(9,13),(13,17),
]


def capturar(señas_a_capturar=None):
    señas = señas_a_capturar if señas_a_capturar else SEÑAS

    extractor = ExtractorSecuencial()
    if not extractor.iniciar():
        print("[ERROR] No se pudo iniciar el extractor. Verifica los modelos en data/")
        return

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] No se puede abrir la cámara")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    idx_seña = 0
    conteo = 0
    grabando = False
    frames_grabados = []
    t_inicio_grabacion = 0
    t_inicio_global = time.time()

    print("\n+================================================+")
    print("|  Captura de SECUENCIAS LSC v2 (mano + cara)     |")
    print("+================================================+")
    print("|  ESPACIO -> grabar secuencia (0.7s automatico)  |")
    print("|  N       -> siguiente sena                       |")
    print("|  R       -> reiniciar contador                   |")
    print("|  Q       -> salir                                 |")
    print("+================================================+")
    if not extractor.tiene_face_landmarker:
        print("  [AVISO] Sin FaceLandmarker — Silencio no usara distancia a la boca\n")
    else:
        print()

    while idx_seña < len(señas):
        seña = señas[idx_seña]
        dir_seña = DIR_SALIDA / seña
        dir_seña.mkdir(parents=True, exist_ok=True)

        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)
        frame_disp = frame.copy()
        timestamp_ms = int((time.time() - t_inicio_global) * 1000)

        features_frame = extractor.procesar_frame(frame, timestamp_ms)
        manos_ok = features_frame.manos_presentes if features_frame else False

        # Dibujar overlay
        h, w = frame_disp.shape[:2]
        color_estado = (0, 200, 0) if manos_ok else (0, 100, 255)

        cv2.rectangle(frame_disp, (0, 0), (w, 90), (0, 0, 0), -1)
        cv2.putText(frame_disp, f"Sena: {seña}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.putText(frame_disp, f"Secuencias: {conteo} / {MUESTRAS_POR_SEÑA}", (15, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 1)

        estado_txt = "Manos OK" if manos_ok else "Sin manos"
        cv2.putText(frame_disp, estado_txt, (w - 200, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_estado, 2)

        if features_frame and features_frame.cara_presente:
            cv2.putText(frame_disp, "Cara OK", (w - 200, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 200), 1)

        if grabando:
            transcurrido = time.time() - t_inicio_grabacion
            progreso_grab = min(transcurrido / DURACION_SEGUNDOS, 1.0)
            cv2.putText(frame_disp, f"GRABANDO {progreso_grab*100:.0f}%", (15, 85),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            cv2.circle(frame_disp, (w - 30, 80), 10, (0, 0, 255), -1)

            if features_frame:
                frames_grabados.append(features_frame)

            if transcurrido >= DURACION_SEGUNDOS:
                # Guardar secuencia
                if len(frames_grabados) >= 5:  # mínimo razonable de frames
                    _guardar_secuencia(extractor, frames_grabados, dir_seña, conteo)
                    conteo += 1
                    print(f"  [{seña}] Secuencia {conteo}/{MUESTRAS_POR_SEÑA} "
                          f"({len(frames_grabados)} frames)")
                else:
                    print(f"  [AVISO] Muy pocos frames capturados ({len(frames_grabados)}), descartando")
                grabando = False
                frames_grabados = []

        # Barra de progreso de señas
        prog = int((conteo / MUESTRAS_POR_SEÑA) * w)
        cv2.rectangle(frame_disp, (0, h - 8), (prog, h), (0, 200, 0), -1)

        cv2.imshow("Captura Secuencias LSC v2", frame_disp)

        if conteo >= MUESTRAS_POR_SEÑA:
            print(f"\nOK {seña}: {conteo} secuencias capturadas\n")
            idx_seña += 1
            conteo = 0
            time.sleep(1)
            continue

        tecla = cv2.waitKey(1) & 0xFF

        if tecla == ord("q"):
            print("\nCaptura interrumpida por el usuario")
            break
        elif tecla == ord(" ") and not grabando:
            grabando = True
            t_inicio_grabacion = time.time()
            frames_grabados = []
        elif tecla == ord("n"):
            print(f"\n-> Saltando {seña} ({conteo} secuencias guardadas)")
            idx_seña += 1
            conteo = 0
        elif tecla == ord("r"):
            conteo = 0
            print(f"\nContador reiniciado para {seña}")

    cap.release()
    cv2.destroyAllWindows()
    extractor.detener()
    print(f"\nOK Secuencias guardadas en: {DIR_SALIDA.absolute()}")
    print("  Ahora ejecuta: python models/entrenar_v2.py")


def _guardar_secuencia(extractor: ExtractorSecuencial, frames: list, dir_seña: Path, idx: int):
    """Guarda el vector agregado de la secuencia como .npy."""
    vector = extractor.agregar_secuencia(frames)
    ruta = dir_seña / f"seq_{idx:04d}.npy"
    np.save(ruta, vector)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capturar secuencias LSC v2")
    parser.add_argument("--sena", type=str, default=None,
                        help="Capturar solo una sena especifica")
    args = parser.parse_args()

    if args.sena:
        if args.sena not in SEÑAS:
            print(f"[ERROR] '{args.sena}' no esta en la lista: {SEÑAS}")
            sys.exit(1)
        capturar(señas_a_capturar=[args.sena])
    else:
        capturar()
