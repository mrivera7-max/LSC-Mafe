"""
Servidor WebSocket + HTTP para el sistema Voz/Texto -> Sena LSC.

Sirve el visor 3D (HTML/JS) por HTTP y transmite las secuencias de
posturas generadas a todos los clientes conectados (navegador o Quest)
via WebSocket en tiempo real.

Uso:
    python voz_a_sena/servidor.py
    Luego abre: http://localhost:8765
"""

import asyncio
import json
import logging
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.logger import configurar_logger
from voz_a_sena.traductor_texto import TraductorTextoLSC
from voz_a_sena.generador_posturas import GeneradorPosturas
from voz_a_sena.reconocimiento_voz import ReconocedorVoz

log = logging.getLogger("voz_a_sena.servidor")

WEBSOCKETS_OK = False
try:
    import websockets
    WEBSOCKETS_OK = True
except ImportError:
    log.warning("websockets no instalado. Instala con: pip install websockets")

PUERTO_HTTP = 8765
PUERTO_WS = 8766
DIR_ESTATICOS = Path(__file__).parent / "static"

_clientes_conectados = set()


# ── Servidor HTTP (sirve el visor 3D) ────────────────────────────────────────

def _iniciar_servidor_http():
    """Sirve los archivos estaticos (HTML/JS del visor 3D) en un hilo separado."""
    import os
    os.chdir(DIR_ESTATICOS)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # silenciar logs de cada request

    servidor = HTTPServer(("0.0.0.0", PUERTO_HTTP), Handler)
    log.info(f"Servidor HTTP del visor 3D en http://localhost:{PUERTO_HTTP}")
    servidor.serve_forever()


# ── Servidor WebSocket (transmite secuencias de posturas) ───────────────────

async def _manejar_cliente(websocket):
    """Registra un nuevo cliente WebSocket (navegador o Quest)."""
    _clientes_conectados.add(websocket)
    log.info(f"Cliente conectado. Total: {len(_clientes_conectados)}")
    try:
        async for mensaje in websocket:
            # Por ahora el visor no envia nada, solo recibe.
            pass
    except Exception:
        pass
    finally:
        _clientes_conectados.discard(websocket)
        log.info(f"Cliente desconectado. Total: {len(_clientes_conectados)}")


async def _transmitir_a_todos(datos: dict):
    """Envia un mensaje JSON a todos los clientes conectados."""
    if not _clientes_conectados:
        log.warning("No hay clientes conectados al visor 3D")
        return
    mensaje = json.dumps(datos, ensure_ascii=False)
    desconectados = set()
    for cliente in _clientes_conectados:
        try:
            await cliente.send(mensaje)
        except Exception:
            desconectados.add(cliente)
    _clientes_conectados.difference_update(desconectados)


class ServidorVozASena:
    """
    Orquestador principal: combina traduccion de texto/voz, generacion
    de posturas, y transmision al visor 3D via WebSocket.
    """

    def __init__(self):
        self.traductor = TraductorTextoLSC()
        self.generador = GeneradorPosturas()
        self.reconocedor_voz = ReconocedorVoz()
        self._loop_ws = None
        self._servidor_ws = None

    def procesar_texto(self, texto: str) -> dict:
        """
        Traduce un texto a secuencia de senas, genera las posturas,
        y las transmite al visor 3D conectado.

        Returns:
            Diccionario serializable con el resultado (para mostrar en GUI).
        """
        resultado_traduccion = self.traductor.traducir(texto)

        if not resultado_traduccion.secuencia_señas:
            log.warning(f"No se reconocieron senas en: '{texto}'")
            return {
                "exito": False,
                "mensaje": "No se reconocio ninguna sena en el texto",
                "no_reconocidas": resultado_traduccion.palabras_no_reconocidas,
            }

        secuencia = self.generador.generar(
            resultado_traduccion.secuencia_señas, texto_original=texto
        )
        datos = self.generador.a_dict_serializable(secuencia)
        datos["exito"] = True
        datos["no_reconocidas"] = resultado_traduccion.palabras_no_reconocidas

        # Transmitir al visor 3D (de forma asincrona, desde el hilo del servidor WS)
        if self._loop_ws and self._loop_ws.is_running():
            asyncio.run_coroutine_threadsafe(
                _transmitir_a_todos(datos), self._loop_ws
            )

        return datos

    def procesar_voz(self, microfono_idx: int = None) -> dict:
        """
        Escucha del microfono, reconoce el texto, y lo procesa igual
        que procesar_texto().
        """
        resultado_voz = self.reconocedor_voz.escuchar(microfono_idx)

        if not resultado_voz.exito:
            log.warning(f"Error de voz: {resultado_voz.error}")
            return {"exito": False, "mensaje": resultado_voz.error}

        resultado = self.procesar_texto(resultado_voz.texto)
        resultado["texto_reconocido"] = resultado_voz.texto
        return resultado

    def iniciar_servidores(self):
        """Inicia los servidores HTTP y WebSocket en hilos separados."""
        hilo_http = threading.Thread(target=_iniciar_servidor_http, daemon=True)
        hilo_http.start()

        hilo_ws = threading.Thread(target=self._iniciar_websocket, daemon=True)
        hilo_ws.start()

    def _iniciar_websocket(self):
        """Corre el servidor WebSocket en su propio event loop."""
        if not WEBSOCKETS_OK:
            log.error("websockets no instalado, el visor 3D no podra conectarse")
            return

        self._loop_ws = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop_ws)

        async def main():
            self._servidor_ws = await websockets.serve(
                _manejar_cliente, "0.0.0.0", PUERTO_WS
            )
            log.info(f"Servidor WebSocket en ws://localhost:{PUERTO_WS}")
            await asyncio.Future()  # correr indefinidamente

        self._loop_ws.run_until_complete(main())


def main():
    configurar_logger()
    log.info("=" * 60)
    log.info("  Voz/Texto a Sena LSC — Servidor")
    log.info("=" * 60)

    servidor = ServidorVozASena()
    servidor.iniciar_servidores()

    print(f"\nAbre en tu navegador: http://localhost:{PUERTO_HTTP}")
    print("Escribe un texto para traducir, o presiona Enter vacio para usar voz.")
    print("Escribe 'salir' para terminar.\n")

    while True:
        try:
            entrada = input("Texto a traducir (o Enter para hablar): ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if entrada.lower() == "salir":
            break

        if entrada == "":
            resultado = servidor.procesar_voz()
        else:
            resultado = servidor.procesar_texto(entrada)

        if resultado.get("exito"):
            señas = [p["sena"] for p in resultado["pasos"]]
            print(f"  -> Senas: {' -> '.join(señas)}")
            if resultado.get("no_reconocidas"):
                print(f"  (palabras no reconocidas: {resultado['no_reconocidas']})")
        else:
            print(f"  [ERROR] {resultado.get('mensaje')}")

    print("\nFinalizando servidor...")


if __name__ == "__main__":
    main()
