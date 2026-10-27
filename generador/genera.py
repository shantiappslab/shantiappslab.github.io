"""Genera la web d'una app de bolets (shantiappslab.github.io/<carpeta>/): portada setmanal, fitxes i llistats.

Ús: python3 generador/genera.py <carpeta>      (p. ex. bolets; només biblioteca estàndard)

Llegeix:
- generador/apps/<carpeta>.json   nom de l'app, paquet de Play i textos de la interfície de cada idioma
- <carpeta>/dades/contingut.json  fitxes, comarques i mapa (l'exporta BoletsApps: tools/web/exporta.py)
- <carpeta>/meteo.json            pluja per comarca (la tasca programada del meteo, cada 6 hores)
Escriu les pàgines HTML de cada idioma (el primer a <carpeta>/, els altres a <carpeta>/<idioma>/) i <carpeta>/sitemap.xml.
Només es reescriuen els fitxers que canvien. La portada canvia amb les dades del meteo i el mes; la resta, només si
canvia el contingut. Els textos són els mateixos (revisats) que surten a l'app.
"""
import datetime as dt
import html
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(AQUI)
DOMINI = "https://shantiappslab.github.io"
TOP_PLUJA = 5
POCA_PLUJA = 15  # mm en 15 dies: per sota, la frase diu que ha plogut poc
DIES_VELLS = 6  # si les dades del meteo tenen més dies, no es mostren
TOX_ORDRE = {"mortal": 0, "toxic": 1, "comestible": 2}
# Muntanya de l'altitud (bosc_muntanya.webp): altura del cim i del mar en fracció de la imatge i metres del cim (com l'app)
MUNTANYA_CIM = 0.040
MUNTANYA_MAR = 0.973
ALTITUD_CIM = 3000
LOCALE = {"ca": "ca_ES", "es": "es_ES", "it": "it_IT", "fr": "fr_FR", "de": "de_DE", "pt": "pt_PT"}


def e(s):
    return html.escape(str(s), quote=True)


def slug(text):
    import unicodedata
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join("".join(c if c.isalnum() else " " for c in s).split())


class Web:
    def __init__(self, carpeta):
        self.carpeta = carpeta
        self.cfg = json.load(open(os.path.join(AQUI, "apps", carpeta + ".json"), encoding="utf-8"))
        self.dades = json.load(open(os.path.join(WEB, carpeta, "dades", "contingut.json"), encoding="utf-8"))
        self.idiomes = self.dades["idiomes"]
        self.bolets = {b["id"]: b for b in self.dades["bolets"]}
        p = os.path.join(WEB, carpeta, "meteo.json")
        self.meteo = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
        self.escrits = 0
        self.urls = []

    # ---------- rutes ----------
    def base(self, idioma):
        return "/%s/" % self.carpeta + ("" if idioma == self.idiomes[0] else idioma + "/")

    def url_bolet(self, b, idioma):
        return self.base(idioma) + b["slug"][idioma] + "/"

    def url_llistat(self, tipus, idioma):
        t = self.cfg["textos"][idioma]
        return self.base(idioma) + slug(t["comestibles" if tipus == "comestibles" else "toxics"]) + "/"

    def play(self, campanya, idioma):
        ref = "utm_source%3Dweb%26utm_medium%3D" + idioma + "%26utm_campaign%3D" + campanya
        return "https://play.google.com/store/apps/details?id=%s&amp;referrer=%s" % (self.cfg["paquet"], ref)

    # ---------- peces ----------
    def pagina(self, idioma, ruta, titol, desc, cos, alternatives, campanya, imatge=None, dades=(), tipus="website"):
        t = self.cfg["textos"][idioma]
        img = DOMINI + (imatge or self.cfg["icona"])
        og = (f'<meta property="og:type" content="{tipus}"><meta property="og:site_name" content="{e(self.cfg["app"])}">'
              f'<meta property="og:title" content="{e(titol)}"><meta property="og:description" content="{e(desc)}">'
              f'<meta property="og:url" content="{DOMINI}{ruta}"><meta property="og:image" content="{img}">'
              f'<meta property="og:locale" content="{LOCALE.get(idioma, idioma)}">'
              f'<meta name="twitter:card" content="{"summary_large_image" if imatge else "summary"}">')
        ld = "".join('<script type="application/ld+json">%s</script>' % json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
                     for d in dades)
        alt = "".join('<link rel="alternate" hreflang="%s" href="%s%s">' % (i, DOMINI, u) for i, u in alternatives.items())
        alt += '<link rel="alternate" hreflang="x-default" href="%s%s">' % (DOMINI, alternatives[self.idiomes[0]])
        canvi = "".join('<a href="%s" hreflang="%s" lang="%s">%s</a>' % (u, i, i, e(self.cfg["textos"][i]["nom_idioma"]))
                        for i, u in alternatives.items() if i != idioma)
        return f"""<!doctype html>
<html lang="{t['lang']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(titol)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{DOMINI}{ruta}">{alt}
{og}
<link rel="icon" href="{self.cfg['icona']}">
<link rel="preload" href="/recursos/fredoka.ttf" as="font" type="font/ttf" crossorigin>
<link rel="stylesheet" href="/recursos/bolets.css">{ld}
</head>
<body>
<header class="cap"><a class="marca" href="{self.base(idioma)}"><img src="{self.cfg['icona']}" width="40" height="40" alt="">{e(self.cfg['app'])}</a>
<nav>{canvi}</nav></header>
<main>
{cos}
{self.baixa(idioma, campanya + "_peu")}
<p class="seguretat">{e(t['avis_seguretat'])}</p>
</main>
<footer><p>{e(t['peu'])}</p>
<p><a href="{self.base(idioma)}">{e(t['inici'])}</a> · <a href="{self.url_llistat('comestibles', idioma)}">{e(t['comestibles'])}</a> · <a href="{self.url_llistat('toxics', idioma)}">{e(t['toxics'])}</a> · <a href="{self.cfg['privacitat']}">{e(t['privacitat'])}</a></p></footer>
</body>
</html>
"""

    def baixa(self, idioma, campanya, text=None):
        """Targeta gran per descarregar l'app (totes les crides a baixar-la de la web són aquesta targeta)."""
        t = self.cfg["textos"][idioma]
        return (f'<aside class="baixa"><img src="{self.cfg["icona"]}" width="72" height="72" alt="">'
                f'<b>{e(t["targeta_titol"])}</b><p>{e(text or t["targeta_text"])}</p>'
                f'<a class="gran" href="{self.play(campanya, idioma)}">{e(t["boto_gran"])}'
                f'<small>{e(t["boto_gran_sub"])}</small></a></aside>')

    def il_lustracio(self, bosc, alt, carrega='loading="lazy"'):
        """Il·lustració d'un bosc (aquarel·la de l'app; mai bolets)."""
        f = self.dades.get("boscos", {}).get(bosc)
        if not f:
            return ""
        return f'<img class="il-lustracio" src="/{self.carpeta}/{f["fitxer"]}" alt="{e(alt)}" {carrega} width="{f["w"]}" height="{f["h"]}">'

    def heroi(self, idioma, bosc, bolets=()):
        """Capçalera: il·lustració d'un bosc i, a sobre, polaroids amb fotos reals de bolets (amb els crèdits a sota)."""
        t = self.cfg["textos"][idioma]
        polaroids, credits = [], []
        for n, bid in enumerate(bolets):
            b = self.bolets.get(bid)
            f = b and b["fotos"].get(b["foto"])
            if not f:
                continue
            polaroids.append(f'<a class="polaroid p{n}" href="{self.url_bolet(b, idioma)}"><img src="/{self.carpeta}/{f["fitxer"]}" '
                             f'alt="{e(b["nom"][idioma])} ({e(b["cientific"])})" width="{f["w"]}" height="{f["h"]}">'
                             f'<span>{e(b["nom"][idioma])}</span></a>')
            credits.append("%s (%s)" % (f.get("autor", "?"), f.get("llicencia", "?")))
        fotos = (f'<div class="polaroids">{"".join(polaroids)}</div>'
                 f'<p class="credits-heroi">{e(t["fotos_credits"].format(credits=", ".join(credits)))}</p>') if polaroids else ""
        bosc_img = self.il_lustracio(bosc, t["bosc_alt"], 'fetchpriority="high"')
        return f'<div class="heroi{" amb-fotos" if polaroids else ""}">{bosc_img}{fotos}</div>'

    def altitud(self, minim, maxim, idioma):
        """Altitud com a la fitxa de camp de l'app: la muntanya dibuixada, l'escala de metres i la franja de l'espècie."""
        t = self.cfg["textos"][idioma]
        mil = lambda m: f"{m:,}".replace(",", ".")  # noqa: E731
        f = self.dades.get("boscos", {}).get("muntanya")
        text = f'<p class="altitud-xifra">{mil(minim)} – {mil(maxim)} m</p><p>{e(t["altitud"].format(min=mil(minim), max=mil(maxim)))}</p>'
        if not f:
            return f'<section class="paper"><h2>{e(t["altitud_titol"])}</h2>{text}</section>'
        w, h = f["w"], f["h"]

        def y(m):  # mateixes proporcions que l'app (FitxaCamp.kt): el cim és a 3.000 m
            return round(h * (MUNTANYA_MAR - (MUNTANYA_MAR - MUNTANYA_CIM) * m / ALTITUD_CIM), 1)

        xe = w + 18
        escala = "".join(f'<line x1="{xe}" y1="{y(m)}" x2="{xe + 12}" y2="{y(m)}"/><text x="{xe + 20}" y="{y(m) + 12}">{mil(m)} m</text>'
                         for m in range(0, ALTITUD_CIM + 1, 500))
        svg = (f'<svg class="muntanya" viewBox="0 0 {w + 175} {h}" role="img" '
               f'aria-label="{e(t["altitud_alt"].format(min=mil(minim), max=mil(maxim)))}">'
               f'<image href="/{self.carpeta}/{f["fitxer"]}" width="{w}" height="{h}"/>'
               f'<rect class="franja" x="0" y="{y(maxim)}" width="{w}" height="{round(y(minim) - y(maxim), 1)}"/>'
               f'<g class="escala"><line x1="{xe}" y1="{y(0)}" x2="{xe}" y2="{y(ALTITUD_CIM)}"/>{escala}</g>'
               f'<line class="barra" x1="{xe}" y1="{y(minim)}" x2="{xe}" y2="{y(maxim)}"/></svg>')
        return f'<section class="paper"><h2>{e(t["altitud_titol"])}</h2>{text}{svg}</section>'

    def molles(self, passos):
        """Dades estructurades de les molles de pa: [(nom, ruta), ...]."""
        return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": n, "name": nom, "item": DOMINI + ruta} for n, (nom, ruta) in enumerate(passos, 1)]}

    def llista(self, elements, idioma):
        t = self.cfg["textos"][idioma]
        return elements[0] if len(elements) == 1 else ", ".join(elements[:-1]) + " " + t["i"] + " " + elements[-1]

    def escriu(self, ruta, text, sitemap=True):
        p = os.path.join(WEB, *ruta.strip("/").split("/"), "index.html")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p) or open(p, encoding="utf-8").read() != text:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            self.escrits += 1
        if sitemap:
            self.urls.append(ruta)

    def figura(self, b, nom_foto, idioma, classe="foto", amplada=900):
        t = self.cfg["textos"][idioma]
        f = b["fotos"].get(nom_foto)
        if not f:
            return ""
        credit = t["foto_de"].format(autor=f.get("autor", "?"), llicencia=f.get("llicencia", "?"))
        enllac = '<a href="%s" rel="nofollow">%s</a>' % (e(f["url"]), e(credit)) if f.get("url") else e(credit)
        # La foto principal és el primer que es veu: es carrega de seguida; la resta, quan s'hi arriba
        carrega = 'fetchpriority="high"' if classe == "foto" else 'loading="lazy"'
        mida = f'width="{f["w"]}" height="{f["h"]}"' if f.get("w") else f'width="{amplada}"'
        return (f'<figure class="{classe}"><img src="/{self.carpeta}/{f["fitxer"]}" alt="{e(b["nom"][idioma])} ({e(b["cientific"])})" '
                f'{carrega} {mida}><figcaption>{enllac}</figcaption></figure>')

    def etiqueta(self, b, idioma):
        return '<span class="tox %s">%s</span>' % (b["toxicitat"], e(self.cfg["textos"][idioma]["toxicitat"][b["toxicitat"]]))

    def targeta(self, b, idioma):
        f = b["fotos"].get(b["foto"], {})
        img = f'<img src="/{self.carpeta}/{f["fitxer"]}" alt="" loading="lazy" width="96" height="96">' if f else ""
        return (f'<a class="targeta" href="{self.url_bolet(b, idioma)}">{img}<span><b>{e(b["nom"][idioma])}</b>'
                f'<i>{e(b["cientific"])}</i>{self.etiqueta(b, idioma)}</span></a>')

    def mesos(self, mesos, millors, idioma):
        t = self.cfg["textos"][idioma]
        cel = "".join('<span class="%s" title="%s">%s</span>' % ("millor" if m in millors else "si" if m in mesos else "no",
                                                                 e(t["mesos"][m - 1]), t["mesos_curts"][m - 1]) for m in range(1, 13))
        text = t["mesos_text"].format(mesos=self.llista([t["mesos"][m - 1] for m in mesos], idioma),
                                      millors=self.llista([t["mesos"][m - 1] for m in millors], idioma)) if millors else ""
        return (f'<div class="mesos">{cel}</div><p class="llegenda"><span class="si"></span>{e(t["temporada"])} '
                f'<span class="millor"></span>{e(t["millors_mesos"])}</p><p class="mesos-text">{e(text)}</p>')

    # ---------- pàgines ----------
    def fitxa(self, b, idioma):
        t = self.cfg["textos"][idioma]
        parts = [f'<p class="molles"><a href="{self.base(idioma)}">{e(t["inici"])}</a> › '
                 f'<a href="{self.url_llistat("comestibles" if b["toxicitat"] == "comestible" else "toxics", idioma)}">'
                 f'{e(t["comestibles" if b["toxicitat"] == "comestible" else "toxics"])}</a></p>',
                 f'<h1>{e(b["nom"][idioma])}</h1>',
                 f'<p class="cientific"><i>{e(b["cientific"])}</i> {self.etiqueta(b, idioma)}</p>']
        if b["altres_noms"][idioma]:
            parts.append(f'<p class="altres">{e(t["altres_noms"].format(noms=b["altres_noms"][idioma]))}</p>')
        if b["avis"]:
            parts.append(f'<p class="avis">{e(b["avis"][idioma])}</p>')
        parts.append(self.figura(b, b["foto"], idioma))
        parts.append(self.baixa(idioma, b["id"]))
        camp = b.get("camp")
        if camp and camp["trets"]:
            parts.append(f'<section class="paper"><h2>{e(t["reconeixer"])}</h2><ol>' +
                         "".join(f"<li>{e(x[idioma])}</li>" for x in camp["trets"]) + "</ol></section>")
        if camp and camp.get("confusio") and camp["confusio"]["bolet"] in self.bolets:
            c = self.bolets[camp["confusio"]["bolet"]]
            parts.append(f'<section class="posit"><h2>{e(t["no_confondre"])}</h2>'
                         f'<p><a href="{self.url_bolet(c, idioma)}"><b>{e(c["nom"][idioma])}</b></a> {self.etiqueta(c, idioma)}</p>'
                         f'<p>{e(camp["confusio"]["text"][idioma])}</p></section>')
        paragrafs = [p.strip() for p in b["descripcio"][idioma].split("\n\n") if p.strip()]
        parts.append(f'<section><h2>{e(t["descripcio"])}</h2>' + "".join(
            "<p>%s</p>" % e(p).replace("\n", "<br>") for p in paragrafs) + "</section>")
        if camp and camp["habitats"]:
            # Cada hàbitat amb la il·lustració del seu bosc, com a la fitxa de camp de l'app
            parts.append(f'<section class="paper"><h2>{e(t["habitat"])}</h2>' + "".join(
                f'<div class="habitat-bosc">{self.il_lustracio(h.get("bosc"), t["il_lustracio"].format(nom=h["titol"][idioma]))}'
                f'<p><b>{e(h["titol"][idioma])}</b>. {e(h["text"][idioma])}</p></div>' for h in camp["habitats"]) + "</section>")
        if camp and camp.get("altitud"):
            parts.append(self.altitud(camp["altitud"][0], camp["altitud"][1], idioma))
        if camp and camp["mesos"]:
            parts.append(f'<section><h2>{e(t["quan_surt"])}</h2>{self.mesos(camp["mesos"], camp["millors"], idioma)}</section>')
        if camp and camp["varietats"]:
            vs = "".join(f'<div class="varietat">{self.figura(b, v["foto"], idioma, "petita", 300) if v["foto"] else ""}'
                         f'<h3>{e(v["nom"][idioma])} <i>{e(v["cientific"])}</i></h3><p>{e(v["text"][idioma])}</p>'
                         f'<p class="habitat">{e(v["habitat"][idioma])}</p></div>' for v in camp["varietats"])
            parts.append(f'<section><h2>{e(t["varietats"])}</h2>{vs}</section>')
        altres = [c for c in b["confusions"] if c in self.bolets and not (camp and camp.get("confusio") and camp["confusio"]["bolet"] == c)]
        if altres:
            parts.append(f'<section><h2>{e(t["es_confon"])}</h2><div class="graella">' +
                         "".join(self.targeta(self.bolets[c], idioma) for c in altres) + "</div></section>")
        galeria = "".join(self.figura(b, g, idioma, "petita", 300) for g in b["galeria"])
        if galeria:
            parts.append(f'<div class="galeria">{galeria}</div>')
        if b["fonts"]:
            parts.append(f'<section class="fonts"><h2>{e(t["fonts"])}</h2><ul>' + "".join(
                f'<li><a href="{e(f["url"])}" rel="nofollow">{e(f["nom"])}</a></li>' for f in b["fonts"]) + "</ul></section>")
        resum = (camp["trets"][0][idioma] + ". ") if camp and camp["trets"] else ""
        desc = f'{b["nom"][idioma]} ({b["cientific"]}): {t["toxicitat"][b["toxicitat"]].lower()}. {resum}{paragrafs[0] if paragrafs else ""}'
        if len(desc) > 155:
            desc = desc[:152].rsplit(" ", 1)[0] + "…"
        cientific = b["cientific"].split(",")[0].split(" (")[0].strip()
        titol = t["titol_fitxa"][b["toxicitat"]].format(nom=b["nom"][idioma], cientific=cientific)
        if len(titol) > 65:  # Google talla els títols llargs: sense el nom científic (ja surt a la pàgina)
            titol = titol.replace(" (%s)" % cientific, "")
        tipus_llistat = "comestibles" if b["toxicitat"] == "comestible" else "toxics"
        foto = b["fotos"].get(b["foto"])
        dades = [self.molles([(t["inici"], self.base(idioma)), (t[tipus_llistat], self.url_llistat(tipus_llistat, idioma)),
                              (b["nom"][idioma], self.url_bolet(b, idioma))])]
        return self.pagina(idioma, self.url_bolet(b, idioma), titol, desc, "\n".join(parts),
                           {i: self.url_bolet(b, i) for i in self.idiomes}, b["id"],
                           imatge="/%s/%s" % (self.carpeta, foto["fitxer"]) if foto else None, dades=dades, tipus="article")

    def llistat(self, tipus, idioma):
        t = self.cfg["textos"][idioma]
        bs = [b for b in self.bolets.values() if (b["toxicitat"] == "comestible") == (tipus == "comestibles")]
        bs.sort(key=lambda b: (TOX_ORDRE[b["toxicitat"]], b["nom"][idioma]))
        titol = t["comestibles" if tipus == "comestibles" else "toxics"]
        cos = (self.heroi(idioma, self.cfg["heroi"][tipus]) + f'<h1>{e(titol)}</h1>' + self.baixa(idioma, tipus) + '<div class="graella">' +
               "".join(self.targeta(b, idioma) for b in bs) + "</div>")
        dades = [self.molles([(t["inici"], self.base(idioma)), (titol, self.url_llistat(tipus, idioma))])]
        return self.pagina(idioma, self.url_llistat(tipus, idioma), t["titol_" + tipus],
                           t["comestibles_desc" if tipus == "comestibles" else "toxics_desc"], cos,
                           {i: self.url_llistat(tipus, i) for i in self.idiomes}, tipus, dades=dades)

    def mapa_svg(self, top, idioma):
        t = self.cfg["textos"][idioma]
        m = self.dades["mapes"].get(self.cfg["regio"])
        if not m:
            return ""
        camins = []
        for cid, d in sorted(m["camins"].items()):
            classe = "pluja" if cid in top else "bosc" if self.dades["comarques"][cid]["bosc"] >= 0.4 else "terra"
            camins.append(f'<path class="{classe}" d="{d}"><title>{e(self.dades["comarques"][cid]["nom"])}</title></path>')
        etiquetes = "".join(f'<circle cx="{m["centres"][c][0]}" cy="{m["centres"][c][1]}" r="11"/>'
                            f'<text x="{m["centres"][c][0]}" y="{m["centres"][c][1] + 4.5}">{n}</text>'
                            for n, c in enumerate(top, 1))
        return (f'<figure class="mapa"><svg viewBox="0 0 {m["ample"]} {m["alt"]}" role="img" aria-label="{e(t["mapa_alt"])}">'
                f'{"".join(camins)}<g class="num">{etiquetes}</g></svg>'
                f'<figcaption><span class="pluja"></span>{e(t["mapa_llegenda_pluja"])} <span class="bosc"></span>{e(t["mapa_llegenda_bosc"])}</figcaption></figure>')

    def portada(self, idioma, avui):
        t = self.cfg["textos"][idioma]
        parts = [self.heroi(idioma, self.cfg["heroi"]["bosc"], self.cfg["heroi"]["bolets"]), f'<h1>{e(t["portada_titol"])}</h1>']
        m = self.meteo
        if m and m.get("comarcas"):
            data = dt.date.fromisoformat(m["fecha_datos"])
            if (avui - data).days <= DIES_VELLS:
                regio = {c for c, d in self.dades["comarques"].items() if d["comunitat"] == self.cfg["regio"]}
                pluja = sorted(((v["p15"], c) for c, v in m["comarcas"].items() if c in regio and "p15" in v), reverse=True)[:TOP_PLUJA]
                top = [c for _, c in pluja]
                noms = ["%s (%d l/m²)" % (self.dades["comarques"][c]["nom"], round(p)) for p, c in pluja]
                llista = ", ".join(noms[:3])
                frase = (t["pluja_resum"] if pluja and pluja[0][0] >= POCA_PLUJA else t["pluja_poca"]).format(llista=llista)
                data_txt = "%d %s de %d" % (data.day, t["de_mes"][data.month - 1], data.year)
                parts.append(f'<p class="data">{e(t["dades_fins"].format(font=self.cfg["font_meteo"][idioma], data=data_txt))}</p>')
                parts.append(f'<p class="resum">{e(frase)}</p>')
                parts.append(self.baixa(idioma, "portada"))
                parts.append(self.mapa_svg(top, idioma))
                parts.append(f'<section class="paper"><h2>{e(t["pluja_titol"])}</h2><ol class="ranquing">' + "".join(
                    f'<li><b>{e(self.dades["comarques"][c]["nom"])}</b> <span>{round(p)} l/m²</span></li>' for p, c in pluja) +
                    f'</ol><p>{e(t["pluja_consell"])}</p></section>')
                parts.append(self.baixa(idioma, "portada_mapa", t["app_mapa"]))
        if not any('class="baixa"' in p for p in parts):
            parts.append(self.baixa(idioma, "portada"))
        mes = avui.month
        millors = [b for b in self.bolets.values() if b.get("camp") and mes in b["camp"]["millors"]]
        tambe = [b for b in self.bolets.values() if b.get("camp") and mes in b["camp"]["mesos"] and b not in millors]
        if millors or tambe:
            ordena = lambda bs: sorted(bs, key=lambda b: b["nom"][idioma])  # noqa: E731
            parts.append(f'<section><h2>{e(t["temporada_titol"].format(mes=t["mesos"][mes - 1]))}</h2>')
            if millors:
                parts.append(f'<h3>{e(t["temporada_millors"])}</h3><div class="graella">' +
                             "".join(self.targeta(b, idioma) for b in ordena(millors)) + "</div>")
            if tambe:
                parts.append(f'<h3>{e(t["temporada_tambe"])}</h3><div class="graella">' +
                             "".join(self.targeta(b, idioma) for b in ordena(tambe)) + "</div>")
            parts.append("</section>")
        parts.append(f'<p class="enllacos"><a href="{self.url_llistat("comestibles", idioma)}">{e(t["tots_comestibles"])}</a>'
                     f'<a href="{self.url_llistat("toxics", idioma)}">{e(t["tots_toxics"])}</a></p>')
        app = {"@context": "https://schema.org", "@type": "MobileApplication", "name": self.cfg["app"],
               "operatingSystem": "Android", "applicationCategory": "ReferenceApplication", "inLanguage": self.idiomes,
               "description": t["app_descripcio"], "image": DOMINI + self.cfg["icona"],
               "url": "https://play.google.com/store/apps/details?id=" + self.cfg["paquet"],
               "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
               "publisher": {"@type": "Organization", "name": "Shanti Apps Lab"}}
        return self.pagina(idioma, self.base(idioma), t["titol_portada"], t["portada_desc"],
                           "\n".join(parts), {i: self.base(i) for i in self.idiomes}, "portada", dades=[app])

    def genera(self):
        avui = dt.datetime.now(dt.timezone.utc).date()
        for i in self.idiomes:
            self.escriu(self.base(i), self.portada(i, avui))
            for tipus in ("comestibles", "toxics"):
                self.escriu(self.url_llistat(tipus, i), self.llistat(tipus, i))
            for b in sorted(self.bolets.values(), key=lambda b: b["id"]):
                self.escriu(self.url_bolet(b, i), self.fitxa(b, i))
        mapa = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
                "".join("<url><loc>%s%s</loc></url>\n" % (DOMINI, u) for u in self.urls) + "</urlset>\n")
        p = os.path.join(WEB, self.carpeta, "sitemap.xml")
        if not os.path.exists(p) or open(p, encoding="utf-8").read() != mapa:
            open(p, "w", encoding="utf-8", newline="\n").write(mapa)
            self.escrits += 1
        print("%s: %d pàgines, %d fitxers canviats" % (self.carpeta, len(self.urls), self.escrits))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    Web(sys.argv[1]).genera()
