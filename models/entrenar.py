"""
Entrenamiento del clasificador de Lengua de Senas Colombiana.
Compatible con MediaPipe 0.10+ (HandLandmarker).

Uso:
    python models/entrenar.py --datos data/signs --salida data/lsc_model.pkl

Estructura esperada de --datos:
    data/signs/
        Hola/
            muestra_0001.jpg
            muestra_0002.jpg
        Gracias/
            muestra_0001.jpg
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import configurar_logger

log = logging.getLogger("lsc_bridge.entrenamiento")

MODELO_TASK = Path("data/hand_landmarker.task")

_detector_cache = None


def parsear_args():
    p = argparse.ArgumentParser(description="Entrenar clasificador LSC")
    p.add_argument("--datos", default="data/signs", help="Directorio con imagenes por clase")
    p.add_argument("--salida", default="data/lsc_model.pkl", help="Ruta de salida del modelo")
    p.add_argument("--test-size", type=float, default=0.2, help="Proporcion de test (default: 0.2)")
    p.add_argument("--modelo", choices=["mlp", "rf", "svm"], default="mlp",
                   help="Tipo de clasificador (default: mlp)")
    return p.parse_args()


def _obtener_detector():
    """Crea (una sola vez) el detector HandLandmarker en modo imagen estatica."""
    global _detector_cache
    if _detector_cache is not None:
        return _detector_cache

    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
    except ImportError:
        log.error("Instala dependencias: pip install mediapipe opencv-python")
        return None

    if not MODELO_TASK.exists():
        log.error(f"No se encontro el modelo en: {MODELO_TASK}")
        log.error(
            "Descargalo con: curl -o data/hand_landmarker.task "
            "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
            "hand_landmarker/float16/1/hand_landmarker.task"
        )
        return None

    BaseOptions = mp_python.BaseOptions
    HandLandmarker = mp_vision.HandLandmarker
    HandLandmarkerOptions = mp_vision.HandLandmarkerOptions
    VisionRunningMode = mp_vision.RunningMode

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODELO_TASK)),
        running_mode=VisionRunningMode.IMAGE,   # modo imagen estatica (no video)
        num_hands=2,
        min_hand_detection_confidence=0.4,
    )
    _detector_cache = HandLandmarker.create_from_options(options)
    return _detector_cache


def extraer_features_imagen(ruta_imagen: str):
    """
    Extrae el vector de features (126-dim) de una imagen usando MediaPipe.

    Returns:
        Array de 126 floats o None si no se detectan manos.
    """
    import cv2
    import mediapipe as mp

    detector = _obtener_detector()
    if detector is None:
        return None

    img = cv2.imread(str(ruta_imagen))
    if img is None:
        return None

    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)

    try:
        resultado = detector.detect(mp_image)
    except Exception as e:
        log.debug(f"Error detectando: {e}")
        return None

    if not resultado.hand_landmarks:
        return None

    def normalizar(mano_lm):
        arr = np.array([[lm.x, lm.y, lm.z] for lm in mano_lm])
        arr -= arr[0]
        escala = np.linalg.norm(arr[9]) + 1e-7
        arr /= escala
        return arr.flatten()

    feat_izq = np.zeros(63)
    feat_der = np.zeros(63)

    for idx, mano_lm in enumerate(resultado.hand_landmarks):
        lado = resultado.handedness[idx][0].category_name if idx < len(resultado.handedness) else "Right"
        if lado == "Left":
            feat_izq = normalizar(mano_lm)
        else:
            feat_der = normalizar(mano_lm)

    return np.concatenate([feat_der, feat_izq])


def cargar_dataset(directorio: str):
    """
    Carga todas las imagenes del directorio y extrae sus features.

    Returns:
        (X, y, clases) donde X es (N, 126), y es (N,) con indices de clase.
    """
    dir_path = Path(directorio)
    if not dir_path.exists():
        log.error(f"Directorio no encontrado: {directorio}")
        log.info("Crea la estructura: data/signs/<NombreSena>/<imagen.jpg>")
        sys.exit(1)

    clases = sorted([d.name for d in dir_path.iterdir() if d.is_dir()])
    if not clases:
        log.error(f"No se encontraron carpetas de clases en {directorio}")
        sys.exit(1)

    log.info(f"Clases encontradas: {clases}")

    X, y = [], []
    extensiones = {".jpg", ".jpeg", ".png", ".bmp"}
    total_sin_manos = 0

    for idx_clase, clase in enumerate(clases):
        carpeta = dir_path / clase
        imagenes = [f for f in carpeta.iterdir() if f.suffix.lower() in extensiones]
        log.info(f"  Procesando {clase}: {len(imagenes)} imagenes...")

        ok_clase = 0
        t0 = time.time()
        for i, img_path in enumerate(imagenes):
            features = extraer_features_imagen(str(img_path))
            if features is not None:
                X.append(features)
                y.append(idx_clase)
                ok_clase += 1
            else:
                total_sin_manos += 1

            if (i + 1) % 25 == 0:
                print(f"    {clase}: {i+1}/{len(imagenes)}", end="\r")

        log.info(f"  OK {clase}: {ok_clase}/{len(imagenes)} validas ({time.time()-t0:.1f}s)")

    if total_sin_manos > 0:
        log.warning(f"Total de imagenes sin manos detectadas: {total_sin_manos}")

    return np.array(X), np.array(y), clases


def entrenar_mlp(X_train, y_train):
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    modelo = Pipeline([
        ("scaler", StandardScaler()),
        ("mlp", MLPClassifier(
            hidden_layer_sizes=(128, 64),
            activation="relu",
            max_iter=800,
            random_state=42,
            early_stopping=True,
            validation_fraction=0.15,
            verbose=False,
        )),
    ])
    modelo.fit(X_train, y_train)
    return modelo


def entrenar_rf(X_train, y_train):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    modelo = Pipeline([
        ("scaler", StandardScaler()),
        ("rf", RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)),
    ])
    modelo.fit(X_train, y_train)
    return modelo


def entrenar_svm(X_train, y_train):
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    modelo = Pipeline([
        ("scaler", StandardScaler()),
        ("svm", SVC(kernel="rbf", probability=True, C=10, gamma="scale")),
    ])
    modelo.fit(X_train, y_train)
    return modelo


def main():
    configurar_logger()
    args = parsear_args()

    try:
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, accuracy_score
        import joblib
    except ImportError:
        log.error("Instala scikit-learn: pip install scikit-learn")
        sys.exit(1)

    log.info(f"Cargando dataset desde '{args.datos}'...")
    X, y, clases = cargar_dataset(args.datos)

    if len(X) == 0:
        log.error("No se extrajeron features. Verifica las imagenes y que MediaPipe detecte manos.")
        sys.exit(1)

    log.info(f"Dataset: {len(X)} muestras, {len(clases)} clases")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, stratify=y, random_state=42
    )

    log.info(f"Entrenando clasificador: {args.modelo.upper()}...")
    entrenadores = {"mlp": entrenar_mlp, "rf": entrenar_rf, "svm": entrenar_svm}
    modelo = entrenadores[args.modelo](X_train, y_train)

    y_pred = modelo.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    log.info(f"\nPrecision en test: {acc*100:.2f}%\n")

    etiquetas_presentes = sorted(set(y_test) | set(y_pred))
    nombres_presentes = [clases[i] for i in etiquetas_presentes]
    print(classification_report(
        y_test, y_pred,
        labels=etiquetas_presentes,
        target_names=nombres_presentes,
        zero_division=0,
    ))

    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, salida)
    log.info(f"Modelo guardado en '{salida}'")

    clases_path = salida.with_suffix(".clases.txt")
    clases_path.write_text("\n".join(clases), encoding="utf-8")
    log.info(f"Clases guardadas en '{clases_path}'")


if __name__ == "__main__":
    main()