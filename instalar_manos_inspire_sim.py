#!/usr/bin/env python3
"""Pone las manos Inspire RH56DFTP-2R en el simulador de SONIC (un solo simulador, sin gemelo).

    python instalar_manos_inspire_sim.py --gr00t ~/DURVVIN/SIM2SIM/blea/GR00T-WholeBodyControl
    python instalar_manos_inspire_sim.py --gr00t ... --quitar      # volver al Dex3 original

Hace (con copia .bak_lsc de cada archivo tocado, y es repetible):
  1. genera scene_43dof_inspire.xml + g1_29dof_inspire.xml + mallas (construir_escena)
  2. copia inspire_hook.py a gear_sonic/utils/mujoco_sim/
  3. base_sim.py: 2 llamadas al hook (al cargar la escena y en cada sim_step)
  4. wbc_configs/g1_29dof_sonic_model12.yaml: ROBOT_SCENE -> scene_43dof_inspire.xml
  5. robot/conector_sonic.py: una linea final que publica las manos hacia el simulador
"""
import argparse
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
MS = Path("gear_sonic/utils/mujoco_sim")
YAML = MS / "wbc_configs/g1_29dof_sonic_model12.yaml"
MARCA = "# [LSC-inspire]"

B1_ANTES = "        self.init_scene()\n"
B1_NUEVO = (B1_ANTES + f"        {MARCA}\n"
            "        from gear_sonic.utils.mujoco_sim.inspire_hook import ManosInspire\n"
            "        self.manos_inspire = ManosInspire.crear(self)\n")
B2_ANTES = "    def sim_step(self):\n"
B2_NUEVO = (B2_ANTES + f"        {MARCA}\n"
            "        if getattr(self, 'manos_inspire', None) is not None:\n"
            "            self.manos_inspire.paso(self)\n")
LINEA_CONECTOR = ("\n# [LSC-inspire] publica las manos Inspire hacia el simulador MuJoCo\n"
                  "from robot.enlace_inspire_sim import instalar as _instalar_inspire\n"
                  "_instalar_inspire(ConectorSonic)\n")


def respaldo(p: Path):
    b = p.with_suffix(p.suffix + ".bak_lsc")
    if not b.exists():
        shutil.copy2(p, b)


def restaurar(p: Path):
    b = p.with_suffix(p.suffix + ".bak_lsc")
    if b.exists():
        shutil.copy2(b, p)
        print(f"  restaurado {p}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gr00t", required=True)
    ap.add_argument("--quitar", action="store_true")
    a = ap.parse_args()
    gr = Path(a.gr00t).expanduser()
    base_sim, yml = gr / MS / "base_sim.py", gr / YAML
    conector = AQUI / "robot" / "conector_sonic.py"
    for p in (base_sim, yml):
        if not p.exists():
            sys.exit(f"No existe {p}: revisa --gr00t")

    if a.quitar:
        for p in (base_sim, yml, conector):
            restaurar(p)
        return

    # 1) escena
    sys.path.insert(0, str(AQUI))
    from robot.sim_inspire.construir_escena import generar
    esc, q, info = generar(gr)
    print(f"1. Escena creada: {esc.name} (geometrias Dex3 ocultas: {q}, manos: {info})")

    # 2) hook
    shutil.copy2(AQUI / "robot/sim_inspire/inspire_hook.py", gr / MS / "inspire_hook.py")
    print("2. inspire_hook.py copiado")

    # 3) base_sim.py
    t = base_sim.read_text()
    if MARCA in t:
        print("3. base_sim.py ya estaba parcheado")
    else:
        if t.count(B1_ANTES) != 1 or t.count(B2_ANTES) != 1:
            sys.exit("3. base_sim.py no tiene la forma esperada (version distinta de SONIC); no se toco nada mas.")
        respaldo(base_sim)
        base_sim.write_text(t.replace(B1_ANTES, B1_NUEVO).replace(B2_ANTES, B2_NUEVO))
        print("3. base_sim.py parcheado")

    # 4) yaml
    ty = yml.read_text()
    if "scene_43dof_inspire.xml" in ty:
        print("4. yaml ya apuntaba a la escena Inspire")
    elif "scene_43dof.xml" in ty:
        respaldo(yml)
        lineas = []
        for l in ty.splitlines(keepends=True):
            if l.lstrip().startswith("ROBOT_SCENE:") and "scene_43dof.xml" in l:
                l = l.replace("scene_43dof.xml", "scene_43dof_inspire.xml")
            lineas.append(l)
        yml.write_text("".join(lineas))
        print("4. yaml: ROBOT_SCENE -> scene_43dof_inspire.xml")
    else:
        sys.exit("4. No encuentro ROBOT_SCENE con scene_43dof.xml en el yaml.")

    # 5) conector de la app
    if conector.exists():
        tc = conector.read_text()
        if "enlace_inspire_sim" in tc:
            print("5. conector_sonic.py ya estaba enlazado")
        elif "class ConectorSonic" not in tc:
            print("5. AVISO: conector_sonic.py sin clase ConectorSonic; enlaza a mano (ver docs)")
        else:
            respaldo(conector)
            conector.write_text(tc.rstrip("\n") + "\n" + LINEA_CONECTOR)
            print("5. conector_sonic.py enlazado")
    else:
        print("5. AVISO: no encuentro robot/conector_sonic.py junto a este script")
    print("\nListo. Cierra MuJoCo si estaba abierto y vuelve a abrirlo desde la app (o run_sim_loop.py).")
    print("Debe aparecer en su terminal: [inspire] Manos Inspire RH56DFTP-2R activas en el simulador")


if __name__ == "__main__":
    main()
