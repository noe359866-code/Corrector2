-- ============================================================================
--  tests/fixture_e2e.sql · 9 filas realistas para el extremo a extremo local
--    psql "$DB" -f tests/fixture_e2e.sql
--    MOCK_DB_URL="$DB" MOCK_PORT=8899 python tests/mock_postgrest.py &
--    SUPABASE_URL=http://localhost:8899 SUPABASE_SERVICE_KEY=mock \
--      python -m src.main all
-- ============================================================================

truncate public.torrents restart identity;
delete from public.title_id_cache;

insert into public.torrents
  (title, type, quality, codec, seeders, audio, subtitles, release_group)
values
  -- 3 versiones de The Matrix (Latino, Castellano, inglés con seeders)
  ('The.Matrix.1999.1080p.BluRay.x264-LAT', 'movie', '1080p', 'x264', 40,
   array['Latino'], '{}', 'LAT'),
  ('The.Matrix.1999.1080p.BluRay.x264-CAST', 'movie', '1080p', 'x264', 35,
   array['Castellano'], '{}', 'CAST'),
  ('The.Matrix.1999.720p.BluRay.x264-ENG', 'movie', '720p', 'x264', 95,
   array['English'], '{}', 'ENG'),
  -- grupo anime SOLO subtitulado (x2: 1080p bueno + 480p peor)
  ('Kimetsu.no.Yaiba.S04E01.1080p.WEB-DL.SUBS-ES', 'series', '1080p', 'x264',
   20, array['Japanese'], array['Español'], 'SUBS'),
  ('Kimetsu.no.Yaiba.S04E01.480p.HDTV.SUBS-ES', 'series', '480p', 'x264',
   5, array['Japanese'], array['Español'], 'SUBS'),
  ('Jujutsu.Kaisen.S02E05.1080p.WEB-DL.SUBS-ES', 'series', '1080p', 'x264',
   12, array['Japanese'], array['SUBS [ES]'], 'SUBS'),
  -- adulto (la limpieza lo borra)
  ('OnlyFans.Leak.Pack.2024.XXX.1080p', 'movie', '1080p', 'x264', 3,
   array['English'], '{}', 'XXXLEAKS'),
  -- solo absolute_episode (la limpieza lo borra)
  ('One.Punch.Man.012.1080p', 'anime', '1080p', 'x264', 8,
   array['Japanese'], '{}', 'ANIME');

insert into public.torrents
  (title, title_text, type, season, episode, quality, codec, seeders,
   audio, subtitles, release_group)
values
  -- nombre "interno" en title, release real en title_text
  ('Frieren interno 999',
   'Frieren.Beyond.Journeys.End.S01E12.1080p.WEB-DL.x264-GROUP',
   'anime', 1, 12, '1080p', 'x264', 15,
   array['Japanese'], array['English'], 'GROUP');

-- el absolute_only necesita episode null + absolute_episode relleno
update public.torrents set season = null, episode = null, absolute_episode = 12
 where title = 'One.Punch.Man.012.1080p';
update public.torrents set season = 4, episode = 1
 where title like 'Kimetsu.no.Yaiba.S04E01.1080p%';
update public.torrents set season = 4, episode = 1
 where title like 'Kimetsu.no.Yaiba.S04E01.480p%';
update public.torrents set season = 2, episode = 5
 where title like 'Jujutsu.Kaisen.S02E05%';

select count(*) as filas from public.torrents;
