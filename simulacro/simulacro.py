#!/usr/bin/env python3
"""
Simulacro de la alerta: reproduce la secuencia de publicación esperada sobre
páginas de prueba (ver generar_fixtures.py) y comprueba que el script avisa
exactamente cuando debe, sin duplicar avisos.

  python simulacro/simulacro.py            # escenarios con páginas de prueba
  python simulacro/simulacro.py --real     # además, una pasada contra la web real

No envía nada fuera: todas las ejecuciones usan --simular y un estado temporal.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "alerta_presupuestos.py"
FIX = Path(__file__).resolve().parent / "fixtures"


def ejecutar(estado: Path, html: str | None, salida_gh: Path) -> tuple[int, str, dict]:
    salida_gh.write_text("")
    entorno = {k: v for k, v in os.environ.items()
               if not k.startswith(("SMTP_", "EMAIL_", "GITHUB_"))}
    entorno["GITHUB_OUTPUT"] = str(salida_gh)
    cmd = [sys.executable, str(SCRIPT), "--simular", "--estado", str(estado)]
    if html:
        cmd += ["--html", str(FIX / html)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=entorno)
    salidas = dict(l.split("=", 1) for l in salida_gh.read_text().splitlines() if "=" in l)
    return r.returncode, r.stdout + r.stderr, salidas


# (descripción, fixture, código esperado, alerta esperada, hitos esperados)
ESCENARIOS = [
    ("1. Primera ejecución con la página actual (línea base)", "01_actual.html", 0, "false", ""),
    ("2. Nueva comprobación sin cambios", "01_actual.html", 0, "false", ""),
    ("3. Se publica el PROYECTO de Ley 2027", "02_proyecto.html", 0, "true", "proyecto"),
    ("4. Siguiente comprobación: no se repite el aviso", "02_proyecto.html", 0, "false", ""),
    ("5. Portal en mantenimiento (error, estado intacto)", "04_mantenimiento.html", 1, "false", ""),
    ("6. Se publica la LEY de Presupuestos 2027", "03_ley.html", 0, "true", "ley"),
    ("7. Siguiente comprobación: no se repite el aviso", "03_ley.html", 0, "false", ""),
]

# Ejecuciones independientes, cada una con estado vacío.
ESCENARIOS_AISLADOS = [
    ("8. Arranque tardío: el proyecto ya estaba publicado", "02_proyecto.html", 0, "true", "proyecto"),
    ("9. Cambio de maquetación del portal", "05_maqueta_nueva.html", 0, "true", "proyecto"),
]


def comprobar(desc, html, codigo, alerta, hitos, estado, tmp, verbose) -> bool:
    rc, log, out = ejecutar(estado, html, tmp / "gh_output")
    ok = rc == codigo and out.get("alerta") == alerta and out.get("hitos", "") == hitos
    marca = "✅" if ok else "❌"
    print(f"{marca} {desc}\n     código={rc} alerta={out.get('alerta')} hitos={out.get('hitos', '') or '-'}")
    if verbose or not ok:
        print("     " + log.strip().replace("\n", "\n     "))
    return ok


def main() -> int:
    verbose = "-v" in sys.argv
    if not FIX.exists():
        print("Faltan las páginas de prueba: ejecuta antes simulacro/generar_fixtures.py")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="simulacro_"))
    resultados = []
    try:
        estado = tmp / "estado.json"
        for esc in ESCENARIOS:
            antes = estado.read_text() if estado.exists() else None
            resultados.append(comprobar(*esc, estado, tmp, verbose))
            if esc[2] == 1 and estado.read_text() != antes:
                print("❌     el estado cambió tras un error")
                resultados.append(False)

        final = json.loads(estado.read_text())
        print(f"\n   Estado final: {len(final['documentos'])} documentos, "
              f"hitos notificados: {sorted(final['hitos_notificados'])}\n")

        for esc in ESCENARIOS_AISLADOS:
            resultados.append(comprobar(*esc, tmp / f"estado_{esc[1]}.json", tmp, verbose))

        if "--real" in sys.argv:
            print("\n— Pasada contra la web real (estado temporal, sin avisos externos) —")
            rc, log, out = ejecutar(tmp / "estado_real.json", None, tmp / "gh_output")
            print("     " + log.strip().replace("\n", "\n     "))
            resultados.append(rc == 0)
            print(f"{'✅' if rc == 0 else '❌'} Web real: código={rc} alerta={out.get('alerta')} "
                  f"hitos={out.get('hitos') or '-'}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total, bien = len(resultados), sum(resultados)
    print(f"\nResultado: {bien}/{total} comprobaciones correctas")
    return 0 if bien == total else 1


if __name__ == "__main__":
    sys.exit(main())
