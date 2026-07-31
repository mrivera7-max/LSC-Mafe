"""
Prueba rapida del traductor de texto a LSC, sin necesidad de GUI,
microfono ni servidor WebSocket. Util para verificar que el motor
de traduccion y generacion de posturas funciona correctamente.

Uso:
    python voz_a_sena/probar_traductor.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voz_a_sena.traductor_texto import TraductorTextoLSC
from voz_a_sena.generador_posturas import GeneradorPosturas

FRASES_DE_PRUEBA = [
    "Hola, muchas gracias",
    "No estoy bien, me siento mal",
    "Si, todo bien",
    "Guarda silencio por favor",
    "Buenos dias, como estas",
    "Esto no deberia traducir nada en espanol raro xyz",
]


def main():
    traductor = TraductorTextoLSC()
    generador = GeneradorPosturas()

    print("\n" + "=" * 60)
    print("  Prueba del Traductor Texto -> LSC")
    print("=" * 60 + "\n")

    for frase in FRASES_DE_PRUEBA:
        resultado = traductor.traducir(frase)
        print(f"Texto:     \"{frase}\"")
        print(f"Senas:     {resultado}")
        if resultado.palabras_no_reconocidas:
            print(f"No reconocidas: {resultado.palabras_no_reconocidas}")

        if resultado.secuencia_señas:
            secuencia = generador.generar(resultado.secuencia_señas, frase)
            print(f"Duracion total: {secuencia.duracion_total_segundos:.1f}s "
                  f"({len(secuencia)} pasos)")
        print("-" * 60)

    print("\nModo interactivo (escribe 'salir' para terminar):\n")
    while True:
        try:
            texto = input("Texto: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if texto.lower() == "salir":
            break
        if not texto:
            continue
        resultado = traductor.traducir(texto)
        print(f"  -> {resultado}")
        if resultado.palabras_no_reconocidas:
            print(f"     (no reconocidas: {resultado.palabras_no_reconocidas})")


if __name__ == "__main__":
    main()
