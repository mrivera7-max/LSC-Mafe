# Manos Inspire RH56DFTP-2R en el simulador (un solo MuJoCo, sin gemelo)

## Por qué salían las manos viejas
El simulador de SONIC carga `scene_43dof.xml`, que trae las manos **Dex3** (7 joints por mano).
La app solo traducía los dedos Inspire a esos 7 joints Dex3, así que el modelo 3D seguía siendo Dex3.

## Qué hace ahora
- `construir_escena.py` genera `scene_43dof_inspire.xml`: mismo robot, **sin la geometría Dex3** y con la
  mano Inspire (12 joints/mano con los *mimic* del URDF) montada en cada muñeca.
- Las manos son cuerpos *mocap* que siguen a `*_wrist_yaw_link`: no se altera el orden de joints ni el
  número de actuadores que espera `base_sim.py` (el Dex3 queda invisible y sigue recibiendo sus comandos).
- La app publica los 6 valores por mano (rad) por ZeroMQ `tcp://127.0.0.1:5601`; `inspire_hook.py`
  (dentro del simulador) los lee y mueve los dedos. Es el mismo simulador que ya abre la app.

## Instalar (una vez; PC con SONIC)
```bash
cd ~/LSC-Mafe
tar xzf ~/Descargas/lsc-manos-inspire-sim.tgz
python3 instalar_manos_inspire_sim.py --gr00t ~/DURVVIN/SIM2SIM/blea/GR00T-WholeBodyControl
```
(usa un Python con `numpy` y `scipy`; el de `.venv_teleop` sirve). Repetirlo no hace daño; `--quitar` vuelve al Dex3.
Cierra el MuJoCo abierto y ábrelo de nuevo desde la app. En la terminal de MuJoCo debe salir:
`[inspire] Manos Inspire RH56DFTP-2R activas en el simulador`

## Probar sin la app
```bash
cd ~/DURVVIN/SIM2SIM/blea/GR00T-WholeBodyControl && python gear_sonic/scripts/run_sim_loop.py
# otra terminal
cd ~/LSC-Mafe && python3 -m robot.sim_inspire.demo_manos
```
Recorre mano abierta → puño → índice → puño con pulgar.

## Si algo no coincide
- Manos cambiadas (la derecha mueve la izquierda): `export LSC_INSPIRE_ORDEN=lr` antes de `python app_unificada.py`.
- No aparece el mensaje `[inspire]`: `grep ROBOT_SCENE .../wbc_configs/g1_29dof_sonic_model12.yaml` debe decir `scene_43dof_inspire.xml`.
- Mano en el sitio equivocado: cambia `X_MANO` en `construir_escena.py` y repite el instalador.
