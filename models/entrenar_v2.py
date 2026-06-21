"""
Entrenamiento del clasificador LSC v2 (secuencias mano + cara).

Uso:
    python models/entrenar_v2.py --datos data/sequences --salida data/lsc_model_v2.pkl
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import configurar_logger

log = logging.getLogger("lsc_bridge.entrenamiento_v2")


def parsear_args():
    p = argparse.ArgumentParser(description="Entrenar clasificador LSC v2 (secuencias)")
    p.add_argument("--datos", default="data/sequences", help="Directorio con secuencias .npy por clase")
    p.add_argument("--salida", default="data/lsc_model_v2.pkl", help="Ruta de salida del modelo")
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--modelo", choices=["mlp", "rf", "svm"], default="mlp")
    return p.parse_args()


def cargar_dataset(directorio: str):
    dir_path = Path(directorio)
    if not dir_path.exists():
        log.error(f"Directorio no encontrado: {directorio}")
        log.info("Ejecuta primero: python models/capturar_secuencias.py")
        sys.exit(1)

    clases = sorted([d.name for d in dir_path.iterdir() if d.is_dir()])
    if not clases:
        log.error(f"No se encontraron carpetas de clases en {directorio}")
        sys.exit(1)

    log.info(f"Clases encontradas: {clases}")

    X, y = [], []
    for idx_clase, clase in enumerate(clases):
        carpeta = dir_path / clase
        archivos = sorted(carpeta.glob("*.npy"))
        log.info(f"  {clase}: {len(archivos)} secuencias")

        for archivo in archivos:
            try:
                vector = np.load(archivo)
                X.append(vector)
                y.append(idx_clase)
            except Exception as e:
                log.warning(f"  Error cargando {archivo.name}: {e}")

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
            max_iter=1000,
            random_state=42,
            early_stopping=True,
            validation_fraction=0.15,
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
        log.error("No se cargaron secuencias.")
        sys.exit(1)

    log.info(f"Dataset: {len(X)} secuencias, {len(clases)} clases, {X.shape[1]} features por secuencia")

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
