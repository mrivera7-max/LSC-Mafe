"""
Carga de datos ya capturados (carpeta o .zip del capturador «dataset_lsc») al conjunto de
entrenamiento del reconocedor v2 (`data/sequences/<Clase>/*.npy`).

Acepta:
  - la estructura del capturador:  dataset_lsc/<sena>/<E01_P001>/sesion_01/<archivo>.npy
  - carpetas planas por clase:     <Clase>/<archivo>.npy
Solo copia vectores de secuencia (342 valores). Omite `*_frames.npy` (features por frame), las
imágenes y los metadatos. No sobrescribe: si el archivo ya está importado lo cuenta como repetido.
"""
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Optional

import numpy as np

DIM_SECUENCIA = 342
DESTINO_DEFECTO = Path("data/sequences")

# Nombre del capturador (minúscula, sin tildes) → nombre de clase del reconocedor / de las señas del robot
CLASES_CANONICAS = {
    "hola": "Hola", "gracias": "Gracias", "si": "Si", "no": "No", "bien": "Bien", "mal": "Mal",
    "silencio": "Silencio", "buenos_dias": "BuenosDias", "buenosdias": "BuenosDias",
    "por_favor": "PorFavor", "porfavor": "PorFavor",
}


def nombre_clase(nombre: str, existentes=()) -> str:
    """'por_favor' → 'PorFavor'; respeta mayúsculas de carpetas que ya existen."""
    clave = nombre.strip().lower().replace("-", "_").replace(" ", "_")
    if clave in CLASES_CANONICAS:
        return CLASES_CANONICAS[clave]
    for e in existentes:
        if e.lower().replace("_", "") == clave.replace("_", ""):
            return e
    return "".join(p.capitalize() for p in clave.split("_") if p)


def contar(destino: Path = DESTINO_DEFECTO) -> Dict[str, int]:
    """Secuencias por clase en el conjunto de entrenamiento."""
    destino = Path(destino)
    if not destino.exists():
        return {}
    return {d.name: sum(1 for a in d.glob("*.npy") if not a.stem.endswith("_frames"))
            for d in sorted(destino.iterdir()) if d.is_dir()}


_SESION = re.compile(r"^sesion_\d+$", re.IGNORECASE)


def _quitar_envoltorios(raiz: Path) -> Path:
    """Si la carpeta (o el zip) trae un único directorio envolvente, entra en él."""
    while True:
        hijos = [h for h in raiz.iterdir() if not h.name.startswith(".")]
        if len(hijos) == 1 and hijos[0].is_dir():
            raiz = hijos[0]
        else:
            return raiz


def _clase_y_prefijo(ruta: Path, raiz: Path):
    """(clase, partes intermedias) de un .npy; (None, ()) si no se puede saber la clase.
    Estructura del capturador: <sena>/<E01_P001>/sesion_01/archivo.npy; plana: <Clase>/archivo.npy."""
    partes = ruta.relative_to(raiz).parts
    for i, p in enumerate(partes[:-1]):
        if _SESION.match(p) and i >= 2:
            return partes[i - 2], partes[i - 1:-1]
    if len(partes) > 1:
        return partes[0], partes[1:-1]
    return None, ()


def importar(origen, destino: Path = DESTINO_DEFECTO) -> dict:
    """Importa `origen` (carpeta o .zip). Devuelve {'nuevas': {clase: n}, 'repetidas', 'omitidas', 'clases_nuevas'}."""
    origen, destino = Path(origen), Path(destino)
    if not origen.exists():
        raise FileNotFoundError(f"No existe: {origen}")
    tmp = None
    try:
        if origen.is_file():
            if origen.suffix.lower() != ".zip":
                raise ValueError("Elige una carpeta o un archivo .zip")
            tmp = Path(tempfile.mkdtemp(prefix="lsc_import_"))
            with zipfile.ZipFile(origen) as z:
                for m in z.namelist():     # solo .npy; evita rutas fuera de tmp
                    if m.endswith(".npy") and ".." not in Path(m).parts and not Path(m).is_absolute():
                        z.extract(m, tmp)
            raiz = tmp
        else:
            raiz = origen
        raiz = _quitar_envoltorios(raiz)
        existentes = [d.name for d in destino.iterdir() if d.is_dir()] if destino.exists() else []
        previas = set(existentes)
        res = {"nuevas": {}, "repetidas": 0, "omitidas": 0, "clases_nuevas": []}
        for f in sorted(raiz.rglob("*.npy")):
            if f.stem.endswith("_frames"):
                continue                           # features por frame: no son secuencias
            sena, rel = _clase_y_prefijo(f, raiz)
            if sena is None:
                res["omitidas"] += 1
                continue
            try:
                v = np.load(f, allow_pickle=False)
            except Exception:  # noqa: BLE001
                res["omitidas"] += 1
                continue
            if v.ndim != 1 or v.shape[0] != DIM_SECUENCIA or not np.isfinite(v).all():
                res["omitidas"] += 1
                continue
            clase = nombre_clase(sena, existentes)
            if clase not in existentes:
                existentes.append(clase)
            # prefijo con participante/sesión para no chocar con otros archivos de igual nombre
            nombre = "_".join(list(rel) + [f.name]) if rel else f.name
            salida = destino / clase / nombre
            if salida.exists():
                res["repetidas"] += 1
                continue
            salida.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, salida)
            res["nuevas"][clase] = res["nuevas"].get(clase, 0) + 1
        res["clases_nuevas"] = [c for c in res["nuevas"] if c not in previas]
        return res
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
