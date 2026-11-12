"""
Diálogo de configuración del sistema.
"""

import tkinter as tk
from tkinter import ttk


class DialogoConfig(tk.Toplevel):
    """Ventana modal de configuración."""

    def __init__(self, parent, config, info_camara=None):
        super().__init__(parent)
        self.config = config
        self._info_camara = info_camara          # callable -> datos de la cámara en uso (la da la ventana de cámara)
        self.title("Configuración — LSC Bridge")
        self.resizable(False, False)
        self.grab_set()  # Modal

        self._bg = "#1a1a2e"
        self._panel = "#16213e"
        self._texto = "#e2e8f0"
        self._texto_sec = "#94a3b8"
        self.configure(bg=self._bg)

        self._construir()
        self._centrar()

    def _construir(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=16, pady=16)

        # Pestaña: Cámara (muestra la cámara que está en uso: externa del robot, cabeza o la de la PC)
        tab_cam = tk.Frame(notebook, bg=self._panel, padx=16, pady=14)
        notebook.add(tab_cam, text="  Cámara  ")
        f = 0
        datos = {"fuente": None, "titulo": "Ninguna cámara activa", "filas": []}
        if self._info_camara is not None:
            try:
                datos = self._info_camara()
            except Exception:  # noqa: BLE001
                pass
        f = self._titulo(tab_cam, "Cámara en uso", f)
        color = "#86efac" if datos["fuente"] else "#60a5fa"       # verde = en funcionamiento, azul = ninguna
        tk.Label(tab_cam, text=("● " if datos["fuente"] else "○ ") + datos["titulo"], bg=self._panel, fg=color,
                 font=("Segoe UI", 10, "bold")).grid(row=f, column=0, columnspan=2, sticky="w", pady=(2, 4)); f += 1
        for etiqueta, valor in datos["filas"]:
            f = self._dato(tab_cam, etiqueta, valor, f)
        if not datos["filas"]:
            f = self._dato(tab_cam, "", "Pulsa «Cámara PC» o «Cámara G1» en la pestaña Cámara → Seña.", f)
        f = self._titulo(tab_cam, "Cámaras detectadas", f)
        f = self._dato(tab_cam, "En la PC", self._camaras_detectadas(), f)
        host = getattr(self.config, "camara_g1_host", "")
        f = self._dato(tab_cam, "En el robot", self._resumen_robot(datos, host), f)
        f = self._titulo(tab_cam, "Ajustes de la cámara de la PC", f)
        self._campo(tab_cam, "Índice de cámara:", "camara_idx", f); f += 1
        self._campo(tab_cam, "Resolución ancho:", "camara_ancho", f); f += 1
        self._campo(tab_cam, "Resolución alto:", "camara_alto", f); f += 1
        self._campo(tab_cam, "FPS objetivo:", "camara_fps", f); f += 1
        tk.Label(tab_cam, text="Si el índice no entrega imagen, la app usa sola la primera cámara de color conectada "
                 "que sí la entregue. La cámara del robot (externa o cabeza) se elige sola por su puerto USB "
                 "(pestaña Robot G1) y su resolución la fija el servidor del robot.",
                 bg=self._panel, fg=self._texto_sec, font=("Segoe UI", 8), wraplength=460,
                 justify="left").grid(row=f, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Pestaña: Modelo
        tab_mod = tk.Frame(notebook, bg=self._panel, padx=16, pady=16)
        notebook.add(tab_mod, text="  Modelo  ")
        f = self._titulo(tab_mod, "Modelo en uso", 0)
        for etiqueta, valor in self._datos_modelo():
            f = self._dato(tab_mod, etiqueta, valor, f)
        f = self._titulo(tab_mod, "Ajustes", f)
        self._opcion(tab_mod, "Reconocedor:", "usar_v2", ("v2 (mano + cara)", "estático"), f); f += 1
        self._campo(tab_mod, "Umbral de confianza (0-1):", "umbral_confianza", f); f += 1
        self._campo(tab_mod, "Modelo v2 (.pkl):", "modelo_pesos_v2", f); f += 1
        self._campo(tab_mod, "Modelo estático (.pkl):", "modelo_pesos", f); f += 1
        self._campo(tab_mod, "Máx. de manos (estático):", "num_manos", f); f += 1
        tk.Label(tab_mod, text="El umbral decide cuándo una seña cuenta como detectada (se muestra en pantalla). "
                 "Para que el robot además la ejecute, la confianza debe superar «Confianza para ejecutar» "
                 "(pestaña Robot G1); esa sí vale al instante al guardar. El modelo v2 siempre usa 2 manos.",
                 bg=self._panel, fg=self._texto_sec, font=("Segoe UI", 8), wraplength=460, justify="left"
                 ).grid(row=f, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Pestaña: Robot G1 (SONIC: simulación y robot real)
        tab_rob = tk.Frame(notebook, bg=self._panel, padx=16, pady=12)
        notebook.add(tab_rob, text="  Robot G1  ")
        f = 0
        f = self._titulo(tab_rob, "Conexión con el robot real", f)
        self._campo(tab_rob, "IP del PC2 del robot:", "camara_g1_host", f); f += 1
        self._campo(tab_rob, "Usuario SSH:", "camara_g1_usuario", f); f += 1
        self._campo(tab_rob, "Interfaz del cable (deploy):", "sonic_iface", f); f += 1
        self._campo(tab_rob, "Puerto USB de la cabeza (no se usa):", "camara_g1_usb_cabeza", f); f += 1
        self._campo(tab_rob, "Puerto USB de la cámara (vacío = la externa):", "camara_g1_usb", f); f += 1
        f = self._titulo(tab_rob, "Movimiento de las señas (solo robot real)", f)
        self._campo(tab_rob, "Amplitud (0-1):", "sonic_escala", f); f += 1
        self._campo(tab_rob, "Factor de tiempo (≥ 1):", "sonic_tiempo", f); f += 1
        self._campo(tab_rob, "Velocidad máx. (rad/s):", "sonic_vel_max", f); f += 1
        self._campo(tab_rob, "Confianza para ejecutar (0-1):", "sonic_confianza", f); f += 1
        f = self._titulo(tab_rob, "Manos y voz", f)
        self._opcion(tab_rob, "Manos Inspire:", "sonic_manos", ("auto", "dex3", "inspire", "ambas", "ninguna"), f); f += 1
        self._opcion(tab_rob, "Micrófono de voz:", "mic_fuente", ("auto", "pc", "robot", "g1"), f); f += 1
        self._campo(tab_rob, "Micrófono de la PC (-1 = auto):", "mic_indice", f); f += 1
        tk.Label(tab_rob, text="Se aplica al reiniciar la app. Lo que se escriba al lanzar (--sonic-vel-max, --mic…) "
                 "tiene prioridad.", bg=self._panel, fg=self._texto_sec, font=("Segoe UI", 8),
                 wraplength=420, justify="left").grid(row=f, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # Botones
        frame_btn = tk.Frame(self, bg=self._bg)
        frame_btn.pack(fill="x", padx=16, pady=(0, 16))

        tk.Button(frame_btn, text="Guardar", bg="#22c55e", fg="#052e16",
                  relief="flat", font=("Segoe UI", 10, "bold"),
                  padx=16, pady=6, cursor="hand2",
                  command=self._guardar).pack(side="right", padx=(8, 0))

        tk.Button(frame_btn, text="Cancelar", bg="#374151", fg=self._texto,
                  relief="flat", font=("Segoe UI", 10),
                  padx=16, pady=6, cursor="hand2",
                  command=self.destroy).pack(side="right")

    @staticmethod
    def _camaras_detectadas() -> str:
        try:
            from utils.detectar_camaras import listar_nodos
            nodos = listar_nodos()
        except Exception:  # noqa: BLE001
            nodos = []
        if not nodos:
            return "ninguna cámara de color"
        return "; ".join(f"{n.ruta} «{n.nombre}»" for n in nodos[:4])

    @staticmethod
    def _resumen_robot(datos: dict, host: str) -> str:
        if datos.get("fuente") == "g1":
            return f"en uso (ver arriba); PC2 {host}"
        return f"PC2 {host}: se detecta al pulsar «Cámara G1» (la externa, sin buscar por nombre)"

    def _datos_modelo(self):
        """Filas (etiqueta, valor) del modelo que está configurado."""
        from datetime import datetime
        from pathlib import Path
        cfg = self.config
        usa_v2 = bool(getattr(cfg, "usar_v2", False))
        ruta = Path(getattr(cfg, "modelo_pesos_v2" if usa_v2 else "modelo_pesos", ""))
        if not ruta.is_absolute():
            ruta = Path(__file__).resolve().parent.parent / ruta
        filas = [("Reconocedor", "v2 (mano + cara, secuencia de fotogramas)" if usa_v2 else "estático (una mano, un fotograma)")]
        if ruta.exists():
            fecha = datetime.fromtimestamp(ruta.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
            filas.append(("Archivo", f"{ruta.name} ({ruta.stat().st_size / 1024:.0f} KB, {fecha})"))
        else:
            filas.append(("Archivo", f"{ruta.name}: NO existe, hay que entrenarlo"))
        clases = ruta.with_suffix(".clases.txt")
        if clases.exists():
            nombres = [c for c in clases.read_text(encoding="utf-8").split() if c]
            filas.append((f"Señas ({len(nombres)})", ", ".join(nombres)))
        filas.append(("Umbral de detección", f"{float(getattr(cfg, 'umbral_confianza', 0)):.2f}"))
        filas.append(("Confianza para ejecutar", f"{float(getattr(cfg, 'sonic_confianza', 0)):.2f} (robot)"))
        return filas

    def _dato(self, parent, etiqueta: str, valor: str, fila: int) -> int:
        """Fila de solo lectura: etiqueta y valor."""
        tk.Label(parent, text=etiqueta, bg=self._panel, fg=self._texto_sec, font=("Segoe UI", 9)
                 ).grid(row=fila, column=0, sticky="nw", pady=2, padx=(0, 16))
        tk.Label(parent, text=valor, bg=self._panel, fg=self._texto, font=("Segoe UI", 9), wraplength=330,
                 justify="left").grid(row=fila, column=1, sticky="w", pady=2)
        return fila + 1

    def _clases_modelo(self) -> str:
        """Señas que reconoce el modelo configurado (lee el .clases.txt junto al .pkl)."""
        from pathlib import Path
        usa_v2 = bool(getattr(self.config, "usar_v2", False))
        ruta = Path(getattr(self.config, "modelo_pesos_v2" if usa_v2 else "modelo_pesos", ""))
        if not ruta.is_absolute():
            ruta = Path(__file__).resolve().parent.parent / ruta
        clases = ruta.with_suffix(".clases.txt")
        if clases.exists():
            nombres = [c for c in clases.read_text(encoding="utf-8").split() if c]
            return f"Señas que reconoce este modelo ({len(nombres)}): {', '.join(nombres)}."
        return f"No encuentro {ruta.name}: la cámara no reconocerá señas hasta entrenarlo."

    def _titulo(self, parent, texto: str, fila: int) -> int:
        tk.Label(parent, text=texto, bg=self._panel, fg="#60a5fa", font=("Segoe UI", 9, "bold")
                 ).grid(row=fila, column=0, columnspan=2, sticky="w", pady=(8, 0))
        return fila + 1

    def _opcion(self, parent, etiqueta: str, atributo: str, opciones, fila: int):
        tk.Label(parent, text=etiqueta, bg=self._panel, fg=self._texto_sec,
                 font=("Segoe UI", 9)).grid(row=fila, column=0, sticky="w", pady=4, padx=(0, 16))
        actual = getattr(self.config, atributo, opciones[0])
        if isinstance(actual, bool):
            actual = opciones[0] if actual else opciones[1]
        var = tk.StringVar(value=str(actual))
        ttk.Combobox(parent, textvariable=var, values=list(opciones), state="readonly", width=26
                     ).grid(row=fila, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)
        setattr(self, f"_var_{atributo}", (var, atributo))

    def _campo(self, parent, etiqueta: str, atributo: str, fila: int):
        tk.Label(parent, text=etiqueta, bg=self._panel, fg=self._texto_sec,
                 font=("Segoe UI", 9)).grid(row=fila, column=0, sticky="w", pady=6, padx=(0, 16))

        var = tk.StringVar(value=str(getattr(self.config, atributo, "")))
        entry = tk.Entry(parent, textvariable=var, bg="#0d1117", fg=self._texto,
                         insertbackground=self._texto, relief="flat",
                         font=("Segoe UI", 10), width=28)
        entry.grid(row=fila, column=1, sticky="ew", pady=6)
        parent.columnconfigure(1, weight=1)
        setattr(self, f"_var_{atributo}", (var, atributo))

    # Rangos válidos de los ajustes del robot: (mín, máx, mensaje)
    _RANGOS = {
        "sonic_escala": (0.05, 1.0, "La amplitud debe estar entre 0.05 y 1"),
        "sonic_tiempo": (1.0, 5.0, "El factor de tiempo debe estar entre 1 y 5 (con el robot real no puede ser menor que 1)"),
        "sonic_vel_max": (0.2, 6.0, "La velocidad máxima debe estar entre 0.2 y 6 rad/s"),
        "sonic_confianza": (0.0, 1.0, "La confianza debe estar entre 0 y 1"),
        "umbral_confianza": (0.0, 1.0, "El umbral de confianza debe estar entre 0 y 1"),
        "mic_indice": (-1, 99, "El índice de micrófono debe ser -1 (automático) o un número de la lista"),
    }

    def _validar(self) -> bool:
        from tkinter import messagebox
        for _, (var, atributo) in {k: v for k, v in self.__dict__.items() if k.startswith("_var_")}.items():
            if atributo in self._RANGOS:
                lo, hi, msg = self._RANGOS[atributo]
                try:
                    v = float(var.get())
                except ValueError:
                    v = None
                if v is None or not lo <= v <= hi:
                    messagebox.showerror("Valor no válido", msg, parent=self)
                    return False
        return True

    def _guardar(self):
        if not self._validar():
            return
        for nombre, (var, atributo) in {
            k: v for k, v in self.__dict__.items()
            if k.startswith("_var_")
        }.items():
            val_str = var.get()
            actual = getattr(self.config, atributo)
            try:
                if isinstance(actual, bool):
                    setattr(self.config, atributo, val_str.startswith("v2"))
                elif isinstance(actual, int):
                    setattr(self.config, atributo, int(val_str))
                elif isinstance(actual, float):
                    setattr(self.config, atributo, float(val_str))
                else:
                    setattr(self.config, atributo, val_str)
            except (ValueError, TypeError):
                pass

        self.config.guardar()
        self.destroy()

    def _centrar(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = self.master.winfo_x() + (self.master.winfo_width() - w) // 2
        y = self.master.winfo_y() + (self.master.winfo_height() - h) // 2
        self.geometry(f"+{x}+{y}")
