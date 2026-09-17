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



\## Pendiente

\- Mallas negras en el visor MuJoCo (render por software en WSL2)

