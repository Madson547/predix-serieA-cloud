# ==========================================================
# Predix Sports — banca.py
# CRUD de apostas reais + cálculo de ROI, cruzando com o que o
# Predix já prevê. Não depende de liga separada em tabela — usa
# uma coluna "liga" na mesma tabela "apostas".
# ==========================================================

from datetime import datetime
from database import supabase


def salvar_aposta(data_aposta, liga, jogos_envolvidos, mercados, categoria_estimada,
                   tipo_aposta, odd, stake, observacoes="", fonte_print=False) -> bool:
    try:
        supabase.table("apostas").insert({
            "data_aposta": data_aposta,
            "liga": liga,
            "jogos_envolvidos": jogos_envolvidos,
            "mercados": mercados,
            "categoria_estimada": categoria_estimada,
            "tipo_aposta": tipo_aposta,
            "odd": odd,
            "stake": stake,
            "status": "pendente",
            "observacoes": observacoes,
            "fonte_print": fonte_print,
        }).execute()
        return True
    except Exception as e:
        print(f"[ERRO] salvar_aposta: {e}")
        return False


def buscar_apostas(status: str = None, liga: str = None, limite: int = 100) -> list:
    """status=None traz todas; 'pendente'/'ganhou'/'perdeu' filtra."""
    try:
        query = supabase.table("apostas").select("*").order("data_aposta", desc=True)
        if status:
            query = query.eq("status", status)
        if liga:
            query = query.eq("liga", liga)
        resp = query.limit(limite).execute()
        return resp.data or []
    except Exception as e:
        print(f"[ERRO] buscar_apostas: {e}")
        return []


def atualizar_resultado_aposta(aposta_id: int, ganhou: bool, retorno_manual: float = None) -> bool:
    """
    Marca a aposta como ganha/perdida e calcula o retorno automaticamente
    (stake*odd se ganhou, -stake se perdeu) — a menos que retorno_manual
    seja informado (ex: perna anulada mudou o valor real do pagamento).
    """
    try:
        aposta = supabase.table("apostas").select("stake,odd").eq("id", aposta_id).execute().data
        if not aposta:
            return False
        stake, odd = aposta[0]["stake"], aposta[0]["odd"]

        if retorno_manual is not None:
            retorno = retorno_manual
        else:
            retorno = round(stake * odd, 2) if ganhou else round(-stake, 2)

        supabase.table("apostas").update({
            "status": "ganhou" if ganhou else "perdeu",
            "retorno": retorno,
            "atualizado_em": datetime.now().isoformat(),
        }).eq("id", aposta_id).execute()
        return True
    except Exception as e:
        print(f"[ERRO] atualizar_resultado_aposta: {e}")
        return False


def excluir_aposta(aposta_id: int) -> bool:
    """Só pra correção de erro de digitação — não use pra 'limpar' histórico
    de apostas resolvidas, elas são o dado que sustenta o ROI real."""
    try:
        supabase.table("apostas").delete().eq("id", aposta_id).execute()
        return True
    except Exception as e:
        print(f"[ERRO] excluir_aposta: {e}")
        return False


def buscar_evolucao_lucro(liga: str = None) -> list:
    """
    Retorna a série histórica de lucro/prejuízo ACUMULADO ao longo do
    tempo, ordenada por data da aposta — pra plotar a curva de evolução
    (não é foto do momento, é tendência). Só considera apostas já
    resolvidas (ganhou/perdeu).
    """
    try:
        query = supabase.table("apostas").select("data_aposta,retorno,stake") \
            .in_("status", ["ganhou", "perdeu"]).order("data_aposta")
        if liga:
            query = query.eq("liga", liga)
        apostas = query.execute().data or []
    except Exception as e:
        print(f"[ERRO] buscar_evolucao_lucro: {e}")
        apostas = []

    evolucao = []
    acumulado = 0.0
    for a in apostas:
        lucro_aposta = (a["retorno"] or 0) - a["stake"]
        acumulado += lucro_aposta
        evolucao.append({"data": a["data_aposta"], "lucro_acumulado": round(acumulado, 2)})
    return evolucao


def calcular_roi(liga: str = None) -> dict:
    """
    Calcula ROI agregado só das apostas já resolvidas (ganhou/perdeu) —
    pendentes não entram na conta ainda. Devolve resumo geral + quebra
    por liga, por categoria de mercado e por tipo de aposta.
    """
    try:
        query = supabase.table("apostas").select("*").in_("status", ["ganhou", "perdeu"])
        if liga:
            query = query.eq("liga", liga)
        apostas = query.execute().data or []
    except Exception as e:
        print(f"[ERRO] calcular_roi: {e}")
        apostas = []

    def _resumo(lista):
        investido = sum(a["stake"] for a in lista)
        retorno = sum(a["retorno"] or 0 for a in lista)
        lucro = retorno - investido
        roi = round(100 * lucro / investido, 1) if investido else 0.0
        vitorias = sum(1 for a in lista if a["status"] == "ganhou")
        return {
            "investido": round(investido, 2),
            "retorno": round(retorno, 2),
            "lucro": round(lucro, 2),
            "roi": roi,
            "total_apostas": len(lista),
            "vitorias": vitorias,
            "taxa_acerto": round(100 * vitorias / len(lista), 1) if lista else 0.0,
        }

    geral = _resumo(apostas)

    por_liga = {}
    for a in apostas:
        por_liga.setdefault(a.get("liga") or "—", []).append(a)
    por_liga = {k: _resumo(v) for k, v in por_liga.items()}

    por_categoria = {}
    for a in apostas:
        por_categoria.setdefault(a.get("categoria_estimada") or "mista", []).append(a)
    por_categoria = {k: _resumo(v) for k, v in por_categoria.items()}

    por_tipo = {}
    for a in apostas:
        por_tipo.setdefault(a.get("tipo_aposta") or "simples", []).append(a)
    por_tipo = {k: _resumo(v) for k, v in por_tipo.items()}

    return {
        "geral": geral,
        "por_liga": por_liga,
        "por_categoria": por_categoria,
        "por_tipo": por_tipo,
    }
