-- ==========================================================
-- Predix Série A — escanteios reais casa/fora por time
-- Rode no SQL Editor do Supabase.
-- ==========================================================

update times set esc_casa=5.25, esc_fora=4.00 where nome='Chapecoense';
update times set esc_casa=6.10, esc_fora=4.66 where nome='Fluminense';
update times set esc_casa=5.85, esc_fora=4.10 where nome='Red Bull Bragantino';
update times set esc_casa=6.80, esc_fora=5.60 where nome='Flamengo';
update times set esc_casa=5.85, esc_fora=4.50 where nome='Corinthians';
update times set esc_casa=5.80, esc_fora=4.64 where nome='Bahia';
update times set esc_casa=6.75, esc_fora=5.50 where nome='Palmeiras';
update times set esc_casa=4.95, esc_fora=3.50 where nome='Grêmio';
update times set esc_casa=5.80, esc_fora=4.50 where nome='Internacional';
update times set esc_casa=6.12, esc_fora=4.47 where nome='Athletico Paranaense';
update times set esc_casa=5.54, esc_fora=4.30 where nome='Coritiba';
update times set esc_casa=6.00, esc_fora=4.82 where nome='São Paulo';
update times set esc_casa=6.25, esc_fora=4.85 where nome='Botafogo';
update times set esc_casa=5.22, esc_fora=4.15 where nome='Vitória';
update times set esc_casa=5.85, esc_fora=3.25 where nome='Atlético Mineiro';
update times set esc_casa=5.80, esc_fora=4.42 where nome='Cruzeiro';
update times set esc_casa=5.85, esc_fora=4.50 where nome='Santos';
update times set esc_casa=5.80, esc_fora=4.42 where nome='Vasco';
update times set esc_casa=5.85, esc_fora=4.50 where nome='Mirassol';
update times set esc_casa=5.80, esc_fora=4.12 where nome='Remo';

-- Conferir no final — nenhuma linha deve vir com esc_casa/esc_fora = 5.2/4.8
-- (o fallback antigo) nem NULL
select nome, esc_casa, esc_fora from times order by nome;
