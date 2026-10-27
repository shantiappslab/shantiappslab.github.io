# Nota micològica setmanal — instruccions per a la tasca programada

Cada diumenge al vespre (temporada: setembre-desembre i abril-juny), una tasca programada de Claude redacta la nota
«Què diuen els micòlegs» de cada app i la publica en aquest repositori. Les apps la baixen de `<carpeta>/nota.json` i
la web la mostra a la portada. **No hi ha revisió humana** (decisió del 2026-10-27): les normes d'aquí i
`comprova_nota.py` són la xarxa de seguretat.

## Apps

| app | carpeta | idiomes | àmbit |
|---|---|---|---|
| Bolets de Catalunya | `bolets` | ca, es | Catalunya |
| Setas de España | `setas` | es | Espanya |
| Funghi d'Italia | `funghi` | it | Itàlia |
| Champignons de France | `champignons` | fr | França |

## Passos (per a cada app)

1. Busca a internet què expliquen **aquesta setmana** (últims 7-8 dies) parcs micològics, societats i associacions
   micològiques, centres de recerca (CTFC…) i premsa que citi micòlegs o boletaires de l'àmbit de l'app: com va la
   temporada, on surten bolets, quines espècies, producció, preus als mercats, previsions dels experts.
   Només fonts de `fonts.json` (o dominis de societats micològiques). Mai índexs algorítmics ni apps competidores.
2. Escriu de 2 a 4 frases **amb paraules pròpies** (mai copiar text), de 280 caràcters com a màxim per idioma,
   cadascuna amb un sol fet concret i la seva font: `{"text": {...}, "font": "<nom de la font>", "url": "<enllaç>"}`.
   - Només fets que la font diu de veritat i amb data d'aquesta setmana. Si la font és d'abans, no la facis servir.
   - Res de comestibilitat, toxicitat, receptes ni identificació (ho diuen les fitxes revisades de l'app).
   - Res de llocs exactes de collita: com a molt la comarca, el departament o el parc.
   - To neutre i prudent («segons…», «expliquen que…»). Sense exagerar ni prometre bolets.
3. Desa l'esborrany i comprova'l: `python3 generador/nota/comprova_nota.py <app> esborrany.json`
   - codi 0: publicada a `<carpeta>/nota.json`;
   - codi 2: massa llarga → **resumeix-la** (menys frases o frases més curtes, sense perdre les fonts) i torna-ho a provar;
   - codi 1: rebutjada → corregeix el que diu (font no permesa, falta un idioma, paraula prohibida) o treu la frase.
     Si no en queda cap de vàlida, aquesta setmana l'app no té nota: no publiquis res.
4. Si aquesta setmana no trobes cap font fiable i recent, **no publiquis res** (no s'inventa mai).
5. Regenera la portada: `python3 generador/genera.py <carpeta>`.
6. Commit i push: `git add <carpeta>/nota.json <carpeta>/index.html <carpeta>/*/index.html <carpeta>/sitemap.xml`,
   missatge «Nota micològica <app> <setmana>».

`setmana` és el dilluns de la setmana que comença: la nota del diumenge 1 de novembre de 2026 porta «2026-11-02».
L'app i la web no mostren notes de més de 10 dies.
