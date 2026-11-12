"""
Interfaz grafica del sistema Voz/Texto -> Sena LSC.

Permite escribir texto o usar el microfono, y muestra la traduccion
mientras la sena se ejecuta en el G1 de 29 GDL dentro de MuJoCo (politica
SONIC, ver robot/conector_sonic.py) o en el G1 compartido de la pestana Camara.
"""

import logging
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.logger import configurar_logger
from voz_a_sena.servidor import ServidorVozASena
from robot.conector_sonic import ConectorSonic
from robot.lanzador_sonic import LanzadorSonic
from voz_a_sena.reconocimiento_voz import ReconocedorVoz

log = logging.getLogger("voz_a_sena.gui")


class VentanaVozASena:
    TITULO = "LSC UDI — Voz/Texto a Sena | Robot G1"

    def __init__(self, modo_real: bool = False, escala=None, factor_tiempo=None,
                 vel_max=None, confianza_min: float = 0.8,
                 mic: str = "pc", mic_iface_ip: str = None,
                 robot_host: str = "192.168.123.164", robot_usuario: str = "unitree", robot_clave: str = None,
                 gr00t_dir: str = None, interfaz: str = None,
                 mujoco_pos=(0, 0), mujoco_tam=None, integrado: bool = True,
                 manos: str = "auto", auto_enlazar_camara: bool = True):
        """
        modo_real      True: el deploy SONIC mueve el G1 FÍSICO (no MuJoCo). Exige confirmar
                       el checklist de seguridad y limita amplitud y velocidad de las señas.
        escala, factor_tiempo, vel_max   ver ConectorSonic (None = valores por defecto del modo).
        confianza_min  confianza mínima de una seña detectada por la cámara para ejecutarla.
        manos          dedos de la mano Inspire RH56DFTP-2R: "auto" (sim -> manos Dex3 de MuJoCo,
                       real -> Inspire por DDS), "dex3", "inspire", "ambas" o "ninguna" (ver ConectorSonic).
        auto_enlazar_camara  si el simulador ya está iniciado («Init done») y llega una seña de la
                       cámara, se enlaza solo y la ejecuta. Nunca con el robot real.
        mic            "pc", "g1" (micrófono del robot, sin respaldo) o "auto" (G1 y, si no
                       llega audio, la PC).
        """
        self.modo_real = modo_real
        self._opciones_sonic = dict(escala=escala, factor_tiempo=factor_tiempo, vel_max=vel_max, manos=manos,
                                    interfaz_dds=interfaz or "")
        self.auto_enlazar_camara = auto_enlazar_camara
        self._pendiente_camara = None                 # (seña, instante) recibida mientras se enlazaba
        self.confianza_min = confianza_min
        self.confianza_fuente = None                  # callable -> confianza vigente (Configuración, sin reiniciar)
        self.motivo_camara = ""                       # por qué se descartó la última seña de la cámara
        self.reintentar_camara = False                # True si el descarte es pasajero (robot ocupado)
        self._avisos_camara = {}                      # (seña, motivo) -> instante del último aviso
        self.mic = mic
        self._mic_iface_ip = mic_iface_ip
        self._robot_ssh = (robot_host, robot_usuario, robot_clave)
        self._escuchando_fuente = None                # "pc" | "robot" mientras se escucha
        self._id_escucha = 0                          # identifica la escucha vigente (para cancelar/vencer)
        self._mic_robot_estado = "sin probar"         # "sin probar" | "preparando" | "listo" | "error"
        self._rec_robot = None                        # reconocedor del micrófono del robot (cuando está listo)
        self._servidor_audio = None
        self.lanzador = LanzadorSonic(gr00t_dir, interfaz, mujoco_pos, mujoco_tam, integrado=integrado)
        self.consola = None
        self._mostrar_pestana_consola = lambda: None
        self._lanzado = {False: False, True: False}   # ¿ya se abrió la sim / el deploy real?
        self._esperando = False
        self.servidor = ServidorVozASena(fuente_mic="pc", mic_fallback_pc=False, mic_iface_ip=mic_iface_ip)
        self._rec_pc = ReconocedorVoz(fuente="pc")
        try:
            self._mic_pc_ok = bool(self._rec_pc.listar_microfonos())
        except Exception:  # noqa: BLE001
            self._mic_pc_ok = False
        self.robot = None  # ConectorG1 compartido; lo asigna app_unificada.py
        self.abrir_datos = lambda nombre=None: None  # la asigna app_unificada.py (lleva a la pestaña Datos)
        self.sonic = None  # ConectorSonic (MuJoCo, 29 GDL); se crea al enlazar con la simulación o el robot real
        self._raiz = None
        self._contenedor = None
        self._escuchando = False
        self._conectando_sonic = False
        self._lock_sonic = threading.Lock()  # las secuencias se ejecutan una tras otra

    def ejecutar(self):
        """Modo autónomo: crea su propia ventana y mainloop."""
        self._raiz = tk.Tk()
        self._raiz.title(self.TITULO)
        self._raiz.geometry("640x560")
        self._raiz.configure(bg="#1a1a2e")
        self._raiz.protocol("WM_DELETE_WINDOW", self._cerrar)

        self._contenedor = self._raiz
        self._construir_ui()
        self._agregar_log_inicial()
        self._raiz.mainloop()

    def montar_en(self, contenedor, raiz):
        """Construye la UI de voz dentro de un contenedor externo (pestaña),
        compartiendo la raíz de la app unificada. No crea tk.Tk() ni mainloop.

        Ya no se levanta el servidor HTTP/WebSocket del visor 3D del navegador:
        las señas se ejecutan en MuJoCo (botón «Abrir simulación MuJoCo»).
        """
        self._raiz = raiz
        self._contenedor = contenedor
        self._construir_ui()
        self._agregar_log_inicial()
        log.info("Panel de voz montado en pestaña")

    @property
    def _nombre_destino(self) -> str:
        return "G1 real" if self.modo_real else "MuJoCo G1"

    def _agregar_log_inicial(self):
        self._agregar_log("Pulsa «🖥 Abrir simulación MuJoCo» para practicar sin riesgo, o "
                          "«🤖 Conectar robot real» para mover el G1 físico (docs/g1_real.md).")

    def _ajustar_mic(self):
        """Con el robot real y --mic robot/auto, deja listo el micrófono del robot al conectar.

        Los dos micrófonos son independientes: el botón «Hablar (micrófono de la PC)» usa siempre la PC y
        «Hablar (micrófono del robot)» usa siempre el del robot, sin respaldo entre ellos."""
        if self.mic in ("robot", "auto") and self.modo_real and self._mic_robot_estado != "listo":
            self._agregar_log("Buscando el micrófono conectado al robot (SSH)…")
            self._preparar_mic_robot_async()

    def _preparar_mic_robot_async(self, escuchar_despues: bool = False):
        if self._mic_robot_estado == "preparando":
            return
        self._poner_estado_mic_robot("preparando")
        threading.Thread(target=self._preparar_mic_robot, args=(escuchar_despues,), daemon=True).start()

    def _poner_estado_mic_robot(self, estado: str, detalle: str = ""):
        self._mic_robot_estado = estado
        texto, color = {
            "sin probar": ("Micrófono del robot: se detecta al pulsar el botón", "#60a5fa"),
            "preparando": ("Micrófono del robot: buscándolo en el PC2…", "#60a5fa"),
            "listo": ("Micrófono del robot: listo" + (f" ({detalle})" if detalle else ""), "#22c55e"),
            "error": ("Micrófono del robot: " + (detalle or "no disponible"), "#ef4444"),
        }[estado]
        if hasattr(self, "_lbl_estado_mic_robot"):
            self._lbl_estado_mic_robot.configure(text=texto, fg=color)
            self._colorear_botones_voz()

    def _preparar_mic_robot(self, escuchar_despues: bool = False):
        """Detecta el micrófono del PC2, arranca su servidor de audio y deja listo su reconocedor."""
        host, usuario, clave = self._robot_ssh
        try:
            if self.mic == "g1":                       # modo antiguo: audio del robot por multicast
                self._rec_robot = ReconocedorVoz(fuente="g1", fallback_pc=False, iface_ip=self._mic_iface_ip)
                detalle = "multicast"
            else:
                from robot.servidor_audio_g1 import ServidorAudioG1
                srv = ServidorAudioG1(host, usuario, clave)
                self._servidor_audio = srv

                def avisar(msg):
                    self._raiz.after(0, self._agregar_log, f"Micrófono del robot: {msg}")
                srv.preparar(avisar)
                self._rec_robot = ReconocedorVoz(fuente="robot", fallback_pc=False, host_robot=host)
                detalle = "USB del PC2"
        except Exception as e:  # noqa: BLE001
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            self._raiz.after(0, self._poner_estado_mic_robot, "error", msg)
            self._raiz.after(0, self._agregar_log, f"[AVISO] Micrófono del robot no disponible: {msg}")
            return
        self._raiz.after(0, self._poner_estado_mic_robot, "listo", detalle)
        self._raiz.after(0, self._agregar_log, "Micrófono del robot listo.")
        if escuchar_despues:
            self._raiz.after(0, self._iniciar_escucha, "robot")

    def cerrar(self):
        """Cierra la conexión con MuJoCo sin destruir la raíz."""
        sonic, self.sonic = self.sonic, None
        if sonic is not None:
            try:
                sonic.cerrar()
            except Exception:
                pass

    # ── Conexión con MuJoCo (SONIC) ──────────────────────────────────

    def _conectado(self) -> bool:
        return self.sonic is not None and self.sonic.conectado

    def _refrescar_botones(self):
        """Rotula los botones según lo que ya está abierto y si hay conexión."""
        conectado = self._conectado() or self._conectando_sonic
        busy = conectado or self._esperando
        con = self._conectado()
        # azul = sin conectar; verde = conectado y funcionando (rojo queda para detener)
        if con and not self.modo_real:
            self._boton_sim.configure(text="✔ Simulación MuJoCo conectada", bg="#22c55e", fg="#052e16",
                                      disabledforeground="#052e16", state="disabled")
        else:
            self._boton_sim.configure(
                text="▶ Enlazar simulación" if self._lanzado[False] else "🖥 Abrir simulación MuJoCo",
                bg="#2563eb", fg="white", state="disabled" if busy else "normal")
        if con and self.modo_real:
            self._boton_real.configure(text="✔ Robot real conectado", bg="#22c55e", fg="#052e16",
                                       disabledforeground="#052e16", state="disabled")
        else:
            self._boton_real.configure(
                text="▶ Enlazar robot real" if self._lanzado[True] else "🤖 Conectar robot real",
                bg="#2563eb", fg="white", state="disabled" if busy else "normal")
        self._boton_sonic.configure(state="normal" if self._conectado() else "disabled")
        self._boton_cerrar_sim.configure(
            state="normal" if (self._lanzado[False] or self._lanzado[True]) and not self._conectando_sonic
            else "disabled")
        self._boton_consola.configure(state="normal" if self.lanzador.integrado else "disabled")

    def _pulsar(self, real: bool):
        """Botón de simulación (real=False) o de robot real (real=True)."""
        if self._conectando_sonic or self._esperando or self._conectado():
            return
        self.modo_real = real
        self._ajustar_mic()
        destino = self._nombre_destino
        if not self._lanzado[real]:
            if real and not messagebox.askokcancel(
                    "Robot real",
                    "Se abrirá el deploy SONIC en modo REAL "
                    f"(interfaz {self.lanzador.interfaz}).\n\n"
                    "El robot debe estar en el arnés, con espacio libre y la persona de la "
                    "tecla O lista. Se abrirá la consola de la app: si el deploy pide confirmar, "
                    "responde ahí (botón ↵ Enter).\n"
                    "Todavía NO se mueve nada: antes de enviar START se pedirá otra confirmación.",
                    icon="warning", parent=self._raiz):
                self._agregar_log("Robot real cancelado.")
                return
            if not self.lanzador.lanzar(real):
                self._agregar_log(f"[ERROR] {self.lanzador.error}")
                self._agregar_log("Ábrelo a mano con estos comandos y luego pulsa de nuevo el botón:\n"
                                  + self.lanzador.comandos_manuales(real))
                self._lanzado[real] = True   # el siguiente clic solo enlaza
                self._refrescar_botones()
                return
            self._lanzado[real] = True
            if real and self.lanzador.integrado:
                self._abrir_consola("deploy_real")
            self._esperando = True
            self._refrescar_botones()
            if self.lanzador.hay_log(real):
                self._agregar_log(("Iniciado el deploy REAL" if real else "Iniciados MuJoCo y el deploy")
                                  + (" (consola de la app, botón «🖥 Consola»)" if self.lanzador.integrado
                                     else " (terminales)")
                                  + ". Si pide confirmar, hazlo ahí; cuando termine de "
                                  + ("iniciar (Init done) se mostrará el checklist de seguridad."
                                     if real else "iniciar (Init done) se enlaza solo."))
                self._lbl_sonic.configure(text="● Esperando «Init done»…", fg="#fde68a")
                self._espera_ini = __import__("time").time()
                self._sondear_deploy(real)
            else:
                self._esperando = False
                self._refrescar_botones()
                self._agregar_log("Cuando veas «Init done» en el deploy, pulsa «▶ Enlazar».")
        else:
            self._enlazar(real)

    def _sondear_deploy(self, real: bool):
        """Cada segundo mira el log del deploy hasta ver «Init done» (máx. 120 s)."""
        import time
        if not self._esperando:
            return
        if self.lanzador.deploy_listo(real):
            self._esperando = False
            self._agregar_log("Deploy listo (Init done).")
            self._enlazar(real)
        elif time.time() - self._espera_ini > 120:
            self._esperando = False
            self._lbl_sonic.configure(text=f"● {self._nombre_destino} sin conectar", fg="#bfdbfe")
            self._refrescar_botones()
            self._agregar_log("No vi «Init done» en 2 min. Revisa la consola (🖥 Consola); "
                              "si ya inició, pulsa «▶ Enlazar».")
        else:
            self._raiz.after(1000, self._sondear_deploy, real)

    def _enlazar(self, real: bool):
        """Conecta por ZMQ con el deploy ya iniciado (envía START)."""
        self._esperando = False
        self.modo_real = real
        if real and not self._confirmar_robot_real():
            self._agregar_log("Conexión con el G1 real cancelada (el deploy sigue abierto).")
            self._lbl_sonic.configure(text=f"● {self._nombre_destino} sin conectar", fg="#bfdbfe")
            self._refrescar_botones()
            return
        self._conectando_sonic = True
        self._boton_sim.configure(state="disabled")
        self._boton_real.configure(state="disabled")
        self._lbl_sonic.configure(text="● Conectando…", fg="#fde68a")
        threading.Thread(target=self._conectar_sonic, daemon=True).start()

    def _cerrar_todo(self):
        """Desconecta (si hace falta) y cierra MuJoCo y los deploys que abrió la app."""
        if self._conectando_sonic:
            return
        if self._lanzado[True] and not messagebox.askokcancel(
                "Cerrar robot real",
                "Se detendrá el deploy del robot real (Ctrl+C).\n\n"
                "Asegúrate de que el G1 esté en una posición segura (arnés / control remoto).",
                icon="warning", parent=self._raiz):
            return
        self._esperando = False
        real, sim = self._lanzado[True], self._lanzado[False]

        def tarea():
            if self._conectado():
                with self._lock_sonic:
                    self.cerrar()
            partes = []
            if sim:
                partes.append(self.lanzador.cerrar_simulacion())
            if real:
                partes.append(self.lanzador.cerrar_deploy_real() or "deploy real (ciérralo en su terminal)")
            self._raiz.after(0, self._todo_cerrado, ", ".join(p for p in partes if p))
        threading.Thread(target=tarea, daemon=True).start()

    def _todo_cerrado(self, msg: str):
        self._lanzado = {False: False, True: False}
        self._lbl_sonic.configure(text="● Sin conectar", fg="#bfdbfe")
        self._boton_parar.configure(state="disabled")
        self._refrescar_botones()
        self._agregar_log(f"Cerrado: {msg or 'nada que cerrar'}.")

    def _abrir_consola(self, clave: str = None):
        """Muestra la pestaña «Consola» (salida del simulador/deploy y botones Enter, O, Ctrl+C)."""
        from voz_a_sena.consola_sonic import ConsolaSonic
        if clave is None:
            clave = "deploy_real" if self.modo_real else "deploy_sim"
        if self.consola is None:                       # app sin pestañas (modo autónomo): ventana propia
            ventana = tk.Toplevel(self._raiz)
            ventana.title("LSC · Consola del simulador / deploy")
            ventana.geometry("900x520+120+100")
            self.consola = ConsolaSonic(ventana, self.lanzador, clave)
            self.consola.pack(fill="both", expand=True)
            self._mostrar_pestana_consola = lambda: ventana.deiconify() or ventana.lift()
        self.consola.seleccionar(clave)
        self._mostrar_pestana_consola()

    def montar_consola(self, contenedor, mostrar):
        """Crea el panel de consola dentro de `contenedor` (una pestaña); `mostrar()` la selecciona."""
        from voz_a_sena.consola_sonic import ConsolaSonic
        self.consola = ConsolaSonic(contenedor, self.lanzador)
        self.consola.pack(fill="both", expand=True)
        self._mostrar_pestana_consola = mostrar

    def cerrar_procesos(self) -> str:
        """Al salir de la app: detiene lo que ella abrió (simulador y deploys)."""
        return self.lanzador.cerrar_todo()

    def _alternar_sonic(self):
        """Botón «Desconectar»."""
        if self._conectado():
            threading.Thread(target=self._desconectar_sonic, daemon=True).start()

    def _confirmar_robot_real(self) -> bool:
        """Checklist de seguridad antes de enviar `start` al deploy que mueve el G1 físico."""
        return messagebox.askokcancel(
            "Conectar con el G1 REAL",
            "Antes de continuar confirma TODO esto:\n\n"
            "  1. El G1 está en el arnés / sostenido, con espacio libre alrededor.\n"
            "  2. El deploy corre en modo REAL y mostró «Init Done».\n"
            "  3. Hay una persona lista para el paro de emergencia: botón «O» de la consola\n"
            "     (o tecla O en la terminal del deploy) y el control remoto de Unitree a mano.\n"
            "  4. Nadie está cerca de los brazos del robot.\n\n"
            "Al aceptar se envía START: la política tomará el control del robot.",
            icon="warning", parent=self._raiz,
        )

    def _conectar_sonic(self):
        opciones = {k: v for k, v in self._opciones_sonic.items() if v is not None}
        opciones["interfaz_dds"] = self.lanzador.interfaz if self.modo_real else ""
        sonic = ConectorSonic(real=self.modo_real, **opciones)
        if not sonic.conectar():
            self._raiz.after(0, self._sonic_fallo, sonic.error or "error desconocido")
            return
        sonic.iniciar_control()
        self.sonic = sonic
        self._raiz.after(0, self._sonic_listo)

    def _desconectar_sonic(self):
        with self._lock_sonic:  # espera a que termine la seña en curso
            self.cerrar()
        self._raiz.after(0, self._sonic_desconectado)

    def _sonic_listo(self):
        self._conectando_sonic = False
        self._refrescar_botones()
        self._lbl_sonic.configure(text=f"● {self._nombre_destino} conectado (:5556)", fg="#86efac")
        self._boton_parar.configure(state="normal")
        if self.modo_real:
            s = self.sonic
            self._agregar_log(f"START enviado al deploy. Amplitud {s.escala:.0%}, tiempos x{s.factor_tiempo:g}, "
                              f"vel. máx. {s.vel_max} rad/s. Botón PARAR = paro por software; "
                              f"botón «O» de la consola = paro de emergencia.")
        else:
            self._agregar_log("Publicando hacia el deploy SONIC. Si el G1 sigue colgado, "
                              "pulsa 9 en la ventana de MuJoCo para soltarlo.")
        s = self.sonic
        if s is not None:
            donde = {"dex3": "manos Dex3 de MuJoCo (índice, medio y pulgar; sin anular ni meñique)",
                     "inspire": "manos Inspire RH56DFTP-2R por DDS (rt/inspire_hand/ctrl)",
                     "ambas": "manos Dex3 (MuJoCo) e Inspire (DDS)", "ninguna": "sin movimiento de dedos"}.get(
                         getattr(s, "manos", None))
            if donde:
                self._agregar_log(f"Dedos: {donde}.")
            if getattr(s, "aviso_manos", None):
                self._agregar_log(f"[AVISO] {s.aviso_manos}")
        pend, self._pendiente_camara = self._pendiente_camara, None
        if pend and __import__("time").time() - pend[1] < 8.0:
            self.ejecutar_desde_camara(pend[0])

    def _sonic_fallo(self, mensaje: str):
        self._conectando_sonic = False
        self._refrescar_botones()
        self._lbl_sonic.configure(text=f"● {self._nombre_destino} sin conectar", fg="#bfdbfe")
        self._agregar_log(f"[ERROR] No se pudo conectar: {mensaje}  (¿el deploy está en «Init done»? "
                          "Pulsa de nuevo el botón.)")

    def _sonic_desconectado(self):
        self._refrescar_botones()
        self._lbl_sonic.configure(text=f"● {self._nombre_destino} sin conectar", fg="#bfdbfe")
        self._boton_parar.configure(state="disabled")
        self._agregar_log(f"{self._nombre_destino} desconectado.")

    def parar(self):
        """Parada por software: envía `stop` al deploy y bloquea nuevas señas."""
        sonic = self.sonic
        if sonic is None or not sonic.conectado:
            return
        threading.Thread(target=sonic.parada_emergencia, daemon=True).start()
        self._boton_parar.configure(state="disabled")
        self._lbl_sonic.configure(text=f"● {self._nombre_destino}: PARADO", fg="#fca5a5")
        self._lanzado[self.modo_real] = False   # tras PARAR hay que reiniciar el deploy
        self._agregar_log("PARADA enviada al deploy. Para reanudar: pulsa Desconectar, «✖ Cerrar todo» "
                          "y vuelve a abrir desde el botón.")

    def _construir_ui(self):
        c_fondo = "#1a1a2e"
        c_panel = "#16213e"
        c_texto = "#e2e8f0"
        c_acento = "#2563eb"
        c_verde = "#22c55e"
        c_azul = "#2563eb"

        cont = self._contenedor

        # Encabezado
        encabezado = tk.Frame(cont, bg=c_acento, height=60)
        encabezado.pack(fill="x")
        tk.Label(encabezado, text="◈ LSC UDI", bg=c_acento, fg="white",
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=16, pady=14)
        tk.Label(encabezado, text="Voz / Texto  →  Sena  →  Robot G1", bg=c_acento,
                 fg="#bfdbfe", font=("Segoe UI", 10)).pack(side="left", pady=14)

        self._boton_sonic = tk.Button(
            encabezado, text="■ Desconectar", bg="#dc2626", fg="white",
            relief="flat", font=("Segoe UI", 9, "bold"), padx=10,
            cursor="hand2", command=self._alternar_sonic, state="disabled",
        )
        self._boton_sonic.pack(side="right", padx=(8, 16), pady=14)

        self._boton_parar = tk.Button(
            encabezado, text="■ PARAR", bg="#dc2626", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=10, cursor="hand2",
            state="disabled", command=self.parar,
        )
        self._boton_parar.pack(side="right", padx=(8, 0), pady=14)

        self._lbl_sonic = tk.Label(
            encabezado, text="● Sin conectar", bg=c_acento, fg="#bfdbfe",
            font=("Segoe UI", 9),
        )
        self._lbl_sonic.pack(side="right", pady=14)

        # Barra de robot: simulación o robot real
        barra = tk.Frame(cont, bg=c_fondo)
        barra.pack(fill="x", padx=16, pady=(12, 0))
        self._boton_sim = tk.Button(
            barra, text="🖥 Abrir simulación MuJoCo", bg=c_azul, fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2",
            command=lambda: self._pulsar(False))
        self._boton_sim.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._boton_real = tk.Button(
            barra, text="🤖 Conectar robot real", bg=c_azul, fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2",
            command=lambda: self._pulsar(True))
        self._boton_real.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self._boton_cerrar_sim = tk.Button(
            barra, text="✖ Cerrar simulación", bg="#dc2626", fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2", state="disabled",
            command=self._cerrar_todo)
        self._boton_cerrar_sim.pack(side="left", padx=(0, 6))
        self._boton_consola = tk.Button(
            barra, text="🖥 Ver consola", bg="#334155", fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2",
            command=lambda: self._abrir_consola())
        self._boton_consola.pack(side="left")

        # Panel de entrada de texto
        panel_texto = tk.Frame(cont, bg=c_panel, padx=20, pady=16)
        panel_texto.pack(fill="x", padx=16, pady=(16, 8))

        tk.Label(panel_texto, text="ESCRIBE UN TEXTO PARA TRADUCIR", bg=c_panel,
                 fg="#64748b", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        frame_entrada = tk.Frame(panel_texto, bg=c_panel)
        frame_entrada.pack(fill="x", pady=(8, 0))

        self._entrada_texto = tk.Entry(
            frame_entrada, bg="#0d1117", fg=c_texto, insertbackground=c_texto,
            relief="flat", font=("Segoe UI", 12), width=40,
        )
        self._entrada_texto.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self._entrada_texto.bind("<Return>", lambda e: self._enviar_texto())

        tk.Button(
            frame_entrada, text="Traducir", bg=c_acento, fg="white",
            relief="flat", font=("Segoe UI", 10, "bold"), padx=16,
            cursor="hand2", command=self._enviar_texto,
        ).pack(side="left")

        # Panel de voz
        panel_voz = tk.Frame(cont, bg=c_panel, padx=20, pady=16)
        panel_voz.pack(fill="x", padx=16, pady=8)

        tk.Label(panel_voz, text="O USA TU VOZ", bg=c_panel,
                 fg="#64748b", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        fila_voz = tk.Frame(panel_voz, bg=c_panel)
        fila_voz.pack(fill="x", pady=(8, 0))
        fila_voz.columnconfigure(0, weight=1, uniform="mic")
        fila_voz.columnconfigure(1, weight=1, uniform="mic")
        self._boton_voz = tk.Button(
            fila_voz, text=self._TEXTO_PC, bg=c_azul, fg="white", relief="flat",
            font=("Segoe UI", 11, "bold"), pady=10, cursor="hand2",
            command=lambda: self._iniciar_escucha("pc"))
        self._boton_voz.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._boton_voz_robot = tk.Button(
            fila_voz, text=self._TEXTO_ROBOT, bg=c_azul, fg="white", relief="flat",
            font=("Segoe UI", 11, "bold"), pady=10, cursor="hand2",
            command=lambda: self._iniciar_escucha("robot"))
        self._boton_voz_robot.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._lbl_estado_mic_robot = tk.Label(panel_voz, text="", bg=c_panel, fg="#64748b",
                                              font=("Segoe UI", 9), anchor="w", justify="left", wraplength=900)
        self._lbl_estado_mic_robot.pack(fill="x", pady=(6, 0))
        self._poner_estado_mic_robot(self._mic_robot_estado)

        # Resultado de la ultima traduccion
        panel_resultado = tk.Frame(cont, bg=c_panel, padx=20, pady=16)
        panel_resultado.pack(fill="x", padx=16, pady=8)

        tk.Label(panel_resultado, text="ULTIMA TRADUCCION", bg=c_panel,
                 fg="#64748b", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self._lbl_secuencia = tk.Label(
            panel_resultado, text="—", bg=c_panel, fg=c_verde,
            font=("Segoe UI", 16, "bold"), wraplength=560, justify="left",
        )
        self._lbl_secuencia.pack(anchor="w", pady=(6, 0))

        self._lbl_no_reconocidas = tk.Label(
            panel_resultado, text="", bg=c_panel, fg="#94a3b8",
            font=("Segoe UI", 9), wraplength=560, justify="left",
        )
        self._lbl_no_reconocidas.pack(anchor="w", pady=(4, 0))

        self._frame_ensenar = tk.Frame(panel_resultado, bg=c_panel)
        self._frame_ensenar.pack(fill="x", pady=(8, 0))
        self._texto_pendiente_ensenar = ""
        tk.Button(
            panel_resultado, text="📁  Enseñar / entrenar señas (capturar con cámara o cargar datos)",
            bg="#374151", fg="#e2e8f0", relief="flat", font=("Segoe UI", 9, "bold"), padx=10, pady=4,
            cursor="hand2", command=lambda: self.abrir_datos(None),
        ).pack(anchor="w", pady=(6, 0))

        # Log de actividad
        panel_log = tk.Frame(cont, bg=c_panel, padx=20, pady=16)
        panel_log.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        tk.Label(panel_log, text="ACTIVIDAD", bg=c_panel,
                 fg="#64748b", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self._log_texto = scrolledtext.ScrolledText(
            panel_log, bg="#0d1117", fg="#c2c0b6", font=("Consolas", 9),
            relief="flat", wrap="word", height=8,
        )
        self._log_texto.pack(fill="both", expand=True, pady=(8, 0))

    def _enviar_texto(self):
        texto = self._entrada_texto.get().strip()
        if not texto:
            return
        self._entrada_texto.delete(0, "end")
        self._agregar_log(f"Texto enviado: \"{texto}\"")
        threading.Thread(target=self._procesar_texto, args=(texto,), daemon=True).start()

    def _procesar_texto(self, texto: str):
        resultado = self.servidor.procesar_texto(texto)
        self._raiz.after(0, self._mostrar_resultado, resultado)

    _TEXTO_PC = "🎤  Hablar (micrófono de la PC)"
    _TEXTO_ROBOT = "🤖  Hablar (micrófono del robot)"

    LIMITE_ESCUCHA_S = 30          # si el micrófono no responde en este tiempo, se libera el botón

    def _iniciar_escucha(self, fuente: str = "pc"):
        """fuente "pc": micrófono de esta PC.  "robot": micrófono conectado al robot (sin respaldo en la PC).

        Mientras escucha, el botón está en rojo («Escuchando… pulsa para detener»): pulsarlo cancela."""
        if self._escuchando:
            if fuente == self._escuchando_fuente:
                self._cancelar_escucha("Escucha detenida.")
            return
        if fuente == "robot" and self._mic_robot_estado != "listo":
            if self._mic_robot_estado == "preparando":
                self._agregar_log("El micrófono del robot todavía se está preparando…")
                return
            self._agregar_log("Buscando el micrófono conectado al robot (SSH)…")
            self._preparar_mic_robot_async(escuchar_despues=True)
            return
        self._escuchando = True
        self._escuchando_fuente = fuente
        self._id_escucha += 1
        id_ = self._id_escucha
        boton = self._boton_voz_robot if fuente == "robot" else self._boton_voz
        boton.configure(text="■  Escuchando… (pulsa para detener)", bg="#dc2626", fg="white")
        self._agregar_log("Escuchando... (micrófono " + ("del robot" if fuente == "robot" else "de la PC") + ")")
        threading.Thread(target=self._procesar_voz, args=(fuente, id_), daemon=True).start()
        self._raiz.after(self.LIMITE_ESCUCHA_S * 1000, self._vencio_escucha, id_)

    def _vencio_escucha(self, id_: int):
        if self._escuchando and id_ == self._id_escucha:
            self._cancelar_escucha(f"[ERROR] El micrófono no respondió en {self.LIMITE_ESCUCHA_S} s.")

    def _cancelar_escucha(self, mensaje: str):
        self._id_escucha += 1                          # el resultado tardío de ese hilo se ignora
        self._escuchando = False
        self._escuchando_fuente = None
        self._restaurar_botones_voz()
        self._agregar_log(mensaje)

    def _colorear_botones_voz(self):
        """Azul = fuente sin verificar; verde = lista y funcionando; (rojo = escuchando, se pone al escuchar)."""
        pc_ok = bool(getattr(self, "_mic_pc_ok", True))
        robot_ok = self._mic_robot_estado == "listo"
        for boton, texto, ok in ((self._boton_voz, self._TEXTO_PC, pc_ok),
                                 (self._boton_voz_robot, self._TEXTO_ROBOT, robot_ok)):
            if self._escuchando and ((boton is self._boton_voz_robot) == (self._escuchando_fuente == "robot")):
                continue
            boton.configure(text=texto, bg="#22c55e" if ok else "#2563eb", fg="#052e16" if ok else "white")

    def _restaurar_botones_voz(self):
        self._colorear_botones_voz()

    def _procesar_voz(self, fuente: str = "pc", id_: int = 0):
        try:
            self.servidor.reconocedor_voz = self._rec_robot if fuente == "robot" else self._rec_pc
            resultado = self.servidor.procesar_voz()
        except Exception as e:  # noqa: BLE001
            log.exception("Error al escuchar")
            resultado = {"exito": False, "mensaje": f"Error al escuchar: {type(e).__name__}: {e}"}
        if fuente == "robot" and not resultado.get("exito") and self._servidor_audio is not None \
                and "paquetes" in str(resultado.get("mensaje", "")):
            cola = self._servidor_audio.log_remoto()       # por qué no llega audio: el log del servidor del robot
            self._raiz.after(0, self._agregar_log, f"Log del audio en el robot:\n{cola}")
        self._raiz.after(0, self._finalizar_escucha, resultado, id_)

    def _finalizar_escucha(self, resultado: dict, id_: int = None):
        if id_ is not None and id_ != self._id_escucha:
            return                                     # escucha cancelada o vencida: se descarta
        self._escuchando = False
        self._escuchando_fuente = None
        self._restaurar_botones_voz()

        if resultado.get("texto_reconocido"):
            self._agregar_log(f"Voz reconocida ({resultado.get('fuente_mic', 'pc')}): "
                              f"\"{resultado['texto_reconocido']}\"")
        elif not resultado.get("exito") and resultado.get("mensaje"):
            self._agregar_log(f"Voz: {resultado['mensaje']}")

        self._mostrar_resultado(resultado)

    def _mostrar_resultado(self, resultado: dict):
        # Limpiar el area de "ensenar" antes de redibujar
        for widget in self._frame_ensenar.winfo_children():
            widget.destroy()

        if resultado.get("exito"):
            señas = [p["sena"] for p in resultado["pasos"]]
            self._lbl_secuencia.configure(text="  →  ".join(señas))
            self._agregar_log(f"Secuencia generada: {' -> '.join(señas)}")

            # Ejecutar en el G1: primero MuJoCo/SONIC; si no, el G1 de la pestaña Cámara
            if self.sonic is not None and self.sonic.conectado:
                threading.Thread(target=self._enviar_a_sonic, args=(señas,),
                                 daemon=True).start()
            elif self.robot is not None and self.robot.conectado:
                threading.Thread(target=self._enviar_al_robot, args=(señas,),
                                 daemon=True).start()
            else:
                self._agregar_log(f"Sin {self._nombre_destino} conectado: pulsa «🖥 Abrir simulación MuJoCo» o «🤖 Conectar robot real»")

            no_reconocidas = resultado.get("no_reconocidas", [])
            if no_reconocidas:
                self._lbl_no_reconocidas.configure(
                    text=f"Palabras no traducidas: {', '.join(no_reconocidas)}"
                )
                self._mostrar_boton_ensenar(" ".join(no_reconocidas))
            else:
                self._lbl_no_reconocidas.configure(text="")
        else:
            self._lbl_secuencia.configure(text="(sin senas reconocidas)")
            mensaje = resultado.get("mensaje", "Error desconocido")
            self._lbl_no_reconocidas.configure(text=mensaje)
            self._agregar_log(f"[ERROR] {mensaje}")

            no_reconocidas = resultado.get("no_reconocidas", [])
            if no_reconocidas:
                self._mostrar_boton_ensenar(" ".join(no_reconocidas))

    def _enviar_a_sonic(self, señas):
        """Ejecuta la secuencia en el G1 de MuJoCo y vuelve a la postura de pie."""
        with self._lock_sonic:
            sonic = self.sonic
            if sonic is None or not sonic.conectado:
                return
            for s in señas:
                sonic.enviar_seña(s)
            sonic.reposo(1.2)
        self._raiz.after(0, self._agregar_log,
                         f"{self._nombre_destino}: ejecutada {' -> '.join(señas)}")

    def _rechazar_camara(self, seña, motivo: str, reintentar: bool = False) -> bool:
        """Descarta la seña de la cámara AVISANDO por qué (antes se perdía en silencio)."""
        import time
        self.motivo_camara, self.reintentar_camara = motivo, reintentar
        ahora, clave = time.time(), (seña.nombre, motivo)
        if ahora - self._avisos_camara.get(clave, 0.0) > 3.0:
            self._avisos_camara[clave] = ahora
            self._raiz.after(0, self._agregar_log,
                             f"Cámara: «{seña.nombre}» ({seña.confianza:.0%}) detectada pero NO se ejecuta: {motivo}")
        return False

    def ejecutar_desde_camara(self, seña) -> bool:
        """Ejecuta en el G1 (MuJoCo o real) la seña que detectó la pestaña Cámara.

        `seña` tiene .nombre y .confianza. Devuelve True si se aceptó. Si se descarta (no hay
        conexión, confianza baja, robot parado o aún ejecutando otra seña: con el G1 real no se
        acumulan órdenes) deja el motivo en `motivo_camara`, lo escribe en la actividad y, si es
        pasajero (`reintentar_camara`), la pestaña Cámara vuelve a intentarlo unos segundos.
        """
        sonic = self.sonic
        minimo = self.confianza_min
        if self.confianza_fuente is not None:
            try:
                minimo = float(self.confianza_fuente())
            except (TypeError, ValueError):
                pass
        if seña.confianza < minimo:
            return self._rechazar_camara(seña, f"confianza {seña.confianza:.0%} menor que «Confianza para ejecutar» "
                                               f"({minimo:.0%}, Configuración → Robot G1)")
        if sonic is None or not sonic.conectado:
            self._auto_enlazar_desde_camara(seña)
            if self._conectando_sonic or self._esperando:
                return self._rechazar_camara(seña, f"{self._nombre_destino} enlazando…", reintentar=True)
            if self.modo_real:
                return self._rechazar_camara(seña, "el robot real no está conectado: pulsa «🤖 Conectar robot real» "
                                                   "en la pestaña Voz / Texto → Seña")
            return self._rechazar_camara(seña, "la simulación no está enlazada: pulsa «🖥 Abrir simulación MuJoCo» "
                                               "en la pestaña Voz / Texto → Seña")
        if sonic.detenido:
            return self._rechazar_camara(seña, "el robot está PARADO (botón PARAR): desconecta, «✖ Cerrar todo» y "
                                               "vuelve a conectar")
        if not self._lock_sonic.acquire(blocking=False):
            return self._rechazar_camara(seña, "el robot sigue ejecutando otra seña", reintentar=True)
        self._lock_sonic.release()
        self.motivo_camara, self.reintentar_camara = "", False

        def _tarea():
            if not self._lock_sonic.acquire(blocking=False):
                return
            try:
                if sonic.conectado and not sonic.detenido:
                    self._raiz.after(0, self._agregar_log,
                                     f"{self._nombre_destino}: ejecutando «{seña.nombre}» detectada por cámara "
                                     f"({seña.confianza:.0%})…")
                    sonic.enviar_seña(seña.nombre)
                    sonic.reposo(1.2)
            except Exception as e:  # noqa: BLE001
                log.exception("Error al ejecutar la seña de la cámara")
                self._raiz.after(0, self._agregar_log, f"[ERROR] «{seña.nombre}» no se pudo ejecutar: {e}")
                return
            finally:
                self._lock_sonic.release()
            self._raiz.after(0, self._agregar_log,
                             f"{self._nombre_destino}: seña detectada por cámara «{seña.nombre}» "
                             f"({seña.confianza:.0%}) ejecutada")
        threading.Thread(target=_tarea, daemon=True).start()
        return True

    def _auto_enlazar_desde_camara(self, seña):
        """Simulador ya iniciado (Init done) pero sin enlazar: se enlaza y luego ejecuta la seña.
        Con el robot real NUNCA se enlaza solo (exige el checklist de seguridad)."""
        if (not self.auto_enlazar_camara or self.modo_real or self._lanzado[True] or not self._lanzado[False]
                or self._conectando_sonic or self._esperando or not self.lanzador.deploy_listo(False)):
            return
        self._pendiente_camara = (seña, __import__("time").time())
        self._raiz.after(0, self._agregar_log, f"Cámara: seña «{seña.nombre}» — enlazando con el simulador…")
        self._raiz.after(0, self._enlazar, False)

    def estado_replica(self):
        """(texto, color) para la barra de la pestaña Cámara: ¿se replican allí las señas detectadas?"""
        destino = self._nombre_destino
        if self._conectado():
            return f"{destino}: replicando", "#86efac"
        if self._conectando_sonic or self._esperando:
            return f"{destino}: enlazando…", "#fde68a"
        if self._lanzado[False] and not self.modo_real and self.auto_enlazar_camara:
            return "Simulador iniciado: se enlaza con la primera seña", "#fde68a"
        return "Simulador: sin enlazar (pestaña Voz → «🖥 Abrir simulación»)", "#60a5fa"

    def _enviar_al_robot(self, señas):
        """Ejecuta la secuencia en el G1, una seña tras otra (hilo aparte)."""
        for s in señas:
            self.robot.enviar_seña(s)
        self._raiz.after(0, self._agregar_log,
                         f"Robot G1: ejecutada {' -> '.join(señas)}")

    def _mostrar_boton_ensenar(self, frase_pendiente: str):
        """Muestra el boton 'Ensenar' para mapear una frase no reconocida a una sena."""
        self._texto_pendiente_ensenar = frase_pendiente
        tk.Button(
            self._frame_ensenar,
            text=f"+ Ensenar \"{frase_pendiente}\"",
            bg="#7c3aed", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=10, pady=4,
            cursor="hand2", command=self._abrir_dialogo_ensenar,
        ).pack(anchor="w")

    def _abrir_dialogo_ensenar(self):
        """Abre una ventana pequena para elegir a que sena mapear la frase."""
        señas_validas = sorted(self.servidor.traductor.señas_validas)

        dialogo = tk.Toplevel(self._raiz)
        dialogo.title("Ensenar nuevo sinonimo")
        dialogo.configure(bg="#16213e")
        dialogo.resizable(False, False)
        dialogo.grab_set()

        tk.Label(
            dialogo, text=f'Cuando diga o escriba:\n"{self._texto_pendiente_ensenar}"',
            bg="#16213e", fg="#e2e8f0", font=("Segoe UI", 11, "bold"),
            justify="left", padx=20, pady=(20, 10),
        ).pack(anchor="w")

        tk.Label(
            dialogo, text="Es otra forma de decir una seña que ya existe. Elige cuál:",
            bg="#16213e", fg="#94a3b8", font=("Segoe UI", 10),
            padx=20,
        ).pack(anchor="w")

        frame_botones = tk.Frame(dialogo, bg="#16213e", padx=20, pady=12)
        frame_botones.pack()

        for i, sena in enumerate(señas_validas):
            fila, col = divmod(i, 4)
            tk.Button(
                frame_botones, text=sena, bg="#2563eb", fg="white",
                relief="flat", font=("Segoe UI", 10, "bold"),
                padx=14, pady=8, cursor="hand2", width=10,
                command=lambda s=sena: self._confirmar_ensenanza(s, dialogo),
            ).grid(row=fila, column=col, padx=4, pady=4)

        tk.Label(dialogo, text="¿O es una seña nueva?", bg="#16213e", fg="#94a3b8",
                 font=("Segoe UI", 10), padx=20).pack(anchor="w")
        tk.Button(
            dialogo, text="➕  Seña nueva: capturar con la cámara o cargar datos",
            bg="#7c3aed", fg="white", relief="flat", font=("Segoe UI", 10, "bold"),
            padx=14, pady=8, cursor="hand2",
            command=lambda: (dialogo.destroy(), self.abrir_datos(self._texto_pendiente_ensenar)),
        ).pack(pady=(4, 8))
        tk.Button(
            dialogo, text="Cancelar", bg="#374151", fg="#e2e8f0",
            relief="flat", font=("Segoe UI", 9), padx=12, pady=6,
            cursor="hand2", command=dialogo.destroy,
        ).pack(pady=(0, 16))

    def _confirmar_ensenanza(self, sena: str, dialogo: tk.Toplevel):
        frase = self._texto_pendiente_ensenar
        exito = self.servidor.traductor.agregar_sinonimo(frase, sena)
        if exito:
            self._agregar_log(f"Aprendido: \"{frase}\" -> {sena}")
        dialogo.destroy()

    def _agregar_log(self, texto: str):
        import time
        hora = time.strftime("%H:%M:%S")
        self._log_texto.insert("end", f"{hora}  {texto}\n")
        self._log_texto.see("end")

    def _cerrar(self):
        log.info("Cerrando aplicacion...")
        self.cerrar()
        self.cerrar_procesos()
        self._raiz.quit()
        self._raiz.destroy()


def main():
    configurar_logger()
    log.info("=" * 60)
    log.info("  LSC UDI — Voz/Texto a Sena (Robot G1)")
    log.info("=" * 60)

    app = VentanaVozASena()
    app.ejecutar()


if __name__ == "__main__":
    main()