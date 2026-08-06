# ==========================================================
# Predix Sports — previsoes.py
# Salva e busca previsões geradas pelo app, pra comparação
# posterior com o resultado real (aba Medidor de Desempenho).
#
# PARAMETRIZADO POR LIGA (sufixo_liga="" Série A, "_b" Série B) —
# mesma tabela lógica, dois conjuntos de dados separados.
# ==========================================================

import json
from database import supabase


def salvar_previsao(resultado, casa: str, fora: str, data_jogo: str, top3: list,
                     multiplas: list = None, mercados: dict = None, sufixo_liga: str = "") -> bool:
    """Salva (ou atualiza) a previsão essencial de um confronto, incluindo
    as 5 múltiplas geradas — pra analisar rodada a rodada qual combinação
    de mercados performa melhor de verdade, não só mercado por mercado.

    mercados: dict retornado por montar_mercados() (app.py), com TODOS os
    mercados individuais já estruturados (tipo/nome/prob/linha/direcao).
    É isso que o diagnostico.py usa depois pra calcular a taxa de acerto
    real por categoria — sem isso, só dá pra comparar texto solto do
    top3/múltiplas, que não dá pra cruzar com o resultado real de forma
    confiável.

    Chamado manualmente pelo botão 'Salvar previsão' no app.py."""
    tabela = f"previsoes{sufixo_liga}"
    registro = {
        "data": data_jogo,
        "time_casa": casa,
        "time_fora": fora,
        "prob_casa": resultado.prob_casa,
        "prob_empate": resultado.prob_empate,
        "prob_fora": resultado.prob_fora,
        "prob_btts": resultado.prob_btts,
        "over15_ft": resultado.over15_ft,
        "over25_ft": resultado.over25_ft,
        "cantos_ft": resultado.cantos_ft,
        "linha_cantos": resultado.linha_cantos,
        "prob_over_cantos": resultado.prob_over_cantos,
        "cartoes_ft": resultado.cartoes_ft,
        "linha_cartoes": resultado.linha_cartoes,
        "prob_over_cartoes": resultado.prob_over_cartoes,
        "placar_mais_provavel": resultado.placar_mais_provavel,
        "top3_json": json.dumps(top3, ensure_ascii=False),
        "multiplas_json": json.dumps(multiplas, ensure_ascii=False) if multiplas else None,
        "mercados_json": json.dumps(mercados, ensure_ascii=False) if mercados else None,
    }
    try:
        existe = supabase.table(tabela).select("id") \
            .eq("data", data_jogo).eq("time_casa", casa).eq("time_fora", fora).execute()
        if existe.data:
            supabase.table(tabela).update(registro) \
                .eq("data", data_jogo).eq("time_casa", casa).eq("time_fora", fora).execute()
        else:
            supabase.table(tabela).insert(registro).execute()
        return True
    except Exception as e:
        print(f"[ERRO] salvar_previsao{sufixo_liga}: {e}")
        return False


def buscar_previsoes_do_dia(data_jogo: str, sufixo_liga: str = "") -> list:
    """Busca todas as previsões salvas pra uma data específica — usado
    pela aba Bingo, que precisa cobrir todos os jogos analisados e
    salvos no dia da análise, não só o confronto selecionado no Painel."""
    try:
        resp = supabase.table(f"previsoes{sufixo_liga}").select("*").eq("data", data_jogo).execute()
        return resp.data or []
    except Exception as e:
        print(f"[ERRO] buscar_previsoes_do_dia{sufixo_liga}: {e}")
        return []


def buscar_previsoes(termo: str = "", sufixo_liga: str = "") -> list:
    """Busca previsões salvas. Sem termo, retorna as mais recentes.
    Com termo, filtra por time (casa OU fora) via ilike."""
    try:
        query = supabase.table(f"previsoes{sufixo_liga}").select("*").order("data", desc=True)
        if termo:
            resp = query.or_(f"time_casa.ilike.%{termo}%,time_fora.ilike.%{termo}%").limit(30).execute()
        else:
            resp = query.limit(30).execute()
        return resp.data or []
    except Exception as e:
        print(f"[ERRO] buscar_previsoes{sufixo_liga}: {e}")
        return []
