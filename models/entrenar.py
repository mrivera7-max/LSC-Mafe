"""
Entrenamiento del clasificador de Lengua de Señas Colombiana.

Uso:
    python models/entrenar.py --datos data/signs --salida data/lsc_model.pkl

Estructura esperada de --datos:
    data/signs/
        Hola/
            muestra_001.jpg
            muestra_002.jpg
            ...
        Gracias/
            muestra_001.jpg
            ...

Para capturar muestras propias usa:
    python models/capturar_muestras.py
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import configurar_logger

log = logging.getLogger("lsc_bridge.entrenamiento")


def parsear_args():
    p = argparse.ArgumentParser(description="Entrenar clasificador LSC")
    p.add_argument("--datos", default="data/signs", help="Directorio con imágenes por clase")
    p.add_argument("--salida", default="data/lsc_model.pkl", help="Ruta de salida del modelo")
    p.add_argument("--test-size", type=float, default=0.2, help="Proporción de test (default: 0.2)")
    p.add_argument("--modelo", choices=["mlp","rf","svm"], default="mlp",
                   help="Tipo de clasificador (default: mlp)")
    return p.parse_args()


def extraer_features_imagen(ruta_imagen: str) -> np.ndarray | None:
    """
    Extrae el vector de features (126-dim) de una imagen usando MediaPipe.

    Returns:
        Array de 126 floats o None si no se detectan manos.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        log.error("Instala dependencias: pip install mediapipe opencv-python")
        return None

    mp_hands = mp.solutions.hands
    detector = mp_hands.Hands(
        static_image_mode=True,
        max_num_hands=2,
        min_detection_confidence=0.5,
    )

    img = cv2.imread(str(ruta_imagen))
    if img is None:
        return None

    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    resultado = detector.process(img_rgb)
    detector.close()

    if not resultado.multi_hand_landmarks:
        return None

    # Construir features para cada mano (igual que en reconocedor.py)
    from models.reconocedor import Landmark

    def normalizar(landmarks_mp):
        arr = np.array([[lm.x, lm.y, lm.z] for lm in landmarks_mp.landmark])
        arr -= arr[0]
        escala = np.linalg.norm(arr[9]) + 1e-7
        arr /= escala
        return arr.flatten()

    feat_izq = np.zeros(63)
    feat_der = np.zeros(63)

    handedness = resultado.multi_handedness or []
    for idx, mano_lm in enumerate(resultado.multi_hand_landmarks):
        lado = handedness[idx].classification[0].label if idx < len(handedness) else "Right"
        if lado == "Left":
            feat_izq = normalizar(mano_lm)
        else:
            feat_der = normalizar(mano_lm)

    return np.concatenate([feat_der, feat_izq])


def cargar_dataset(directorio: str):
    """
    Carga todas las imágenes del directorio y extrae sus features.

    Returns:
        (X, y, clases) donde X es (N, 126), y es (N,) con índices de clase.
    """
    dir_path = Path(directorio)
    if not dir_path.exists():
        log.error(f"Directorio no encontrado: {directorio}")
        log.info("Crea la estructura: data/signs/<NombreSeña>/<imagen.jpg>")
        sys.exit(1)

    clases = sorted([d.name for d in dir_path.iterdir() if d.is_dir()])
    if not clases:
        log.error(f"No se encontraron carpetas de clases en {directorio}")
        sys.exit(1)

    log.info(f"Clases encontradas: {clases}")

    X, y = [], []
    extensiones = {".jpg", ".jpeg", ".png", ".bmp"}

    for idx_clase, clase in enumerate(clases):
        carpeta = dir_path / clase
        imagenes = [f for f in carpeta.iterdir() if f.suffix.lower() in extensiones]
        log.info(f"  {clase}: {len(imagenes)} imágenes")

        for img_path in imagenes:
            features = extraer_features_imagen(str(img_path))
            if features is not None:
                X.append(features)
                y.append(idx_clase)
            else:
                log.debug(f"  Sin manos detectadas en: {img_path.name}")

    return np.array(X), np.array(y), clases


def entrenar_mlp(X_train, y_train):
    """Entrena un MLP con sklearn."""
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    modelo = Pipeline([
        ("scaler", StandardScaler()),
        ("mlp", MLPClassifier(
            hidden_layer_sizes=(256, 128, 64),
            activation="relu",
            max_iter=500,
            random_state=42,
            early_stopping=True,
            validation_fraction=0.1,
            verbose=True,
        )),
    ])
    modelo.fit(X_train, y_train)
    return modelo


def entrenar_rf(X_train, y_train):
    """Entrena un Random Forest."""
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
    """Entrena un SVM con kernel RBF."""
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
        log.error("No se extrajeron features. Verifica las imágenes y que MediaPipe detecte manos.")
        sys.exit(1)

    log.info(f"Dataset: {len(X)} muestras, {len(clases)} clases")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, stratify=y, random_state=42
    )

    log.info(f"Entrenando clasificador: {args.modelo.upper()}...")
    entrenadores = {"mlp": entrenar_mlp, "rf": entrenar_rf, "svm": entrenar_svm}
    modelo = entrenadores[args.modelo](X_train, y_train)

    # Evaluación
    y_pred = modelo.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    log.info(f"\nPrecisión en test: {acc*100:.2f}%\n")
    print(classification_report(y_test, y_pred, target_names=clases))

    # Guardar modelo + metadata
    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, salida)
    log.info(f"Modelo guardado en '{salida}'")

    # Guardar lista de clases
    clases_path = salida.with_suffix(".clases.txt")
    clases_path.write_text("\n".join(clases), encoding="utf-8")
    log.info(f"Clases guardadas en '{clases_path}'")


if __name__ == "__main__":
    main()
