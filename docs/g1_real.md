# G1 físico: ejecutar las señas con el robot real

La app publica las posturas de torso y brazos por ZMQ (:5556) y el deploy de SONIC mueve el
robot. Es **el mismo circuito que con MuJoCo**; solo cambia a quién le habla el deploy:

```
Simulación:  app ──ZMQ :5556──▶ deploy ... sim  ──▶ MuJoCo
Robot real:  app ──ZMQ :5556──▶ deploy ... enp131s0 ──DDS (cable)──▶ G1 (motores)
```

El deploy corre en la **PC 5062robotika** (ya compilado y probado en simulación), por el cable de
red del robot. No se usa MuJoCo (Terminal 1) ni el Jetson. Para correr el deploy en el Jetson,
como en el notebook `sim2real_v1`, ver el apartado «Variante: deploy en el Jetson».

> **Estado:** el software está probado en el sandbox contra un deploy simulado (mensajes ZMQ,
> escalado, límite de velocidad, PARAR y bloqueo). **No se ha probado todavía con el robot.**
> Haz la primera prueba en el arnés y con alguien en la tecla `O`.

## Qué agrega la app en modo real

| Elemento | Qué hace |
|---|---|
| `--sonic-real` | Botón «Conectar G1 real» con checklist de seguridad; sin aceptar no se envía nada |
| Amplitud 60 % | Cada seña se reduce al 60 % respecto a la postura de pie (`--sonic-escala`) |
| Tiempos x1.5 | Cada tramo dura 1.5 veces más (`--sonic-tiempo`) |
| Tope 1.2 rad/s | Si un tramo pide más velocidad, se alarga (`--sonic-vel-max`) |
| Botón **■ PARAR** | Envía `stop` al deploy, congela la consigna y bloquea nuevas señas |
| Cámara → G1 | La seña detectada por la cámara la ejecuta el G1 si la confianza es ≥ 80 % (`--sonic-confianza`); mientras ejecuta otra, las nuevas se descartan |

Con la app cerrada o colgada, el deploy vuelve a IDLE si pasa más de 1 s sin recibir `planner`
(el robot sigue de pie y deja de seguir los brazos). La tecla **`O`** (botón «O» de la consola de la app) es
el paro de emergencia y siempre manda.

## Antes de empezar (checklist físico)

1. G1 **en arnés o sostenido**, con espacio libre alrededor, batería cargada.
2. Una persona **con la mano en la tecla `O`** de la terminal del deploy y otra con el control
   remoto de Unitree a mano.
3. Robot preparado para control de bajo nivel según el manual de Unitree de tu firmware
   (modo depuración / damping; el controlador de movimiento de fábrica no debe estar activo).
   Cierra cualquier otra aplicación que controle al robot (Pico, `xr_teleoperate`, app de Unitree).
4. MuJoCo **apagado** (`pgrep -af run_sim_loop` sin salida): comparte puertos y confunde.
5. Red: PC por cable.
   ```bash
   ping -I enp131s0 -c 3 192.168.123.161     # PC1 del G1, 0 % de pérdida
   ```

## Arranque

### T-A (opcional): cámara del robot, en el PC2 por SSH
```bash
ssh unitree@192.168.123.164
cd ~/teleimager && teleimager-server
```

### T-2: deploy SONIC en modo real
```bash
cd /home/udirobotika/DURVVIN/SIM2SIM/blea/GR00T-WholeBodyControl/gear_sonic_deploy
source scripts/setup_env.sh
./deploy.sh --input-type zmq_manager enp131s0
```
- Se indica la interfaz **por nombre** (`enp131s0`) en lugar de `real`: `real` busca la primera
  interfaz 192.168.123.x y la PC también tiene el Wi-Fi en esa red.
- Debe decir `Environment: real` y avisar «This will start the REAL robot control system».
  Responde `y` solo si el checklist está cumplido.
- Espera `Init Done`. Hasta ahí no mueve nada; el control empieza cuando la app envía `start`.

### T-3: app en modo real
```bash
cd ~/LSC-Mafe && source venv/bin/activate
python app_unificada.py --sin-robot --v2 --sonic-real \
    --camara-g1 --camara-g1-puerto 55556           # la cámara externa es opcional
```
1. Pestaña **Voz / Texto → Seña** → **▶ Conectar G1 real**.
2. Lee el checklist y acepta: se envía `start` y la política toma el control.
3. Debe verse `● G1 real conectado (:5556)` y, en T-2, `[ZMQManager] Planner enabled`.

## Primeras pruebas, de menos a más riesgo

| Etapa | Condición | Qué hacer |
|---|---|---|
| 1 | Arnés, pies en el aire | `python -m robot.probar_sonic --real articulaciones` (con la app cerrada) |
| 2 | Arnés, pies apoyados y cable flojo | `python -m robot.probar_sonic --real Hola Gracias` |
| 3 | Misma condición | La app: escribe «hola», luego frases de varias señas |
| 4 | Misma condición | Seña por cámara: hazla frente a la OBSBOT |
| 5 | De pie con ayudante cerca | Solo cuando las etapas 1-4 salgan sin tirones |

Antes de subir amplitud o velocidad, hazlo de a poco, por ejemplo:
`--sonic-escala 0.8 --sonic-tiempo 1.2`. No uses valores menores a los de simulación
(`--sonic-tiempo` no puede ser < 1 en modo real).

## Si algo se ve mal
1. **Tecla `O`** en la terminal del deploy (paro de emergencia).
2. Botón **■ PARAR** de la app.
3. Control remoto de Unitree.

Tras un PARAR el deploy termina: para reanudar, reinicia el deploy, pulsa Desconectar y Conectar.

## Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| T-2: `LowState is not available` | El deploy no recibe el estado del robot | Revisa cable, `ping -I enp131s0 192.168.123.161` y que no esté otra app controlando |
| T-2 usa el Wi-Fi | Se lanzó con `real` | Indica la interfaz: `enp131s0` |
| `No se pudo abrir el puerto 5556` | Otro proceso lo usa (MuJoCo o una app anterior) | `pkill -f app_unificada; pkill -f run_sim_loop` |
| El robot no sigue los brazos | Faltó `start` o la app murió >1 s | Desconectar y Conectar en la app |
| La cámara detecta pero el G1 no se mueve | Confianza < 80 % o ya ejecutaba otra seña | Mira el registro; baja `--sonic-confianza` con cuidado |
| La seña se ve pequeña o lenta | Es el modo real (60 % y x1.5) | Sube de a poco `--sonic-escala` / baja `--sonic-tiempo` |

## Variante: deploy en el Jetson (como el notebook `sim2real_v1`)
El notebook entrena y despliega **una política por seña** con `--input-type zmq` en el Jetson
(`eth0`). Para usar nuestra app (política general con planner, cualquier seña) en el Jetson:

1. Copiar al Jetson los ONNX generales (`policy/release/model_encoder.onnx`, `model_decoder.onnx`),
   el planner compatible con TRT 8.5 (`planner_sonic_opset14_cf2.onnx`) y `observation_config.yaml`
   parcheado, como en §8.4 del notebook.
2. Lanzar allí el binario con `--input-type zmq_manager --zmq-host 192.168.123.222`
   (IP de la PC en el cable) y la interfaz `eth0`.
3. En la PC, la app queda igual (`--sonic-real`); abre el puerto 5556 hacia el Jetson.

Esta variante no está probada con nuestra app y depende de que el build del Jetson esté completo.

## Micrófono (voz) — usa el del robot

Con `--sonic-real` o `--camara-g1` la voz se captura del **micrófono del G1** (UDP multicast
`239.168.123.161:5555`, PCM 16 kHz mono) y, si no llega audio, cae al micrófono de la PC
(modo `auto`). Opciones de `app_unificada.py`:

| Opción | Efecto |
|---|---|
| `--mic auto` (defecto) | G1 si hay `--sonic-real`/`--camara-g1` (con respaldo a la PC); si no, PC |
| `--mic g1` | Solo el del robot; si no llega audio, muestra el error |
| `--mic pc` | Solo el de la PC |
| `--mic-g1-ip IP` | IP de la PC en la red 192.168.123.x (si no se detecta sola) |

Diagnóstico (antes de usar la app):

```bash
python -m voz_a_sena.probar_microfono g1 --ip 192.168.123.X   # paquetes y nivel del audio del robot
python -m voz_a_sena.probar_microfono pc                       # lista micrófonos de la PC
python -m voz_a_sena.probar_microfono g1 --reconocer           # además transcribe una frase
```

Notas: el formato del audio del G1 proviene de proyectos de terceros (no de la documentación
oficial) y la recepción en una PC externa no está verificada; si `probar_microfono g1` da 0
paquetes, revisar cable/IP en 192.168.123.x y firewall UDP 5555. El reconocimiento (Google)
requiere internet.

## Una sola terminal: la app verifica y deja todo listo
Se abre solo la terminal que lanza la app (`python app_unificada.py --sin-robot --v2`); todo lo demás se
controla desde ella. Al abrir, la pestaña **🔌 Conexión** verifica y deja preparado lo necesario:

| Comprobación | Qué hace |
|---|---|
| Robot en la red | Conecta al puerto SSH del PC2 (192.168.123.164) |
| Acceso SSH al robot | Prueba el acceso sin contraseña; si falta, el botón **🔑 Configurar acceso SSH** pide la contraseña del robot **una sola vez**, crea la llave y la copia (no se guarda) |
| Cable al robot | La interfaz del deploy (`enp131s0`) tiene IP 192.168.123.x y es la ruta hacia el robot |
| Carpeta GR00T / deploy | Existe `deploy.sh` y el entorno `.venv*` |
| Procesos antiguos | Avisa si quedó MuJoCo o un deploy de otra sesión; **🧹 Cerrar procesos antiguos** los termina |
| Cámara del robot | Detecta la OBSBOT, desactiva la RealSense y deja `teleimager-server` publicando en :55556 |
| Cámara de la PC, internet, micrófono, xdotool | Avisos informativos |

El resultado es **✔ Todo listo**, **⚠ Listo con avisos** o **✖ Hay problemas** (con el motivo y qué hacer
en cada fila). **↻ Verificar de nuevo** repite todo. Para saltarse la verificación: `--sin-verificar`.

## Arranque con los botones de la app (simulación o robot real)

Todo se controla desde la app; **no hace falta abrir terminales** (solo la de la app). El simulador y el
deploy corren dentro de ella y se ven en la pestaña **🖥 Consola** (botón «Ver consola» de la barra; con el robot real se abre sola):

- **🖥 Abrir simulación MuJoCo**: inicia MuJoCo y el deploy `sim`, espera «Init done» y enlaza solo.
  Después sueltas al G1 con `7` ×2 y `9` en la ventana de MuJoCo.
- **🤖 Conectar robot real**: pide confirmar, inicia el deploy con la interfaz del cable
  (`enp131s0`; cámbiala con `--iface`), abre la pestaña Consola (si el deploy pide confirmar, pulsa **↵ Enter** ahí),
  y al ver «Init done» muestra el checklist de seguridad antes de enviar START. El micrófono pasa al del G1.
- **✖ Cerrar todo**: desconecta y detiene lo que abrió la app (MuJoCo y/o deploy) con Ctrl+C.
  Con el robot real pide confirmación.
- **■ Desconectar**: corta el enlace sin cerrar el deploy. **■ PARAR**: paro por software.

Si el botón dice **▶ Enlazar …**, el deploy ya está en marcha y solo falta conectar.
Tras **■ PARAR**: Desconectar, «✖ Cerrar todo» y volver a pulsar el botón.

### Pestaña Consola
Muestra la salida del proceso elegido (MuJoCo, deploy simulación, deploy robot real) y tiene los botones
**↵ Enter**, **O Paro de emergencia** (envía la tecla `O` al deploy), **Ctrl+C** y una línea para escribir.
Cambiar de pestaña no detiene nada. Al cerrar la app se detienen los procesos que abrió.
Para volver a terminales externas (como antes): `--terminales`.

Ruta del repositorio GR00T: `--gr00t-dir RUTA` o variable `GR00T_DIR` (por defecto
`/home/udirobotika/DURVVIN/SIM2SIM/blea/GR00T-WholeBodyControl`).

### Posición de la ventana de MuJoCo
La app mueve la ventana de MuJoCo al abrirse (por defecto esquina superior izquierda, `0,0`):
`--mujoco-pos X,Y` y `--mujoco-tam ANCHOxALTO` (por ejemplo `--mujoco-pos 40,80 --mujoco-tam 960x600`;
`--mujoco-pos no` la deja donde el sistema la ponga). Requiere `sudo apt install xdotool` (o `wmctrl`)
y una sesión X11; con Wayland puro hay que arrastrar la ventana a mano.
Si hay ventanas de MuJoCo duplicadas: `pkill -f run_sim_loop.py; pkill -f g1_deploy_onnx_ref`.

## Cámara: botones «Cámara PC» y «Cámara G1»
En la pestaña **📷 Cámara → Seña**, arriba a la derecha, hay dos botones. Al abrir la app no hay cámara activa:
- **📷 Cámara PC**: webcam USB (índice `--camara N`, por defecto 0).
- **🤖 Cámara G1**: la app prepara sola el robot por SSH y abre la cámara externa (OBSBOT):
  1. busca qué `/dev/videoN` es la OBSBOT (el número cambia entre reinicios);
  2. escribe `~/teleimager/cam_config_server.yaml` con la RealSense desactivada y la externa activa
     (copia de seguridad en `cam_config_server.yaml.lsc_bak`);
  3. inicia `teleimager-server` en segundo plano (registro en `/tmp/teleimager_lsc.log` del robot) si no corría;
  4. espera el puerto 55556 y abre la imagen (960x540).
  Ya no se necesita la «Terminal A» ni editar el yaml a mano.

**Una sola vez** (acceso SSH sin contraseña al PC2), en una terminal de la PC:
```
ssh-copy-id unitree@192.168.123.164
```
(Alternativa: `sudo apt install sshpass` y `--g1-clave CLAVE`.) Opciones: `--g1-usuario`, `--g1-camara-nombre TXT`
(texto que identifica la cámara en `v4l2-ctl --list-devices`, por defecto `OBSBOT`), `--camara-g1 [IP]`,
`--sin-auto-g1` (no preparar por SSH: `teleimager-server` ya corre a mano; usa `--camara-g1-puerto` y `--camara-g1-mono`).

Pulsar el botón de la cámara activa la detiene; pulsar el otro cambia de cámara sin reiniciar la app. Si algo
falla (SSH, cámara no encontrada, servidor sin arrancar) aparece el motivo y la cámara queda «inactiva».
El servidor del robot sigue encendido al detener la cámara (se reactiva al instante). El botón antiguo
«⚡ Conectar G1» solo aparece si la app se abre sin `--sin-robot`.

## Señas completas con dedos (mano Inspire RH56DFTP-2R)

**Convención del codo.** En `g1_29dof_old_freebase.xml` el codo (índice 25) vale 0 con el antebrazo horizontal hacia
adelante; **positivo BAJA el antebrazo, negativo lo SUBE** (rango −1.05…2.09). Las señas usaban el signo contrario.
Las 7 señas (Hola, Gracias, Si, No, Bien, Mal, Silencio) están recalculadas con cinemática inversa sobre el modelo del robot.

**Forma de la mano por seña = datos del repositorio.** `python -m robot.extraer_formas_mano --ver` lee
`data/sequences/<Seña>/*.npy` (21 puntos MediaPipe por mano), calcula cuánto se flexiona cada dedo y escribe
`data/formas_mano_lsc.json` con 6 valores por seña (1 = abierto, 0 = cerrado) en el orden de la mano Inspire:
meñique, anular, medio, índice, pulgar-flexión, pulgar-rotación. Resultado: Hola/Gracias = mano abierta;
No/Silencio = índice extendido y el resto cerrado; Si = puño; Bien/Mal = puño con el pulgar abierto.
El movimiento del brazo (altura de la mano, cabeceo, saludo…) no sale de los datos (solo guardan estadísticas
por seña, no trayectorias): viene de la cinemática inversa.

**Dónde se mueven los dedos (`--manos`).**

| Modo | Qué hace |
|---|---|
| `auto` (defecto) | simulación → `dex3`; robot real → `inspire` |
| `dex3` | manda `left_hand_joints` / `right_hand_joints` (7 articulaciones Dex3) en el mensaje `planner`: se ven en la ventana de MuJoCo (solo pulgar, índice y medio; el simulador de SONIC no tiene manos Inspire ni anular/meñique) |
| `inspire` | publica `inspire_hand_ctrl` en `rt/inspire_hand/ctrl/{l,r}` (modo ángulo, `angle_set` 0–1000, 1000 = abierto). Requiere `pip install inspire_sdkpy` y el servicio de la mano en el PC2 (DFX_inspire_service / `inspire_g1`) |
| `ambas` / `ninguna` | las dos salidas / sin mover los dedos |

Con el robot real los dedos no se reducen con `--sonic-escala` (solo los brazos).

**Ver la mano Inspire en 3D** (sin simulador): `git clone --depth 1 https://github.com/unitreerobotics/xr_teleoperate ~/xr_teleoperate`
y luego `python -m robot.vista_inspire --png manos.png` (imagen) o `--visor` (ventana que recorre las señas).

**Comprobar las señas sin abrir MuJoCo:** `python -m robot.verificar_sena [Seña …] [--png x.png]` — altura de la mano,
hacia dónde apuntan dedos y pulgar, forma de la mano y límites articulares.

**Cámara → simulador.** Con el simulador iniciado («Init done») y una cámara activa (PC o G1), la seña detectada se
replica en MuJoCo: si aún no estaba enlazado, la app se enlaza sola con la primera seña. La barra inferior de la
pestaña Cámara muestra «● MuJoCo G1: replicando» y tiene el interruptor «Replicar la seña detectada». La misma seña
puede repetirse tras 1.5 s sin detección. Con el robot real nunca se enlaza sola (exige el checklist). Para
desactivarlo: `--sin-auto-enlace`.

**Límites conocidos.** «Mal» (pulgar abajo) exige abrir el hombro (roll −1.26) y girar la muñeca casi a su tope
(−1.60) porque el G1 no tiene pronación de antebrazo; con el robot real queda amortiguado por la amplitud 0.6.
El pulgar de la mano Dex3 en MuJoCo es una aproximación (no hay mallas disponibles para validarlo visualmente);
la forma de la Inspire sí se ve en `vista_inspire`. Confirmar las formas con un intérprete de LSC.
