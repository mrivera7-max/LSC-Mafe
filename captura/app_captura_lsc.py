import csv
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np

# Integrado en LSC-Mafe: usa el MISMO extractor que el reconocedor (models/extractor_v2.py) y los
# modelos .task de data/, de modo que lo capturado es idéntico a lo que ve la app en vivo.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ))
try:
    from models.extractor_v2 import ExtractorSecuencial, VENTANA_FRAMES
    EXTRACTOR_OK = True
except Exception as _e:
    EXTRACTOR_OK = False
    _IMPORT_ERROR = str(_e)

# =====================================================
# SOFTWARE DE CAPTURA DATASET LSC - UDI
# Versión GUI DUAL — cada secuencia guarda, de los MISMOS frames:
#   * <base>_NNN.npy          vector agregado de 342 features (modelo v2, RF, MLP)
#   * <base>_NNN_frames.npy   features por frame (N, 152)
#   * <base>_NNN/000.jpg ...  los frames RGB (MobileNetV2)
# Así los tres modelos de la comparación ven exactamente las mismas muestras.
# Solo se usan los frames con mano detectada (igual que el reconocedor en vivo).
# Conserva metadatos: estudiante, participante, sesión, mano, luz, fondo
# =====================================================

# El panel «Datos» de la app pasa estas dos variables al lanzar este programa.
DATASET_DIR = Path(os.environ.get("LSC_DATASET_DIR") or "dataset_lsc")
CAMARA_IDX = int(os.environ.get("LSC_CAMARA_IDX") or 0)
MANOS_VALIDAS = ["derecha", "izquierda", "ambas", "sin_manos"]
ILUMINACION_VALIDA = ["buena", "media", "baja"]
FONDOS_VALIDOS = ["claro", "oscuro", "cotidiano"]

# Cuántas secuencias grabar por defecto (antes eran 100 fotos; ahora son
# secuencias completas, así que el número razonable es mucho menor).
SECUENCIAS_POR_DEFECTO = 30
# v3: la ráfaga de 0,7 s solo alcanzaba el inicio y el final de la seña.
# Ahora se graba la seña COMPLETA (2 s por defecto, editable en la GUI),
# a la tasa nativa de la cámara (sin MediaPipe dentro del bucle de grabación)
# y luego se remuestrea a N_FRAMES_SECUENCIA frames equiespaciados en el tiempo.
DURACION_SECUENCIA_S = 2.0  # duración por defecto de cada seña
CUENTA_REGRESIVA_S = 1.5    # tiempo para volver a la posición inicial antes de cada seña
N_FRAMES_SECUENCIA = 20     # frames guardados por secuencia (equiespaciados)
MIN_FRAMES_MANO = 8         # secuencias con menos frames con mano se descartan y se repiten
ANCHO_PROCESO = 640         # ancho al que se reduce el frame (MediaPipe usa coordenadas normalizadas)
ANCHO_JPG = 640             # ancho de las imágenes guardadas (MobileNetV2 usa 224 px de alto)
CALIDAD_JPG = 85


def vector_frame(ff) -> np.ndarray:
    """Features de un frame: mano_der(63) + mano_izq(63) + boca(24) + dist(1) + cara(1) = 152."""
    return np.concatenate([ff.mano_der, ff.mano_izq, ff.boca,
                           [ff.dist_mano_boca], [float(ff.cara_presente)]])


def guardar_secuencia_dual(ruta_salida: Path, nombre_base: str, extractor, tomas) -> dict:
    """tomas: [(frame_bgr, FrameFeatures, t_mediapipe_ms)] de UNA secuencia.
    Guarda npy agregado + npy por frame + jpg de los mismos frames."""
    feats = [ff for _, ff, _ in tomas]
    ruta_npy = ruta_salida / f"{nombre_base}.npy"
    np.save(ruta_npy, extractor.agregar_secuencia(feats))
    np.save(ruta_salida / f"{nombre_base}_frames.npy", np.stack([vector_frame(f) for f in feats]))
    carpeta = ruta_salida / nombre_base
    carpeta.mkdir(parents=True, exist_ok=True)
    for i, (frame, _, _) in enumerate(tomas):
        h, w = frame.shape[:2]
        if w > ANCHO_JPG:
            frame = cv2.resize(frame, (ANCHO_JPG, int(round(h * ANCHO_JPG / w))), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(carpeta / f"{i:03d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_JPG])
    return {
        "ruta_npy": ruta_npy,
        "carpeta_frames": carpeta,
        "n_frames": len(tomas),
        "cara_frac": float(np.mean([f.cara_presente for f in feats])) if feats else 0.0,
        "t_mp_ms": float(np.mean([t for _, _, t in tomas])) if tomas else 0.0,
    }


def reducir(frame, ancho=ANCHO_PROCESO):
    h, w = frame.shape[:2]
    if w <= ancho:
        return frame
    return cv2.resize(frame, (ancho, int(round(h * ancho / w))), interpolation=cv2.INTER_AREA)


def indices_equiespaciados(n_total: int, n_objetivo: int) -> list:
    """Índices uniformes en [0, n_total-1]; si hay menos frames que el objetivo, se usan todos."""
    if n_total <= n_objetivo:
        return list(range(n_total))
    return [int(round(i)) for i in np.linspace(0, n_total - 1, n_objetivo)]


def abrir_camara(indice=0):
    """En Windows DirectShow + MJPG permite 30 fps a 1280x720; con el backend
    por defecto muchas webcams caen a 5-10 fps (frames muy espaciados)."""
    if os.name == "nt":
        cap = cv2.VideoCapture(indice, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(indice)
    else:
        cap = cv2.VideoCapture(indice)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def limpiar_texto(texto: str) -> str:
    texto = texto.lower().strip()
    reemplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
        "Á": "a", "É": "e", "Í": "i", "Ó": "o", "Ú": "u", "Ñ": "n"
    }
    for original, nuevo in reemplazos.items():
        texto = texto.replace(original, nuevo)
    texto = texto.replace(" ", "_")
    texto = re.sub(r"[^a-zA-Z0-9_\-]", "", texto)
    texto = re.sub(r"_+", "_", texto)
    return texto.strip("_")


def limpiar_codigo(texto: str) -> str:
    texto = texto.strip().upper().replace(" ", "_")
    texto = re.sub(r"[^a-zA-Z0-9_\-]", "", texto)
    return texto


def crear_metadata_si_no_existe(metadata_path: Path):
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    if not metadata_path.exists():
        with metadata_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow([
                "archivo",
                "sena",
                "estudiante",
                "participante",
                "sesion",
                "mano",
                "iluminacion",
                "fondo",
                "frames_usados",
                "manos_detectadas",
                "fecha",
                "ruta",
                "carpeta_frames",
                "rostro_frac",
                "t_mediapipe_ms"
            ])


def guardar_metadata(metadata_path: Path, fila):
    with metadata_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(fila)


def eliminar_filas_metadata_global(metadata_global_path: Path, ruta_sesion: Path):
    if not metadata_global_path.exists():
        return

    with metadata_global_path.open("r", newline="", encoding="utf-8") as file:
        filas = list(csv.reader(file))

    if not filas:
        return

    encabezado = filas[0]
    ruta_abs = str(ruta_sesion.resolve())
    filas_filtradas = [encabezado]

    for fila in filas[1:]:
        if len(fila) < 12:
            continue
        ruta_imagen = Path(fila[11])
        try:
            ruta_imagen_abs = str(ruta_imagen.resolve())
        except OSError:
            ruta_imagen_abs = str(ruta_imagen)
        if not ruta_imagen_abs.startswith(ruta_abs):
            filas_filtradas.append(fila)

    with metadata_global_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerows(filas_filtradas)


class CapturaApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Captura Dataset LSC - UDI")
        self.root.resizable(True, True)

        self.capturando = False
        self.cancelar = False
        self.datos_actuales = None

        self.crear_interfaz()
        self.ajustar_ventana()

    def crear_interfaz(self):
        titulo = ttk.Label(
            self.root,
            text="Captura Dual (secuencias + imágenes) - Dataset LSC",
            font=("Arial", 16, "bold")
        )
        titulo.pack(pady=(10, 2))

        subtitulo = ttk.Label(
            self.root,
            text="Proyecto Lengua de Señas Colombiana | Interacción con Unitree G1",
            font=("Arial", 10)
        )
        subtitulo.pack(pady=2)

        frame = ttk.LabelFrame(self.root, text="Información de captura")
        frame.pack(padx=20, pady=(8, 6), fill="x")

        self.var_sena = tk.StringVar()
        self.var_estudiante = tk.StringVar(value="E01")
        self.var_participante = tk.StringVar(value="P001")
        self.var_sesion = tk.StringVar(value="01")
        self.var_mano = tk.StringVar(value="derecha")
        self.var_iluminacion = tk.StringVar(value="buena")
        self.var_fondo = tk.StringVar(value="claro")
        self.var_cantidad = tk.StringVar(value=str(SECUENCIAS_POR_DEFECTO))
        self.var_duracion = tk.StringVar(value=str(DURACION_SECUENCIA_S))

        self.agregar_campo(frame, "Seña a capturar:", self.var_sena, 0, ejemplo="Ej: hola, gracias, silencio")
        self.agregar_campo(frame, "Código estudiante:", self.var_estudiante, 1, ejemplo="Ej: E01")
        self.agregar_campo(frame, "Código participante:", self.var_participante, 2, ejemplo="Ej: P001")
        self.agregar_campo(frame, "Sesión:", self.var_sesion, 3, ejemplo="Ej: 01")

        ttk.Label(frame, text="Mano:").grid(row=4, column=0, padx=10, pady=4, sticky="e")
        ttk.Combobox(frame, textvariable=self.var_mano, values=MANOS_VALIDAS, state="readonly", width=28).grid(row=4, column=1, padx=10, pady=4, sticky="w")

        ttk.Label(frame, text="Iluminación:").grid(row=5, column=0, padx=10, pady=4, sticky="e")
        ttk.Combobox(frame, textvariable=self.var_iluminacion, values=ILUMINACION_VALIDA, state="readonly", width=28).grid(row=5, column=1, padx=10, pady=4, sticky="w")

        ttk.Label(frame, text="Fondo:").grid(row=6, column=0, padx=10, pady=4, sticky="e")
        ttk.Combobox(frame, textvariable=self.var_fondo, values=FONDOS_VALIDOS, state="readonly", width=28).grid(row=6, column=1, padx=10, pady=4, sticky="w")

        self.agregar_campo(frame, "Cantidad de secuencias:", self.var_cantidad, 7, ejemplo="Recomendado: 30")
        self.agregar_campo(frame, "Duración de la seña (s):", self.var_duracion, 8, ejemplo="1.0 a 4.0 — debe cubrir la seña completa")

        frame_botones = ttk.Frame(self.root)
        frame_botones.pack(pady=6)

        self.btn_iniciar = ttk.Button(frame_botones, text="Iniciar captura", command=self.iniciar_captura)
        self.btn_iniciar.grid(row=0, column=0, padx=8)

        self.btn_salir = ttk.Button(frame_botones, text="Salir", command=self.root.destroy)
        self.btn_salir.grid(row=0, column=1, padx=8)

        self.estado = tk.StringVar(value="Estado: esperando información de captura")
        ttk.Label(self.root, textvariable=self.estado, font=("Arial", 10, "bold")).pack(pady=(4, 6))

        instrucciones = (
            "1. Seña en minúscula y sin tildes: hola, gracias, si, no, bien, mal, silencio.\n"
            "2. No use nombres reales de participantes. Use códigos: P001, P002...\n"
            "3. Deben verse la MANO y el ROSTRO completos (el rostro es necesario para silencio y gracias).\n"
            "4. En 'POSICIÓN INICIAL' baje las manos; en 'GRABANDO' haga la seña COMPLETA de principio a fin.\n"
            "5. Al finalizar podrá aceptar la captura o repetirla si quedó mal. Q cancela la captura."
        )
        marco = ttk.LabelFrame(self.root, text="Indicaciones")
        marco.pack(padx=20, pady=(0, 12), fill="x")
        ttk.Label(marco, text=instrucciones, justify="left", wraplength=680).pack(padx=10, pady=6, anchor="w")

    def ajustar_ventana(self):
        """Tamaño según el contenido (evita que se corte con escalado de pantalla 125 %/150 %)
        y ventana centrada; si la pantalla es baja, se limita a su alto."""
        self.root.update_idletasks()
        ancho = self.root.winfo_reqwidth()
        alto = min(self.root.winfo_reqheight(), self.root.winfo_screenheight() - 80)
        x = max(0, (self.root.winfo_screenwidth() - ancho) // 2)
        y = max(0, (self.root.winfo_screenheight() - alto) // 2 - 20)
        self.root.geometry(f"{ancho}x{alto}+{x}+{y}")
        self.root.minsize(ancho, min(alto, 520))

    def agregar_campo(self, frame, etiqueta, variable, fila, ejemplo=""):
        ttk.Label(frame, text=etiqueta).grid(row=fila, column=0, padx=10, pady=4, sticky="e")
        entrada = ttk.Entry(frame, textvariable=variable, width=31)
        entrada.grid(row=fila, column=1, padx=10, pady=4, sticky="w")
        if ejemplo:
            ttk.Label(frame, text=ejemplo, foreground="gray").grid(row=fila, column=2, padx=6, pady=4, sticky="w")

    def validar_datos(self):
        sena = limpiar_texto(self.var_sena.get())
        estudiante = limpiar_codigo(self.var_estudiante.get())
        participante = limpiar_codigo(self.var_participante.get())
        sesion = limpiar_texto(self.var_sesion.get()) or "01"
        mano = limpiar_texto(self.var_mano.get())
        iluminacion = limpiar_texto(self.var_iluminacion.get())
        fondo = limpiar_texto(self.var_fondo.get())

        try:
            cantidad = int(self.var_cantidad.get())
        except ValueError:
            messagebox.showerror("Error", "La cantidad de secuencias debe ser un número entero.")
            return None

        if cantidad <= 0:
            messagebox.showerror("Error", "La cantidad de secuencias debe ser mayor que cero.")
            return None

        try:
            duracion = float(self.var_duracion.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Error", "La duración debe ser un número (ej: 2.0).")
            return None
        if not 1.0 <= duracion <= 4.0:
            messagebox.showerror("Error", "La duración debe estar entre 1.0 y 4.0 segundos.")
            return None

        if not sena:
            messagebox.showerror("Error", "Debe escribir la seña a capturar.")
            return None
        if not estudiante:
            messagebox.showerror("Error", "Debe escribir el código del estudiante.")
            return None
        if not participante:
            messagebox.showerror("Error", "Debe escribir el código del participante.")
            return None

        return {
            "sena": sena,
            "estudiante": estudiante,
            "participante": participante,
            "sesion": sesion,
            "mano": mano,
            "iluminacion": iluminacion,
            "fondo": fondo,
            "cantidad": cantidad,
            "duracion": duracion,
        }

    def iniciar_captura(self):
        datos = self.validar_datos()
        if datos is None:
            return

        id_participante = f"{datos['estudiante']}_{datos['participante']}"
        ruta_salida = DATASET_DIR / datos["sena"] / id_participante / f"sesion_{datos['sesion']}"

        if ruta_salida.exists():
            respuesta = messagebox.askyesno(
                "Captura existente",
                "Ya existe una captura con estos datos.\n\n"
                f"Ruta:\n{ruta_salida}\n\n"
                "¿Desea reemplazarla?\n\n"
                "Sí: borra la captura anterior y repite.\n"
                "No: cancela para cambiar sesión o participante."
            )
            if respuesta:
                shutil.rmtree(ruta_salida, ignore_errors=True)
                eliminar_filas_metadata_global(DATASET_DIR / "metadata_global.csv", ruta_salida)
            else:
                return

        resumen = (
            f"Seña: {datos['sena']}\n"
            f"Estudiante: {datos['estudiante']}\n"
            f"Participante: {id_participante}\n"
            f"Sesión: {datos['sesion']}\n"
            f"Mano: {datos['mano']}\n"
            f"Iluminación: {datos['iluminacion']}\n"
            f"Fondo: {datos['fondo']}\n"
            f"Cantidad: {datos['cantidad']}\n"
            f"Duración por seña: {datos['duracion']:.1f} s\n\n"
            "¿Desea iniciar la captura?"
        )

        if not messagebox.askyesno("Confirmar captura", resumen):
            return

        self.btn_iniciar.config(state="disabled")
        self.estado.set("Estado: captura en proceso")
        self.datos_actuales = datos

        hilo = threading.Thread(target=self.capturar, args=(datos,), daemon=True)
        hilo.start()

    def capturar(self, datos):
        id_participante = f"{datos['estudiante']}_{datos['participante']}"
        sesion_txt = f"s{datos['sesion']}"
        ruta_salida = DATASET_DIR / datos["sena"] / id_participante / f"sesion_{datos['sesion']}"
        ruta_salida.mkdir(parents=True, exist_ok=True)

        metadata_path = ruta_salida / "metadata.csv"
        metadata_global_path = DATASET_DIR / "metadata_global.csv"
        crear_metadata_si_no_existe(metadata_path)
        crear_metadata_si_no_existe(metadata_global_path)

        # Iniciar el extractor de features (mano + cara). Cada secuencia se
        # convierte en un vector de 342 dimensiones, igual que el dataset del v2.
        if not EXTRACTOR_OK:
            self.root.after(0, lambda: messagebox.showerror(
                "Falta MediaPipe",
                "No se pudo cargar el extractor de features.\n\n"
                f"Detalle: {_IMPORT_ERROR}\n\n"
                "Instala las dependencias y descarga los modelos .task."))
            self.root.after(0, lambda: self.btn_iniciar.config(state="normal"))
            self.root.after(0, lambda: self.estado.set("Estado: falta MediaPipe"))
            return

        extractor = ExtractorSecuencial()
        if not extractor.iniciar():
            self.root.after(0, lambda: messagebox.showerror(
                "Error de modelos",
                "No se pudo iniciar el extractor.\n"
                "Verifica que data/hand_landmarker.task exista."))
            self.root.after(0, lambda: self.btn_iniciar.config(state="normal"))
            self.root.after(0, lambda: self.estado.set("Estado: error de modelos"))
            return

        cap = abrir_camara(CAMARA_IDX)
        if not cap.isOpened():
            extractor.detener()
            self.root.after(0, lambda: messagebox.showerror("Error", "No se pudo abrir la cámara."))
            self.root.after(0, lambda: self.btn_iniciar.config(state="normal"))
            self.root.after(0, lambda: self.estado.set("Estado: error de cámara"))
            return

        # ¿Esta seña usa manos? Para "sin_manos" NO exigimos manos presentes.
        exige_manos = datos["mano"] != "sin_manos"
        duracion = datos.get("duracion", DURACION_SECUENCIA_S)
        t_global = time.time()
        ventana = "Capturador Dataset LSC"

        def mostrar(img, lineas, punto_rojo=False):
            disp = reducir(img, 960).copy()
            for i, (txt, color) in enumerate(lineas):
                cv2.putText(disp, txt, (20, 35 + 35 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            if punto_rojo:
                cv2.circle(disp, (disp.shape[1] - 40, 40), 12, (0, 0, 255), -1)
            cv2.imshow(ventana, disp)
            return (cv2.waitKey(1) & 0xFF) == ord("q")

        def cancelar_todo(msg="Estado: captura cancelada"):
            cap.release(); cv2.destroyAllWindows(); extractor.detener()
            self.root.after(0, lambda: self.btn_iniciar.config(state="normal"))
            self.root.after(0, lambda: self.estado.set(msg))

        # Vista previa inicial (3 s)
        inicio_previa = time.time()
        while time.time() - inicio_previa < 3:
            ret, frame = cap.read()
            if not ret:
                continue
            frame = cv2.flip(frame, 1)
            restante = 3 - int(time.time() - inicio_previa)
            if mostrar(frame, [(f"Prepare la sena: {datos['sena']}", (255, 255, 255)),
                               (f"Inicia en {restante}", (0, 255, 255))]):
                cancelar_todo(); return

        contador = 0
        objetivo = datos["cantidad"]
        fps_medidos = []

        # Bucle principal: cada iteración graba UNA seña completa.
        while contador < objetivo:
            cancelado = False

            # --- 1. Cuenta regresiva: volver a la posición inicial ---
            t0 = time.time()
            while time.time() - t0 < CUENTA_REGRESIVA_S:
                ret, frame = cap.read()
                if not ret:
                    continue
                frame = cv2.flip(frame, 1)
                falta = CUENTA_REGRESIVA_S - (time.time() - t0)
                if mostrar(frame, [(f"Secuencia {contador+1}/{objetivo} - POSICION INICIAL", (0, 255, 255)),
                                   (f"Grabando en {falta:.1f} s", (0, 255, 255))]):
                    cancelado = True; break
            if cancelado:
                break

            # --- 2. Grabación cruda a la tasa de la cámara (sin MediaPipe en el bucle) ---
            crudos = []  # (frame_reducido, t_ms)
            t_inicio_seq = time.time()
            while time.time() - t_inicio_seq < duracion:
                ret, frame = cap.read()
                if not ret:
                    break
                t_ms = int((time.time() - t_global) * 1000)
                frame = reducir(cv2.flip(frame, 1))
                crudos.append((frame, t_ms))
                avance = (time.time() - t_inicio_seq) / duracion
                if mostrar(frame, [(f"Sena: {datos['sena']} | {id_participante}", (255, 255, 255)),
                                   (f"Secuencia: {contador+1}/{objetivo}", (0, 255, 0)),
                                   (f"GRABANDO {int(avance*100):3d} %  - haga la sena COMPLETA", (0, 0, 255))],
                           punto_rojo=True):
                    cancelado = True; break
            if cancelado:
                break
            dur_real = max(time.time() - t_inicio_seq, 1e-3)
            fps_medidos.append(len(crudos) / dur_real)

            # --- 3. Procesar TODOS los frames con MediaPipe (después de grabar) ---
            if crudos:
                mostrar(crudos[-1][0], [("Procesando...", (0, 255, 255))])
            procesados = []  # (frame, ff, t_mp_ms)
            manos_vistas = cara_vista = False
            ult_ts = -1
            for frame, t_ms in crudos:
                t_ms = max(t_ms, ult_ts + 1)  # MediaPipe VIDEO exige timestamps crecientes
                ult_ts = t_ms
                t_mp = time.perf_counter()
                ff = extractor.procesar_frame(frame, t_ms)
                t_mp = (time.perf_counter() - t_mp) * 1000
                if ff is None:
                    continue
                manos_vistas |= ff.manos_presentes
                cara_vista |= ff.cara_presente
                if ff.manos_presentes or not exige_manos:
                    procesados.append((frame, ff, t_mp))

            n_frames_total = len(crudos)
            if n_frames_total < 5 or not procesados:
                continue
            if exige_manos and len(procesados) < MIN_FRAMES_MANO:
                ret, frame = cap.read()
                if ret:
                    mostrar(cv2.flip(frame, 1), [(f"Mano en {len(procesados)} frames (min {MIN_FRAMES_MANO}), repite",
                                                  (0, 165, 255))])
                    cv2.waitKey(700)
                continue

            # --- 4. Remuestreo uniforme a N_FRAMES_SECUENCIA (cubre toda la seña) ---
            tomas = [procesados[i] for i in indices_equiespaciados(len(procesados), N_FRAMES_SECUENCIA)]

            # --- Guardar la secuencia (npy + frames de la MISMA toma) ---
            contador += 1
            nombre_base = (
                f"{datos['sena']}_{id_participante}_{sesion_txt}_"
                f"{datos['mano']}_luz{datos['iluminacion']}_fondo{datos['fondo']}_{contador:03d}"
            )
            info = guardar_secuencia_dual(ruta_salida, nombre_base, extractor, tomas)
            nombre_archivo = f"{nombre_base}.npy"
            ruta_npy = info["ruta_npy"]

            fila_metadata = [
                nombre_archivo,
                datos["sena"],
                datos["estudiante"],
                id_participante,
                datos["sesion"],
                datos["mano"],
                datos["iluminacion"],
                datos["fondo"],
                info["n_frames"],
                "si" if manos_vistas else "no",
                datetime.now().isoformat(),
                str(ruta_npy),
                info["carpeta_frames"].name,
                f"{info['cara_frac']:.2f}",
                f"{info['t_mp_ms']:.1f}"
            ]
            guardar_metadata(metadata_path, fila_metadata)
            guardar_metadata(metadata_global_path, fila_metadata)

            # Aviso breve de guardado (la cuenta regresiva siguiente da tiempo de reposicionar)
            ret, frame = cap.read()
            if ret:
                lineas = [(f"Guardada {contador}/{objetivo} ({info['n_frames']} de {len(procesados)} frames con mano,"
                           f" {fps_medidos[-1]:.0f} fps)", (0, 255, 0))]
                if exige_manos and not cara_vista:
                    lineas.append(("AVISO: no se vio el rostro", (0, 165, 255)))
                if fps_medidos[-1] < 15:
                    lineas.append(("AVISO: camara lenta (<15 fps). Mejore la luz.", (0, 165, 255)))
                if mostrar(cv2.flip(frame, 1), lineas):
                    break
                cv2.waitKey(300)

        cap.release()
        cv2.destroyAllWindows()
        extractor.detener()

        self.root.after(0, lambda: self.finalizar_captura(contador, datos, ruta_salida, metadata_global_path))

    def finalizar_captura(self, contador, datos, ruta_salida, metadata_global_path):
        self.estado.set(f"Estado: captura finalizada. Secuencias guardadas: {contador}")

        quedo_bien = messagebox.askyesno(
            "Validar captura",
            f"Se guardaron {contador} secuencias (npy + imágenes) en:\n{ruta_salida}\n\n"
            "¿La captura quedó bien?\n\n"
            "Sí: conservar captura.\n"
            "No: borrar y repetir con los mismos datos."
        )

        if quedo_bien:
            messagebox.showinfo("Captura aceptada", "La captura fue conservada correctamente.")
            self.estado.set("Estado: captura aceptada. Puede registrar otra seña o participante.")
            self.btn_iniciar.config(state="normal")
        else:
            shutil.rmtree(ruta_salida, ignore_errors=True)
            eliminar_filas_metadata_global(metadata_global_path, ruta_salida)
            self.estado.set("Estado: captura eliminada. Repitiendo con los mismos datos...")
            self.btn_iniciar.config(state="disabled")
            hilo = threading.Thread(target=self.capturar, args=(datos,), daemon=True)
            hilo.start()


if __name__ == "__main__":
    root = tk.Tk()
    app = CapturaApp(root)
    root.mainloop()
