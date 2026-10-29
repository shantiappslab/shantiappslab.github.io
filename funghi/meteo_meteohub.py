"""Datos de lluvia y temperatura por zona para la app «Funghi d'Italia», a partir de MeteoHub (Agenzia ItaliaMeteo).

Lo ejecuta una tarea programada de GitHub Actions (.github/workflows/funghi-meteo.yml) cada 6 horas: la app solo
descarga el resultado (funghi/meteo.json, unos 30 KB), así que MeteoHub recibe las mismas pocas consultas al día tenga
la app los usuarios que tenga.

- API pública de MeteoHub (https://meteohub.agenziaitaliameteo.it/api/observations), sin clave. Solo las redes con
  licencia CC BY 4.0 (grupo de licencias CCBY_COMPLIANT): estaciones de los servicios meteorológicos regionales y de
  protección civil, ARPAE, y la red de aficionados MeteoNetwork (esta solo cuando una zona no tiene estaciones oficiales).
- Lluvia: suma de los datos brutos de cada estación (acumulados de 1 min a 1 h, según la red), pedidos en tramos de
  6 horas. No se usa el modo «daily» de la API: desde octubre de 2026 en los días recientes solo devuelve las últimas
  1-3 horas, y el filtro de horas mínimas dejaba esos días sin ninguna estación.
- Temperatura: media de las lecturas instantáneas a las 0, 6, 12 y 18 UTC (aproximación clásica de la media diaria).
- Sin cuenta, MeteoHub solo da los últimos ~10 días («to access archived data the user has to be logged»). Si existen
  las variables METEOHUB_USER y METEOHUB_PASSWORD (secretos del repositorio), se inicia sesión y se rellenan también los
  días más antiguos que falten, como mucho MAX_DIAS_NUEVOS por ejecución para no pasar del tiempo de la tarea. El
  historial (45 días de lluvia, 7 de temperatura) se va acumulando en funghi/estado_meteohub.json, que se guarda en la
  caché de Actions (no en git). Cada ejecución vuelve a pedir los últimos DIAS_PEDIR días y, además, los días aún
  accesibles que falten en el historial (p. ej. si se pierde la caché). Tras 15 días seguidos de ejecuciones ya está
  la lluvia de 15 días; tras 30, la de 30 días.
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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generador"))
import balanc_sol  # noqa: E402  humitat del sòl (balanç d'aigua)

UA = {"User-Agent": "FunghiDItalia/1.0 (app gratuita; shantiappslab@gmail.com)", "Accept": "application/json"}
LOGIN = "https://meteohub.agenziaitaliameteo.it/auth/login"


def inicia_sesion():
    """Con los secretos METEOHUB_USER y METEOHUB_PASSWORD, añade el token a UA. Devuelve True si hay sesión."""
    usuario, clave_acceso = os.environ.get("METEOHUB_USER"), os.environ.get("METEOHUB_PASSWORD")
    if not usuario or not clave_acceso:
        return False
    cuerpo = json.dumps({"username": usuario, "password": clave_acceso}).encode("utf-8")
    req = urllib.request.Request(LOGIN, data=cuerpo, headers=dict(UA, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print("Sin sesión en MeteoHub (%s): se sigue sin cuenta" % e, file=sys.stderr)
        return False
    token = resp if isinstance(resp, str) else next(
        (resp.get(k) for k in ("access_token", "token", "jwt") if isinstance(resp, dict) and resp.get(k)), None)
    if not token:
        print("Sin sesión en MeteoHub (respuesta sin token: %s): se sigue sin cuenta" % type(resp).__name__,
              file=sys.stderr)
        return False
    UA["Authorization"] = "Bearer " + token
    print("Sesión iniciada en MeteoHub")
    return True
DIAS_LLUVIA = 75  # 30 de lluvia + una semana para el «fa7»; la humedad del suelo usa toda la serie
DIAS_TEMP = 75  # la media de 7 días y la serie de la humedad del suelo
DIAS_MIN_SOL = 30  # días de historial necesarios para publicar la humedad del suelo
DIAS_PEDIR = 4  # días completos que se vuelven a pedir en cada ejecución (algunas redes envían con retraso)
DIAS_ANONIMO = 10  # hasta dónde se buscan días que falten en el historial sin cuenta (antes da HTTP 401)
MAX_DIAS_NUEVOS = 10  # días antiguos que faltan que se piden en cada ejecución con cuenta
MIN_ESTACIONES = 500  # un día con menos estaciones se considera incompleto
HORAS_TEMP = (0, 6, 12, 18)
HORAS_MIN_LLUVIA = 20  # horas del día cubiertas (en proporción, con datos de cualquier resolución)
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
    """{estación: mm} del día (UTC) con al menos HORAS_MIN_LLUVIA horas cubiertas.

    Cada estación puede dar el acumulado en varios pasos (p. ej. 15 min y 1 h): se usa el que más cubre del día. Los
    acumulados diarios (timerange 1,0,86400) no se usan: todas las estaciones que los dan tienen también horarios."""
    acumulados = {}  # (estación, paso en s) -> {hora de referencia: mm}
    for h in range(0, 24, 6):
        d = pide("reftime: >=%s %02d:00,<=%s %02d:59;product:B13011" % (dia, h, dia, h + 5))
        if d is None:
            return None
        for s in d.get("data", []):
            k = clave(s["stat"])
            pos[k] = [round(s["stat"]["lon"], 4), round(s["stat"]["lat"], 4), s["stat"]["net"]]
            for p in s["prod"]:
                try:
                    paso = int(p.get("trange", "").split(",")[2])
                except (IndexError, ValueError):
                    continue
                if p["var"] != "B13011" or not 0 < paso < 86400 or 86400 % paso:
                    continue
                vals = acumulados.setdefault((k, paso), {})
                for v in p["val"]:
                    if v.get("rel", 1) == 1 and v["ref"].startswith(dia) and v["val"] is not None and 0 <= v["val"] < 200:
                        vals[v["ref"]] = v["val"]
        del d
        time.sleep(2)
    mejor = {}  # estación -> (cobertura, paso, mm)
    for (k, paso), vals in acumulados.items():
        cobertura = min(1.0, len(vals) * paso / 86400)
        if cobertura >= HORAS_MIN_LLUVIA / 24 and (cobertura, paso) > mejor.get(k, (0, 0))[:2]:
            mejor[k] = (cobertura, paso, sum(vals.values()) / cobertura)
    return {k: round(mm, 1) for k, (_, _, mm) in mejor.items() if mm < 400}


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
    return {k: [round(sum(v.values()) / len(v), 1), round(max(v.values()), 1), round(min(v.values()), 1)]
            for k, v in lecturas.items() if len(v) >= 3}


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

    # Los últimos DIAS_PEDIR días completos (algunas redes envían los datos con retraso) y los días aún accesibles sin
    # cuenta que falten o estén incompletos en el estado; los demás, del estado.
    # Un día que vuelva con menos estaciones que las guardadas no se sobrescribe.
    con_cuenta = inicia_sesion()
    nuevos = 0
    for i in range(DIAS_LLUVIA if con_cuenta else DIAS_ANONIMO):
        dia = (ayer - dt.timedelta(days=i)).isoformat()
        pedir_l = i < DIAS_PEDIR or len(lluvia.get(dia, {})) < MIN_ESTACIONES
        pedir_t = i < DIAS_PEDIR or len(temp.get(dia, {})) < MIN_ESTACIONES
        if i >= DIAS_ANONIMO and (pedir_l or pedir_t):
            if nuevos >= MAX_DIAS_NUEVOS:
                print("historial: quedan días por pedir; siguen en la próxima ejecución")
                break
            nuevos += 1
        if pedir_l:
            l = lluvia_dia(dia, pos)
            if l is not None and len(l) >= len(lluvia.get(dia, {})):
                lluvia[dia] = l
            print("lluvia", dia, "sin acceso" if l is None else len(l), "estaciones")
            if l is None:
                break
            time.sleep(2)
        if pedir_t:
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
    if maximo < MIN_ESTACIONES:
        raise RuntimeError("Muy pocas estaciones con datos (%d): no se publica" % maximo)

    def t_media(v):
        """Temperatura guardada: [media, máx, mín] (o solo la media, en estados antiguos)."""
        return v[0] if isinstance(v, list) else v

    def resumen(k, desfase=0):
        def ventana(n, serie):
            vals = [serie.get((ref - dt.timedelta(days=i + desfase)).isoformat(), {}).get(k) for i in range(n)]
            vals = [t_media(v) if isinstance(v, list) else v for v in vals]
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
        if desfase:
            return r
        for i in range(DIAS_LLUVIA):
            p = lluvia.get((ref - dt.timedelta(days=i)).isoformat(), {}).get(k)
            if p is not None and p >= LLUVIA_FUERTE:
                r["dias"] = i
                break
        return r

    por_estacion = {k: resumen(k) for k in pos}
    por_estacion = {k: r for k, r in por_estacion.items() if r}
    hace7 = {k: resumen(k, 7) for k in por_estacion}  # bloque «fa7» para el parte semanal

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
        # Datos de hace una semana, con el mismo orden de grupos de estaciones
        antes = {}
        for campo in ("p7", "p15", "p30", "t7"):
            for estaciones, _ in grupos:
                vals = [hace7[e][campo] for e in estaciones if campo in hace7.get(e, {})]
                if vals:
                    antes[campo] = round(sum(vals) / len(vals), 1)
                    break
        if m and antes:
            m["fa7"] = antes
        # Humedad del suelo: serie diaria media del primer grupo de estaciones con lluvia
        usadas = next((ests for ests, _ in grupos if any(e in por_estacion for e in ests)), [])
        if m and usadas:
            serie = []
            for i in range(DIAS_LLUVIA - 1, -1, -1):
                d = (ref - dt.timedelta(days=i)).isoformat()
                ps = [lluvia.get(d, {}).get(e) for e in usadas]
                ps = [x for x in ps if x is not None]
                if not ps:
                    continue
                ts = [temp.get(d, {}).get(e) for e in usadas]
                ts = [x if isinstance(x, list) else [x, None, None] for x in ts if x is not None]

                def med(j):
                    xs = [x[j] for x in ts if x[j] is not None]
                    return sum(xs) / len(xs) if xs else None
                serie.append((dt.date.fromisoformat(d), sum(ps) / len(ps), med(0), med(1), med(2)))
            sol = balanc_sol.humitat(serie, cy, DIAS_MIN_SOL)
            if sol:
                m["sol"], m["sol14"], m["sol_retard"] = sol
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
