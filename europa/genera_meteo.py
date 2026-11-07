"""Trabajo diario de meteo para «Mushrooms of Europe»: lluvia, temperatura y humedad del suelo por región.

Fuentes (CC BY 4.0, sin clave):
- ECMWF Open Data, IFS 0,25° (tp, mx2t3, mn2t3, vsw): pasada de las 00 UTC. Descarga solo los campos necesarios
  (fichero .index + peticiones HTTP Range). Los últimos días están en data.ecmwf.int y el archivo completo en las
  copias públicas de Google Cloud y AWS (mismo formato y licencia), de donde se rellena el historial.
- DWD ICON-EU, rejilla regular 1/16° (W_SO, capas 0-81 cm): opendata.dwd.de solo guarda la última ejecución, así que
  este dato se acumula día a día (no se puede rellenar hacia atrás). Cubre 29,5-70,5° N, 23,5° O-62,5° E.

Cada ejecución:
1. Busca la última pasada de las 00 UTC completa.
2. Completa el estado (~100 días: lluvia, Tmax, Tmin, humedad ECMWF y DWD por región) con los días que falten.
3. Calcula la humedad del suelo con el balance de agua validado de las otras apps (generador/balanc_sol.py de la web:
   cubo de 100 mm, lluvia efectiva con tope de 15 mm/día, ET0 de Hargreaves).
4. Escribe un JSON por país (solo el resumen listo para el índice, la previsión de 5 días y la humedad de DWD de hoy) y un
   manifest. El historial de 30 días no se publica: queda en el estado (caché de Actions).

Uso: python -E -X utf8 genera_meteo.py [--salida DIR] [--estado FICHERO.npz] [--dias-estado 100] [--paises ES,FR]
                                       [--sin-dwd] [--run AAAAMMDD]
Si algo falla (fuente caída, demasiadas regiones sin dato) sale con error SIN tocar la carpeta de salida.
Dependencias (pip): numpy, imagecodecs (decodifica el GRIB2 CCSDS/JPEG2000 con grib_min.py). Pillow solo para pesos_regions.py.
"""
import argparse
import bz2
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import grib_min  # noqa: E402

UA = {"User-Agent": "mushrooms-meteo-job/1.0 (+https://github.com/shantiappslab)"}
BASES_ECMWF = [
    "https://storage.googleapis.com/ecmwf-open-data",
    "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "https://data.ecmwf.int/forecasts",
]
DWD = "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/w_so/"
NIVELES_DWD = (0, 1, 3, 9, 27)  # capas 0-1, 1-3, 3-9, 9-27, 27-81 cm → 0-81 cm = 810 mm de agua si θ = 1
FORMA = {"ecmwf": (721, 1440), "dwd": (657, 1377)}  # (filas, columnas) de las rejillas de pesos_regions.py
DIAS_PUBLICADOS = 30
DIAS_PREVISION = 5
WMAX = 100.0
CAP_DIA = 15.0
ATRIBUCION = (
    "Contains modified ECMWF Open Data (IFS, CC BY 4.0, © ECMWF, https://data.ecmwf.int/) and modified DWD ICON-EU data "
    "(CC BY 4.0, © Deutscher Wetterdienst, https://opendata.dwd.de/). Cropped, averaged by region and processed; soil "
    "moisture (sol) is a water balance computed from rain and temperature. Indicative only."
)


# ───────────────────────── red ─────────────────────────
def http(url, rango=None, intentos=4):
    cab = dict(UA)
    if rango:
        cab["Range"] = f"bytes={rango[0]}-{rango[1]}"
    ultimo = None
    for i in range(intentos):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):  # no existe: no insistir
                raise
            ultimo = e
        except Exception as e:  # noqa: BLE001
            ultimo = e
        time.sleep(2 * (i + 1))
    raise ultimo


class Ecmwf:
    """Acceso a los campos de una pasada de las 00 UTC con caché de los índices."""

    def __init__(self):
        self.indices = {}
        self.base_ok = {}

    @staticmethod
    def _url(base, fecha, paso, ext):
        return f"{base}/{fecha}/00z/ifs/0p25/oper/{fecha}000000-{paso}h-oper-fc.{ext}"

    def indice(self, fecha, paso):
        k = (fecha, paso)
        if k in self.indices:
            return self.indices[k]
        err = None
        bases = list(BASES_ECMWF)
        if fecha in self.base_ok:  # la misma copia que ya funcionó para esta pasada
            bases.remove(self.base_ok[fecha])
            bases.insert(0, self.base_ok[fecha])
        for b in bases:
            try:
                txt = http(self._url(b, fecha, paso, "index"), intentos=2).decode()
            except Exception as e:  # noqa: BLE001
                err = e
                continue
            d = {}
            for linea in txt.splitlines():
                e = json.loads(linea)
                d[(e["param"], e.get("levelist"))] = (e["_offset"], e["_length"])
            self.base_ok[fecha] = b
            self.indices[k] = d
            return d
        raise err

    def existe(self, fecha, paso):
        try:
            self.indice(fecha, paso)
            return True
        except Exception:  # noqa: BLE001
            return False

    def campo(self, fecha, paso, param, nivel=None):
        off, ln = self.indice(fecha, paso)[(param, nivel)]
        datos = http(self._url(self.base_ok[fecha], fecha, paso, "grib2"), rango=(off, off + ln - 1))
        return next(grib_min.mensajes(datos))["valores"]


# ───────────────────────── regiones ─────────────────────────
class Regiones:
    def __init__(self, ruta):
        z = np.load(ruta)
        self.ids = z["ids"].astype(str)
        self.paises = z["paises"].astype(str)
        self.lat = z["lat"].astype(np.float64)
        self.n = len(self.ids)
        self.g = {}
        for n in ("ecmwf", "dwd"):
            self.g[n] = dict(ptr=z[n + "_ptr"], idx=z[n + "_idx"], w=z[n + "_w"].astype(np.float64), cob=z[n + "_cob"])

    def promedia(self, campo, red, solo_positivos=False):
        """Media ponderada del campo [ny, nx] por región; NaN si no hay dato. solo_positivos: ignora celdas ≤ 0 (mar)."""
        g = self.g[red]
        if campo.shape != FORMA[red]:
            raise RuntimeError(f"La rejilla de {red} ha cambiado: {campo.shape} en vez de {FORMA[red]} (rehacer pesos_regions.py)")
        v = campo.ravel()[g["idx"]].astype(np.float64)
        ok = np.isfinite(v) & (v > 0 if solo_positivos else True)
        w = np.where(ok, g["w"], 0.0)
        v = np.where(ok, v, 0.0)
        ptr = g["ptr"]
        cw = np.concatenate([[0.0], np.cumsum(w)])
        cv = np.concatenate([[0.0], np.cumsum(w * v)])
        den = cw[ptr[1:]] - cw[ptr[:-1]]
        num = cv[ptr[1:]] - cv[ptr[:-1]]
        with np.errstate(invalid="ignore", divide="ignore"):
            out = num / den
        out[den < 0.05] = np.nan
        return out


# ───────────────────────── extracción de datos ─────────────────────────
def pasos_dia(k):
    return list(range(24 * k + 3, 24 * k + 25, 3))


def dia_ecmwf(ec, reg, fecha, k=0, humedad=False):
    """Día k (0 = el de la pasada) de la pasada de [fecha]: dict(p mm, tx °C, tn °C[, sm θ 0-100 cm]) por región."""
    p = reg.promedia(ec.campo(fecha, 24 * k + 24, "tp"), "ecmwf") * 1000.0
    if k:
        p = p - reg.promedia(ec.campo(fecha, 24 * k, "tp"), "ecmwf") * 1000.0
    p = np.maximum(p, 0.0)
    mx = [reg.promedia(ec.campo(fecha, s, "mx2t3"), "ecmwf") for s in pasos_dia(k)]
    mn = [reg.promedia(ec.campo(fecha, s, "mn2t3"), "ecmwf") for s in pasos_dia(k)]
    d = dict(p=p, tx=np.max(mx, axis=0) - 273.15, tn=np.min(mn, axis=0) - 273.15)
    if humedad:
        # vsw (m³/m³) en 4 capas de 7, 21, 72 y 189 cm: media ponderada de las 3 primeras (0-100 cm)
        c = [reg.promedia(ec.campo(fecha, 24, "vsw", str(n)), "ecmwf", solo_positivos=True) for n in (1, 2, 3)]
        d["sm"] = (7 * c[0] + 21 * c[1] + 72 * c[2]) / 100.0
    return d


def dwd_hoy(reg):
    """Humedad del suelo 0-81 cm (θ m³/m³) de la última ejecución de las 00 UTC de ICON-EU: (AAAAMMDD, [R]) o None."""
    try:
        html = http(DWD, intentos=3).decode()
    except Exception as e:  # noqa: BLE001
        print("DWD: sin listado:", e)
        return None
    fechas = sorted(set(re.findall(r"soil-level_(\d{8})00_000_0_W_SO", html)))
    if not fechas:
        return None
    f = fechas[-1]
    total = None
    for niv in NIVELES_DWD:
        nombre = f"icon-eu_europe_regular-lat-lon_soil-level_{f}00_000_{niv}_W_SO.grib2.bz2"
        try:
            g = next(grib_min.mensajes(bz2.decompress(http(DWD + nombre))))
        except Exception as e:  # noqa: BLE001
            print("DWD: falla", nombre, e)
            return None
        v = g["valores"].astype(np.float64)
        total = v if total is None else total + v
    # kg/m² (mm) acumulados en 81 cm → θ; las celdas sin suelo (mar) valen 0 y se ignoran
    return f, reg.promedia((total / 810.0).astype(np.float32), "dwd", solo_positivos=True)


# ───────────────────────── balance de agua (igual que generador/balanc_sol.py, vectorizado) ─────────────────────────
def radiacion_mm(lat_rad, doy):
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(lat_rad) * math.tan(dec), -1.0, 1.0))
    ra = 24 * 60 / math.pi * 0.0820 * dr * (ws * np.sin(lat_rad) * math.sin(dec) + np.cos(lat_rad) * math.cos(dec) * np.sin(ws))
    return 0.408 * ra


def paso_balance(w, lat_rad, fecha, p, tx, tn):
    """Un día del balance. w en mm [R]; p, tx, tn con NaN donde falta el dato (ET0 = 1,5 mm y lluvia 0, como en la web)."""
    t = (tx + tn) / 2
    with np.errstate(invalid="ignore"):
        et0 = np.maximum(0.0, 0.0023 * radiacion_mm(lat_rad, fecha.timetuple().tm_yday) * (t + 17.8) * np.sqrt(np.maximum(0.0, tx - tn)))
    et0 = np.where(np.isfinite(et0), et0, 1.5)
    p = np.where(np.isfinite(p), p, 0.0)
    efectiva = np.where(p <= CAP_DIA, p, CAP_DIA + 0.2 * (p - CAP_DIA))
    w = w + efectiva
    return np.clip(w - et0 * (w / WMAX), 0.0, WMAX)


# ───────────────────────── estado ─────────────────────────
class Estado:
    """Ventana de N días con lluvia, Tmax, Tmin, humedad ECMWF y DWD por región, más el agua del suelo al inicio."""

    CAMPOS = ("p", "tx", "tn", "sm", "smd")

    def __init__(self, ids):
        self.ids = ids
        self.ini = None
        self.w_ini = np.full(len(ids), 0.3 * WMAX)
        self.dias = {}  # date → {campo: [R]}

    @classmethod
    def carga(cls, ruta, ids):
        e = cls(ids)
        if not ruta or not os.path.exists(ruta):
            return e
        try:
            z = np.load(ruta, allow_pickle=False)
            if list(z["ids"].astype(str)) != list(ids):
                print("Estado: las regiones han cambiado, se rehace desde cero")
                return e
            e.ini = dt.date.fromisoformat(str(z["ini"]))
            e.w_ini = z["w_ini"].astype(np.float64)
            for i, d in enumerate(z["fechas"].astype(str)):
                e.dias[dt.date.fromisoformat(d)] = {c: z[c][i].astype(np.float64) for c in cls.CAMPOS}
        except Exception as ex:  # noqa: BLE001
            print("Estado ilegible, se rehace desde cero:", ex)
            return cls(ids)
        return e

    def guarda(self, ruta):
        fs = sorted(self.dias)
        np.savez_compressed(
            ruta, ids=np.array(self.ids), ini=str(self.ini), w_ini=self.w_ini, fechas=np.array([f.isoformat() for f in fs]),
            **{c: np.array([self.dias[f][c] for f in fs], dtype=np.float32) for c in self.CAMPOS},
        )

    def dia(self, f):
        d = self.dias.get(f)
        if d:
            return d["p"], d["tx"], d["tn"]
        nan = np.full(len(self.ids), np.nan)
        return nan, nan, nan

    def desliza(self, nueva_ini, lat_rad):
        """Avanza el inicio de la ventana, llevando el agua del suelo al nuevo inicio."""
        if self.ini is None:
            self.ini = nueva_ini
            return
        while self.ini < nueva_ini:
            self.w_ini = paso_balance(self.w_ini, lat_rad, self.ini, *self.dia(self.ini))
            self.dias.pop(self.ini, None)
            self.ini += dt.timedelta(days=1)


def serie_balance(est, lat_rad, hasta, extra=()):
    """Humedad del suelo (0..1) de cada día de la ventana hasta [hasta]; [extra] = previsión [(fecha, p, tx, tn)]."""
    w = est.w_ini.copy()
    fechas, out = [], []
    f = est.ini
    while f <= hasta:
        w = paso_balance(w, lat_rad, f, *est.dia(f))
        fechas.append(f)
        out.append(w / WMAX)
        f += dt.timedelta(days=1)
    prev = []
    for fp, p, tx, tn in extra:
        w = paso_balance(w, lat_rad, fp, p, tx, tn)
        prev.append(w / WMAX)
    return fechas, np.array(out), np.array(prev)


def resumen(sol, p, tx, tn, i):
    """Valores para el índice como si hoy fuese el día [i] de la serie (nombres del meteo.json de las otras apps)."""
    def suma(a):
        return round(float(np.nansum(a)), 1) if np.isfinite(a).any() else None
    t = (tx[max(0, i - 6):i + 1] + tn[max(0, i - 6):i + 1]) / 2
    s = sol[max(0, i - 20):i + 1]  # hasta 21 días
    ret = s[:-7] if len(s) > 7 else s
    return {
        "p7": suma(p[max(0, i - 6):i + 1]),
        "p15": suma(p[max(0, i - 14):i + 1]),
        "p30": suma(p[max(0, i - 29):i + 1]),
        "t7": round(float(np.nanmean(t)), 1) if np.isfinite(t).any() else None,
        "sol": round(float(sol[i]), 3),
        "sol14": round(float(np.mean(s[-14:])), 3),
        "sol_retard": round(float(np.mean(ret)), 3),
    }


# ───────────────────────── principal ─────────────────────────
def ultima_pasada(ec, forzada=None):
    if forzada:
        return forzada
    hoy = dt.datetime.now(dt.timezone.utc).date()
    for atras in range(0, 4):
        f = (hoy - dt.timedelta(days=atras)).strftime("%Y%m%d")
        if ec.existe(f, 24):
            return f
    raise RuntimeError("No hay ninguna pasada reciente de ECMWF")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=os.path.join(AQUI, "salida"))
    ap.add_argument("--estado", default=os.path.join(AQUI, "estado", "estado.npz"))
    ap.add_argument("--pesos", default=os.path.join(AQUI, "pesos_regions.npz"))
    ap.add_argument("--dias-estado", type=int, default=100)
    ap.add_argument("--paises", default="")
    ap.add_argument("--sin-dwd", action="store_true")
    ap.add_argument("--run", default="")
    ap.add_argument("--hilos", type=int, default=8)
    a = ap.parse_args()

    reg = Regiones(a.pesos)
    lat_rad = np.radians(reg.lat)
    ec = Ecmwf()
    fecha_run = ultima_pasada(ec, a.run)
    D = dt.datetime.strptime(fecha_run, "%Y%m%d").date()
    print("Última pasada de ECMWF:", fecha_run)

    est = Estado.carga(a.estado, reg.ids)
    est.desliza(D - dt.timedelta(days=a.dias_estado - 1), lat_rad)
    todos = [est.ini + dt.timedelta(days=i) for i in range((D - est.ini).days + 1)]
    faltan = [f for f in todos if f not in est.dias]
    print(f"Ventana {est.ini}..{D}: {len(todos) - len(faltan)} días en el estado, {len(faltan)} por descargar")

    def trae(f):
        try:
            d = dia_ecmwf(ec if f == D else Ecmwf(), reg, f.strftime("%Y%m%d"), 0, humedad=True)
            d["smd"] = np.full(reg.n, np.nan)
            return f, d
        except Exception as e:  # noqa: BLE001
            print("  día", f, "no disponible:", repr(e)[:120])
            return f, None

    with cf.ThreadPoolExecutor(a.hilos) as ex:
        for f, d in ex.map(trae, faltan):
            if d is not None:
                est.dias[f] = d
    if D not in est.dias:
        raise RuntimeError("No se pudo obtener el día de hoy de ECMWF")

    # DWD: solo la ejecución actual (no hay histórico); si falla, se publica sin ella
    if not a.sin_dwd:
        r = dwd_hoy(reg)
        fd = dt.datetime.strptime(r[0], "%Y%m%d").date() if r else None
        if r and fd in est.dias:
            est.dias[fd]["smd"] = r[1]
            print("DWD: humedad del", fd, "añadida")
        else:
            print("AVISO: sin humedad de DWD hoy; se publica el resto")

    # previsión: días D+1..D+5 de la pasada de hoy
    prev = []
    for k in range(1, DIAS_PREVISION + 1):
        if not ec.existe(fecha_run, 24 * k + 24):
            break
        try:
            d = dia_ecmwf(ec, reg, fecha_run, k)
            prev.append((D + dt.timedelta(days=k), d["p"], d["tx"], d["tn"]))
        except Exception as e:  # noqa: BLE001
            print("Previsión día", k, "falla:", repr(e)[:100])
            break

    fechas, sol, solp = serie_balance(est, lat_rad, D, prev)
    nd = len(fechas)
    P, TX, TN = (np.array([est.dia(f)[i] for f in fechas]) for i in range(3))
    SM = np.array([est.dias[f]["sm"] if f in est.dias else np.full(reg.n, np.nan) for f in fechas])
    SMD = np.array([est.dias[f]["smd"] if f in est.dias else np.full(reg.n, np.nan) for f in fechas])

    # comprobaciones antes de publicar
    ok_p, ok_t = np.isfinite(P[-1]).mean(), np.isfinite(TX[-1]).mean()
    if ok_p < 0.99 or ok_t < 0.99:
        raise RuntimeError(f"Demasiadas regiones sin dato hoy (lluvia {ok_p:.2%}, temperatura {ok_t:.2%})")
    if np.nanmin(TN[-1]) < -60 or np.nanmax(TX[-1]) > 55 or np.nanmax(P[-1]) > 600:
        raise RuntimeError("Valores fuera de rango, no se publica")

    def lista(v, nd_):
        return [None if not math.isfinite(x) else round(float(x), nd_) for x in v]

    n_pub = min(DIAS_PUBLICADOS, nd)
    paises = [p for p in sorted(set(reg.paises)) if not a.paises or p in a.paises.split(",")]
    tmp = a.salida + ".tmp"
    os.makedirs(tmp, exist_ok=True)
    generado = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    manifest = {"v": 1, "fecha_datos": D.isoformat(), "generado": generado, "atribucion": ATRIBUCION,
                "fuentes": {"ecmwf": "https://data.ecmwf.int/forecasts/", "dwd": "https://opendata.dwd.de/weather/nwp/icon-eu/"},
                "dias_historial": n_pub, "dias_prevision": len(prev), "paises": {}}
    for pais in paises:
        rs = np.nonzero(reg.paises == pais)[0]
        salida = {}
        for r in rs:
            # Solo lo que necesita la app (el historial de 30 días queda en el estado, en la caché de Actions)
            res = resumen(sol[:, r], P[:, r], TX[:, r], TN[:, r], nd - 1)
            if nd >= 29:  # lo mismo de hace una semana, para comparar el índice
                res["fa7"] = resumen(sol[:, r], P[:, r], TX[:, r], TN[:, r], nd - 8)
            smd = SMD[-1, r]
            e = {"res": res}
            if math.isfinite(smd):
                e["smd"] = round(float(smd), 2)  # humedad ICON-EU (m³/m³, 0-81 cm) de hoy; dato extra
            if prev:
                e["prev"] = {"p": lista([x[1][r] for x in prev], 1), "tx": lista([x[2][r] for x in prev], 1),
                             "tn": lista([x[3][r] for x in prev], 1), "sol": lista(solp[:, r], 2)}
            salida[str(reg.ids[r])] = e
        doc = {"v": 1, "pais": pais, "fecha_datos": D.isoformat(), "prevision": [x[0].isoformat() for x in prev],
               "regiones": salida}
        ruta = os.path.join(tmp, f"meteo_{pais}.json")
        with open(ruta, "w", encoding="utf8") as fh:
            json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        manifest["paises"][pais] = {"archivo": f"meteo_{pais}.json", "regiones": len(rs), "bytes": os.path.getsize(ruta)}
    with open(os.path.join(tmp, "meteo_manifest.json"), "w", encoding="utf8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, separators=(",", ":"))

    # solo ahora se toca la salida y el estado
    os.makedirs(a.salida, exist_ok=True)
    for f in os.listdir(tmp):
        os.replace(os.path.join(tmp, f), os.path.join(a.salida, f))
    os.rmdir(tmp)
    os.makedirs(os.path.dirname(os.path.abspath(a.estado)), exist_ok=True)
    est.guarda(a.estado)
    print(f"Hecho: {len(paises)} países, datos del {D}, {n_pub} días de historial, {len(prev)} de previsión")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("ERROR:", repr(e))
        sys.exit(1)
