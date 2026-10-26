#!/usr/bin/env python3
"""Hace que robot/conector_sonic.py mueva las manos Inspire por Modbus TCP (robot/inspire_modbus.py)
en lugar de DDS (que exige inspire_sdkpy/cyclonedds, imposibles en el Python 3.14 de la app).
Repetible; deja copia robot/conector_sonic.py.bak_modbus."""
import shutil
import sys
from pathlib import Path

p = Path(__file__).resolve().parent / "robot" / "conector_sonic.py"
t = p.read_text(encoding="utf-8")
if "SalidaInspireModbus" in t:
    sys.exit("Ya estaba aplicado.")

IMP_ANTES = "from robot.mano_inspire import SalidaInspireDDS, a_dex3, cargar_formas"
IMP_NUEVO = ("from robot.mano_inspire import SalidaInspireDDS, a_dex3, cargar_formas\n"
             "from robot.inspire_modbus import SalidaInspireModbus")
BLQ_ANTES = '''            self.salida_inspire = SalidaInspireDDS(self.interfaz_dds)
            if self.salida_inspire.abrir():
                log.info("Manos Inspire: publicando en rt/inspire_hand/ctrl/{l,r}")
            else:'''
BLQ_NUEVO = '''            self.salida_inspire = SalidaInspireModbus(self.interfaz_dds)
            if self.salida_inspire.abrir():
                log.info(f"Manos Inspire por Modbus TCP: {self.salida_inspire.estado()}")
                if self.salida_inspire.error:
                    self.aviso_manos = self.salida_inspire.error
            else:'''
for a in (IMP_ANTES, BLQ_ANTES):
    if t.count(a) != 1:
        sys.exit("Tu conector_sonic.py no tiene el bloque esperado; no se modificó nada.")
shutil.copy2(p, p.with_suffix(".py.bak_modbus"))
p.write_text(t.replace(IMP_ANTES, IMP_NUEVO).replace(BLQ_ANTES, BLQ_NUEVO), encoding="utf-8")
print("Listo: las manos Inspire van por Modbus TCP (derecha 192.168.123.211, izquierda 192.168.123.210).")
