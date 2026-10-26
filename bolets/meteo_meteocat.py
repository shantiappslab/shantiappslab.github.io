"""Datos de lluvia y temperatura por comarca para la app «Bolets de Catalunya», a partir del Meteocat.

Lo ejecuta una tarea programada de GitHub Actions (.github/workflows/bolets-meteo.yml) cada 6 horas: la app solo
descarga el resultado (bolets/meteo.json, unos 6 KB), así que el portal de datos abiertos recibe las mismas pocas
consultas al día tenga la app los usuarios que tenga.

- Datos abiertos del Meteocat en el portal de la Generalitat (analisi.transparenciacatalunya.cat), sin clave:
  «Dades meteorològiques diàries de la XEMA» (7bvh-jvq2) y «Metadades estacions meteorològiques automàtiques»
  (yqwd-vj5e). Se piden sumas y medias ya agregadas por estación (respuestas de pocos KB).
- Es el mismo cálculo que hacía la app hasta la versión 0.5.1 (data/Meteo.kt): cada comarca toma la media de sus
  estaciones; si no tiene (p. ej. el Moianès) o no han enviado datos, la de las 3 más cercanas a su centro (≤ 40 km).
- Comarcas (id, nombre ICGC y centro): bolets/comarques_meteo.json, generado en BoletsApps
  (tools/catalunya/converteix.py).

Uso: python3 bolets/meteo_meteocat.py   (solo biblioteca estándar)
Si el portal falla, sale con error sin tocar meteo.json (la app sigue con los últimos datos buenos).
Fuente: Servei Meteorològic de Catalunya, datos abiertos de la Generalitat de Catalunya.
"""
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.environ.get("SALIDA") or os.path.join(AQUI, "meteo.json")
COMARCAS = os.path.join(AQUI, "comarques_meteo.json")
BASE = "https://analisi.transparenciacatalunya.cat/resource/"
DIARIAS = "7bvh-jvq2"
ESTACIONES = "yqwd-vj5e"
LLUVIA = "1300"  # Precipitación acumulada diaria
TEMPERATURA = "1000"  # Temperatura media diaria
DIST_MAX_KM = 40.0
UA = {"User-Agent": "BoletsDeCatalunya/1.0 (app gratuita; shantiappslab@gmail.com)", "Accept": "application/json"}


def consulta(dataset, params, intentos=4):
    url = BASE + dataset + ".json?" + urllib.parse.urlencode(params)
    for i in range(intentos):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            if i == intentos - 1:
                raise
            print("Reintento (%s): %s" % (e, url), file=sys.stderr)
            time.sleep(10 * (i + 1))


def offset_madrid(utc):
    """Diferencia de la hora de Madrid con UTC en el instante [utc]: +2 h en horario de verano (del último domingo de
    marzo al último domingo de octubre, a las 1:00 UTC), +1 h el resto del año. Sin zoneinfo (que en Windows no tiene datos)."""
    def ultimo_domingo(mes):
        d = dt.datetime(utc.year, mes, 31 if mes in (3, 10) else 30, 1, tzinfo=dt.timezone.utc)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    return dt.timedelta(hours=2 if ultimo_domingo(3) <= utc < ultimo_domingo(10) else 1)


def ahora_madrid():
    utc = dt.datetime.now(dt.timezone.utc)
    return utc + offset_madrid(utc)


def hace(dias):
    """Medianoche (hora de Madrid) de hace [dias] días, como en la app: «yyyy-MM-ddT00:00:00»."""
    d = ahora_madrid().date() - dt.timedelta(days=dias)
    return d.strftime("%Y-%m-%dT00:00:00")


def instante(s):
    """«2026-10-26T00:00:00.000» (hora de Madrid) → milisegundos."""
    local = dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    utc = local - offset_madrid(local - dt.timedelta(hours=1))
    return int(utc.timestamp() * 1000)


def agregado(funcion, variable, desde):
    filas = consulta(DIARIAS, {
        "$select": "codi_estacio,%s(valor) as v" % funcion,
        "$where": "codi_variable='%s' AND data_lectura>='%s'" % (variable, desde),
        "$group": "codi_estacio", "$limit": "1000",
    })
    return {f["codi_estacio"]: float(f["v"]) for f in filas if f.get("v") not in (None, "")}


def ultima_lluvia_fuerte(desde):
    """Código de estación → instante (ms) del último día con ≥ 10 mm."""
    filas = consulta(DIARIAS, {
        "$select": "codi_estacio,max(data_lectura) as d",
        "$where": "codi_variable='%s' AND valor>=10 AND data_lectura>='%s'" % (LLUVIA, desde),
        "$group": "codi_estacio", "$limit": "1000",
    })
    return {f["codi_estacio"]: instante(f["d"]) for f in filas if f.get("d")}


def main():
    comarcas = json.load(open(COMARCAS, encoding="utf-8"))
    filas = consulta(ESTACIONES, {
        "$select": "codi_estacio,nom_comarca,latitud,longitud",
        "$where": "nom_estat_ema='Operativa'", "$limit": "1000",
    })

    def num(s):
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    estaciones = {f["codi_estacio"]: (f["nom_comarca"], num(f.get("latitud")), num(f.get("longitud"))) for f in filas}
    p7, p15, p30 = agregado("sum", LLUVIA, hace(7)), agregado("sum", LLUVIA, hace(15)), agregado("sum", LLUVIA, hace(30))
    fuerte = ultima_lluvia_fuerte(hace(60))
    temp = agregado("avg", TEMPERATURA, hace(7))
    ultima = consulta(DIARIAS, {"$select": "max(data_lectura) as d", "$where": "codi_variable='%s'" % LLUVIA})[0]["d"]
    darrera = instante(ultima)
    if not p15 or not temp:
        sys.exit("El Meteocat no ha devuelto datos: no se toca meteo.json")

    def cercanas(c):
        k = math.cos(math.radians(c["lat"]))
        dist = [(cod, math.hypot((lon - c["lon"]) * k, lat - c["lat"]) * 111.0)
                for cod, (_, lat, lon) in estaciones.items() if lat is not None and lon is not None]
        return [cod for cod, d in sorted((x for x in dist if x[1] < DIST_MAX_KM), key=lambda x: x[1])]

    resultado = {}
    for c in comarcas:
        propias = [cod for cod, (nom, _, _) in estaciones.items() if nom == c["nom"]]
        cerca = None
        estimado = False

        def media(m):
            nonlocal cerca, estimado
            v = [m[e] for e in propias if e in m]
            if v:
                return sum(v) / len(v)
            if cerca is None:
                cerca = cercanas(c)
            v = [m[e] for e in [e for e in cerca if e in m][:3]]
            if v:
                estimado = True
                return sum(v) / len(v)
            return None

        datos = {"p7": media(p7), "p15": media(p15), "p30": media(p30)}
        # Días desde la última lluvia fuerte: con las mismas estaciones que la lluvia
        con_lluvia = [e for e in propias if e in p15]
        if not con_lluvia:
            if cerca is None:
                cerca = cercanas(c)
            con_lluvia = [e for e in cerca if e in p15][:3]
        fechas = [fuerte[e] for e in con_lluvia if e in fuerte]
        datos["dias"] = int((darrera - max(fechas)) / 86_400_000) if fechas else None
        datos["t7"] = media(temp)
        m = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in datos.items() if v is not None}
        if estimado:
            m["est"] = 1
        resultado[c["id"]] = m

    salida = {
        "comarcas": resultado,
        "fecha_datos": ultima[:10],
        "fuente": "Servei Meteorològic de Catalunya - dades obertes de la Generalitat de Catalunya",
        "generado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
    }
    with open(SALIDA, "w", encoding="utf-8", newline="\n") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print("%d comarcas, datos del %s" % (len(resultado), ultima[:10]))


if __name__ == "__main__":
    main()
