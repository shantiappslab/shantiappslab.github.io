"""Lluvia y temperatura por departamento para la app «Champignons de France», con los datos diarios de Météo-France.

Tarea programada de GitHub Actions en shantiappslab.github.io (champignons-meteo.yml → champignons/meteo.json, cada
6 horas), como bolets/meteo_meteocat.py; servicio aprobado por el usuario el 2026-10-27. Allí se copian este script y
comarcas.json como champignons/departements_meteo.json. En local genera también los datos de prueba de la versión de
depuración (app/src/francaDebug/assets/meteo_prueba.json).

- Fuente: Météo-France, «Données climatologiques de base – quotidiennes» (data.gouv.fr, Licence Ouverte Etalab 2.0,
  uso comercial permitido citando «Météo-France»), sin clave. Archivos por departamento en el bucket público
  https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/QUOT/Q_<dep>_latest-<años>_RR-T-Vent.csv.gz
  (la ruta antigua de object.files.data.gouv.fr dejó de actualizarse en junio de 2026). Se publican una vez al día
  con el día anterior completo; el nombre «latest-…» se busca en el listado del bucket (cambia con el año).
- Cada estación se asigna a su departamento con los límites de comarcas.json (así Córcega, un solo archivo «20»,
  queda en 2A y 2B). Lluvia: RR (descartando QRR = 2, dudosa); temperatura: TM o, si falta, TNTXM.
- Último día con datos: el más reciente en que al menos el 80 % de las estaciones tienen RR.
- Cada departamento toma la media de sus estaciones con al menos el 80 % de días con dato; si no tiene ninguna, la de las
  3 más cercanas a su centro (≤ 40 km), marcado como estimado («est»). Mismo formato de salida que Setas, Funghi y Bolets.

Uso: python -I -X utf8 franca/tools/meteo_meteofrance.py [salida.json]  (en la web: python3 champignons/meteo_meteofrance.py champignons/meteo.json)   (solo biblioteca estándar)
"""
import csv
import datetime as dt
import gzip
import io
import json
import math
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(ROOT), "app", "src", os.path.basename(ROOT))
# En la web, el script va en champignons/ junto a departements_meteo.json (copia de comarcas.json de la app)
_JUNTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "departements_meteo.json")
COMARCAS = os.environ.get("COMARCAS") or (_JUNTO if os.path.exists(_JUNTO) else
                                          os.path.join(APP, "assets", "content", "comarcas.json"))
BUCKET = "https://meteofrance.s3.sbg.io.cloud.ovh.net/"
PREFIX = "data/synchro_ftp/BASE/QUOT/Q_"
UA = {"User-Agent": "ChampignonsDeFrance/1.0 (app gratuite; shantiappslab@gmail.com)"}
DIAS = 60
DIST_MAX_KM = 40.0
LLUVIA_FUERTE = 10.0


def baja(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        return r.read()


def num(s):
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def dentro(anillos, x, y):
    ok = False
    for r in anillos:
        j = len(r) - 1
        for i in range(len(r)):
            xi, yi = r[i]
            xj, yj = r[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                ok = not ok
            j = i
    return ok


def main():
    salida = sys.argv[1] if len(sys.argv) > 1 else "meteo.json"
    comarcas = json.load(open(COMARCAS, encoding="utf-8"))["comarcas"]
    cajas = {}
    for c in comarcas:
        xs = [p[0] for r in c["poligonos"] for p in r]
        ys = [p[1] for r in c["poligonos"] for p in r]
        cajas[c["id"]] = (min(xs), max(xs), min(ys), max(ys))

    listado = baja(BUCKET + "?list-type=2&prefix=" + PREFIX).decode("utf-8")
    claves = sorted(set(re.findall(r"<Key>(data/synchro_ftp/BASE/QUOT/Q_(\d\d)_latest-[0-9-]+_RR-T-Vent\.csv\.gz)</Key>", listado)))
    claves = [k for k, dep in claves if dep != "00" and int(dep) <= 95]
    if len(claves) < 90:
        sys.exit("El listado de Météo-France solo tiene %d departamentos: no se toca nada" % len(claves))
    desde = (dt.date.today() - dt.timedelta(days=DIAS + 5)).strftime("%Y%m%d")

    estaciones = {}  # NUM_POSTE → {"lat", "lon", "rr": {fecha: mm}, "t": {fecha: °C}}
    for k in claves:
        texto = gzip.decompress(baja(BUCKET + k)).decode("utf-8", "replace")
        filas = csv.reader(io.StringIO(texto), delimiter=";")
        cab = [h.strip() for h in next(filas)]
        i = {h: n for n, h in enumerate(cab)}
        for f in filas:
            if len(f) < len(cab) or f[i["AAAAMMJJ"]] < desde:
                continue
            e = estaciones.setdefault(f[i["NUM_POSTE"]], {"lat": num(f[i["LAT"]]), "lon": num(f[i["LON"]]), "rr": {}, "t": {}})
            d = f[i["AAAAMMJJ"]]
            rr = num(f[i["RR"]])
            if rr is not None and f[i["QRR"]].strip() != "2":
                e["rr"][d] = rr
            t = num(f[i["TM"]]) if "TM" in i else None
            if t is None and "TNTXM" in i:
                t = num(f[i["TNTXM"]])
            if t is not None:
                e["t"][d] = t

    # Último día con datos: el más reciente con RR en al menos el 80 % de las estaciones activas
    activas = [e for e in estaciones.values() if e["rr"]]
    fechas = sorted({d for e in activas for d in e["rr"]}, reverse=True)
    ultimo = next(d for d in fechas if sum(d in e["rr"] for e in activas) >= 0.8 * len(activas))
    ult = dt.datetime.strptime(ultimo, "%Y%m%d").date()

    def dias_atras(n):
        return [(ult - dt.timedelta(days=k)).strftime("%Y%m%d") for k in range(n)]

    def suma(e, n):
        ds = dias_atras(n)
        v = [e["rr"][d] for d in ds if d in e["rr"]]
        return sum(v) * n / len(v) if len(v) >= 0.8 * n else None

    def media_t(e):
        v = [e["t"][d] for d in dias_atras(7) if d in e["t"]]
        return sum(v) / len(v) if len(v) >= 5 else None

    def ultima_fuerte(e):
        for k, d in enumerate(dias_atras(DIAS)):
            if e["rr"].get(d, 0) >= LLUVIA_FUERTE:
                return k
        return None

    # Departamento de cada estación
    por_dep = {}
    for cod, e in estaciones.items():
        if e["lat"] is None or e["lon"] is None:
            continue
        for c in comarcas:
            x0, x1, y0, y1 = cajas[c["id"]]
            if x0 <= e["lon"] <= x1 and y0 <= e["lat"] <= y1 and dentro(c["poligonos"], e["lon"], e["lat"]):
                por_dep.setdefault(c["id"], []).append(cod)
                break

    resultado = {}
    for c in comarcas:
        x0, x1, y0, y1 = cajas[c["id"]]
        clat, clon = (y0 + y1) / 2, (x0 + x1) / 2
        propias = por_dep.get(c["id"], [])
        k = math.cos(math.radians(clat))
        cercanas = sorted((math.hypot((e["lon"] - clon) * k, e["lat"] - clat) * 111.0, cod)
                          for cod, e in estaciones.items() if e["lat"] is not None and e["lon"] is not None)
        cercanas = [cod for d, cod in cercanas if d <= DIST_MAX_KM]
        estimado = False

        def media(f):
            nonlocal estimado
            v = [x for x in (f(estaciones[s]) for s in propias) if x is not None]
            if v:
                return sum(v) / len(v)
            v = [x for x in (f(estaciones[s]) for s in cercanas) if x is not None][:3]
            if v:
                estimado = True
                return sum(v) / len(v)
            return None

        m = {"p7": media(lambda e: suma(e, 7)), "p15": media(lambda e: suma(e, 15)), "p30": media(lambda e: suma(e, 30)),
             "t7": media(media_t)}
        fuertes = [x for x in (ultima_fuerte(estaciones[s]) for s in (propias or cercanas[:3])) if x is not None]
        m["dias"] = min(fuertes) if fuertes else None
        m = {kk: (round(v, 2) if isinstance(v, float) else v) for kk, v in m.items() if v is not None}
        if estimado:
            m["est"] = 1
        resultado[c["id"]] = m

    datos = {"comarcas": resultado, "fecha_datos": ult.isoformat(),
             "fuente": "Météo-France - données climatologiques de base quotidiennes (Licence Ouverte Etalab 2.0)",
             "generado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": 1}
    with open(salida, "w", encoding="utf-8", newline="\n") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    sin = [c["id"] for c in comarcas if "p15" not in resultado[c["id"]]]
    print("%d departamentos, %d estaciones, datos del %s; sin lluvia: %s" % (len(resultado), len(estaciones), ult, sin or "-"))


if __name__ == "__main__":
    main()
