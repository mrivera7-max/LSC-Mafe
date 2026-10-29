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
        raise ValueError(f"Directorio no encontrado: {directorio} (captura o carga datos primero)")

    clases = sorted([d.name for d in dir_path.iterdir() if d.is_dir()])
    if not clases:
        raise ValueError(f"No se encontraron carpetas de clases en {directorio}")

    log.info(f"Clases encontradas: {clases}")

    X, y = [], []
    for idx_clase, clase in enumerate(clases):
        carpeta = dir_path / clase
        # «*_frames.npy» (features por frame del capturador dual) no son vectores de secuencia
        archivos = sorted(a for a in carpeta.glob("*.npy") if not a.stem.endswith("_frames"))
        log.info(f"  {clase}: {len(archivos)} secuencias")

        for archivo in archivos:
            try:
                vector = np.load(archivo)
                if vector.ndim != 1 or (X and vector.shape != X[0].shape):
                    log.warning(f"  {archivo.name}: forma {vector.shape} distinta, se omite")
                    continue
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


def entrenar_y_guardar(datos="data/sequences", salida="data/lsc_model_v2.pkl", modelo="mlp", test_size=0.2):
    """Entrena con las secuencias de `datos` y guarda modelo + .clases.txt. Devuelve un dict con
    precisión, clases y reporte. Lanza ValueError/ImportError con un mensaje claro si no se puede."""
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, accuracy_score
    import joblib

    X, y, clases = cargar_dataset(datos)
    if len(X) == 0:
        raise ValueError("No se cargaron secuencias.")
    if len(clases) < 2:
        raise ValueError("Se necesitan al menos 2 clases para entrenar.")
    conteo = np.bincount(y)
    if conteo.min() < 2:
        pocas = [clases[i] for i in np.where(conteo < 2)[0]]
        raise ValueError(f"Clases con menos de 2 secuencias: {', '.join(pocas)}")
    log.info(f"Dataset: {len(X)} secuencias, {len(clases)} clases, {X.shape[1]} features por secuencia")
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, stratify=y, random_state=42)
    log.info(f"Entrenando clasificador: {modelo.upper()}...")
    entrenadores = {"mlp": entrenar_mlp, "rf": entrenar_rf, "svm": entrenar_svm}
    clf = entrenadores[modelo](X_train, y_train)
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    presentes = sorted(set(y_test) | set(y_pred))
    reporte = classification_report(y_test, y_pred, labels=presentes,
                                    target_names=[clases[i] for i in presentes], zero_division=0)
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, salida)
    salida.with_suffix(".clases.txt").write_text("\n".join(clases), encoding="utf-8")
    log.info(f"Modelo guardado en '{salida}' (precisión en test {acc * 100:.2f}%)")
    return {"precision": float(acc), "clases": clases, "reporte": reporte,
            "n_secuencias": int(len(X)), "salida": str(salida)}


def main():
    configurar_logger()
    args = parsear_args()
    try:
        r = entrenar_y_guardar(args.datos, args.salida, args.modelo, args.test_size)
    except (ValueError, ImportError) as e:
        log.error(str(e))
        sys.exit(1)
    log.info(f"\nPrecision en test: {r['precision'] * 100:.2f}%\n")
    print(r["reporte"])


if __name__ == "__main__":
    main()
