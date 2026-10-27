"""Comprova i publica la nota micològica setmanal d'una app («Què diuen els micòlegs»).

La nota la redacta cada setmana una tasca programada (vegeu generador/nota/INSTRUCCIONS.md) a partir de fonts fiables,
sense revisió humana (decisió del 2026-10-27). Aquest script és la xarxa de seguretat abans de publicar-la:

- cada frase ha de portar un enllaç d'una font de la llista tancada de fonts.json (o d'una societat micològica);
- cap frase pot parlar de comestibilitat ni de toxicitat (això només ho diuen les fitxes revisades de l'app);
- totes les frases han d'estar en tots els idiomes de l'app;
- si és massa llarga NO es descarta: surt amb el codi 2 i la tasca l'ha de resumir i tornar-ho a provar.

Ús: python3 generador/nota/comprova_nota.py <app> <esborrany.json>
  app: bolets | setas | funghi | champignons
  Codi 0: correcta, escrita a <carpeta>/nota.json. Codi 1: rebutjada (no es publica). Codi 2: massa llarga (resumir).

Format de l'esborrany:
  {"setmana": "2026-10-26",   (dilluns de la setmana)
   "frases": [{"text": {"ca": "...", "es": "..."}, "font": "Parc Micològic d'Ultzama", "url": "https://..."}]}
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(os.path.dirname(AQUI))
MAX_FRASES = 4
MAX_CARACTERS = 280  # per frase i idioma
IDIOMES = {"bolets": ["ca", "es"], "setas": ["es"], "funghi": ["it"], "champignons": ["fr"]}

# Paraules que indiquen que la frase parla de si un bolet es pot menjar o és perillós (en qualsevol idioma de les apps)
PROHIBIDES = re.compile(
    r"comestib|tòxic|toxic|tóxic|mortal|verin|venen|velen|intoxica|empoison|vénéneu|veleno|mangia|mangea|comible|"
    r"es pot menjar|se puede comer|si può mangiare|se mange|digest|indigest",
    re.IGNORECASE,
)


def domini(url):
    d = urllib.parse.urlparse(url).hostname or ""
    return d[4:] if d.startswith("www.") else d


def font_permesa(url, fonts):
    if not url.startswith("https://"):
        return False
    d = domini(url)
    if any(d == x or d.endswith("." + x) for x in fonts["excloses"]):
        return False
    if any(paraula in d for paraula in fonts["paraules_domini"]):
        return True
    return any(d == x or d.endswith("." + x) for x in fonts["dominis"])


def comprova(app, nota):
    fonts = json.load(open(os.path.join(AQUI, "fonts.json"), encoding="utf-8"))
    fonts = {"dominis": fonts["comuns"] + fonts[app], "excloses": fonts["excloses"], "paraules_domini": fonts["paraules_domini"]}
    errors, llarga = [], []
    try:
        dilluns = dt.date.fromisoformat(nota["setmana"])
    except (KeyError, ValueError):
        return ["falta «setmana» (AAAA-MM-DD)"], []
    if dilluns.weekday() != 0:
        errors.append("«setmana» ha de ser un dilluns")
    frases = nota.get("frases") or []
    if not frases:
        errors.append("no hi ha cap frase")
    if len(frases) > MAX_FRASES:
        llarga.append("%d frases (màxim %d)" % (len(frases), MAX_FRASES))
    for n, f in enumerate(frases, 1):
        if not font_permesa(f.get("url", ""), fonts):
            errors.append("frase %d: la font %s no és a la llista de fonts fiables" % (n, f.get("url")))
        if not f.get("font"):
            errors.append("frase %d: falta el nom de la font" % n)
        for idioma in IDIOMES[app]:
            text = (f.get("text") or {}).get(idioma, "").strip()
            if not text:
                errors.append("frase %d: falta el text en «%s»" % (n, idioma))
                continue
            if PROHIBIDES.search(text):
                errors.append("frase %d (%s): parla de comestibilitat o toxicitat: «%s»" % (n, idioma, PROHIBIDES.search(text).group(0)))
            if len(text) > MAX_CARACTERS:
                llarga.append("frase %d (%s): %d caràcters (màxim %d)" % (n, idioma, len(text), MAX_CARACTERS))
    return errors, llarga


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in IDIOMES:
        sys.exit(__doc__)
    app = sys.argv[1]
    nota = json.load(open(sys.argv[2], encoding="utf-8"))
    errors, llarga = comprova(app, nota)
    if errors:
        print("REBUTJADA (no es publica):\n- " + "\n- ".join(errors))
        sys.exit(1)
    if llarga:
        print("MASSA LLARGA: resumeix-la (sense perdre les fonts) i torna-ho a provar:\n- " + "\n- ".join(llarga))
        sys.exit(2)
    neta = {"setmana": nota["setmana"],
            "frases": [{"text": {i: f["text"][i].strip() for i in IDIOMES[app]}, "font": f["font"].strip(), "url": f["url"]}
                       for f in nota["frases"]],
            "generat": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    desti = os.path.join(WEB, app, "nota.json")
    with open(desti, "w", encoding="utf-8", newline="\n") as fo:
        json.dump(neta, fo, ensure_ascii=False, indent=1)
        fo.write("\n")
    print("Correcta: %d frases a %s" % (len(neta["frases"]), os.path.relpath(desti, WEB)))


if __name__ == "__main__":
    main()
