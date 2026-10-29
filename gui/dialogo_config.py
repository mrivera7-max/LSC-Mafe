"""
Diálogo de configuración del sistema.
"""

import tkinter as tk
from tkinter import ttk


class DialogoConfig(tk.Toplevel):
    """Ventana modal de configuración."""

    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
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

        # Pestaña: Cámara
        tab_cam = tk.Frame(notebook, bg=self._panel, padx=16, pady=16)
        notebook.add(tab_cam, text="  Cámara  ")
        self._campo(tab_cam, "Índice de cámara:", "camara_idx", 0)
        self._campo(tab_cam, "Resolución ancho:", "camara_ancho", 1)
        self._campo(tab_cam, "Resolución alto:", "camara_alto", 2)
        self._campo(tab_cam, "FPS objetivo:", "camara_fps", 3)
        tk.Label(tab_cam, text="Si el índice no entrega imagen, la app usa sola la primera cámara de color "
                 "conectada que sí la entregue.\n" + self._camaras_detectadas(),
                 bg=self._panel, fg=self._texto_sec, font=("Segoe UI", 8), wraplength=420,
                 justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

        # Pestaña: Modelo
        tab_mod = tk.Frame(notebook, bg=self._panel, padx=16, pady=16)
        notebook.add(tab_mod, text="  Modelo  ")
        self._opcion(tab_mod, "Reconocedor:", "usar_v2", ("v2 (mano + cara)", "estático"), 0)
        self._campo(tab_mod, "Umbral de confianza (0-1):", "umbral_confianza", 1)
        self._campo(tab_mod, "Modelo v2 (.pkl):", "modelo_pesos_v2", 2)
        self._campo(tab_mod, "Modelo estático (.pkl):", "modelo_pesos", 3)
        self._campo(tab_mod, "Máx. de manos (estático):", "num_manos", 4)
        tk.Label(tab_mod, text=self._clases_modelo(), bg=self._panel, fg=self._texto_sec,
                 font=("Segoe UI", 8), wraplength=420, justify="left"
                 ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))
        tk.Label(tab_mod, text="El umbral decide cuándo una seña cuenta como detectada. Para que el robot la ejecute "
                 "se usa además «Confianza para ejecutar» (pestaña Robot G1). El modelo v2 siempre usa 2 manos.",
                 bg=self._panel, fg=self._texto_sec, font=("Segoe UI", 8), wraplength=420, justify="left"
                 ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Pestaña: Robot G1 (SONIC: simulación y robot real)
        tab_rob = tk.Frame(notebook, bg=self._panel, padx=16, pady=12)
        notebook.add(tab_rob, text="  Robot G1  ")
        f = 0
        f = self._titulo(tab_rob, "Conexión con el robot real", f)
        self._campo(tab_rob, "IP del PC2 del robot:", "camara_g1_host", f); f += 1
        self._campo(tab_rob, "Usuario SSH:", "camara_g1_usuario", f); f += 1
        self._campo(tab_rob, "Interfaz del cable (deploy):", "sonic_iface", f); f += 1
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
            return "Cámaras de color detectadas: ninguna."
        return "Cámaras de color detectadas: " + "; ".join(f"{n.ruta} «{n.nombre}»" for n in nodos[:4])

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
