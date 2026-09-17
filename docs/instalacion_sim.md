\# Entorno de simulación Unitree G1 — instalación verificada 16/09/2026



\## Sistema

\- Windows 11 + WSL2 2.7.14, Ubuntu 22.04

\- WSLg funcionando (verificado con xeyes)

\- Python 3.10, entorno virtual: \~/lsc-sim



\## Paquetes

\- CycloneDDS 0.10.2 (instalado por pip; la compilación manual no fue necesaria)

\- unitree\_sdk2py 1.0.1  (\~/unitree\_sdk2\_python, pip install -e .)

\- MuJoCo 3.13.0, pygame 2.6.1

\- unitree\_mujoco (\~/unitree\_mujoco)



\## config.py del simulador (simulate\_python/)

\- ROBOT = "g1"

\- USE\_JOYSTICK = 0

\- ENABLE\_ELASTIC\_BAND = True

\- INTERFACE = "lo"



\## Cómo ejecutar

Terminal 1:  source \~/lsc-sim/bin/activate \&\& cd \~/unitree\_mujoco/simulate\_python \&\& python unitree\_mujoco.py

Terminal 2:  source \~/lsc-sim/bin/activate \&\& cd \~/unitree\_mujoco/simulate\_python \&\& python test/test\_unitree\_sdk2.py



\## Verificado

\- unitree\_mujoco.py abre la escena g1\_29dof

\- test\_unitree\_sdk2.py recibe estado del robot por DDS



\## Resuelto

\- Mallas negras / pantalla negra en el visor MuJoCo: export LIBGL\_ALWAYS\_SOFTWARE=1 (añadido a \~/.bashrc)



\## Fase 2 — Ejemplo low-level verificado (16/09/2026)

Script: \~/unitree\_sdk2\_python/example/g1/low\_level/g1\_low\_level\_example.py

Adaptaciones necesarias para el simulador:

\- Línea \~95: `while result\['name']:` -> `while result and result.get('name'):`

&#x20; (el simulador no tiene MotionSwitcherClient; con el robot real funciona igual)

\- Línea \~196: `ChannelFactoryInitialize(0, ...)` -> `ChannelFactoryInitialize(1, ...)`

&#x20; (el simulador usa DOMAIN\_ID = 1; con el robot real volver a 0)

Ejecutar: python g1\_low\_level\_example.py lo

Resultado: el G1 colgado mueve brazos y piernas. Canal Python -> DDS -> robot confirmado.



\## Fase 3 — Script propio (en curso)

Objetivo: mover left\_shoulder\_pitch (índice 15) a un ángulo dado con interpolación

suave y verificar por LowState.

Ubicación: robot\_sim/mover\_joint.py (repo clonado también en WSL: \~/LSC-Mafe)

Índices de brazos (G1 29 DOF):

\- Izquierdo 15-21: shoulder\_pitch, shoulder\_roll, shoulder\_yaw, elbow, wrist\_roll, wrist\_pitch, wrist\_yaw

\- Derecho   22-28: mismo orden



| Joint            | Índice | Signo + significa       |

|------------------|--------|-------------------------|

| LeftShoulderPitch| 15     | brazo hacia atrás (al frente=negativo       |

| LeftShoulderRoll | 16     | brazo se abre lateralmente (abducción)                    |

&#x20; LeftShoulderYaw    17       rotación del brazo sobre su eje (codo gira hacia fuera)

&#x20; joint LeftElbow    18       flexión del codo (antebrazo se cierra)

&#x20; joint LeftWristPitch 20     mano baja (flexión)



\## Fase 4: "verificada con Hola, error máx 4° (gravedad, kp=40); SIGNOS del brazo derecho: roll -1, yaw -1 confirmados".



"Escena de pie (scene\_lsc.xml, pelvis soldada) probada; descartada por ahora porque sin controlador el tronco se vence. Se trabaja colgado (banda elástica)."

