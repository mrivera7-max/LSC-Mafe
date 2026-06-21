"""
Herramienta para capturar muestras de senas LSC y construir el dataset.
Compatible con MediaPipe 0.10+ (HandLandmarker).

Uso:
    python models/capturar_muestras.py

Controles durante la captura:
    ESPACIO  - capturar frame actual
    A        - activar/desactivar captura automatica
    N        - pasar a la siguiente sena
    Q        - salir
    R        - reiniciar contador de la sena actual
"""

import cv2
import time
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SEÑAS = [
    "Hola", "Gracias", "Si", "No", "Bien", "Mal", "Silencio",
]
MUESTRAS_POR_SEÑA = 100
DIR_SALIDA = Path("data/signs")
MODELO_TASK = Path("data/hand_landmarker.task")

# Conexiones de la mano para dibujar el esqueleto manualmente
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
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
    except ImportError as e:
        print(f"[ERROR] MediaPipe no instalado correctamente: {e}")
        print("Instala con: pip install mediapipe")
        return

    if not MODELO_TASK.exists():
        print(f"[ERROR] No se encontro el modelo en: {MODELO_TASK}")
        print("Descargalo con:")
        print("  curl -o data/hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")
        return

    BaseOptions = mp_python.BaseOptions
    HandLandmarker = mp_vision.HandLandmarker
    HandLandmarkerOptions = mp_vision.HandLandmarkerOptions
    VisionRunningMode = mp_vision.RunningMode

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODELO_TASK)),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    detector = HandLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] No se puede abrir la camara")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    idx_seña = 0
    conteo = 0
    modo_auto = False
    ultimo_auto = 0
    t_inicio = time.time()

    print("\n+============================================+")
    print("|  Captura de muestras LSC                    |")
    print("+============================================+")
    print("|  ESPACIO -> capturar frame                  |")
    print("|  A       -> captura automatica               |")
    print("|  N       -> siguiente sena                   |")
    print("|  R       -> reiniciar contador                |")
    print("|  Q       -> salir                             |")
    print("+============================================+\n")

    while idx_seña < len(señas):
        seña = señas[idx_seña]
        dir_seña = DIR_SALIDA / seña
        dir_seña.mkdir(parents=True, exist_ok=True)

        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)
        frame_disp = frame.copy()

        # Deteccion con HandLandmarker
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        timestamp_ms = int((time.time() - t_inicio) * 1000)

        try:
            resultado = detector.detect_for_video(mp_image, timestamp_ms)
        except Exception:
            resultado = None

        manos_ok = bool(resultado and resultado.hand_landmarks)

        # Dibujar landmarks manualmente
        h, w = frame_disp.shape[:2]
        if manos_ok:
            for mano_lm in resultado.hand_landmarks:
                puntos = [(int(lm.x * w), int(lm.y * h)) for lm in mano_lm]
                for a, b in CONEXIONES:
                    if a < len(puntos) and b < len(puntos):
                        cv2.line(frame_disp, puntos[a], puntos[b], (0, 200, 0), 2)
                for px, py in puntos:
                    cv2.circle(frame_disp, (px, py), 5, (0, 255, 255), -1)
                    cv2.circle(frame_disp, (px, py), 5, (0, 0, 0), 1)

        # UI overlay
        color_estado = (0, 200, 0) if manos_ok else (0, 100, 255)
        cv2.rectangle(frame_disp, (0, 0), (w, 80), (0, 0, 0), -1)
        cv2.putText(frame_disp, f"Sena: {seña}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.putText(frame_disp, f"Capturas: {conteo} / {MUESTRAS_POR_SEÑA}", (15, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 1)

        estado_txt = "Manos detectadas" if manos_ok else "Sin manos"
        cv2.putText(frame_disp, estado_txt, (w - 260, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_estado, 2)

        if modo_auto:
            cv2.putText(frame_disp, "MODO AUTO ON", (w - 260, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 200), 1)

        # Barra de progreso
        prog = int((conteo / MUESTRAS_POR_SEÑA) * w)
        cv2.rectangle(frame_disp, (0, h - 8), (prog, h), (0, 200, 0), -1)

        cv2.imshow("Captura LSC - LSC Bridge", frame_disp)

        # Captura automatica
        if modo_auto and manos_ok:
            ahora = time.time()
            if ahora - ultimo_auto >= 0.33:
                nombre = f"muestra_{conteo:04d}.jpg"
                cv2.imwrite(str(dir_seña / nombre), frame)
                conteo += 1
                ultimo_auto = ahora
                print(f"  [{seña}] {conteo}/{MUESTRAS_POR_SEÑA}", end="\r")

        if conteo >= MUESTRAS_POR_SEÑA:
            print(f"\nOK {seña}: {conteo} muestras capturadas")
            idx_seña += 1
            conteo = 0
            modo_auto = False
            time.sleep(1)
            continue

        tecla = cv2.waitKey(1) & 0xFF

        if tecla == ord("q"):
            print("\nCaptura interrumpida por el usuario")
            break
        elif tecla == ord(" ") and manos_ok:
            nombre = f"muestra_{conteo:04d}.jpg"
            cv2.imwrite(str(dir_seña / nombre), frame)
            conteo += 1
            print(f"  [{seña}] {conteo}/{MUESTRAS_POR_SEÑA}", end="\r")
        elif tecla == ord("n"):
            print(f"\n-> Saltando {seña} ({conteo} capturas guardadas)")
            idx_seña += 1
            conteo = 0
            modo_auto = False
        elif tecla == ord("r"):
            conteo = 0
            print(f"\nContador reiniciado para {seña}")
        elif tecla == ord("a"):
            modo_auto = not modo_auto
            print(f"\nModo automatico: {'ON' if modo_auto else 'OFF'}")

    cap.release()
    cv2.destroyAllWindows()
    detector.close()
    print(f"\nOK Dataset guardado en: {DIR_SALIDA.absolute()}")
    print("  Ahora ejecuta: python models/entrenar.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capturar muestras LSC")
    parser.add_argument("--sena", type=str, default=None,
                        help="Capturar solo una sena especifica (ej: --sena Sí)")
    args = parser.parse_args()

    if args.sena:
        if args.sena not in SEÑAS:
            print(f"[ERROR] '{args.sena}' no esta en la lista de senas: {SEÑAS}")
            sys.exit(1)
        capturar(señas_a_capturar=[args.sena])
    else:
        capturar()