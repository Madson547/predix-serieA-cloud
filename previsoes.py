# ==========================================================
# Predix Série A — previsoes.py
# Salva e busca previsões geradas pelo app, pra comparação
# posterior com o resultado real (aba Medidor de Desempenho).
# ==========================================================

import json
from database import supabase


def salvar_previsao(resultado, casa: str, fora: str, data_jogo: str, top3: list) -> bool:
    """Salva (ou atualiza) a previsão essencial de um confronto.
    Chamado manualmente pelo botão 'Salvar previsão' no app.py."""
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
    }
    try:
        existe = supabase.table("previsoes").select("id") \
            .eq("data", data_jogo).eq("time_casa", casa).eq("time_fora", fora).execute()
        if existe.data:
            supabase.table("previsoes").update(registro) \
                .eq("data", data_jogo).eq("time_casa", casa).eq("time_fora", fora).execute()
        else:
            supabase.table("previsoes").insert(registro).execute()
        return True
    except Exception as e:
        print(f"[ERRO] salvar_previsao: {e}")
        return False


def buscar_previsoes(termo: str = "") -> list:
    """Busca previsões salvas. Sem termo, retorna as mais recentes.
    Com termo, filtra por time (casa OU fora) via ilike."""
    try:
        query = supabase.table("previsoes").select("*").order("data", desc=True)
        if termo:
            resp = query.or_(f"time_casa.ilike.%{termo}%,time_fora.ilike.%{termo}%").limit(30).execute()
        else:
            resp = query.limit(30).execute()
        return resp.data or []
    except Exception as e:
        print(f"[ERRO] buscar_previsoes: {e}")
        return []
