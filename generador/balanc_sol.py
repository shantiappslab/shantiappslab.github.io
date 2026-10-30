"""Humitat del sòl calculada amb un balanç d'aigua diari (el comparteixen les tasques meteo de totes les apps).

Validat el 2026-10-28 amb les observacions de GBIF (2008-2024) a Catalunya, Espanya, França i Itàlia: distingeix
millor les setmanes amb troballes de cep i rovelló que la pluja de 30 dies.
- Cada dia, la pluja suma fins a 15 mm sencers; la resta d'un xàfec només un 20 % (l'aigua s'escola i no s'infiltra).
- Cada dia resta l'evapotranspiració (Hargreaves, amb la temperatura màxima i mínima i la radiació solar segons la
  latitud i el dia de l'any), menys com més sec és el sòl.
- Capacitat: 100 mm. Es comença al 30 % i calen uns 60 dies de sèrie perquè el valor sigui estable.
- Bolets de Catalunya (des de la 0.6.3) fa servir també un sòl de 50 mm («sol50»): s'asseca abans després d'un mes
  molt plujós i, amb el sostre del 85 % a l'app, distingeix millor les bones temporades de les dolentes (calibratge del
  2026-10-30 amb la XEMA i GBIF 2008-2024; a Espanya, França i Itàlia no millora i no es fa servir).
Retorna la humitat d'avui, la mitjana dels últims 14 dies i la de fa 7 a 21 dies, de 0 a 1. L'índex fa la mitjana
de les dues últimes: els bolets responen a la humitat de les setmanes anteriors (validat el 2026-10-28: millor o igual
que només els últims 14 dies als quatre països).
"""
import collections
import math

CAP_DIA = 15.0
WMAX = 100.0


def radiacio_mm(lat, dia_any):
    """Radiació extraterrestre (FAO-56) en mm/dia d'evaporació equivalent."""
    fi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * dia_any / 365)
    dec = 0.409 * math.sin(2 * math.pi * dia_any / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(fi) * math.tan(dec))))
    ra = 24 * 60 / math.pi * 0.0820 * dr * (ws * math.sin(fi) * math.sin(dec) + math.cos(fi) * math.cos(dec) * math.sin(ws))
    return 0.408 * ra


def humitat(dies, lat, minim=45, wmax=WMAX):
    """[dies]: llista ordenada de (date, pluja, tmitjana, tmax, tmin), amb None on no hi ha dada.
    Retorna (humitat d'avui, mitjana de 14 dies, mitjana de fa 7 a 21 dies), de 0 a 1, o None si la sèrie és massa curta."""
    if len(dies) < minim:
        return None
    w = 0.3 * wmax
    hist = collections.deque(maxlen=21)
    for d, p, t, tx, tn in dies:
        if t is None and tx is not None and tn is not None:
            t = (tx + tn) / 2
        if t is not None and tx is not None and tn is not None:
            et0 = max(0.0, 0.0023 * radiacio_mm(lat, d.timetuple().tm_yday) * (t + 17.8) * math.sqrt(max(0.0, tx - tn)))
        elif t is not None:
            et0 = max(0.0, 0.15 * (t + 5))
        else:
            et0 = 1.5
        p = p or 0.0
        efectiva = p if p <= CAP_DIA else CAP_DIA + 0.2 * (p - CAP_DIA)
        w = w + efectiva
        w = max(0.0, min(wmax, w - et0 * (w / wmax)))
        hist.append(w / wmax)
    h = list(hist)
    ultims14 = h[-14:]
    retard = h[:-7] or h
    return round(h[-1], 3), round(sum(ultims14) / len(ultims14), 3), round(sum(retard) / len(retard), 3)
