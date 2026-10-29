"""
Prepara desde la app el servidor de cámara (teleimager-server) del PC2 del G1.

Hace por SSH lo que antes se hacía a mano en la «Terminal A»:

  1. Busca qué /dev/videoN es la cámara externa (OBSBOT, o una RealSense: usa su nodo de color) — el
     número cambia entre reinicios.
  2. Escribe cam_config_server.yaml con la RealSense (cabeza) desactivada y la externa activa.
  3. (Re)inicia teleimager-server en segundo plano si hace falta.
  4. Espera a que el puerto ZMQ de la cámara responda.

Requiere acceso SSH sin contraseña al PC2 (una sola vez:  ssh-copy-id unitree@192.168.123.164)
o, si está instalado `sshpass`, indicar la clave con --g1-clave.
"""

import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("robot.servidor_camara_g1")

HOST_DEFECTO = "192.168.123.164"
USUARIO_DEFECTO = "unitree"
DIR_TELEIMAGER = "~/teleimager"
PUERTO_EXTERNA = 55556
NOMBRE_EXTERNA = "OBSBOT"
LOG_REMOTO = "/tmp/teleimager_lsc.log"
FORMA = (540, 960)               # alto, ancho de la imagen que publica la cámara externa
FORMA_REALSENSE = (480, 640)     # la RealSense (color YUYV) trabaja bien a 640x480 @ 30


class ErrorCamaraG1(RuntimeError):
    """Fallo al preparar la cámara; el mensaje se muestra tal cual al usuario."""


def parsear_dispositivos(texto: str) -> List[Tuple[str, List[str]]]:
    """Salida de `v4l2-ctl --list-devices` → [(nombre, [/dev/videoN, ...]), ...]."""
    bloques: List[Tuple[str, List[str]]] = []
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        if not linea[0].isspace():
            bloques.append((linea.strip().rstrip(":"), []))
        elif bloques:
            m = re.search(r"/dev/video\d+", linea)
            if m:
                bloques[-1][1].append(m.group(0))
    return bloques


def elegir_video(texto: str, clave: str = NOMBRE_EXTERNA) -> str:
    """Devuelve el número (str) del primer /dev/video de la cámara externa."""
    bloques = parsear_dispositivos(texto)
    for nombre, videos in bloques:
        if clave.lower() in nombre.lower() and videos:
            return videos[0].replace("/dev/video", "")
    for nombre, videos in bloques:      # sin coincidencia: la primera que no sea RealSense ni Tegra
        if videos and not re.search(r"realsense|tegra", nombre, re.IGNORECASE):
            return videos[0].replace("/dev/video", "")
    vistos = "; ".join(f"{n}: {' '.join(v) or '-'}" for n, v in bloques) or "ninguno"
    raise ErrorCamaraG1(f"No encuentro la cámara externa «{clave}» en el robot. Dispositivos: {vistos}. "
                        "¿Está conectada por USB al PC2?")


def es_realsense(nombre: str) -> bool:
    return bool(re.search(r"realsense", nombre, re.IGNORECASE))


def buscar_dispositivo(texto: str, clave: str = NOMBRE_EXTERNA) -> Tuple[str, List[str]]:
    """(nombre, [/dev/videoN...]) de la cámara externa.

    Orden: 1) el dispositivo cuyo nombre contiene `clave`; 2) el primero que no sea RealSense ni Tegra;
    3) una RealSense (si hay varias, la última: la recién conectada suele quedar al final)."""
    bloques = parsear_dispositivos(texto)
    coinciden = [(n, v) for n, v in bloques if v and clave.lower() in n.lower()]
    if coinciden:
        return coinciden[-1] if es_realsense(coinciden[-1][0]) else coinciden[0]
    for nombre, videos in bloques:
        if videos and not re.search(r"realsense|tegra", nombre, re.IGNORECASE):
            return nombre, videos
    reales = [(n, v) for n, v in bloques if v and es_realsense(n)]
    if reales:
        return reales[-1]
    vistos = "; ".join(f"{n}: {' '.join(v) or '-'}" for n, v in bloques) or "ninguno"
    raise ErrorCamaraG1(f"No encuentro la cámara externa «{clave}» en el robot. Dispositivos: {vistos}. "
                        "¿Está conectada por USB al PC2?")


_INTERNAS = re.compile(r"tegra|platform:|vi-output|nvhost", re.IGNORECASE)


def ordenar_grupos(texto: str, clave: str = NOMBRE_EXTERNA) -> List[Tuple[str, List[str]]]:
    """Todas las cámaras USB del robot en el orden en que conviene probarlas (sin las internas de Tegra):
    primero la que coincide con `clave`, luego cualquier otra que no sea RealSense, al final las RealSense."""
    grupos = [(n, v) for n, v in parsear_dispositivos(texto) if v and not _INTERNAS.search(n)]

    def rango(g):
        n = g[0]
        if clave and clave.lower() in n.lower():
            return 0
        return 2 if es_realsense(n) else 1
    return sorted(grupos, key=rango)


_NO_COLOR = ("Z16", "GREY", "Y8I", "Y12I", "Y16")
_COLOR = ("YUYV", "MJPG", "RGB3", "BGR3", "NV12")


def elegir_nodo_color(formatos: str) -> Optional[str]:
    """Salida de un bucle `@@ /dev/videoN` + `v4l2-ctl --list-formats` -> número del nodo de COLOR.

    Una RealSense crea varios /dev/video (profundidad, infrarrojo, color, metadatos); sirve el que
    ofrece YUYV/MJPG y no es de profundidad ni infrarrojo."""
    for trozo in formatos.split("@@")[1:]:
        lineas = trozo.strip().splitlines()
        if not lineas:
            continue
        m = re.search(r"/dev/video(\d+)", lineas[0])
        if not m:
            continue
        codigos = set(re.findall(r"'([A-Za-z0-9 ]{4})'", "\n".join(lineas[1:])))
        codigos = {c.strip() for c in codigos}
        if codigos & set(_COLOR) and not codigos & set(_NO_COLOR):
            return m.group(1)
    return None


SCRIPT_REMOTO = "~/lsc_camara_server.py"
LOG_LIGERO = "/tmp/lsc_camara.log"


def bloques_realsense(texto: str) -> List[Tuple[str, List[str]]]:
    """Todas las RealSense listadas, la última conectada primero (suele ser la externa)."""
    return [(n, v) for n, v in reversed(parsear_dispositivos(texto)) if v and es_realsense(n)]


def nodos_color_realsense(salida: str) -> List[int]:
    """Del bucle `@@ /dev/videoN` + `v4l2-ctl --list-formats`: nodos que ofrecen color (YUYV/MJPG...)."""
    nodos = []
    for trozo in salida.split("@@")[1:]:
        lineas = trozo.strip().splitlines()
        m = re.search(r"/dev/video(\d+)", lineas[0]) if lineas else None
        if not m:
            continue
        codigos = {c.strip() for c in re.findall(r"'([A-Za-z0-9 ]{4})'", "\n".join(lineas[1:]))}
        if codigos & set(_COLOR) and not codigos & set(_NO_COLOR):
            nodos.append(int(m.group(1)))
    return nodos


def generar_yaml(video_id: str, puerto: int = PUERTO_EXTERNA, forma: Tuple[int, int] = FORMA) -> str:
    """cam_config_server.yaml con la RealSense apagada y la cámara externa publicando por ZMQ."""
    def apagada(vid: str) -> str:
        return (f"  enable_zmq: false\n  enable_webrtc: false\n  type: opencv\n  binocular: false\n"
                f"  fps: 30\n  image_shape: [480, 640]\n  video_id: \"{vid}\"\n"
                f"  serial_number: null\n  physical_path: null\n")
    return (
        "head_camera:\n" + apagada("2") + "\n"
        "left_wrist_camera:\n"
        f"  enable_zmq: true\n  zmq_port: {puerto}\n  enable_webrtc: false\n  type: opencv\n"
        f"  binocular: false\n  fps: 30\n  image_shape: [{forma[0]}, {forma[1]}]\n"
        f"  video_id: \"{video_id}\"\n  serial_number: null\n  physical_path: null\n\n"
        "right_wrist_camera:\n" + apagada("1")
    )


class ServidorCamaraG1:
    def __init__(self, host: str = HOST_DEFECTO, usuario: str = USUARIO_DEFECTO,
                 clave: Optional[str] = None, puerto: int = PUERTO_EXTERNA,
                 nombre_camara: str = NOMBRE_EXTERNA, directorio: str = DIR_TELEIMAGER):
        self.host, self.usuario, self.clave = host, usuario, clave
        self.puerto, self.nombre_camara, self.directorio = puerto, nombre_camara, directorio

    # ── SSH ──────────────────────────────────────────────────────────
    def _argv_ssh(self, remoto: str) -> Tuple[List[str], dict]:
        base = ["ssh", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ServerAliveInterval=5", f"{self.usuario}@{self.host}", remoto]
        env = dict(os.environ)
        if self.clave and shutil.which("sshpass"):
            env["SSHPASS"] = self.clave
            return ["sshpass", "-e"] + base, env
        return base[:1] + ["-o", "BatchMode=yes"] + base[1:], env

    def _ssh(self, remoto: str, entrada: Optional[str] = None, timeout: float = 20.0) -> str:
        if shutil.which("ssh") is None:
            raise ErrorCamaraG1("No está instalado el cliente SSH (sudo apt install openssh-client).")
        argv, env = self._argv_ssh(remoto)
        try:
            r = subprocess.run(argv, input=entrada, capture_output=True, text=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ErrorCamaraG1(f"El robot ({self.host}) no respondió por SSH a tiempo ({timeout:.0f} s) en: "
                                f"{(remoto or entrada or '').strip()[:70]!r}")
        if r.returncode == 255:
            err = "\n".join(l for l in (r.stderr or "").splitlines()
                            if l.strip() and not l.lstrip().startswith(("**", "Warning:"))).strip()
            if "denied" in err.lower():
                raise ErrorCamaraG1(
                    f"El robot pide contraseña por SSH. Haz esto una sola vez en una terminal de la PC:\n"
                    f"   ssh-copy-id {self.usuario}@{self.host}\n"
                    f"(o instala sshpass y usa --g1-clave).")
            raise ErrorCamaraG1(f"No se pudo conectar por SSH con {self.host}: {err or 'sin respuesta'}. "
                                "¿El robot está encendido y en la misma red?")
        return r.stdout

    # ── Pasos ────────────────────────────────────────────────────────
    def puerto_abierto(self, espera: float = 1.0) -> bool:
        try:
            with socket.create_connection((self.host, self.puerto), timeout=espera):
                return True
        except OSError:
            return False

    def _yaml_actual(self) -> str:
        try:
            return self._ssh(f"cat {self.directorio}/cam_config_server.yaml 2>/dev/null")
        except ErrorCamaraG1:
            raise

    modo = "ligero"      # "ligero": servidor propio (rápido, solo abre la cámara elegida); "teleimager": el del robot

    def preparar(self, avisar: Optional[Callable[[str], None]] = None, espera_s: float = 30.0) -> int:
        """Deja la cámara externa publicando. Devuelve el puerto ZMQ. Lanza ErrorCamaraG1."""
        def paso(msg: str):
            log.info(msg)
            if avisar:
                avisar(msg)

        paso("Conectando por SSH con el robot…")
        listado = self._ssh("timeout 40 v4l2-ctl --list-devices 2>&1", timeout=50)
        if not listado.strip():
            raise ErrorCamaraG1("El robot no devolvió la lista de cámaras (¿falta v4l2-ctl?).")
        if self.modo != "ligero":
            nombre, videos = buscar_dispositivo(listado, self.nombre_camara)
            video = videos[0].replace("/dev/video", "")
            forma = FORMA
            paso(f"Cámara externa «{nombre}» en /dev/video{video}.")
        else:
            # Detecta TODAS las cámaras conectadas por USB (no solo la que se llame como `nombre_camara`) y
            # prueba cada una hasta que alguna entregue imagen: la preferida por nombre, luego cualquier otra.
            grupos = ordenar_grupos(listado, self.nombre_camara)
            if not grupos:
                raise ErrorCamaraG1("El robot no tiene cámaras USB conectadas (solo las internas de Tegra). "
                                    "¿Está enchufada la cámara externa al PC2?")
            paso("Cámaras USB del robot: " + "; ".join(f"{n} ({' '.join(v)})" for n, v in grupos))
            fallos, realsense_probada = [], False
            for nombre, videos in grupos:
                try:
                    if es_realsense(nombre):
                        if realsense_probada:
                            continue
                        realsense_probada = True
                        paso(f"Probando la RealSense «{nombre}»…")
                        return self._preparar_realsense(listado, paso, espera_s=min(espera_s, 45.0))
                    nodos = [int(v.replace("/dev/video", "")) for v in videos]
                    paso(f"Probando «{nombre}» (nodos {nodos})…")
                    return self._lanzar_ligero(nodos, FORMA, paso, espera_s=min(espera_s, 45.0))
                except ErrorCamaraG1 as e:
                    fallos.append(f"{nombre}: {str(e).splitlines()[0]}")
                    paso(f"«{nombre}» no entrega imagen; pruebo la siguiente.")
            raise ErrorCamaraG1("Ninguna cámara del robot entregó imagen. " + " | ".join(fallos))

        nuevo = generar_yaml(video, self.puerto, forma)
        igual = self._yaml_actual().strip() == nuevo.strip()
        if igual and self.puerto_abierto():
            paso("El servidor de cámara ya estaba listo.")
            return self.puerto

        if not igual:
            paso("Configurando la cámara externa en el servidor del robot…")
            d = self.directorio
            self._ssh(f"cd {d} && {{ [ -f cam_config_server.yaml.lsc_bak ] || "
                      f"cp cam_config_server.yaml cam_config_server.yaml.lsc_bak 2>/dev/null; }}; "
                      f"cat > cam_config_server.yaml", entrada=nuevo)

        paso("Iniciando teleimager-server en el robot…")
        # El script va por stdin (`bash -s`): si fuera argumento de ssh, la línea de comandos del shell remoto
        # contendría «teleimager-server» y `pkill -f` mataría su propio shell (ssh devolvía 255).
        try:
            self._ssh("bash -s",
                      entrada="exec >/dev/null 2>&1\n"
                              "pkill -f '[t]eleimager-server'; sleep 1\n"
                              "pkill -9 -f '[t]eleimager-server'; sleep 1\n"
                              f"cd {self.directorio} && PATH=$HOME/.local/bin:$PATH "
                              f"setsid nohup teleimager-server > {LOG_REMOTO} 2>&1 < /dev/null &\n"
                              "sleep 0.5\n",
                      timeout=12)
        except ErrorCamaraG1 as e:
            # si solo fue lentitud de SSH (el servidor ya arrancó), el sondeo del puerto lo confirma
            if "a tiempo" not in str(e):
                raise
            log.warning(f"SSH tardó al lanzar teleimager-server; compruebo el puerto igualmente: {e}")
        fin = time.time() + espera_s
        while time.time() < fin:
            if self.puerto_abierto():
                paso("Cámara del robot lista.")
                return self.puerto
            time.sleep(1.0)
        cola = ""
        try:
            cola = self._ssh(f"tail -n 8 {LOG_REMOTO}")
        except ErrorCamaraG1:
            pass
        raise ErrorCamaraG1(f"El servidor de cámara no abrió el puerto {self.puerto} en {espera_s:.0f} s."
                            + (f"\nÚltimas líneas del robot ({LOG_REMOTO}):\n{cola.strip()}" if cola else ""))

    def _preparar_realsense(self, listado: str, paso, espera_s: float) -> int:
        """RealSense: servidor ligero propio (no teleimager). Prueba los nodos de color de TODAS las
        RealSense del robot, la última conectada primero, hasta que una entregue imagen."""
        bloques = bloques_realsense(listado)
        candidatos: List[int] = []
        for nombre, videos in bloques:
            salida = self._ssh("for d in " + " ".join(videos) + "; do echo \"@@ $d\"; "
                               "v4l2-ctl -d $d --list-formats 2>/dev/null; done", timeout=30)
            color = nodos_color_realsense(salida)
            paso(f"{nombre}: nodo(s) de color {color or 'ninguno'}.")
            candidatos += [n for n in color if n not in candidatos]
        if not candidatos:
            raise ErrorCamaraG1("Ninguna RealSense del robot ofrece un nodo de color.")

        return self._lanzar_ligero(candidatos, FORMA_REALSENSE, paso, espera_s)

    def _lanzar_ligero(self, candidatos: List[int], forma: Tuple[int, int], paso, espera_s: float) -> int:
        """Copia y arranca remoto_camara_lsc.py en el robot probando `candidatos` (nodos /dev/videoN)."""
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remoto_camara_lsc.py")
        with open(ruta, encoding="utf-8") as f:
            codigo = f.read()
        paso("Copiando el servidor de cámara ligero al robot…")
        self._ssh(f"cat > {SCRIPT_REMOTO}", entrada=codigo)

        paso("Liberando cámaras y arrancando el servidor ligero…")
        nodos = ",".join(str(n) for n in candidatos)
        try:
            self._ssh("bash -s",
                      entrada="exec >/dev/null 2>&1\n"
                              "pkill -9 -f '[t]eleimager-server'; pkill -9 -f '[l]sc_camara_server'; sleep 1\n"
                              f"setsid nohup python3 {SCRIPT_REMOTO} --port {self.puerto} --nodes {nodos} "
                              f"--w {forma[1]} --h {forma[0]} > {LOG_LIGERO} 2>&1 < /dev/null &\n"
                              "sleep 0.5\n",
                      timeout=12)
        except ErrorCamaraG1 as e:
            if "a tiempo" not in str(e):
                raise
        fin = time.time() + espera_s
        while time.time() < fin:
            if self.puerto_abierto():
                fuente = ""
                try:
                    fuente = next((l for l in self._ssh(f"cat {LOG_LIGERO}").splitlines() if "FUENTE" in l), "")
                except ErrorCamaraG1:
                    pass
                paso(f"Cámara del robot lista. {fuente.strip()}")
                return self.puerto
            time.sleep(1.0)
        cola = ""
        try:
            cola = self._ssh(f"tail -n 12 {LOG_LIGERO}")
        except ErrorCamaraG1:
            pass
        raise ErrorCamaraG1(f"La cámara no entregó imagen (nodos probados: {nodos}).\n"
                            f"Log del robot ({LOG_LIGERO}):\n{cola.strip()}")

    def detener(self):
        """Apaga teleimager-server en el robot (libera la cámara)."""
        try:
            self._ssh("pkill -f '[t]eleimager-server'; pkill -f '[l]sc_camara_server'; sleep 1; "
                      "pkill -9 -f '[t]eleimager-server'; pkill -9 -f '[l]sc_camara_server'; echo ok", timeout=10)
        except ErrorCamaraG1 as e:
            log.warning(f"No se pudo detener teleimager-server: {e}")


def configurar_acceso_ssh(host: str, usuario: str, clave: str,
                          avisar: Optional[Callable[[str], None]] = None, espera_s: float = 45.0) -> Tuple[bool, str]:
    """Deja SSH sin contraseña hacia el PC2: crea una llave en la PC si falta y la copia con
    ssh-copy-id usando la clave dada (solo se usa para esto; no se guarda ni se escribe en logs)."""
    import fcntl, pty, signal, termios, struct   # noqa: E401
    ssh_dir = os.path.expanduser("~/.ssh")
    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
    if not any(os.path.exists(os.path.join(ssh_dir, f)) for f in ("id_ed25519.pub", "id_rsa.pub", "id_ecdsa.pub")):
        if shutil.which("ssh-keygen") is None:
            return False, "No está instalado ssh-keygen (sudo apt install openssh-client)."
        if avisar:
            avisar("Creando llave SSH en la PC…")
        r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f",
                            os.path.join(ssh_dir, "id_ed25519")], capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"No se pudo crear la llave SSH: {r.stderr.strip()}"
    if shutil.which("ssh-copy-id") is None:
        return False, "No está instalado ssh-copy-id (sudo apt install openssh-client)."
    if avisar:
        avisar("Copiando la llave al robot…")
    maestro, esclavo = pty.openpty()
    proc = subprocess.Popen(["ssh-copy-id", "-o", "StrictHostKeyChecking=accept-new",
                             "-o", "ConnectTimeout=8", f"{usuario}@{host}"],
                            stdin=esclavo, stdout=esclavo, stderr=esclavo, close_fds=True,
                            preexec_fn=lambda: (os.setsid(), fcntl.ioctl(0, termios.TIOCSCTTY, 0)))
    os.close(esclavo)
    buf, enviada, fin = b"", False, time.time() + espera_s
    import select
    try:
        while time.time() < fin and proc.poll() is None:
            if select.select([maestro], [], [], 0.3)[0]:
                try:
                    datos = os.read(maestro, 2048)
                except OSError:
                    break
                buf = (buf + datos)[-4000:]
                if not enviada and b"assword" in buf:
                    os.write(maestro, clave.encode() + b"\n")
                    enviada = True
                    buf = b""
                elif enviada and b"assword" in buf:        # volvió a pedirla: clave incorrecta
                    proc.kill()
                    return False, "La contraseña del robot no es correcta."
        if proc.poll() is None:
            proc.kill()
            return False, "ssh-copy-id no terminó a tiempo."
        ok = proc.returncode == 0
    finally:
        try:
            os.close(maestro)
        except OSError:
            pass
    if ok:
        return True, "Acceso SSH sin contraseña configurado."
    texto = buf.decode(errors="replace").strip().splitlines()
    return False, "ssh-copy-id falló: " + (texto[-1] if texto else f"código {proc.returncode}")
