# Alerta · Presupuestos CAC 2027

Vigila la [página de Presupuestos 2027](https://www.gobiernodecanarias.org/hacienda/planificacionypresupuesto/presupuestos/2027/index.html)
de la Consejería de Hacienda del Gobierno de Canarias y avisa cuando se publica:

| Hito | Cómo se detecta |
|---|---|
| **Proyecto de Ley** de Presupuestos 2027 | sección titulada «Proyecto de Ley de Presupuestos…» o PDFs `…/Presupuestos/2027/TOMO-n-…` |
| **Ley** de Presupuestos 2027 (aprobada) | sección titulada «Ley de Presupuestos…» o PDFs bajo `…/Presupuestos/2027/ley/` |
| Cualquier documento nuevo | enlace que no estaba en `estado.json` (aviso secundario) |

Los criterios se basan en cómo se publicaron los presupuestos de 2026 en esa misma web.
Cada hito se avisa **una sola vez**.

## Estructura

```
alerta_presupuestos.py      script principal
requirements.txt            requests + beautifulsoup4
.github/workflows/alerta.yml  ejecución programada en GitHub Actions
estado.json                 lo ya visto y avisado (lo commitea el workflow)
simulacro/                  pruebas locales con páginas simuladas
```

## Simulacro local

```bash
pip install -r requirements.txt
python simulacro/generar_fixtures.py   # rehace las páginas de prueba desde la web real
python simulacro/simulacro.py --real   # 9 escenarios + una pasada contra la web real
```

Escenarios: línea base, sin cambios, publicación del proyecto, no duplicar aviso,
portal en mantenimiento, publicación de la ley, arranque tardío y cambio de maqueta.
El simulacro nunca envía avisos ni toca `estado.json`.

## Despliegue

Repositorio: <https://github.com/Cristianodesocan/presupuestos-2027>. Sigue el mismo
patrón que `Odesocan/transparencia-gobcan`:

- **Cadencia** (lunes a viernes, hora canaria = UTC en invierno): 8:17 y 8:47, luego
  cada hora a las :17 hasta las 19:17, y cierre diario a las 21:07 todos los días.
  Minutos no redondos porque GitHub descarta más trabajos en :00 y :30.
- **Correo** por Gmail (STARTTLS, 587) con las mismas variables que transparencia:

  | Tipo | Nombre | Contenido |
  |---|---|---|
  | Secreto | `SMTP_USUARIO` | cuenta de Gmail que envía |
  | Secreto | `SMTP_CONTRASENA` | contraseña de aplicación de esa cuenta |
  | Variable | `ALERTAS_DESTINATARIAS` | direcciones separadas por comas |

  ```bash
  gh secret set SMTP_USUARIO -R Cristianodesocan/presupuestos-2027
  gh secret set SMTP_CONTRASENA -R Cristianodesocan/presupuestos-2027
  gh variable set ALERTAS_DESTINATARIAS -R Cristianodesocan/presupuestos-2027 --body "a@odesocan.org,b@odesocan.org"
  ```

  Sin ellos el aviso llega solo como issue del repositorio.
- **Issue** en el repo por cada aviso (con *Watch → All activity* también llega por correo).
- **Estado**: `estado.json` se commitea solo cuando cambia la página.
- **Ejecución manual**: Actions → *Alerta Presupuestos CAC 2027* → *Run workflow*
  (con la casilla «Simulación» no avisa ni guarda nada).

## Comportamiento ante fallos

- Si la web no responde o no parece la página de 2027 (mantenimiento, cambio de URL),
  el job falla (código 1) y GitHub lo notifica; el estado no se modifica.
- Si se detecta un hito pero ningún canal consigue avisar, el job falla (código 2) y
  se reintenta en la siguiente ejecución.
- Si cambia la maquetación del portal, el script recurre a buscar todos los enlaces a
  documentos de `Presupuestos/2027`.
- GitHub desactiva los cron tras 60 días sin actividad en el repo: el cierre diario
  vuelve a habilitar el workflow por la API para que no ocurra entre el proyecto y la ley.
