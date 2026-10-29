"""
Pestaña «Datos»: ampliar el reconocedor con señas nuevas, de dos formas.

  1. Capturar con la cámara: abre el capturador dual (captura/app_captura_lsc.py) en su ventana.
     Para que la cámara quede libre, la de la pestaña «Cámara → Seña» se detiene antes.
  2. Cargar datos ya capturados: una carpeta o un .zip con la estructura del capturador
     (dataset_lsc/<seña>/<E01_P001>/sesion_01/*.npy) o carpetas planas por clase.

Ambos caminos terminan en data/sequences/<Clase>/ y desde aquí se reentrena el modelo v2.
"""
import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from models import importar_dataset as imp

log = logging.getLogger("lsc_bridge.panel_datos")

RAIZ = Path(__file__).resolve().parent.parent
DIR_CAPTURAS = RAIZ / "dataset_lsc"
DIR_SECUENCIAS = RAIZ / "data" / "sequences"

BG, PANEL, TXT, SEC = "#1a1a2e", "#16213e", "#e2e8f0", "#94a3b8"
VERDE, AZUL, ROJO = "#22c55e", "#0f3460", "#ef4444"
MIN_RECOMENDADO = 30


class PanelDatos(tk.Frame):
    def __init__(self, padre, config, raiz_tk,
                 antes_de_capturar: Optional[Callable[[], None]] = None):
        super().__init__(padre, bg=BG)
        self.config = config
        self._raiz = raiz_tk
        self._antes = antes_de_capturar
        self._proc: Optional[subprocess.Popen] = None
        self._entrenando = False
        self._construir()
        self.refrescar()

    # ── interfaz ────────────────────────────────────────────────
    def _caja(self, titulo):
        f = tk.LabelFrame(self, text=f"  {titulo}  ", bg=PANEL, fg=TXT, font=("Segoe UI", 10, "bold"),
                          padx=12, pady=8, bd=1, relief="solid")
        f.pack(fill="x", padx=16, pady=(10, 0))
        return f

    def _boton(self, padre, texto, comando, color=AZUL):
        b = tk.Button(padre, text=texto, command=comando, bg=color, fg="white", relief="flat",
                      font=("Segoe UI", 10, "bold"), padx=12, pady=5, cursor="hand2",
                      activebackground=color, activeforeground="white", disabledforeground="#64748b")
        return b

    def _construir(self):
        tk.Label(self, text="Datos de señas: capturar o cargar, y reentrenar el reconocedor",
                 bg=BG, fg=TXT, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16, pady=(12, 0))

        c1 = self._caja("1 · Capturar con la cámara")
        tk.Label(c1, text="Abre el capturador dual (la cámara de la pestaña «Cámara → Seña» se detiene "
                 "mientras tanto). Cada secuencia guarda vector, frames e imágenes en dataset_lsc/.",
                 bg=PANEL, fg=SEC, font=("Segoe UI", 9), wraplength=820, justify="left").pack(anchor="w")
        fila = tk.Frame(c1, bg=PANEL)
        fila.pack(anchor="w", pady=(6, 0))
        self._btn_cap = self._boton(fila, "📷  Abrir capturador", self._abrir_capturador)
        self._btn_cap.pack(side="left")
        self._btn_imp_cap = self._boton(fila, "Importar lo capturado", self._importar_capturas)
        self._btn_imp_cap.pack(side="left", padx=8)
        self._lbl_cap = tk.Label(fila, text="", bg=PANEL, fg=SEC, font=("Segoe UI", 9))
        self._lbl_cap.pack(side="left", padx=8)

        c2 = self._caja("2 · Cargar datos ya capturados")
        tk.Label(c2, text="Carpeta o .zip del capturador (dataset_lsc/<seña>/<E01_P001>/sesion_01/*.npy) o "
                 "carpetas por clase. Solo se copian los vectores de secuencia; no se pisa lo ya cargado.",
                 bg=PANEL, fg=SEC, font=("Segoe UI", 9), wraplength=820, justify="left").pack(anchor="w")
        fila = tk.Frame(c2, bg=PANEL)
        fila.pack(anchor="w", pady=(6, 0))
        self._boton(fila, "📁  Cargar carpeta…", self._cargar_carpeta).pack(side="left")
        self._boton(fila, "🗜  Cargar .zip…", self._cargar_zip).pack(side="left", padx=8)
        self._lbl_imp = tk.Label(fila, text="", bg=PANEL, fg=SEC, font=("Segoe UI", 9))
        self._lbl_imp.pack(side="left", padx=8)

        c3 = self._caja("Secuencias disponibles para entrenar")
        est = ttk.Style()
        est.configure("Datos.Treeview", background="#0d1117", fieldbackground="#0d1117", foreground=TXT,
                      rowheight=22)
        self._tabla = ttk.Treeview(c3, columns=("clase", "n", "estado"), show="headings", height=6,
                                   style="Datos.Treeview")
        for col, texto, ancho in (("clase", "Seña", 180), ("n", "Secuencias", 100), ("estado", "Estado", 520)):
            self._tabla.heading(col, text=texto)
            self._tabla.column(col, width=ancho, anchor="w")
        self._tabla.pack(fill="x")

        c4 = self._caja("3 · Reentrenar el modelo v2")
        fila = tk.Frame(c4, bg=PANEL)
        fila.pack(anchor="w")
        tk.Label(fila, text="Clasificador:", bg=PANEL, fg=TXT, font=("Segoe UI", 10)).pack(side="left")
        self._var_modelo = tk.StringVar(value="mlp")
        ttk.Combobox(fila, textvariable=self._var_modelo, values=("mlp", "rf", "svm"), state="readonly",
                     width=6).pack(side="left", padx=8)
        self._btn_ent = self._boton(fila, "🧠  Reentrenar", self._reentrenar, VERDE)
        self._btn_ent.pack(side="left", padx=8)
        self._lbl_ent = tk.Label(fila, text="", bg=PANEL, fg=SEC, font=("Segoe UI", 9))
        self._lbl_ent.pack(side="left", padx=8)
        self._log = tk.Text(c4, height=9, bg="#0d1117", fg="#cbd5e1", font=("Consolas", 9), relief="flat",
                            wrap="word", state="disabled")
        self._log.pack(fill="x", pady=(6, 0))

    # ── utilidades ──────────────────────────────────────────────
    def _escribir(self, texto: str):
        def poner():
            self._log.configure(state="normal")
            self._log.insert("end", texto.rstrip("\n") + "\n")
            self._log.see("end")
            self._log.configure(state="disabled")
        self._raiz.after(0, poner)

    def _ruta_modelo(self) -> Path:
        ruta = Path(getattr(self.config, "modelo_pesos_v2", "data/lsc_model_v2.pkl"))
        return ruta if ruta.is_absolute() else RAIZ / ruta

    def _clases_modelo(self):
        f = self._ruta_modelo().with_suffix(".clases.txt")
        return set(f.read_text(encoding="utf-8").split()) if f.exists() else set()

    def refrescar(self):
        conteo = imp.contar(DIR_SECUENCIAS)
        en_modelo = self._clases_modelo()
        self._tabla.delete(*self._tabla.get_children())
        if not conteo:
            self._tabla.insert("", "end", values=("(sin datos)", 0, "Captura o carga señas para empezar"))
        for clase, n in conteo.items():
            if clase not in en_modelo:
                estado = "NUEVA: el modelo actual aún no la reconoce, reentrena"
            elif n < MIN_RECOMENDADO:
                estado = f"en el modelo; pocas muestras (recomendado ≥ {MIN_RECOMENDADO})"
            else:
                estado = "en el modelo"
            self._tabla.insert("", "end", values=(clase, n, estado))
        hay_capturas = DIR_CAPTURAS.exists() and any(DIR_CAPTURAS.rglob("*.npy"))
        self._btn_imp_cap.configure(state="normal" if hay_capturas else "disabled")

    # ── 1. capturar ─────────────────────────────────────────────
    def _abrir_capturador(self):
        if self._proc and self._proc.poll() is None:
            messagebox.showinfo("Capturador", "El capturador ya está abierto.")
            return
        if self._antes:
            try:
                self._antes()                     # libera la cámara de la pestaña «Cámara → Seña»
            except Exception as e:  # noqa: BLE001
                log.warning(f"No se pudo detener la cámara de la app: {e}")
        env = dict(os.environ, LSC_DATASET_DIR=str(DIR_CAPTURAS))
        try:
            from utils.detectar_camaras import elegir_camara
            idx, msg = elegir_camara(int(getattr(self.config, "camara_idx", 0)))
        except Exception as e:  # noqa: BLE001
            idx, msg = int(getattr(self.config, "camara_idx", 0)), f"detección no disponible ({e})"
        if idx is None:
            messagebox.showerror("Cámara", f"No hay cámara disponible para capturar:\n{msg}")
            return
        env["LSC_CAMARA_IDX"] = str(idx)
        log.info(f"Capturador: {msg}")
        try:
            self._proc = subprocess.Popen([sys.executable, str(RAIZ / "captura" / "app_captura_lsc.py")],
                                          cwd=str(RAIZ), env=env)
        except OSError as e:
            messagebox.showerror("Capturador", f"No se pudo abrir el capturador:\n{e}")
            return
        self._btn_cap.configure(state="disabled")
        self._lbl_cap.configure(text=f"Capturador abierto · cámara: {msg}", fg=VERDE)
        self._vigilar()

    def _vigilar(self):
        if self._proc and self._proc.poll() is None:
            self._raiz.after(1000, self._vigilar)
            return
        self._btn_cap.configure(state="normal")
        self._lbl_cap.configure(text="Capturador cerrado. Importa lo capturado para usarlo; "
                                "vuelve a pulsar el botón de cámara para reactivarla.", fg=SEC)
        self.refrescar()

    def _importar_capturas(self):
        self._importar(DIR_CAPTURAS)

    # ── 2. cargar ───────────────────────────────────────────────
    def _cargar_carpeta(self):
        ruta = filedialog.askdirectory(title="Carpeta con las capturas (dataset_lsc)", parent=self._raiz)
        if ruta:
            self._importar(ruta)

    def _cargar_zip(self):
        ruta = filedialog.askopenfilename(title="Zip con las capturas", parent=self._raiz,
                                          filetypes=[("Zip", "*.zip"), ("Todos", "*.*")])
        if ruta:
            self._importar(ruta)

    def _importar(self, origen):
        try:
            r = imp.importar(origen, DIR_SECUENCIAS)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Cargar datos", str(e))
            return
        total = sum(r["nuevas"].values())
        detalle = ", ".join(f"{c}: {n}" for c, n in r["nuevas"].items()) or "nada nuevo"
        resumen = f"{total} secuencias nuevas ({detalle}) · {r['repetidas']} ya estaban · {r['omitidas']} omitidas"
        self._lbl_imp.configure(text=resumen, fg=VERDE if total else SEC)
        self._escribir(f"Importado desde {origen}: {resumen}")
        if r["clases_nuevas"]:
            self._escribir(f"Clases nuevas: {', '.join(r['clases_nuevas'])}. Reentrena para que el modelo las aprenda.")
        self.refrescar()

    # ── 3. reentrenar ───────────────────────────────────────────
    def _reentrenar(self):
        if self._entrenando:
            return
        conteo = imp.contar(DIR_SECUENCIAS)
        if len(conteo) < 2:
            messagebox.showwarning("Reentrenar", "Hacen falta al menos 2 señas con datos.")
            return
        pocas = [c for c, n in conteo.items() if n < MIN_RECOMENDADO]
        if pocas and not messagebox.askyesno(
                "Pocas muestras", f"Estas señas tienen menos de {MIN_RECOMENDADO} secuencias: {', '.join(pocas)}.\n"
                "El modelo puede confundirlas. ¿Entrenar de todos modos?"):
            return
        self._entrenando = True
        self._btn_ent.configure(state="disabled")
        self._lbl_ent.configure(text="Entrenando… (puede tardar unos minutos)", fg="#fbbf24")
        threading.Thread(target=self._hilo_entrenar, args=(self._var_modelo.get(),), daemon=True).start()

    def _hilo_entrenar(self, tipo: str):
        from models.entrenar_v2 import entrenar_y_guardar
        manejador = _ManejadorTexto(self._escribir)
        lg = logging.getLogger("lsc_bridge.entrenamiento_v2")
        lg.addHandler(manejador)
        salida = self._ruta_modelo()
        copia = salida.with_name(salida.stem + ".bak" + salida.suffix)
        try:
            if salida.exists():                   # respaldo del modelo actual, por si el nuevo es peor
                import shutil
                shutil.copy2(salida, copia)
                self._escribir(f"Respaldo del modelo anterior: {copia.name}")
            r = entrenar_y_guardar(str(DIR_SECUENCIAS), str(salida), tipo)
            self._escribir(r["reporte"])
            msg = (f"Listo: precisión {r['precision'] * 100:.1f}% en {len(r['clases'])} señas. "
                   "Reinicia la app para usar el modelo nuevo.")
            self._raiz.after(0, lambda: self._fin_entrenar(msg, True))
        except (ValueError, ImportError) as e:
            self._escribir(f"ERROR: {e}")
            self._raiz.after(0, lambda: self._fin_entrenar(f"No se pudo entrenar: {e}", False))
        except Exception as e:  # noqa: BLE001
            log.exception("Entrenamiento")
            self._escribir(f"ERROR inesperado: {e}")
            self._raiz.after(0, lambda: self._fin_entrenar(f"Error inesperado: {e}", False))
        finally:
            lg.removeHandler(manejador)

    def _fin_entrenar(self, msg: str, ok: bool):
        self._entrenando = False
        self._btn_ent.configure(state="normal")
        self._lbl_ent.configure(text=msg, fg=VERDE if ok else ROJO)
        self.refrescar()

    def cerrar(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


class _ManejadorTexto(logging.Handler):
    def __init__(self, escribir):
        super().__init__(level=logging.INFO)
        self._escribir = escribir

    def emit(self, record):
        try:
            self._escribir(record.getMessage())
        except Exception:  # noqa: BLE001
            pass
