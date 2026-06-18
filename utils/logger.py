"""
Configuración del sistema de logging con colores para la consola.
"""

import logging
import sys
from pathlib import Path


class ColorFormatter(logging.Formatter):
    """Formatter con colores ANSI para la terminal."""

    COLORES = {
        logging.DEBUG:    "\033[36m",   # Cyan
        logging.INFO:     "\033[32m",   # Verde
        logging.WARNING:  "\033[33m",   # Amarillo
        logging.ERROR:    "\033[31m",   # Rojo
        logging.CRITICAL: "\033[35m",   # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORES.get(record.levelno, self.RESET)
        record.levelname = f"{color}{record.levelname:8}{self.RESET}"
        return super().format(record)


def configurar_logger(nivel: str = "INFO", archivo: str = "lsc_bridge.log"):
    """
    Configura el logger raíz del sistema.
    - Consola: con colores, solo INFO+
    - Archivo: sin colores, todo nivel
    """
    nivel_num = getattr(logging, nivel.upper(), logging.INFO)
    root = logging.getLogger("lsc_bridge")
    root.setLevel(logging.DEBUG)  # Capturar todo; filtrar por handler

    if root.handlers:
        return  # Ya configurado

    # ── Handler de consola ──────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(nivel_num)
    ch.setFormatter(ColorFormatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(ch)

    # ── Handler de archivo ──────────────────────────────────────
    try:
        fh = logging.FileHandler(archivo, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(fh)
    except OSError:
        root.warning("No se pudo crear el archivo de log")
