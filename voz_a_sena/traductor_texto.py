"""
Traductor de texto a Lengua de Senas Colombiana (LSC).

Convierte una frase en espanol a una secuencia ordenada de senas conocidas,
usando coincidencia de palabras clave y sinonimos comunes.

Los sinonimos se pueden ampliar en tiempo de ejecucion y se guardan en
data/sinonimos_personalizados.json para persistir entre sesiones.

Ejemplo:
    "Hola, muchas gracias" -> ["Hola", "Gracias"]
    "no estoy bien, me siento mal" -> ["No", "Bien", "Mal"]
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("voz_a_sena.traductor")

RUTA_SINONIMOS_PERSONALIZADOS = Path("data/sinonimos_personalizados.json")


# Diccionario de palabras/frases -> sena LSC.
# Las claves deben estar en minuscula y sin tildes (se normaliza en tiempo de busqueda).
SINONIMOS_A_SENA = {
    # Hola
    "hola": "Hola",
    "buenas": "Hola",
    "buenos dias": "Hola",
    "buenas tardes": "Hola",
    "buenas noches": "Hola",
    "saludos": "Hola",
    "que tal": "Hola",

    # Gracias
    "gracias": "Gracias",
    "muchas gracias": "Gracias",
    "te agradezco": "Gracias",
    "agradecido": "Gracias",
    "agradecida": "Gracias",

    # Si
    "si": "Si",
    "claro": "Si",
    "afirmativo": "Si",
    "por supuesto": "Si",
    "asi es": "Si",
    "correcto": "Si",

    # No
    "no": "No",
    "negativo": "No",
    "para nada": "No",
    "nunca": "No",

    # Bien
    "bien": "Bien",
    "estoy bien": "Bien",
    "todo bien": "Bien",
    "todo esta bien": "Bien",
    "muy bien": "Bien",
    "excelente": "Bien",
    "de maravilla": "Bien",
    "perfecto": "Bien",
    "genial": "Bien",

    # Mal
    "mal": "Mal",
    "me siento mal": "Mal",
    "todo esta mal": "Mal",
    "estoy mal": "Mal",
    "terrible": "Mal",
    "pesimo": "Mal",
    "fatal": "Mal",

    # Silencio
    "silencio": "Silencio",
    "guarda silencio": "Silencio",
    "callate": "Silencio",
    "shh": "Silencio",
    "no hagas ruido": "Silencio",
}

SEÑAS_VALIDAS = ["Hola", "Gracias", "Si", "No", "Bien", "Mal", "Silencio"]


@dataclass
class ResultadoTraduccion:
    texto_original: str
    secuencia_señas: list = field(default_factory=list)
    palabras_no_reconocidas: list = field(default_factory=list)

    def __str__(self):
        return " -> ".join(self.secuencia_señas) if self.secuencia_señas else "(sin senas detectadas)"


def _normalizar(texto: str) -> str:
    """Quita tildes, pasa a minuscula y limpia espacios."""
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s]", " ", texto)  # quitar puntuacion
    texto = re.sub(r"\s+", " ", texto)
    return texto


class TraductorTextoLSC:
    """
    Traduce texto en espanol a una secuencia de senas LSC conocidas.

    Estrategia: busca las frases multi-palabra mas largas primero (para
    capturar "buenos dias" antes que solo "dias"), luego palabras sueltas.
    """

    def __init__(self, diccionario: Optional[dict] = None):
        self.diccionario = dict(diccionario) if diccionario else dict(SINONIMOS_A_SENA)
        self._cargar_sinonimos_personalizados()
        self._reordenar_claves()

    def _reordenar_claves(self):
        # Ordenar las claves por longitud de palabras (descendente) para
        # priorizar coincidencias de frases largas sobre palabras sueltas.
        self._claves_ordenadas = sorted(
            self.diccionario.keys(), key=lambda k: -len(k.split())
        )

    def _cargar_sinonimos_personalizados(self):
        """Carga sinonimos agregados previamente desde el archivo JSON."""
        if not RUTA_SINONIMOS_PERSONALIZADOS.exists():
            return
        try:
            with open(RUTA_SINONIMOS_PERSONALIZADOS, "r", encoding="utf-8") as f:
                personalizados = json.load(f)
            self.diccionario.update(personalizados)
            log.info(f"Cargados {len(personalizados)} sinonimos personalizados")
        except Exception as e:
            log.error(f"Error cargando sinonimos personalizados: {e}")

    def _guardar_sinonimos_personalizados(self):
        """Guarda solo los sinonimos que NO estan en el diccionario base."""
        personalizados = {
            k: v for k, v in self.diccionario.items()
            if k not in SINONIMOS_A_SENA
        }
        try:
            RUTA_SINONIMOS_PERSONALIZADOS.parent.mkdir(parents=True, exist_ok=True)
            with open(RUTA_SINONIMOS_PERSONALIZADOS, "w", encoding="utf-8") as f:
                json.dump(personalizados, f, indent=2, ensure_ascii=False)
            log.info(f"Guardados {len(personalizados)} sinonimos personalizados")
        except Exception as e:
            log.error(f"Error guardando sinonimos personalizados: {e}")

    def traducir(self, texto: str) -> ResultadoTraduccion:
        """
        Traduce un texto completo a una secuencia de senas.

        Args:
            texto: Frase en espanol, ej. "Hola, como estas? Todo bien"

        Returns:
            ResultadoTraduccion con la secuencia de senas detectadas en orden.
        """
        texto_norm = _normalizar(texto)
        palabras_restantes = texto_norm.split()
        secuencia = []
        no_reconocidas = []

        i = 0
        while i < len(palabras_restantes):
            coincidencia = self._buscar_coincidencia(palabras_restantes, i)
            if coincidencia:
                sena, n_palabras = coincidencia
                secuencia.append(sena)
                i += n_palabras
            else:
                no_reconocidas.append(palabras_restantes[i])
                i += 1

        resultado = ResultadoTraduccion(
            texto_original=texto,
            secuencia_señas=secuencia,
            palabras_no_reconocidas=no_reconocidas,
        )

        log.info(f"Traduccion: '{texto}' -> {resultado}")
        if no_reconocidas:
            log.debug(f"Palabras no reconocidas: {no_reconocidas}")

        return resultado

    def _buscar_coincidencia(self, palabras: list, inicio: int):
        """
        Intenta encontrar la frase mas larga que coincida a partir de `inicio`.

        Returns:
            (nombre_sena, cantidad_de_palabras_consumidas) o None
        """
        max_palabras = 4  # frases de hasta 4 palabras en el diccionario
        for largo in range(min(max_palabras, len(palabras) - inicio), 0, -1):
            fragmento = " ".join(palabras[inicio:inicio + largo])
            if fragmento in self.diccionario:
                return self.diccionario[fragmento], largo
        return None

    def agregar_sinonimo(self, frase: str, sena: str) -> bool:
        """Agrega un nuevo sinonimo y lo guarda en disco. Retorna True si fue exitoso."""
        if sena not in SEÑAS_VALIDAS:
            log.warning(f"'{sena}' no es una sena valida. Validas: {SEÑAS_VALIDAS}")
            return False
        clave = _normalizar(frase)
        if not clave:
            return False
        self.diccionario[clave] = sena
        self._reordenar_claves()
        self._guardar_sinonimos_personalizados()
        log.info(f"Sinonimo agregado: '{frase}' -> {sena}")
        return True

    def quitar_sinonimo(self, frase: str) -> bool:
        """Quita un sinonimo personalizado (no afecta los del diccionario base)."""
        clave = _normalizar(frase)
        if clave in self.diccionario and clave not in SINONIMOS_A_SENA:
            del self.diccionario[clave]
            self._reordenar_claves()
            self._guardar_sinonimos_personalizados()
            log.info(f"Sinonimo eliminado: '{frase}'")
            return True
        return False

    def listar_sinonimos_personalizados(self) -> dict:
        """Retorna solo los sinonimos agregados por el usuario (no los base)."""
        return {k: v for k, v in self.diccionario.items() if k not in SINONIMOS_A_SENA}
