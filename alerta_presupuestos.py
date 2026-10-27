#!/usr/bin/env python3
"""
Alerta de publicación de los Presupuestos Generales de la Comunidad Autónoma
de Canarias para 2027.

Visita la página oficial de la Consejería de Hacienda, extrae los documentos
publicados y avisa cuando aparece:

  * el PROYECTO de Ley de Presupuestos 2027 (lo que se remite al Parlamento), o
  * la LEY de Presupuestos 2027 (aprobada),

y, con menor prioridad, cuando se cuelga cualquier documento nuevo.

Pensado para ejecutarse de forma periódica en GitHub Actions: guarda lo visto
en `estado.json` (que el workflow vuelve a commitear) para no repetir avisos.

Canales de aviso (se usan los que estén configurados por variables de entorno):
  * Consola + resumen del job de GitHub Actions (siempre).
  * Issue en el propio repositorio (GITHUB_TOKEN + GITHUB_REPOSITORY).
  * Correo SMTP, con las mismas variables que transparencia-gobcan:
    SMTP_USUARIO, SMTP_CONTRASENA, ALERTAS_DESTINATARIAS (separadas por comas)
    y, opcionales, SMTP_HOST (smtp.gmail.com), SMTP_PORT (587), ALERTAS_REMITENTE.

Uso local:
  python alerta_presupuestos.py                     # comprobación real
  python alerta_presupuestos.py --simular           # sin enviar avisos externos
  python alerta_presupuestos.py --html pagina.html  # analizar un HTML guardado

Códigos de salida: 0 = comprobación correcta (haya o no aviso),
                   1 = error al descargar/interpretar la página,
                   2 = se detectó algo pero no se pudo enviar ningún aviso.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ANIO = "2027"
URL_POR_DEFECTO = (
    "https://www.gobiernodecanarias.org/hacienda/planificacionypresupuesto/"
    f"presupuestos/{ANIO}/index.html"
)
ESTADO_POR_DEFECTO = Path(__file__).with_name("estado.json")
USER_AGENT = (
    "Mozilla/5.0 (compatible; ODESOCAN-alerta-presupuestos/1.0; "
    "+https://www.odesocan.org)"
)

# Hitos que disparan la alerta principal. Se comprueban sobre el título de
# la sección (h4) normalizado sin tildes y sobre la URL del documento.
HITOS = {
    "proyecto": {
        "nombre": f"Proyecto de Ley de Presupuestos de la CAC {ANIO}",
        "titulo": re.compile(r"\bproyecto de ley de presupuestos\b"),
        "url": re.compile(rf"/presupuestos/{ANIO}/(?!ley/).*tomo[-_ ]?\d", re.I),
    },
    "ley": {
        "nombre": f"Ley de Presupuestos de la CAC {ANIO}",
        "titulo": re.compile(r"^ley de presupuestos\b"),
        "url": re.compile(rf"/presupuestos/{ANIO}/ley/", re.I),
    },
}


@dataclass
class Documento:
    url: str
    texto: str
    seccion: str


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios simples."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def limpiar(texto: str) -> str:
    return " ".join(texto.split())


def ahora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------
# Descarga y análisis
# --------------------------------------------------------------------------

def descargar(url: str, intentos: int = 3) -> str:
    ultimo_error: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except requests.RequestException as e:
            ultimo_error = e
            log(f"Intento {intento}/{intentos} fallido: {e}")
            if intento < intentos:
                time.sleep(10 * intento)
    raise RuntimeError(f"No se pudo descargar {url}: {ultimo_error}")


def extraer_documentos(html: str, url_base: str) -> tuple[list[Documento], list[str]]:
    """Devuelve (documentos, títulos de sección) del bloque de contenido."""
    soup = BeautifulSoup(html, "html.parser")

    titulo = limpiar(soup.title.get_text()) if soup.title else ""
    if ANIO not in titulo and f"Año Presupuestario {ANIO}" not in soup.get_text():
        raise RuntimeError(
            f"La página no parece la de presupuestos {ANIO} (título: {titulo!r}). "
            "¿Ha cambiado la URL o está en mantenimiento?"
        )

    bloques = soup.select("div.bloque_basico_modernizacion")
    if bloques:
        enlaces = [a for b in bloques for a in b.find_all("a", href=True)]
        secciones = [limpiar(h.get_text(" ")) for b in bloques for h in b.find_all("h4")]
    else:
        # Si el portal cambia de maqueta, recurrimos a todos los enlaces que
        # apunten a la galería de documentos del año.
        log("AVISO: no se encontró el bloque de contenido habitual; uso modo amplio.")
        enlaces = [
            a for a in soup.find_all("a", href=True)
            if re.search(rf"presupuestos/?{ANIO}", a["href"], re.I)
        ]
        secciones = [limpiar(h.get_text(" ")) for h in soup.find_all(["h3", "h4"])]

    documentos: dict[str, Documento] = {}
    for a in enlaces:
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(url_base, href)
        h4 = a.find_previous("h4")
        documentos.setdefault(url, Documento(
            url=url,
            texto=limpiar(a.get_text(" ")) or url.rsplit("/", 1)[-1],
            seccion=limpiar(h4.get_text(" ")) if h4 else "",
        ))
    return list(documentos.values()), secciones


def detectar_hitos(documentos: list[Documento], secciones: list[str]) -> dict[str, list[Documento]]:
    """Para cada hito presente, devuelve los documentos asociados."""
    encontrados: dict[str, list[Documento]] = {}
    for clave, hito in HITOS.items():
        por_titulo = any(hito["titulo"].search(normalizar(s)) for s in secciones)
        docs = [
            d for d in documentos
            if hito["titulo"].search(normalizar(d.seccion)) or hito["url"].search(d.url)
        ]
        if por_titulo or docs:
            encontrados[clave] = docs
    return encontrados


# --------------------------------------------------------------------------
# Estado
# --------------------------------------------------------------------------

def cargar_estado(ruta: Path) -> dict | None:
    if not ruta.exists():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))


def guardar_estado(ruta: Path, estado: dict) -> None:
    ruta.write_text(json.dumps(estado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def huella(documentos: list[Documento]) -> str:
    claves = sorted(f"{d.seccion}|{d.texto}|{d.url}" for d in documentos)
    return hashlib.sha256("\n".join(claves).encode()).hexdigest()


# --------------------------------------------------------------------------
# Avisos
# --------------------------------------------------------------------------

def componer_aviso(url: str, hitos_nuevos: dict[str, list[Documento]],
                   docs_nuevos: list[Documento]) -> tuple[str, str]:
    if hitos_nuevos:
        nombres = " y ".join(HITOS[c]["nombre"] for c in hitos_nuevos)
        asunto = f"🚨 Publicado: {nombres}"
    else:
        asunto = f"Nuevos documentos en la página de Presupuestos CAC {ANIO}"

    lineas = [f"Detectado el {ahora()} en {url}", ""]
    for clave, docs in hitos_nuevos.items():
        lineas.append(f"## {HITOS[clave]['nombre']}")
        lineas += [f"- [{d.texto}]({d.url})" for d in docs] or ["- (sección visible, aún sin documentos enlazados)"]
        lineas.append("")
    ya_listados = {d.url for docs in hitos_nuevos.values() for d in docs}
    resto = [d for d in docs_nuevos if d.url not in ya_listados]
    if resto:
        lineas.append("## Otros documentos nuevos")
        lineas += [f"- [{d.texto}]({d.url})" + (f" — _{d.seccion}_" if d.seccion else "") for d in resto]
        lineas.append("")
    lineas.append("_Aviso generado automáticamente por alerta-presupuestos-2027 (ODESOCAN)._")
    return asunto, "\n".join(lineas)


def avisar_issue(asunto: str, cuerpo: str) -> bool | None:
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if not (token and repo):
        return None
    r = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": asunto, "body": cuerpo},
        timeout=30,
    )
    if r.ok:
        log(f"Issue creado: {r.json().get('html_url')}")
        return True
    log(f"ERROR creando issue ({r.status_code}): {r.text[:300]}")
    return False


def _escapar(texto: str) -> str:
    return (texto or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def componer_html(asunto: str, cuerpo: str) -> str:
    """Correo en HTML con la identidad visual de ODESOCAN.

    Mismas reglas que transparencia-gobcan: estilos en línea, maquetación con
    tablas y tipografías del sistema, porque Gmail descarta el resto.
    """
    navy, teal, teal_oscuro, tenue, borde, fondo = (
        "#0D1B2A", "#00C8B4", "#009E8E", "#6B8CA5", "#E4EDF5", "#F5F8FA")
    tipo = "'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

    filas = []
    for linea in cuerpo.splitlines():
        if linea.startswith("## "):
            filas.append(
                f'<div style="font-family:{tipo};font-size:15px;font-weight:700;color:#1A2F45;'
                f'padding:16px 0 6px;">{_escapar(linea[3:])}</div>')
        elif m := re.match(r"- \[(.+?)\]\((.+?)\)(?: — _(.+)_)?$", linea):
            texto, url, seccion = m.groups()
            extra = f'<span style="color:{tenue};"> · {_escapar(seccion)}</span>' if seccion else ""
            filas.append(
                f'<div style="font-family:{tipo};font-size:14px;line-height:1.6;padding:2px 0;">'
                f'&rarr; <a href="{_escapar(url)}" style="color:{teal_oscuro};font-weight:700;'
                f'text-decoration:none;">{_escapar(texto)}</a>{extra}</div>')
        elif linea.startswith("Detectado el"):
            filas.append(f'<div style="font-family:{tipo};font-size:12px;color:{tenue};">{_escapar(linea)}</div>')

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{fondo};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{fondo};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
    <tr><td style="background:{navy};border-radius:14px 14px 0 0;padding:26px 24px;">
      <div style="font-family:{tipo};font-size:10px;font-weight:700;letter-spacing:.14em;
                  text-transform:uppercase;color:rgba(255,255,255,.55);padding-bottom:10px;">
        ODESOCAN · Observatorio de Derechos Sociales de Canarias
      </div>
      <div style="font-family:{tipo};font-size:22px;font-weight:700;color:{teal};line-height:1.25;">
        {_escapar(asunto.lstrip("🚨 "))}
      </div>
    </td></tr>
    <tr><td style="background:#ffffff;border:1px solid {borde};border-top:0;
                   border-radius:0 0 14px 14px;padding:20px 24px;">
      {"".join(filas)}
      <div style="padding-top:18px;">
        <a href="{URL_POR_DEFECTO}" style="font-family:{tipo};font-size:13px;font-weight:700;
           color:{teal_oscuro};text-decoration:none;">Abrir la página de Presupuestos {ANIO} &rarr;</a>
      </div>
    </td></tr>
    <tr><td style="padding:16px 4px;font-family:{tipo};font-size:11px;color:{tenue};text-align:center;">
      Aviso automático de alerta-presupuestos-2027 · ODESOCAN
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def avisar_email(asunto: str, cuerpo: str) -> bool | None:
    # Mismas variables que transparencia-gobcan, para compartir configuración.
    usuario, destino = os.getenv("SMTP_USUARIO"), os.getenv("ALERTAS_DESTINATARIAS")
    if not (usuario and destino):
        return None
    servidor = os.getenv("SMTP_HOST") or "smtp.gmail.com"
    puerto = int(os.getenv("SMTP_PORT") or "587")
    # Gmail rechaza remitentes distintos de la cuenta autenticada.
    remitente = os.getenv("ALERTAS_REMITENTE") or usuario
    destinatarias = [d.strip() for d in destino.split(",") if d.strip()]

    msg = EmailMessage()
    msg["Subject"] = f"ODESOCAN · {asunto}"
    msg["From"] = formataddr(("ODESOCAN · Presupuestos 2027", remitente))
    msg["To"] = ", ".join(destinatarias)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=remitente.split("@")[-1])
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(cuerpo)
    msg.add_alternative(componer_html(asunto, cuerpo), subtype="html")
    try:
        with smtplib.SMTP(servidor, puerto, timeout=60) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(usuario, os.getenv("SMTP_CONTRASENA", ""))
            s.send_message(msg)
        log(f"Correo enviado a {msg['To']}")
        return True
    except (smtplib.SMTPException, OSError) as e:
        log(f"ERROR enviando correo: {e}")
        return False


def escribir_salidas_github(alerta: bool, hitos: list[str], resumen: str) -> None:
    if salida := os.getenv("GITHUB_OUTPUT"):
        with open(salida, "a", encoding="utf-8") as f:
            f.write(f"alerta={'true' if alerta else 'false'}\n")
            f.write(f"hitos={','.join(hitos)}\n")
    if sumario := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(sumario, "a", encoding="utf-8") as f:
            f.write(resumen + "\n")


# --------------------------------------------------------------------------
# Programa principal
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.getenv("URL_PRESUPUESTOS", URL_POR_DEFECTO))
    p.add_argument("--html", type=Path, help="analizar un HTML local en vez de descargar la página")
    p.add_argument("--estado", type=Path, default=ESTADO_POR_DEFECTO, help="fichero de estado JSON")
    p.add_argument("--simular", action="store_true", help="no enviar avisos externos (solo consola)")
    args = p.parse_args(argv)

    try:
        html = args.html.read_text(encoding="utf-8") if args.html else descargar(args.url)
        documentos, secciones = extraer_documentos(html, args.url)
    except Exception as e:  # noqa: BLE001 — cualquier fallo debe hacer fallar el job
        log(f"ERROR: {e}")
        escribir_salidas_github(False, [], f"### ❌ Error en la comprobación\n\n{e}")
        return 1

    log(f"Página analizada: {len(documentos)} documentos, secciones: {secciones}")

    estado = cargar_estado(args.estado)
    primera_vez = estado is None
    estado = estado or {"url": args.url, "documentos": {}, "hitos_notificados": {}}

    hitos = detectar_hitos(documentos, secciones)
    hitos_nuevos = {c: d for c, d in hitos.items() if c not in estado["hitos_notificados"]}
    # En la primera ejecución los documentos existentes son la línea base y
    # no generan aviso; los hitos sí, por si ya estuvieran publicados.
    docs_nuevos = [] if primera_vez else [d for d in documentos if d.url not in estado["documentos"]]

    if primera_vez:
        log("Primera ejecución: se registra la situación actual como línea base.")

    hay_aviso = bool(hitos_nuevos or docs_nuevos)
    if not hay_aviso:
        log("Sin novedades.")
        escribir_salidas_github(False, [], f"### Sin novedades\n\n{len(documentos)} documentos en {args.url}")
    else:
        asunto, cuerpo = componer_aviso(args.url, hitos_nuevos, docs_nuevos)
        print("\n" + "=" * 72 + f"\n{asunto}\n" + "-" * 72 + f"\n{cuerpo}\n" + "=" * 72 + "\n", flush=True)
        escribir_salidas_github(True, list(hitos_nuevos), f"### {asunto}\n\n{cuerpo}")

        if args.simular:
            log("Modo simulación: no se envían avisos externos.")
        else:
            resultados = [avisar_issue(asunto, cuerpo), avisar_email(asunto, cuerpo)]
            configurados = [r for r in resultados if r is not None]
            if not configurados:
                log("No hay canales externos configurados; el aviso queda solo en consola.")
            elif not any(configurados):
                # No marcamos nada como notificado para reintentar en la próxima ejecución.
                log("ERROR: ningún canal pudo enviar el aviso; se reintentará.")
                return 2

    momento = ahora()
    for c in hitos_nuevos:
        estado["hitos_notificados"][c] = momento
    for d in documentos:
        estado["documentos"].setdefault(d.url, {**asdict(d), "visto": momento})
    nueva_huella = huella(documentos)
    if primera_vez or hay_aviso or estado.get("huella") != nueva_huella:
        estado["url"] = args.url
        estado["huella"] = nueva_huella
        guardar_estado(args.estado, estado)
        log(f"Estado actualizado en {args.estado}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
