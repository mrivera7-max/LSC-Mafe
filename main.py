"""
LSC Bridge - Reconocimiento de Lengua de Señas Colombiana para Unitree G1
==========================================================================
Punto de entrada principal del sistema.

Uso:
    python main.py                    # Modo GUI completo
    python main.py --modo consola     # Solo consola, sin GUI
    python main.py --sin-robot        # Sin conexión al G1 (modo prueba)
"""

import argparse
import sys
import logging
from pathlib import Path

# Agregar directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import configurar_logger
from utils.config import Configuracion


def parsear_argumentos():
    parser = argparse.ArgumentParser(
        description="LSC Bridge — Señas Colombianas para Unitree G1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--modo",
        choices=["gui", "consola"],
        default="gui",
        help="Modo de ejecución (default: gui)",
    )
    parser.add_argument(
        "--sin-robot",
        action="store_true",
        help="Ejecutar sin conectar al robot (modo demostración)",
    )
    parser.add_argument(
        "--camara",
        type=int,
        default=0,
        help="Índice de la cámara a usar (default: 0)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Archivo de configuración (default: config.json)",
    )
    parser.add_argument(
        "--log-nivel",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Nivel de logging (default: INFO)",
    )
    return parser.parse_args()


def main():
    args = parsear_argumentos()

    # Configurar logger global
    configurar_logger(nivel=args.log_nivel)
    log = logging.getLogger("lsc_bridge")

    log.info("=" * 60)
    log.info("  LSC Bridge v1.0 — Lengua de Señas Colombiana")
    log.info("  Integración: Unitree G1")
    log.info("=" * 60)

    # Cargar configuración
    config = Configuracion(args.config)
    config.camara_idx = args.camara
    config.robot_activo = not args.sin_robot

    if args.sin_robot:
        log.warning("Modo demostración: robot deshabilitado")

    if args.modo == "gui":
        log.info("Iniciando interfaz gráfica...")
        try:
            from gui.ventana_principal import VentanaPrincipal
            app = VentanaPrincipal(config)
            app.ejecutar()
        except ImportError as e:
            log.error(f"Error al cargar GUI: {e}")
            log.info("Verifica que tkinter esté instalado: sudo apt install python3-tk")
            sys.exit(1)
    else:
        log.info("Iniciando modo consola...")
        from utils.modo_consola import ModoConsola
        consola = ModoConsola(config)
        consola.ejecutar()


if __name__ == "__main__":
    main()
