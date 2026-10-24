"""Aviso de renovación de la clave de AEMET (la usa .github/workflows/setas-meteo.yml en cada ejecución).

Las claves de AEMET OpenData caducan a los ~3 meses. La fecha va dentro de la propia clave (JWT, campo «exp»), así
que este aviso sirve también para las claves futuras sin tocar nada. Si faltan 15 días o menos (o la clave falta o
no se puede leer), abre un aviso (issue) en el repositorio con el procedimiento; GitHub lo envía por correo.
No abre un segundo aviso si ya hay uno abierto. Variables: AEMET_API_KEY, GH_TOKEN, GITHUB_REPOSITORY y AVISO_PRUEBA
(«1» = abre siempre el aviso con «(prueba)» en el título, para comprobar que el correo llega; Run workflow → prueba).
"""
import base64
import datetime as dt
import json
import os
import subprocess
import sys

DIAS_AVISO = 15
TITULO = "Renovar la clave de AEMET de Setas de España"

PROCEDIMIENTO = """La clave de AEMET OpenData que usa la tarea «Setas - datos de AEMET» **caduca el {caduca}** ({dias}).
Si caduca, el mapa de lluvia, temperatura y probabilidad de la app deja de actualizarse (sigue mostrando los últimos datos con su fecha).

## Cómo renovarla (5 minutos)

1. **Pide una clave nueva** en https://opendata.aemet.es/centrodedescargas/obtencionAPIKey con el correo `shantiappslab@gmail.com`. AEMET la envía por correo (es gratuita).
2. **Guárdala en el PC**: abre `C:\\Users\\paesb\\keystores\\aemet-api-key.txt` con el Bloc de notas, sustituye el texto por la clave nueva y guarda. No la pegues en ningún chat ni en git.
3. **Cámbiala en GitHub**: https://github.com/shantiappslab/shantiappslab.github.io/settings/secrets/actions → `AEMET_API_KEY` → icono del lápiz (**Update**) → pega la clave nueva → **Update secret**.
4. **Compruébalo**: https://github.com/shantiappslab/shantiappslab.github.io/actions → «Setas - datos de AEMET» → **Run workflow**. Debe terminar con ✓ verde.
5. **Cierra este aviso** (botón *Close issue*). Cuando la nueva clave esté a punto de caducar, se abrirá otro igual automáticamente.
"""


def caducidad(clave):
    payload = clave.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
    return dt.datetime.fromtimestamp(exp, dt.timezone.utc)


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def main():
    clave = os.environ.get("AEMET_API_KEY", "").strip()
    try:
        fin = caducidad(clave)
        dias = (fin - dt.datetime.now(dt.timezone.utc)).days
        texto_dias = "ya ha caducado" if dias < 0 else "quedan %d días" % dias
        print("La clave de AEMET caduca el %s (%s)" % (fin.strftime("%d-%m-%Y"), texto_dias))
    except Exception:  # noqa: BLE001
        fin, dias, texto_dias = None, -1, "no se ha podido leer la clave: falta o no es válida"
        print("No se puede leer la caducidad de la clave de AEMET")
    prueba = os.environ.get("AVISO_PRUEBA") == "1"
    if dias > DIAS_AVISO and not prueba:
        return
    titulo = TITULO + (" (prueba)" if prueba else "")
    repo = os.environ["GITHUB_REPOSITORY"]
    abiertos = [i for i in json.loads(gh("issue", "list", "--repo", repo, "--state", "open", "--search",
                                         TITULO + " in:title", "--json", "number,title")) if i["title"] == titulo]
    if abiertos:
        print("Ya hay un aviso abierto: #%d" % abiertos[0]["number"])
        return
    cuerpo = PROCEDIMIENTO.format(caduca=fin.strftime("%d-%m-%Y") if fin else "(desconocida)", dias=texto_dias)
    if prueba:
        cuerpo = "**Esto es una PRUEBA del aviso: no hace falta renovar nada todavía. Ciérralo.**\n\n" + cuerpo
    print(gh("issue", "create", "--repo", repo, "--title", titulo, "--body", cuerpo, "--assignee", "shantiappslab"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        # El aviso nunca debe impedir que se actualicen los datos
        print("No se pudo comprobar o crear el aviso:", e, file=sys.stderr)
