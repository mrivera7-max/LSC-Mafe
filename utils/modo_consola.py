"""
Modo consola: reconocimiento LSC sin interfaz gráfica.
Muestra el video en una ventana OpenCV con las señas detectadas.

Útil para pruebas rápidas o entornos sin pantalla gráfica completa.
"""

import logging
import sys
import time

log = logging.getLogger("lsc_bridge.consola")


class ModoConsola:
    """Ejecuta el reconocedor en modo consola con ventana OpenCV."""

    def __init__(self, config):
        self.config = config

    def ejecutar(self):
        try:
            import cv2
        except ImportError:
            log.error("OpenCV no instalado: pip install opencv-python")
            sys.exit(1)

        from models.reconocedor import ReconocedorLSC
        from robot.conector_g1 import ConectorG1

        reconocedor = ReconocedorLSC(self.config)
        robot = ConectorG1(self.config)

        if not reconocedor.iniciar():
            log.error("No se pudo iniciar el reconocedor")
            sys.exit(1)

        if self.config.robot_activo:
            robot.conectar()

        cap = cv2.VideoCapture(self.config.camara_idx)
        if not cap.isOpened():
            log.error(f"No se pudo abrir la cámara {self.config.camara_idx}")
            sys.exit(1)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.config.camara_ancho)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camara_alto)

        log.info("Modo consola activo. Presiona 'Q' para salir.")
        ultima_seña = None
        total = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame = cv2.flip(frame, 1)
            seña = reconocedor.procesar_frame(frame)

            if seña and (ultima_seña is None or seña.nombre != ultima_seña.nombre):
                ultima_seña = seña
                total += 1
                print(f"[{time.strftime('%H:%M:%S')}] {seña.nombre} "
                      f"({seña.confianza*100:.0f}%) → {seña.traduccion}")
                if robot.conectado:
                    robot.enviar_seña(seña.nombre)

            frame_vis = reconocedor.dibujar_landmarks(frame, seña)
            cv2.putText(frame_vis, f"Total señas: {total}", (10, frame_vis.shape[0] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 255), 1)
            cv2.imshow("LSC Bridge — Consola (Q para salir)", frame_vis)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        cap.release()
        cv2.destroyAllWindows()
        reconocedor.detener()
        if robot.conectado:
            robot.desconectar()
        log.info(f"Sesión finalizada. Total de señas detectadas: {total}")
