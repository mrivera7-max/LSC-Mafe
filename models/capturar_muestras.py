"""
Herramienta para capturar muestras de señas LSC y construir el dataset.

Uso:
    python models/capturar_muestras.py

Controles durante la captura:
    ESPACIO  — capturar frame actual
    N        — pasar a la siguiente seña
    Q        — salir
    R        — reiniciar contador de la seña actual
"""

import cv2
import os
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SEÑAS = [
    "Hola", "Gracias", "Ayuda", "Sí", "No", "Por favor",
    "¿Cómo estás?", "Bien", "Mal", "Buenos días",
    "Espera", "Ven", "Para", "Entiendo", "No entiendo",
    "Agua", "Comida", "Baño", "Emergencia",
]
MUESTRAS_POR_SEÑA = 100
DIR_SALIDA = Path("data/signs")


def capturar():
    try:
        import mediapipe as mp
        mp_hands = mp.solutions.hands
        mp_draw = mp.solutions.drawing_utils
        detector = mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.5,
        )
    except ImportError:
        print("[ERROR] MediaPipe no instalado: pip install mediapipe")
        return

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] No se puede abrir la cámara")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    idx_seña = 0
    conteo = 0
    capturando = False

    print("\n╔══════════════════════════════════════════╗")
    print("║  Captura de muestras LSC                ║")
    print("╠══════════════════════════════════════════╣")
    print("║  ESPACIO → capturar frame               ║")
    print("║  A       → captura automática           ║")
    print("║  N       → siguiente seña               ║")
    print("║  R       → reiniciar contador           ║")
    print("║  Q       → salir                        ║")
    print("╚══════════════════════════════════════════╝\n")

    modo_auto = False
    ultimo_auto = 0

    while idx_seña < len(SEÑAS):
        seña = SEÑAS[idx_seña]
        dir_seña = DIR_SALIDA / seña
        dir_seña.mkdir(parents=True, exist_ok=True)

        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)
        frame_disp = frame.copy()

        # Detectar manos para visualización
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resultado = detector.process(rgb)
        manos_ok = bool(resultado.multi_hand_landmarks)

        if resultado.multi_hand_landmarks:
            for mano_lm in resultado.multi_hand_landmarks:
                mp_draw.draw_landmarks(
                    frame_disp, mano_lm, mp_hands.HAND_CONNECTIONS
                )

        # UI overlay
        h, w = frame_disp.shape[:2]
        color_estado = (0, 200, 0) if manos_ok else (0, 100, 255)

        cv2.rectangle(frame_disp, (0, 0), (w, 80), (0, 0, 0), -1)
        cv2.putText(frame_disp, f"Seña: {seña}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.putText(frame_disp, f"Capturas: {conteo} / {MUESTRAS_POR_SEÑA}", (15, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 1)

        estado_txt = "Manos detectadas" if manos_ok else "Sin manos"
        cv2.putText(frame_disp, estado_txt, (w - 240, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_estado, 2)

        if modo_auto:
            cv2.putText(frame_disp, "MODO AUTO ON", (w - 240, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 200), 1)

        # Barra de progreso
        prog = int((conteo / MUESTRAS_POR_SEÑA) * w)
        cv2.rectangle(frame_disp, (0, h - 8), (prog, h), (0, 200, 0), -1)

        cv2.imshow("Captura LSC — LSC Bridge", frame_disp)

        # Captura automática
        if modo_auto and manos_ok:
            ahora = time.time()
            if ahora - ultimo_auto >= 0.3:  # 3 fps de captura
                nombre = f"muestra_{conteo:04d}.jpg"
                cv2.imwrite(str(dir_seña / nombre), frame)
                conteo += 1
                ultimo_auto = ahora
                print(f"  [{seña}] {conteo}/{MUESTRAS_POR_SEÑA}", end="\r")

        if conteo >= MUESTRAS_POR_SEÑA:
            print(f"\n✓ {seña}: {conteo} muestras capturadas")
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
            print(f"\n→ Saltando {seña} ({conteo} capturas guardadas)")
            idx_seña += 1
            conteo = 0
            modo_auto = False
        elif tecla == ord("r"):
            conteo = 0
            print(f"\nContador reiniciado para {seña}")
        elif tecla == ord("a"):
            modo_auto = not modo_auto
            print(f"\nModo automático: {'ON' if modo_auto else 'OFF'}")

    cap.release()
    cv2.destroyAllWindows()
    detector.close()
    print(f"\n✓ Dataset guardado en: {DIR_SALIDA.absolute()}")
    print("  Ahora ejecuta: python models/entrenar.py")


if __name__ == "__main__":
    capturar()
