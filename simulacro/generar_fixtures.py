#!/usr/bin/env python3
"""
Genera las páginas de prueba del simulacro a partir de las páginas reales:

  01_actual.html         página 2027 tal y como está hoy
  02_proyecto.html       2027 + sección "Proyecto de Ley" (copiada de 2026)
  03_ley.html            02 + sección "Ley" y "Resumen gráfico" (copiadas de 2026)
  04_mantenimiento.html  página que no es la de presupuestos (debe dar error)
  05_maqueta_nueva.html  el proyecto publicado con una maquetación distinta

Las secciones de 2026 se trasladan a 2027 cambiando el año en textos y URLs.
"""

import copy
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.gobiernodecanarias.org/hacienda/planificacionypresupuesto/presupuestos/{}/index.html"
DESTINO = Path(__file__).with_name("fixtures")
UA = {"User-Agent": "Mozilla/5.0 (compatible; ODESOCAN-alerta-presupuestos/1.0)"}


def bajar(anio: str) -> BeautifulSoup:
    r = requests.get(BASE.format(anio), headers=UA, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def secciones_2026(soup: BeautifulSoup, patron: str) -> list:
    """Bloques <ul class="con-titulo"> cuyo h4 casa con el patrón, con el año cambiado a 2027."""
    resultado = []
    for ul in soup.select("div.bloque_basico_modernizacion ul.con-titulo"):
        h4 = ul.find("h4")
        if h4 and re.search(patron, h4.get_text(" ", strip=True)):
            html = str(ul).replace("2026", "2027")
            resultado.append(BeautifulSoup(html, "html.parser").ul)
    return resultado


def insertar_al_principio(soup: BeautifulSoup, uls: list) -> BeautifulSoup:
    soup = copy.copy(soup)
    apartado = soup.select_one("div.bloque_basico_modernizacion div.apartado")
    primero = apartado.find("ul", class_="con-titulo")
    for ul in uls:
        primero.insert_before(copy.copy(ul))
    return soup


def main() -> None:
    DESTINO.mkdir(exist_ok=True)
    actual, p2026 = bajar("2027"), bajar("2026")

    proyecto = secciones_2026(p2026, r"^Proyecto de Ley de Presupuestos")
    ley = secciones_2026(p2026, r"^(Ley de Presupuestos|Resumen Gráfico)")
    assert proyecto and ley, "No se encontraron las secciones de referencia en la página de 2026"

    con_proyecto = insertar_al_principio(actual, proyecto)
    con_ley = insertar_al_principio(con_proyecto, ley)

    mantenimiento = (
        "<html><head><title>Portal en mantenimiento</title></head>"
        "<body><h1>Estamos realizando tareas de mantenimiento</h1></body></html>"
    )

    enlaces = "".join(
        f'<li><a href="{a["href"]}">{a.get_text(" ", strip=True)}</a></li>'
        for ul in proyecto for a in ul.find_all("a", href=True)
    )
    maqueta_nueva = (
        "<html><head><title>Presupuestos Generales de la CAC para 2027</title></head><body>"
        "<main><h2>Año Presupuestario 2027</h2><h3>Documentación remitida al Parlamento</h3>"
        f"<ul>{enlaces}</ul></main></body></html>"
    )

    paginas = {
        "01_actual.html": str(actual),
        "02_proyecto.html": str(con_proyecto),
        "03_ley.html": str(con_ley),
        "04_mantenimiento.html": mantenimiento,
        "05_maqueta_nueva.html": maqueta_nueva,
    }
    for nombre, html in paginas.items():
        (DESTINO / nombre).write_text(html, encoding="utf-8")
        print(f"  {nombre}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
