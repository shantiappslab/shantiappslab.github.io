# shantiappslab.github.io
## games.json (recomendaciones dentro de los juegos)

Los juegos descargan `games.json` (como mucho una vez al día) y recomiendan los demás
juegos de la lista ("Más juegos" y alguna sugerencia tras ganar). Para añadir un juego
publicado: una entrada nueva en `games` (`title` = nombre exacto del proyecto de Godot,
`package`, `icon` y `tagline` en inglés y español) y su icono de 192 px en `games/icons/`.
Cada juego se oculta a sí mismo. No borrar ni renombrar campos: los juegos ya publicados
los leen así (si hace falta otro formato, subir `version`).

## pirineu/pirineu.json (app «Pirineu Neu»)

Partes de nieve y previsión de las estaciones del Pirineo. Lo genera y sube el bot
(`Documents\PirineoApp\pirineo-bot`, comandos `export` + `publish`) desde la tarea
programada de Windows «PirineoBot», dos veces al día; solo hay commit si el fichero cambia.
La app lo descarga de `https://shantiappslab.github.io/pirineu/pirineu.json`.
No renombrar campos: si hace falta otro formato, subir `versio` (la app rechaza versiones que no conoce).

## Google Search Console (no borrar)

`google1a49205417b24dee.html` (raíz) verifica la propiedad `https://shantiappslab.github.io/` y `versant/google1a49205417b24dee.html` la de `/versant/`. Si se borran, se pierde la verificación. Sitemaps enviados: `bolets/sitemap.xml` (y `versant/sitemap.xml`); están también en `robots.txt`.

## bolets/ (web de Bolets de Catalunya)

Páginas generadas con `generador/genera.py bolets` a partir de `bolets/dades/` (lo exporta BoletsApps: `tools/web/exporta.py catalunya`). La tarea `bolets-meteo.yml` regenera la portada cada 6 horas. No editar los HTML a mano.
