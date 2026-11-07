"""Decodificador mínimo de GRIB2 para los campos del open data de ECMWF y de DWD (sin eccodes).

Soporta lo que usan estas fuentes (probado el 2026-11-07 con los campos reales):
- Rejilla: plantilla 3.0 (lat/lon regular): IFS 0,25° de ECMWF e ICON-EU regular de DWD (1/16°).
- Empaquetado: 5.0 (simple), 5.42 (CCSDS/AEC, el de ECMWF) y 5.40 (JPEG2000, el de DWD).
- Sin máscara (sección 6 = 255) o con máscara de bits (sección 6 = 0).
Necesita numpy e imagecodecs (``pip install numpy imagecodecs``). Con cualquier otra plantilla lanza ``NoSoportado``
y la tarea falla sin publicar nada (entonces habría que pasar a eccodes: ``pip install eccodes``).
"""
import struct

import numpy as np


class NoSoportado(Exception):
    pass


def _iee(b):
    return struct.unpack(">f", b)[0]


def _signed(v, nbytes):
    """Entero con signo en magnitud+signo (convenio GRIB)."""
    bit = 1 << (nbytes * 8 - 1)
    return -(v & (bit - 1)) if v & bit else v


def mensajes(datos):
    """Itera los mensajes GRIB2 de un bloque de bytes: devuelve cada uno como dict con la rejilla y los valores."""
    pos = 0
    while True:
        i = datos.find(b"GRIB", pos)
        if i < 0 or i + 16 > len(datos):
            return
        edicion = datos[i + 7]
        if edicion != 2:
            raise NoSoportado(f"GRIB edición {edicion}")
        total = struct.unpack(">Q", datos[i + 8:i + 16])[0]
        yield decodifica(datos[i:i + total])
        pos = i + total


def decodifica(m):
    """Un mensaje GRIB2 completo → dict(nx, ny, lat0, lon0, dlat, dlon, scan, valores[ny, nx] como float32)."""
    pos = 16
    g = {}
    bitmap = None
    ref = esc_bin = esc_dec = nbits = None
    plantilla5 = None
    datos7 = None
    npuntos = 0
    while pos < len(m) - 4:
        ln = struct.unpack(">I", m[pos:pos + 4])[0]
        num = m[pos + 4]
        s = m[pos:pos + ln]
        if num == 3:
            plantilla = struct.unpack(">H", s[12:14])[0]
            if plantilla != 0:
                raise NoSoportado(f"rejilla plantilla 3.{plantilla}")
            nx, ny = struct.unpack(">II", s[30:38])
            la1 = _signed(struct.unpack(">I", s[46:50])[0], 4) / 1e6
            lo1 = _signed(struct.unpack(">I", s[50:54])[0], 4) / 1e6
            la2 = _signed(struct.unpack(">I", s[55:59])[0], 4) / 1e6
            lo2 = _signed(struct.unpack(">I", s[59:63])[0], 4) / 1e6
            di = struct.unpack(">I", s[63:67])[0] / 1e6
            dj = struct.unpack(">I", s[67:71])[0] / 1e6
            scan = s[71]
            g.update(nx=nx, ny=ny, la1=la1, lo1=lo1, la2=la2, lo2=lo2, di=di, dj=dj, scan=scan)
        elif num == 5:
            npuntos = struct.unpack(">I", s[5:9])[0]
            plantilla5 = struct.unpack(">H", s[9:11])[0]
            ref = _iee(s[11:15])
            esc_bin = _signed(struct.unpack(">H", s[15:17])[0], 2)
            esc_dec = _signed(struct.unpack(">H", s[17:19])[0], 2)
            nbits = s[19]
            if plantilla5 == 42:
                ccsds = (s[21], s[22], struct.unpack(">H", s[23:25])[0])  # flags, blocksize, rsi
        elif num == 6:
            if s[5] == 0:
                bitmap = np.unpackbits(np.frombuffer(s[6:], dtype=np.uint8))
            elif s[5] != 255:
                raise NoSoportado(f"bitmap {s[5]}")
        elif num == 7:
            datos7 = s[5:]
        pos += ln
    n = npuntos if bitmap is None else int(bitmap[:g["nx"] * g["ny"]].sum())
    if plantilla5 == 0:
        if nbits == 0:
            x = np.zeros(n)
        else:
            bits = np.unpackbits(np.frombuffer(datos7, dtype=np.uint8))[:n * nbits].reshape(n, nbits)
            x = (bits.astype(np.uint64) << np.arange(nbits - 1, -1, -1, dtype=np.uint64)).sum(axis=1)
    elif plantilla5 == 42:
        flags, bs, rsi = ccsds
        nbytes = 1 if nbits <= 8 else 2 if nbits <= 16 else (3 if flags & 2 else 4)
        raw = _aec(datos7, nbits, flags, bs, rsi, n, nbytes)
        be = bool(flags & 4)  # AEC_DATA_MSB: muestras en big endian
        if nbytes == 3:
            a = np.frombuffer(raw, dtype=np.uint8, count=n * 3).reshape(n, 3).astype(np.uint32)
            x = (a[:, 0] << 16 | a[:, 1] << 8 | a[:, 2]) if be else (a[:, 2] << 16 | a[:, 1] << 8 | a[:, 0])
        else:
            x = np.frombuffer(raw, dtype=(">" if be else "<") + {1: "u1", 2: "u2", 4: "u4"}[nbytes], count=n)
    elif plantilla5 == 40:
        import imagecodecs
        img = imagecodecs.jpeg2k_decode(bytes(datos7))
        x = img.ravel()[:n]
    else:
        raise NoSoportado(f"empaquetado 5.{plantilla5}")
    v = (ref + x.astype(np.float64) * 2.0 ** esc_bin) / 10.0 ** esc_dec
    if bitmap is not None:
        full = np.full(g["nx"] * g["ny"], np.nan)
        full[bitmap[:g["nx"] * g["ny"]] == 1] = v
        v = full
    v = v.reshape(g["ny"], g["nx"]).astype(np.float32)
    # Orientación: norte arriba (scan bit 0x40 = 0 → de norte a sur) y longitudes crecientes
    if g["scan"] & 0x40:
        v = v[::-1]
    g["lat_norte"] = max(g["la1"], g["la2"])
    g["valores"] = v
    return g


def _aec(datos, nbits, flags, bs, rsi, n, nbytes):
    import imagecodecs
    # imagecodecs.aec_decode devuelve bytes con la muestra a su ancho natural (1, 2 o 4 bytes, little endian)
    return imagecodecs.aec_decode(bytes(datos), bitspersample=nbits, flags=flags, blocksize=bs, rsi=rsi,
                                  out=n * nbytes)
