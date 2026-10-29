"""Pestaña «Conexión»: verifica al abrir la app que todo está listo y permite repetirlo y arreglar lo común."""

import logging
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog

from robot import verificacion as V
from robot.servidor_camara_g1 import configurar_acceso_ssh

log = logging.getLogger("gui.panel_conexion")

FONDO, PANEL, TEXTO, TENUE = "#1a1a2e", "#16213e", "#e2e8f0", "#94a3b8"
ICONOS = {V.OK: ("✔", "#22c55e"), V.AVISO: ("⚠", "#f59e0b"), V.ERROR: ("✖", "#ef4444"),
          V.EN_CURSO: ("⟳", "#60a5fa"), V.PENDIENTE: ("•", "#64748b"), V.OMITIDO: ("–", "#64748b")}
BANNER = {V.OK: ("✔  Todo listo para arrancar", "#166534"),
          V.AVISO: ("⚠  Listo, con avisos (revisa lo marcado)", "#92400e"),
          V.ERROR: ("✖  Hay problemas que impiden arrancar (revisa lo marcado)", "#991b1b")}
BANNER_SIM = ("✔  Simulación lista   ·   ⚠  Robot real no disponible (revisa lo marcado en rojo para usarlo)", "#0f766e")
BANNER_SIM_ERROR = ("✖  No se puede abrir la simulación (revisa lo marcado)", "#991b1b")


class PanelConexion(tk.Frame):
    def __init__(self, master, verificador, raiz, al_terminar=None):
        super().__init__(master, bg=FONDO)
        self.verificador, self._raiz, self.al_terminar = verificador, raiz, al_terminar
        self._corriendo = False
        self._filas = {}
        self._resultados = []
        self._construir()
        for r in verificador.titulos():
            self._pintar(r)

    # ── UI ───────────────────────────────────────────────────────────
    def _construir(self):
        self._banner = tk.Label(self, text="Verificando…", bg="#1e3a8a", fg="white",
                                font=("Segoe UI", 13, "bold"), pady=12)
        self._banner.pack(fill="x", padx=16, pady=(16, 8))
        lista = tk.Frame(self, bg=PANEL, padx=16, pady=10)
        lista.pack(fill="x", padx=16)
        lista.columnconfigure(2, weight=1)
        for i, r in enumerate(self.verificador.titulos()):
            ico = tk.Label(lista, text="•", bg=PANEL, fg="#64748b", font=("Segoe UI", 13, "bold"), width=2)
            ico.grid(row=i * 2, column=0, sticky="w")
            tit = tk.Label(lista, text=r.titulo, bg=PANEL, fg=TEXTO, font=("Segoe UI", 10, "bold"), anchor="w")
            tit.grid(row=i * 2, column=1, sticky="w", padx=(4, 16))
            det = tk.Label(lista, text="", bg=PANEL, fg=TENUE, font=("Segoe UI", 9), anchor="w",
                           justify="left", wraplength=560)
            det.grid(row=i * 2, column=2, sticky="we")
            sol = tk.Label(lista, text="", bg=PANEL, fg="#fcd34d", font=("Segoe UI", 9), anchor="w",
                           justify="left", wraplength=640)
            sol.grid(row=i * 2 + 1, column=1, columnspan=2, sticky="w", padx=(4, 0))
            self._filas[r.clave] = (ico, tit, det, sol)
        barra = tk.Frame(self, bg=FONDO)
        barra.pack(fill="x", padx=16, pady=12)
        self._btn_repetir = tk.Button(barra, text="↻ Verificar de nuevo", bg="#2563eb", fg="white", relief="flat",
                                      font=("Segoe UI", 10, "bold"), padx=12, pady=6, cursor="hand2",
                                      command=self.verificar)
        self._btn_repetir.pack(side="left")
        self._btn_ssh = tk.Button(barra, text="🔑 Configurar acceso SSH", bg="#f59e0b", fg="#1c1917", relief="flat",
                                  font=("Segoe UI", 10, "bold"), padx=12, pady=6, cursor="hand2",
                                  command=self._configurar_ssh)
        self._btn_proc = tk.Button(barra, text="🧹 Cerrar procesos antiguos", bg="#475569", fg="white", relief="flat",
                                   font=("Segoe UI", 10, "bold"), padx=12, pady=6, cursor="hand2",
                                   command=self._cerrar_procesos)
        self._lbl_aviso = tk.Label(self, text="", bg=FONDO, fg=TENUE, font=("Segoe UI", 9), anchor="w")
        self._lbl_aviso.pack(fill="x", padx=18)

    def _pintar(self, r):
        ico, tit, det, sol = self._filas[r.clave]
        simbolo, color = ICONOS[r.estado]
        ico.configure(text=simbolo, fg=color)
        det.configure(text=r.detalle, fg=color if r.estado in (V.ERROR, V.AVISO) else TENUE)
        sol.configure(text=("→ " + r.solucion) if r.solucion and r.estado in (V.ERROR, V.AVISO) else "")

    # ── Verificación ─────────────────────────────────────────────────
    def verificar(self):
        if self._corriendo:
            return
        self._corriendo = True
        self._btn_repetir.configure(state="disabled")
        self._btn_ssh.pack_forget()
        self._btn_proc.pack_forget()
        self._banner.configure(text="Verificando…", bg="#1e3a8a")
        self._lbl_aviso.configure(text="")
        for r in self.verificador.titulos():
            self._pintar(r)

        def cambio(r):
            self._raiz.after(0, self._pintar, r)

        def aviso(clave, msg):
            self._raiz.after(0, lambda: self._lbl_aviso.configure(text=msg))

        def tarea():
            resultados = self.verificador.ejecutar(cambio, aviso)
            self._raiz.after(0, self._terminado, resultados)

        threading.Thread(target=tarea, daemon=True).start()

    def _terminado(self, resultados):
        self._corriendo = False
        self._resultados = resultados
        self._btn_repetir.configure(state="normal")
        self._lbl_aviso.configure(text="")
        estado_sim, estado_robot = self.verificador.resumen_doble(resultados)
        estado = self.verificador.resumen(resultados)
        if estado_sim == V.ERROR:
            texto, color = BANNER_SIM_ERROR
        elif estado_robot in (V.ERROR, V.AVISO) and estado_sim in (V.OK, V.AVISO) and estado == V.ERROR:
            texto, color = BANNER_SIM       # solo falla el robot: la simulación y la cámara de la PC siguen disponibles
        else:
            texto, color = BANNER[estado]
        self._banner.configure(text=texto, bg=color)
        acciones = {r.accion for r in resultados if r.accion}
        if "ssh" in acciones:
            self._btn_ssh.pack(side="left", padx=(8, 0))
        if "procesos" in acciones:
            self._btn_proc.pack(side="left", padx=(8, 0))
        if self.al_terminar:
            self.al_terminar(estado, resultados)

    # ── Arreglos ─────────────────────────────────────────────────────
    def _configurar_ssh(self):
        v = self.verificador
        clave = simpledialog.askstring(
            "Acceso SSH al robot",
            f"Contraseña de {v.usuario}@{v.host} (se usa una sola vez para copiar la llave;\n"
            "no se guarda):", show="*", parent=self._raiz)
        if not clave:
            return
        self._btn_ssh.configure(state="disabled")
        self._lbl_aviso.configure(text="Configurando acceso SSH…")

        def tarea():
            ok, msg = configurar_acceso_ssh(v.host, v.usuario, clave,
                                            lambda m: self._raiz.after(0, lambda: self._lbl_aviso.configure(text=m)))
            self._raiz.after(0, self._ssh_configurado, ok, msg)
        threading.Thread(target=tarea, daemon=True).start()

    def _ssh_configurado(self, ok, msg):
        self._btn_ssh.configure(state="normal")
        if ok:
            self.verificar()
        else:
            self._lbl_aviso.configure(text="")
            messagebox.showerror("Acceso SSH", msg, parent=self._raiz)

    def _cerrar_procesos(self):
        if not messagebox.askokcancel(
                "Cerrar procesos antiguos",
                "Se cerrarán MuJoCo y el deploy SONIC que estén en marcha (también el del robot real).\n\n"
                "Si el G1 está en movimiento, déjalo primero en una posición segura.",
                icon="warning", parent=self._raiz):
            return
        msg = V.cerrar_procesos_antiguos()
        self._lbl_aviso.configure(text=f"Cerrado: {msg}")
        self.after(1500, self.verificar)
