"""Datos de lluvia y temperatura por comarca agraria para la app «Setas de España», a partir de AEMET OpenData.

Lo ejecuta una tarea programada de GitHub Actions (.github/workflows/setas-meteo.yml) cada 6 horas: la app solo
descarga el resultado (setas/meteo.json, unos 30 KB), así que AEMET recibe las mismas pocas consultas al día tenga la
app los usuarios que tenga, y la clave no va dentro de la app.

- Valores climatológicos diarios de todas las estaciones de los últimos 60 días (4 consultas de 15 días: es el máximo
  que admite AEMET). Llegan con 3–4 días de retraso.
- Para esos últimos días, las observaciones horarias (que solo cubren ~12 h) se van guardando en
  setas/estado_obs.json en cada ejecución y se suman por día.
- Cada comarca toma la media de sus estaciones; si no tiene ninguna con datos, la de las 3 más cercanas (≤ 50 km).

Uso: AEMET_API_KEY=<clave> python3 setas/meteo_aemet.py   (solo biblioteca estándar)
Si AEMET falla, sale con error sin tocar meteo.json (la app sigue con los últimos datos buenos).
Fuente de los datos: © AEMET (Agencia Estatal de Meteorología).
"""
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generador"))
import balanc_sol  # noqa: E402  humitat del sòl (balanç d'aigua)

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = "https://opendata.aemet.es/opendata"
SALIDA = os.path.join(AQUI, "meteo.json")
ESTADO = os.path.join(AQUI, "estado_obs.json")
COMARCAS = os.path.join(AQUI, "comarcas_meteo.json")
DIAS = 75  # 60 per al «fa7» i la lluvia; la humedad del suelo necesita ~60 días de serie
DIAS_ESTADO = 12
HORAS_MIN = 18  # horas observadas para dar por bueno un día a partir de las observaciones
DIST_MAX_KM = 50
LLUVIA_FUERTE = 10.0  # mm en un día


def pide(url, clave=None, intentos=4):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"api_key": clave} if clave else {})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            print("  error (%s), reintento %d" % (e, i + 1), file=sys.stderr)
            time.sleep(10 * (i + 1))
    raise RuntimeError("AEMET no responde: " + url)


def consulta(ruta, clave):
    """Las respuestas de AEMET van en dos pasos: primero un JSON con la URL de los datos."""
    meta = json.loads(pide(BASE + ruta, clave).decode("latin-1"))
    if meta.get("estado") != 200:
        raise RuntimeError("%s → %s %s" % (ruta, meta.get("estado"), meta.get("descripcion")))
    return json.loads(pide(meta["datos"]).decode("latin-1"))


def numero(v):
    if v is None:
        return None
    v = str(v).strip().replace(",", ".")
    if v == "Ip":  # inapreciable
        return 0.0
    try:
        return float(v)
    except ValueError:
        return None  # «Acum», «Varias»…


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


def dms(s):
    v = int(s[:2]) + int(s[2:4]) / 60 + int(s[4:6]) / 3600 if len(s) == 7 else \
        int(s[:3]) + int(s[3:5]) / 60 + int(s[5:7]) / 3600
    return -v if s[-1] in "SW" else v


def main():
    clave = os.environ.get("AEMET_API_KEY", "").strip()
    if not clave:
        sys.exit("Falta AEMET_API_KEY")
    hoy = dt.datetime.now(dt.timezone.utc).date()

    # 1. Observaciones horarias (últimas ~12 h), acumuladas en el estado: estado[estacion][dia][hora] = [prec, ta]
    estado = json.load(open(ESTADO, encoding="utf-8")) if os.path.exists(ESTADO) else {}
    for o in consulta("/api/observacion/convencional/todas", clave):
        idema, fint = o.get("idema"), o.get("fint", "")
        if not idema or len(fint) < 13:
            continue
        dia, hora = fint[:10], fint[11:13]
        estado.setdefault(idema, {}).setdefault(dia, {})[hora] = [numero(o.get("prec")), numero(o.get("ta"))]
    limite = (hoy - dt.timedelta(days=DIAS_ESTADO)).isoformat()
    estado = {e: {d: h for d, h in dias.items() if d >= limite} for e, dias in estado.items()}
    estado = {e: dias for e, dias in estado.items() if dias}

    # 2. Valores diarios (60 días en tramos de 15)
    diario = {}  # estacion → dia → (prec, tmed)
    for tramo in range(DIAS // 15):
        fin = hoy - dt.timedelta(days=1 + 15 * tramo)
        ini = fin - dt.timedelta(days=14)
        datos = consulta("/api/valores/climatologicos/diarios/datos/fechaini/%sT00:00:00UTC/fechafin/%sT23:59:59UTC/todasestaciones"
                         % (ini.isoformat(), fin.isoformat()), clave)
        for d in datos:
            diario.setdefault(d["indicativo"], {})[d["fecha"]] = (numero(d.get("prec")), numero(d.get("tmed")),
                                                                  numero(d.get("tmax")), numero(d.get("tmin")))
        time.sleep(3)
    estaciones = consulta("/api/valores/climatologicos/inventarioestaciones/todasestaciones", clave)
    posicion = {e["indicativo"]: (dms(e["longitud"]), dms(e["latitud"])) for e in estaciones}

    # Los días que aún no tienen valor diario se completan con las horas observadas
    for e, dias in estado.items():
        for dia, horas in dias.items():
            if dia >= hoy.isoformat() or (e in diario and dia in diario[e]):
                continue
            precs = [p for p, _ in horas.values() if p is not None]
            temps = [t for _, t in horas.values() if t is not None]
            if len(precs) >= HORAS_MIN:
                diario.setdefault(e, {})[dia] = (round(sum(precs), 1), round(sum(temps) / len(temps), 1) if temps else None,
                                                 max(temps) if temps else None, min(temps) if temps else None)

    # Último día con datos de la mayoría de estaciones = día de referencia
    cuenta = {}
    for dias in diario.values():
        for dia, (p, *_) in dias.items():
            if p is not None:
                cuenta[dia] = cuenta.get(dia, 0) + 1
    maximo = max(cuenta.values())
    referencia = max(d for d, n in cuenta.items() if n >= 0.5 * maximo)
    ref = dt.date.fromisoformat(referencia)

    def resumen(dias, desfase=0):
        """Lluvia de 7, 15 y 30 días y temperatura de 7 hasta el día de referencia, o hasta [desfase] días antes."""
        def ventana(n, campo):
            vals = [dias.get((ref - dt.timedelta(days=i + desfase)).isoformat(), (None, None, None, None))[campo] for i in range(n)]
            vals = [v for v in vals if v is not None]
            return vals if len(vals) >= 0.8 * n else None
        r = {}
        for n in (7, 15, 30):
            v = ventana(n, 0)
            if v is not None:
                r["p%d" % n] = sum(v) * n / len(v)  # días sueltos sin dato: proporcional
        t = ventana(7, 1)
        if t is not None:
            r["t7"] = sum(t) / len(t)
        if desfase:
            return r
        for i in range(DIAS):
            p = dias.get((ref - dt.timedelta(days=i)).isoformat(), (None, None, None, None))[0]
            if p is not None and p >= LLUVIA_FUERTE:
                r["dias"] = i
                break
        return r

    por_estacion = {e: resumen(d) for e, d in diario.items()}
    por_estacion = {e: r for e, r in por_estacion.items() if r and e in posicion}
    # Los mismos datos de hace una semana (bloque «fa7»), para el parte semanal: qué comarcas suben y cuáles bajan
    hace7 = {e: resumen(diario[e], 7) for e in por_estacion}

    # 3. Comarcas
    comarcas = json.load(open(COMARCAS, encoding="utf-8"))
    salida = {}
    for c in comarcas:
        anillos = c["poligonos"]
        x0, x1, y0, y1 = c["caja"]
        propias = [e for e, (x, y) in posicion.items()
                   if e in por_estacion and x0 <= x <= x1 and y0 <= y <= y1 and dentro(anillos, x, y)]
        cx, cy = c["centro"]
        k = math.cos(math.radians(cy))
        cerca = sorted((math.hypot((x - cx) * k, y - cy) * 111, e) for e, (x, y) in posicion.items() if e in por_estacion)
        cerca = [e for dist, e in cerca if dist <= DIST_MAX_KM]
        m = {}
        estimado = False
        for campo in ("p7", "p15", "p30", "t7"):
            vals = [por_estacion[e][campo] for e in propias if campo in por_estacion[e]]
            if not vals:
                vals = [por_estacion[e][campo] for e in cerca if campo in por_estacion[e]][:3]
                estimado = estimado or bool(vals)
            if vals:
                m[campo] = round(sum(vals) / len(vals), 1)
        # Días desde la última lluvia fuerte: con las mismas estaciones que la lluvia de 15 días
        con_lluvia = [e for e in propias if "p15" in por_estacion[e]] or [e for e in cerca if "p15" in por_estacion[e]][:3]
        dias = [por_estacion[e]["dias"] for e in con_lluvia if "dias" in por_estacion[e]]
        if dias:
            m["dias"] = min(dias)
        antes = {}
        for campo in ("p7", "p15", "p30", "t7"):
            vals = [hace7[e][campo] for e in propias if campo in hace7[e]] or \
                [hace7[e][campo] for e in cerca if campo in hace7[e]][:3]
            if vals:
                antes[campo] = round(sum(vals) / len(vals), 1)
        if m and antes:
            m["fa7"] = antes
        # Humedad del suelo: serie diaria media de las estaciones de la comarca (o de las 3 más cercanas)
        usadas = [e for e in propias if e in diario] or [e for e in cerca if e in diario][:3]
        if m and usadas:
            serie = []
            for k in range(DIAS - 1, -1, -1):
                f = (ref - dt.timedelta(days=k)).isoformat()
                vals = [diario[e][f] for e in usadas if f in diario[e]]

                def med(i):
                    xs = [v[i] for v in vals if v[i] is not None]
                    return sum(xs) / len(xs) if xs else None
                if vals:
                    serie.append((dt.date.fromisoformat(f), med(0), med(1), med(2), med(3)))
            sol = balanc_sol.humitat(serie, cy)
            if sol:
                m["sol"], m["sol14"] = sol
        if m:
            if estimado:
                m["est"] = 1
            salida[c["id"]] = m

    resultado = {
        "version": 1,
        "fecha_datos": referencia,
        "generado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuente": "AEMET",
        "comarcas": salida,
    }
    with open(SALIDA, "w", encoding="utf-8", newline="\n") as f:
        json.dump(resultado, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with open(ESTADO, "w", encoding="utf-8", newline="\n") as f:
        json.dump(estado, f, separators=(",", ":"), sort_keys=True)
    print("OK: datos hasta %s, %d estaciones, %d de %d comarcas" % (referencia, len(por_estacion), len(salida), len(comarcas)))


if __name__ == "__main__":
    main()
