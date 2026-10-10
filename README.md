# shantiappslab.github.io
## games.json (recomendaciones dentro de los juegos)

Los juegos descargan `games.json` (como mucho una vez al día) y recomiendan los demás
juegos de la lista ("Más juegos" y alguna sugerencia tras ganar). Para añadir un juego
publicado: una entrada nueva en `games` (`title` = nombre exacto del proyecto de Godot,
`package`, `icon` y `tagline` en inglés y español) y su icono de 192 px en `games/icons/`.
Cada juego se oculta a sí mismo. No borrar ni renombrar campos: los juegos ya publicados
los leen así (si hace falta otro formato, subir `version`).
