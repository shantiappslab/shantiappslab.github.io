"""Datos de lluvia y temperatura por zona para la app «Funghi d'Italia», a partir de MeteoHub (Agenzia ItaliaMeteo).

Lo ejecuta una tarea programada de GitHub Actions (.github/workflows/funghi-meteo.yml) cada 6 horas: la app solo
descarga el resultado (funghi/meteo.json, unos 30 KB), así que MeteoHub recibe las mismas pocas consultas al día tenga
la app los usuarios que tenga.

- API pública de MeteoHub (https://meteohub.agenziaitaliameteo.it/api/observations), sin clave. Solo las redes con
  licencia CC BY 4.0 (grupo de licencias CCBY_COMPLIANT): estaciones de los servicios meteorológicos regionales y de
  protección civil, ARPAE, y la red de aficionados MeteoNetwork (esta solo cuando una zona no tiene estaciones oficiales).
- Lluvia: suma de los valores horarios de cada día (modo «daily» de la API, que ya los armoniza a una hora).
- Temperatura: media de las lecturas instantáneas a las 0, 6, 12 y 18 UTC (aproximación clásica de la media diaria).
- Sin cuenta, MeteoHub solo da los últimos ~4 días («to access archived data the user has to be logged»): el
  historial (45 días de lluvia, 7 de temperatura) se va acumulando en funghi/estado_meteohub.json, que se guarda en la
  caché de Actions (no en git). Cada ejecución vuelve a pedir los últimos DIAS_PEDIR días. Tras 15 días seguidos de
  ejecuciones ya están la lluvia de 15 días y el índice; tras 30, la lluvia de 30 días.
- Cada zona toma la media de sus estaciones oficiales; si no tiene, la de las 3 más cercanas (≤ 25 km) y, si tampoco,
  las de aficionados.

Uso: python3 funghi/meteo_meteohub.py   (solo biblioteca estándar)
Si MeteoHub falla, sale con error sin tocar meteo.json (la app sigue con los últimos datos buenos).
Fuente: Agenzia ItaliaMeteo – MeteoHub, datos de las redes regionales (CC BY 4.0).
"""
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
API = "https://meteohub.agenziaitaliameteo.it/api/observations"
SALIDA = os.environ.get("SALIDA") or os.path.join(AQUI, "meteo.json")
# Fracción de días con dato que pide cada ventana. Solo para generar datos de prueba con poco historial se baja
# (UMBRAL_DIAS=0.4); la tarea publica siempre con 0,8.
UMBRAL_DIAS = float(os.environ.get("UMBRAL_DIAS") or 0.8)
ESTADO = os.path.join(AQUI, "estado_meteohub.json")
ZONAS = os.path.join(AQUI, "zonas_meteo.json")
UA = {"User-Agent": "FunghiDItalia/1.0 (app gratuita; shantiappslab@gmail.com)", "Accept": "application/json"}
DIAS_LLUVIA = 45
DIAS_TEMP = 7
DIAS_PEDIR = 4  # días completos que se vuelven a pedir en cada ejecución (el acceso anónimo no llega más atrás)
HORAS_TEMP = (0, 6, 12, 18)
HORAS_MIN_LLUVIA = 20
DIST_MAX_KM = 25
LLUVIA_FUERTE = 10.0  # mm en un día
AFICIONADOS = "mnw"


def pide(q, extra=None, intentos=4):
    """Respuesta de la API, o None si el día ya no es accesible sin cuenta (HTTP 401)."""
    params = {"q": q + ";license:CCBY_COMPLIANT"}
    params.update(extra or {})
    url = API + "?" + urllib.parse.urlencode(params)
    for i in range(intentos):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=240) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return None
            print("  error (%s), reintento %d" % (e, i + 1), file=sys.stderr)
            time.sleep(15 * (i + 1))
        except Exception as e:  # noqa: BLE001
            print("  error (%s), reintento %d" % (e, i + 1), file=sys.stderr)
            time.sleep(15 * (i + 1))
    raise RuntimeError("MeteoHub no responde: " + q)


def clave(stat):
    return "%s|%.4f|%.4f" % (stat["net"], stat["lat"], stat["lon"])


def lluvia_dia(dia, pos):
    """{estación: mm} del día (UTC) con al menos HORAS_MIN_LLUVIA horas."""
    d = pide("reftime: >=%s 00:00,<=%s 23:59;product:B13011" % (dia, dia), {"daily": "true"})
    if d is None:
        return None
    res = {}
    for s in d.get("data", []):
        k = clave(s["stat"])
        pos[k] = [round(s["stat"]["lon"], 4), round(s["stat"]["lat"], 4), s["stat"]["net"]]
        for p in s["prod"]:
            if p["var"] != "B13011":
                continue
            horas = {}
            for v in p["val"]:
                if v.get("rel", 1) == 1 and v["ref"].startswith(dia) and v["val"] is not None and 0 <= v["val"] < 200:
                    horas[v["ref"][11:13]] = v["val"]
            if len(horas) >= HORAS_MIN_LLUVIA:
                total = sum(horas.values()) * 24 / len(horas)
                if total < 400:
                    res[k] = round(total, 1)
    return res


def temperatura_dia(dia, pos):
    """{estación: °C} del día: media de las lecturas a las HORAS_TEMP (al menos 3 de 4)."""
    lecturas = {}
    for h in HORAS_TEMP:
        d = pide("reftime: >=%s %02d:00,<=%s %02d:09;product:B12101;timerange:254,0,0;level:103,2000,0,0" % (dia, h, dia, h))
        if d is None:
            return None
        for s in d.get("data", []):
            k = clave(s["stat"])
            pos[k] = [round(s["stat"]["lon"], 4), round(s["stat"]["lat"], 4), s["stat"]["net"]]
            for p in s["prod"]:
                vals = [v["val"] for v in p["val"] if v.get("rel", 1) == 1 and v["val"] is not None]
                vals = [v - 273.15 for v in vals if 238 < v < 323]  # kelvin; fuera de -35..50 °C, error del sensor
                if vals:
                    lecturas.setdefault(k, {})[h] = vals[0]
        time.sleep(2)
    return {k: round(sum(v.values()) / len(v), 1) for k, v in lecturas.items() if len(v) >= 3}


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
    hoy = dt.datetime.now(dt.timezone.utc).date()
    ayer = hoy - dt.timedelta(days=1)
    estado = json.load(open(ESTADO, encoding="utf-8")) if os.path.exists(ESTADO) else {}
    pos = estado.get("pos", {})
    lluvia = estado.get("lluvia", {})
    temp = estado.get("temp", {})

    # Los últimos DIAS_PEDIR días completos (algunas redes envían los datos con retraso); los anteriores, del estado.
    # Un día que vuelva con menos estaciones que las guardadas (p. ej. ya fuera del acceso anónimo) no se sobrescribe.
    for i in range(DIAS_PEDIR):
        dia = (ayer - dt.timedelta(days=i)).isoformat()
        l = lluvia_dia(dia, pos)
        if l is not None and len(l) >= len(lluvia.get(dia, {})):
            lluvia[dia] = l
        print("lluvia", dia, "sin acceso" if l is None else len(l), "estaciones")
        time.sleep(2)
        t = temperatura_dia(dia, pos)
        if t is not None and len(t) >= len(temp.get(dia, {})):
            temp[dia] = t
        print("temperatura", dia, "sin acceso" if t is None else len(t), "estaciones")
    limite_l = (ayer - dt.timedelta(days=DIAS_LLUVIA - 1)).isoformat()
    limite_t = (ayer - dt.timedelta(days=DIAS_TEMP - 1)).isoformat()
    lluvia = {d: v for d, v in lluvia.items() if d >= limite_l}
    temp = {d: v for d, v in temp.items() if d >= limite_t}

    if not lluvia:
        raise RuntimeError("Sin datos de lluvia: no se publica")
    # Día de referencia: el último con datos de lluvia de la mayoría de estaciones
    maximo = max(len(v) for v in lluvia.values())
    referencia = max(d for d, v in lluvia.items() if len(v) >= 0.6 * maximo)
    ref = dt.date.fromisoformat(referencia)
    if maximo < 500:
        raise RuntimeError("Muy pocas estaciones con datos (%d): no se publica" % maximo)

    def resumen(k):
        def ventana(n, serie):
            vals = [serie.get((ref - dt.timedelta(days=i)).isoformat(), {}).get(k) for i in range(n)]
            vals = [v for v in vals if v is not None]
            return vals if len(vals) >= UMBRAL_DIAS * n else None
        r = {}
        for n in (7, 15, 30):
            v = ventana(n, lluvia)
            if v is not None:
                r["p%d" % n] = sum(v) * n / len(v)  # días sueltos sin dato: proporcional
        t = ventana(7, temp)
        if t is not None:
            r["t7"] = sum(t) / len(t)
        for i in range(DIAS_LLUVIA):
            p = lluvia.get((ref - dt.timedelta(days=i)).isoformat(), {}).get(k)
            if p is not None and p >= LLUVIA_FUERTE:
                r["dias"] = i
                break
        return r

    por_estacion = {k: resumen(k) for k in pos}
    por_estacion = {k: r for k, r in por_estacion.items() if r}

    salida = {}
    for z in json.load(open(ZONAS, encoding="utf-8")):
        x0, x1, y0, y1 = z["caja"]
        cx, cy = z["centro"]
        k = math.cos(math.radians(cy))
        dentro_zona = [e for e in por_estacion
                       if x0 <= pos[e][0] <= x1 and y0 <= pos[e][1] <= y1 and dentro(z["poligonos"], pos[e][0], pos[e][1])]
        cerca = sorted((math.hypot((pos[e][0] - cx) * k, pos[e][1] - cy) * 111, e) for e in por_estacion)
        cerca = [e for dist, e in cerca if dist <= DIST_MAX_KM]
        oficiales = [e for e in dentro_zona if pos[e][2] != AFICIONADOS]
        grupos = [(oficiales, False), ([e for e in cerca if pos[e][2] != AFICIONADOS][:3], True),
                  ([e for e in dentro_zona if pos[e][2] == AFICIONADOS], True), ([e for e in cerca if pos[e][2] == AFICIONADOS][:3], True)]
        m, estimado = {}, False
        for campo in ("p7", "p15", "p30", "t7"):
            for estaciones, est in grupos:
                vals = [por_estacion[e][campo] for e in estaciones if campo in por_estacion[e]]
                if vals:
                    m[campo] = round(sum(vals) / len(vals), 1)
                    estimado = estimado or est
                    break
        # Días desde la última lluvia fuerte: con el mismo grupo de estaciones que la lluvia de 15 días
        for estaciones, _ in grupos:
            con_lluvia = [e for e in estaciones if "p15" in por_estacion[e]]
            if con_lluvia:
                dias = [por_estacion[e]["dias"] for e in con_lluvia if "dias" in por_estacion[e]]
                if dias:
                    m["dias"] = min(dias)
                break
        if m:
            if estimado:
                m["est"] = 1
            salida[z["id"]] = m

    resultado = {
        "version": 1,
        "fecha_datos": referencia,
        "generado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuente": "Agenzia ItaliaMeteo - MeteoHub (CC BY 4.0)",
        "comarcas": salida,
    }
    with open(SALIDA, "w", encoding="utf-8", newline="\n") as f:
        json.dump(resultado, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with open(ESTADO, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"pos": pos, "lluvia": lluvia, "temp": temp}, f, separators=(",", ":"), sort_keys=True)
    print("OK: datos hasta %s, %d estaciones, %d de %d zonas" % (referencia, len(por_estacion), len(salida),
                                                               len(json.load(open(ZONAS, encoding="utf-8")))))


if __name__ == "__main__":
    main()
