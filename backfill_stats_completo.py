"""
Predix Sports — backfill_stats_completo.py
Reprocessa TODAS as rodadas já encerradas da temporada (Série A e/ou
Série B), usando o mesmo buscar_stats_por_time()/atualizar_stats_times()
do coletor — ou seja, já herda automaticamente o filtro de sanidade
(posse_bola, piso/teto de chutes e faltas) adicionado em 07/08/2026.

MOTIVO: o coletor diário roda em modo incremental (últimas 4 rodadas),
então médias calculadas ANTES do filtro de sanidade existir continuam
"presas" nas colunas fin_/fing_/esc_/cart_/falta_ até a rodada em
questão sair da janela — o que pode levar semanas. Rodar este backfill
uma vez recalcula tudo do zero, já limpo, pra toda a temporada.

IMPORTANTE: isso SOBRESCREVE as colunas fin_/fing_/esc_/cart_/falta_
de cada time em times(_b) com a média recalculada de todas as rodadas
já jogadas — não é um merge suave, é um recálculo completo.

CUSTO DE API: 1 chamada de /partidas/{id}/estatisticas por partida já
encerrada na temporada. Pra ~20 rodadas x ~10 jogos = ~190 partidas por
liga, com 0.3s de sleep entre chamadas — só a espera já fica uns
60-90s por liga, fora latência de rede. Roda fora de horário de jogo.

Uso: só via GitHub Actions (workflow_dispatch) — ver backfill_manual.yml
"""

import argparse
import time


def rodar_backfill_serie_a():
    import coletor
    print("=" * 60)
    print("[BACKFILL] Série A — iniciando")
    print("=" * 60)
    rodada_atual = coletor.buscar_numero_rodada_atual()
    if not rodada_atual:
        print("[BACKFILL] Não consegui identificar a rodada atual da Série A — abortando.")
        return
    ultima_rodada_jogada = max(1, rodada_atual - 1)
    rodadas = list(range(1, ultima_rodada_jogada + 1))
    print(f"[BACKFILL] Reprocessando rodadas 1 a {ultima_rodada_jogada} ({len(rodadas)} rodadas)")
    coletor.atualizar_stats_times(rodadas=rodadas)
    print("[BACKFILL] Série A concluído.")


def rodar_backfill_serie_b():
    import coletor_b
    print("=" * 60)
    print("[BACKFILL] Série B — iniciando")
    print("=" * 60)
    rodada_atual = coletor_b.buscar_numero_rodada_atual()
    if not rodada_atual:
        print("[BACKFILL] Não consegui identificar a rodada atual da Série B — abortando.")
        return
    ultima_rodada_jogada = max(1, rodada_atual - 1)
    rodadas = list(range(1, ultima_rodada_jogada + 1))
    print(f"[BACKFILL] Reprocessando rodadas 1 a {ultima_rodada_jogada} ({len(rodadas)} rodadas)")
    coletor_b.atualizar_stats_times(rodadas=rodadas)
    print("[BACKFILL] Série B concluído.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--liga", choices=["a", "b", "ambas"], default="ambas")
    args = parser.parse_args()

    inicio = time.time()
    if args.liga in ("a", "ambas"):
        rodar_backfill_serie_a()
    if args.liga in ("b", "ambas"):
        rodar_backfill_serie_b()
    duracao = time.time() - inicio
    print(f"[BACKFILL] Tudo concluído em {duracao:.1f}s")