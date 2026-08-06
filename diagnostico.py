# ==========================================================
# Predix Sports — diagnostico.py
# Calcula a taxa de acerto histórica por categoria de mercado,
# comparando as previsões salvas (mercados_json, gerado pelo
# montar_mercados() do app.py) com o resultado real do jogo
# (tabela "jogos"/"jogos_b" — gols_casa/gols_fora/status). Alimenta
# a aba Bingo.
#
# PARAMETRIZADO POR LIGA (sufixo_liga="" Série A, "_b" Série B) —
# cada liga tem sua própria taxa de acerto histórica, calculada
# separadamente (times/mercados diferentes, não faz sentido misturar).
#
# LIMITAÇÃO CONHECIDA (arquitetura atual do coletor.py): só dá pra
# diagnosticar categorias cujo resultado real vem direto do placar.
# Escanteios/cartões/chutes/faltas hoje só têm a MÉDIA MÓVEL do time
# salva, não o valor real daquela partida específica — então não tem
# como comparar "previsão daquele jogo" com "quanto saiu de verdade
# naquele jogo" pra essas categorias ainda. Por isso as categorias
# diagnosticáveis são: resultado, gols, btts, casa_marca, fora_marca.
# ==========================================================

import json
from database import supabase

CATEGORIAS_DIAGNOSTICAVEIS = ["resultado", "gols", "btts", "casa_marca", "fora_marca"]

NOMES_CATEGORIAS = {
    "resultado": "Resultado (1X2 / Dupla Chance)",
    "gols": "Gols (Over/Under)",
    "btts": "Ambas Marcam",
    "casa_marca": "Mandante Marca",
    "fora_marca": "Visitante Marca",
}


def _acertou(categoria, mercado, gols_casa, gols_fora):
    """Compara a direção escolhida pelo mercado (salva em mercados_json)
    com o placar real do jogo. Retorna True/False, ou None se não dá
    pra avaliar (dado incompleto)."""
    direcao = mercado.get("direcao")

    if categoria == "resultado":
        if gols_casa > gols_fora:
            vencedor = "casa"
        elif gols_casa < gols_fora:
            vencedor = "fora"
        else:
            vencedor = "empate"
        return {
            "vitoria_casa": vencedor == "casa",
            "vitoria_fora": vencedor == "fora",
            "empate": vencedor == "empate",
            "dupla_1x": vencedor in ("casa", "empate"),
            "dupla_x2": vencedor in ("fora", "empate"),
        }.get(direcao)

    if categoria == "gols":
        linha = mercado.get("linha")
        if linha is None:
            return None
        total = gols_casa + gols_fora
        if direcao == "mais":
            return total > linha
        if direcao == "menos":
            return total < linha
        return None

    if categoria == "btts":
        ambas_marcaram = gols_casa > 0 and gols_fora > 0
        return ambas_marcaram if direcao == "sim" else not ambas_marcaram

    if categoria == "casa_marca":
        marcou = gols_casa > 0
        return marcou if direcao == "sim" else not marcou

    if categoria == "fora_marca":
        marcou = gols_fora > 0
        return marcou if direcao == "sim" else not marcou

    return None


def calcular_taxas_acerto(min_amostras: int = 15, sufixo_liga: str = "") -> dict:
    """
    Varre todas as previsões salvas (da liga indicada) com mercados_json
    preenchido, cruza com o resultado real do jogo correspondente (quando
    encerrado) e devolve, por categoria diagnosticável:

        {"acertos": int, "total": int, "taxa": float, "confiavel": bool}

    'confiavel' só vira True quando total >= min_amostras — enquanto
    isso a categoria não deve ser usada pra decidir o Bingo.
    """
    tabela_previsoes = f"previsoes{sufixo_liga}"
    tabela_jogos = f"jogos{sufixo_liga}"

    taxas = {cat: {"acertos": 0, "total": 0} for cat in CATEGORIAS_DIAGNOSTICAVEIS}

    try:
        previsoes = supabase.table(tabela_previsoes).select(
            "data,time_casa,time_fora,mercados_json"
        ).execute().data or []
    except Exception as e:
        print(f"[ERRO] calcular_taxas_acerto{sufixo_liga} (previsoes): {e}")
        previsoes = []

    for p in previsoes:
        if not p.get("mercados_json"):
            continue

        try:
            jogo_real = supabase.table(tabela_jogos).select("gols_casa,gols_fora,status") \
                .eq("casa_nome", p["time_casa"]).eq("fora_nome", p["time_fora"]) \
                .eq("data", p["data"]).execute().data
        except Exception as e:
            print(f"[ERRO] calcular_taxas_acerto{sufixo_liga} (jogos): {e}")
            continue

        if not jogo_real or jogo_real[0].get("status") != "encerrado":
            continue

        gc, gf = jogo_real[0].get("gols_casa"), jogo_real[0].get("gols_fora")
        if gc is None or gf is None:
            continue

        try:
            mercados = json.loads(p["mercados_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        for cat in CATEGORIAS_DIAGNOSTICAVEIS:
            mercado = mercados.get(cat)
            if not mercado:
                continue
            acerto = _acertou(cat, mercado, gc, gf)
            if acerto is None:
                continue
            taxas[cat]["total"] += 1
            if acerto:
                taxas[cat]["acertos"] += 1

    for cat, dados in taxas.items():
        total = dados["total"]
        dados["taxa"] = round(100 * dados["acertos"] / total, 1) if total else 0.0
        dados["confiavel"] = total >= min_amostras

    return taxas


def melhores_categorias(taxas: dict, n: int = 2) -> list:
    """Retorna as N categorias diagnosticáveis com maior taxa de acerto,
    considerando só as que já têm amostra suficiente (confiavel=True).
    Se menos de N categorias forem confiáveis, retorna as que houver."""
    confiaveis = [(cat, d["taxa"]) for cat, d in taxas.items() if d["confiavel"]]
    confiaveis.sort(key=lambda x: x[1], reverse=True)
    return [cat for cat, _ in confiaveis[:n]]
