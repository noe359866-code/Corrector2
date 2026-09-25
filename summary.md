<!-- reportes/1-limpieza.md -->
# 🎬 Reporte del editor de torrents

**Ejecución:** `2026-09-25T02:52:30+0000` · **Duración:** 0.3 s · **Modo dry-run:** no

## 📊 Antes → Después

| Métrica | Antes | Después |
| --- | ---: | ---: |
| total | 9 | 7 |
| absolute_only | 1 | 0 |
| sin_id | 9 | 7 |
| sin_seeders | 0 | 0 |
| tipo · movie | 3 | 3 |
| tipo · series | 2 | — |
| tipo · anime | 4 | 4 |

## 🧹 Limpieza

> 🎚️ **Sin tope esta corrida** (`MAX_DELETES_PER_RUN=0`): se borra todo lo que coincida. Pon 500 para limitar el total de filas borradas.

| Etapa | Coincidencias | Borradas | Fuera por tope |
| --- | ---: | ---: | ---: |
| Basura / títulos vacíos | 0 | 0 | — |
| xxx · onlyfans · adulto | 1 | 1 | — |
| Episodio solo en absolute_episode | 1 | 1 | — |

<details><summary>Ejemplos borrados · xxx · onlyfans · adulto</summary>

- `OnlyFans Exclusive Pack XXX 1080p`

</details>

<details><summary>Ejemplos borrados · Episodio solo en absolute_episode</summary>

- `Serie Rara 1080p WEB x264 Latino`

</details>

---
*Generado automáticamente por `torrents-enricher`*
---

<!-- reportes/2-ids.md -->
# 🎬 Reporte del editor de torrents

**Ejecución:** `2026-09-25T02:52:31+0000` · **Duración:** 1.6 s · **Modo dry-run:** no

## 🛡️ Seguridad

| Nivel | Hallazgo |
| --- | --- |
| ⚠️ MEDIO | RLS está desactivado en public.torrents (actívalo y deja solo SELECT público) |

## 🔗 IDs resueltos (TMDB / IMDb / AniList / Kitsu / MAL)

| Métrica | Valor |
| --- | --- |
| Filas procesadas | 7 |
| Resueltas completas | 4 |
| Parciales | 3 |
| Sin match | 0 |
| Errores | 0 |
| Aciertos de caché | 7 |
| Filas con fallback agresivo | 3 |
| ...de las cuales se rescató el ID | 0 |
| IDs descartados por formato inválido | 0 |

**Aportes por proveedor:** `anilist+kitsu+imdb:suggest`=4, `imdb:suggest`=3

<details><summary>Estadísticas de APIs</summary>

| Proveedor | Llamadas | Aciertos | Sin match | Errores |
| --- | ---: | ---: | ---: | ---: |
| anilist | 0 | 0 | 0 | 0 |
| kitsu | 0 | 0 | 0 | 0 |
| jikan | 0 | 0 | 0 | 0 |
| imdb | 0 | 0 | 0 | 0 |
| tvmaze | 0 | 0 | 0 | 0 |

</details>

<details><summary>Ejemplos de matches</summary>

- Frieren Beyond Journeys End -> anilist+kitsu+imdb:suggest (0.97)
- Kimetsu no Yaiba -> anilist+kitsu+imdb:suggest (0.97)
- (parcial) The Matrix -> imdb:suggest
- Jujutsu Kaisen -> anilist+kitsu+imdb:suggest (0.97)
- Kimetsu no Yaiba -> anilist+kitsu+imdb:suggest (0.97)
- (parcial) The Matrix -> imdb:suggest
- (parcial) The Matrix -> imdb:suggest

</details>

**Caché local:** titulos_cache=4, sin_match_cache=0, ids_externos=0

---
*Generado automáticamente por `torrents-enricher`*
---

<!-- reportes/3-reintentos.md -->
# 🎬 Reporte del editor de torrents

**Ejecución:** `2026-09-25T02:52:33+0000` · **Duración:** 0.4 s · **Modo dry-run:** no

## 📊 Antes → Después

| Métrica | Antes | Después |
| --- | ---: | ---: |
| total | 7 | 7 |
| sin_id | 0 | 0 |

## ♻️ Reintentos de IDs que quedaron NULL

| Métrica | Valor |
| --- | --- |
| Filas sin ningún ID detectadas | 0 |
| Devueltas a la cola | 0 |

## 🔗 IDs resueltos (TMDB / IMDb / AniList / Kitsu / MAL)

| Métrica | Valor |
| --- | --- |
| Filas procesadas | 0 |
| Resueltas completas | 0 |
| Parciales | 0 |
| Sin match | 0 |
| Errores | 0 |
| Aciertos de caché | 0 |
| Filas con fallback agresivo | 0 |
| ...de las cuales se rescató el ID | 0 |
| IDs descartados por formato inválido | 0 |

<details><summary>Estadísticas de APIs</summary>

| Proveedor | Llamadas | Aciertos | Sin match | Errores |
| --- | ---: | ---: | ---: | ---: |
| anilist | 0 | 0 | 0 | 0 |
| kitsu | 0 | 0 | 0 | 0 |
| jikan | 0 | 0 | 0 | 0 |
| imdb | 0 | 0 | 0 | 0 |
| tvmaze | 0 | 0 | 0 | 0 |

</details>

**Caché local:** titulos_cache=4, sin_match_cache=0, ids_externos=0

---
*Generado automáticamente por `torrents-enricher`*
---

<!-- reportes/4-espanol.md -->
# 🎬 Reporte del editor de torrents

**Ejecución:** `2026-09-25T02:52:34+0000` · **Duración:** 0.3 s · **Modo dry-run:** no

## 📊 Antes → Después

| Métrica | Antes | Después |
| --- | ---: | ---: |
| total | 7 | 6 |
| absolute_only | 0 | 0 |
| sin_id | 0 | 0 |
| sin_seeders | 0 | 0 |
| tipo · movie | 3 | 2 |
| tipo · anime | 4 | 4 |

## 🧹 Limpieza

> 🎚️ **Sin tope esta corrida** (`MAX_DELETES_PER_RUN=0`): se borra todo lo que coincida. Pon 500 para limitar el total de filas borradas.

| Etapa | Coincidencias | Borradas | Fuera por tope |
| --- | ---: | ---: | ---: |
| Español + deduplicado + top 3 | 0 | 0 | — |

## 🇪🇸 Regla de español y deduplicado

| Métrica | Valor |
| --- | --- |
| Repetidos eliminados | 1 |
| Filas borradas (por tope de 3) | 0 |
| Filas conservadas | 6 |
| Grupos (obra/temporada/episodio) | 4 |
| Grupos con 2+ en AUDIO | 0 |
| Grupos con 2+ en audio o subtítulos | 1 |
| Grupos cortos de español | 3 |
| Conservados POR SUBTITULADO (sin audio ES) | 4 |
| Sin agrupar (packs sin episodio) | 0 |
| ⏳ Pendientes para la próxima corrida (tope) | 0 |

> 🔁 Cascada: **4** torrents se conservaron por tener **subtítulos en español** (no había audio en español suficiente).

<details><summary>Grupos que no llegan a 2 en español</summary>

- `The Matrix 1999 720p WEB-DL x264 Inglés`
- `Jujutsu Kaisen S02E05 1080p CR WEB-DL x264 Subs ES`
- `Frieren Beyond Journeys End S01E01 1080p WEB-DL x264 Sub Español`
- `The Matrix 1999 1080p BluRay x264 AC3 Latino`

</details>

---
*Generado automáticamente por `torrents-enricher`*
---

<!-- reportes/5-reordenar.md -->
# 🎬 Reporte del editor de torrents

**Ejecución:** `2026-09-25T02:52:34+0000` · **Duración:** — s · **Modo dry-run:** no

## 📊 Antes → Después

| Métrica | Antes | Después |
| --- | ---: | ---: |
| total | 6 | 6 |
| absolute_only | 0 | 0 |
| sin_id | 0 | 0 |
| sin_seeders | 0 | 0 |
| tipo · movie | 2 | 2 |
| tipo · anime | 4 | 4 |


## 🧮 Columna `id` (reordenador)

**Antes:** total=6 · rango `1..9` · huecos=3 · en orden: no

| Métrica | Valor |
| --- | --- |
| Filas renumeradas | 6 |
| Rango nuevo | `1..6` |
| Huecos rellenados | 3 |
| Secuencia identity | `public.torrents_id_seq` |

> renumerado de 1..9 a 1..6

**Después:** total=6 · rango `1..6` · huecos=0 · en orden: ✅ sí
---
*Generado automáticamente por `torrents-enricher`*
---

