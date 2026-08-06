# ==========================================================
# Predix Sports — diagnostico.py
# Calcula a taxa de acerto histórica por categoria de mercado,
# comparando as previsões salvas (mercados_json, gerado pelo
# montar_mercados() do app.py) com o resultado real do jogo.
#
# Duas fontes de "resultado real", combinadas num só dict por jogo:
#   1. jogos/jogos_b            -> gols_casa, gols_fora, status
#   2. estatisticas_partidas(_b) -> escanteios/cartões/chutes/faltas
#      REAIS daquela partida específica (07/08/2026 — antes o coletor
#      calculava esses valores só pra alimentar a média móvel do time
#      e descartava; agora fica salvo por partida, o que finalmente
#      permite diagnosticar esses mercados também, não só resultado/
#      gols/btts/casa_marca/fora_marca).
#
# PARAMETRIZADO POR LIGA (sufixo_liga="" Série A, "_b" Série B).
#
# LIMITAÇÃO REMANESCENTE: jogos analisados/salvos ANTES de 07/08/2026
# não têm linha correspondente em estatisticas_partidas (a tabela é
# nova) — pra esses, escanteios/cartões/chutes/faltas continuam sem
# poder ser avaliados, mas resultado/gols/btts/casa_marca/fora_marca
# funcionam normalmente desde sempre.
# ==========================================================

import json
from database import supabase

CATEGORIAS_DIAGNOSTICAVEIS = [
    "resultado", "gols", "btts", "casa_marca", "fora_marca",
    "escanteios", "cartoes", "chutes_casa", "chutes_fora",
    "chutes_gol_casa", "chutes_gol_fora", "faltas_casa", "faltas_fora",
]

NOMES_CATEGORIAS = {
    "resultado": "Resultado (1X2 / Dupla Chance)",
    "gols": "Gols (Over/Under)",
    "btts": "Ambas Marcam",
    "casa_marca": "Mandante Marca",
    "fora_marca": "Visitante Marca",
    "escanteios": "Escanteios (Over/Under)",
    "cartoes": "Cartões (Over/Under)",
    "chutes_casa": "Chutes — Mandante",
    "chutes_fora": "Chutes — Visitante",
    "chutes_gol_casa": "Chutes no Gol — Mandante",
    "chutes_gol_fora": "Chutes no Gol — Visitante",
    "faltas_casa": "Faltas — Mandante",
    "faltas_fora": "Faltas — Visitante",
}


def _acertou(categoria, mercado, real):
    """
    Compara a direção escolhida pelo mercado (salva em mercados_json)
    com o valor real da partida. 'real' é um dict combinando jogos +
    estatisticas_partidas: gols_casa, gols_fora, escanteios_casa,
    escanteios_fora, cartoes_casa, cartoes_fora, chutes_casa,
    chutes_fora, chutes_gol_casa, chutes_gol_fora, faltas_casa,
    faltas_fora — qualquer campo pode vir None se ainda não coletado.
    Retorna True/False, ou None se não dá pra avaliar.
    """
    direcao = mercado.get("direcao")
    linha = mercado.get("linha")
    gols_casa, gols_fora = real.get("gols_casa"), real.get("gols_fora")

    if categoria == "resultado":
        if gols_casa is None or gols_fora is None:
            return None
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
        if gols_casa is None or gols_fora is None or linha is None:
            return None
        total = gols_casa + gols_fora
        if direcao == "mais":
            return total > linha
        if direcao == "menos":
            return total < linha
        return None

    if categoria == "btts":
        if gols_casa is None or gols_fora is None:
            return None
        ambas_marcaram = gols_casa > 0 and gols_fora > 0
        return ambas_marcaram if direcao == "sim" else not ambas_marcaram

    if categoria == "casa_marca":
        if gols_casa is None:
            return None
        marcou = gols_casa > 0
        return marcou if direcao == "sim" else not marcou

    if categoria == "fora_marca":
        if gols_fora is None:
            return None
        marcou = gols_fora > 0
        return marcou if direcao == "sim" else not marcou

    if categoria == "escanteios":
        ec, ef = real.get("escanteios_casa"), real.get("escanteios_fora")
        if ec is None or ef is None or linha is None:
            return None
        total = ec + ef
        return total > linha if direcao == "mais" else total < linha

    if categoria == "cartoes":
        cc, cf = real.get("cartoes_casa"), real.get("cartoes_fora")
        if cc is None or cf is None or linha is None:
            return None
        total = cc + cf
        return total > linha if direcao == "mais" else total < linha

    if categoria in ("chutes_casa", "chutes_fora", "chutes_gol_casa", "chutes_gol_fora",
                     "faltas_casa", "faltas_fora"):
        valor = real.get(categoria)
        if valor is None or linha is None:
            return None
        return valor > linha if direcao == "mais" else valor < linha

    return None


def _montar_real(jogo_row, estat_row):
    """Combina a linha de jogos (placar) com a linha de estatisticas_partidas
    (escanteios/cartões/chutes/faltas) num único dict pra _acertou()."""
    real = {
        "gols_casa": jogo_row.get("gols_casa") if jogo_row else None,
        "gols_fora": jogo_row.get("gols_fora") if jogo_row else None,
    }
    if estat_row:
        for campo in ("escanteios_casa", "escanteios_fora", "cartoes_casa", "cartoes_fora",
                      "chutes_casa", "chutes_fora", "chutes_gol_casa", "chutes_gol_fora",
                      "faltas_casa", "faltas_fora"):
            real[campo] = estat_row.get(campo)
    return real


def _buscar_real_do_jogo(time_casa, time_fora, data_jogo, sufixo_liga):
    """Busca placar (jogos) + estatística real da partida (estatisticas_partidas),
    combinando as duas fontes. Retorna (real_dict, encerrado: bool)."""
    tabela_jogos = f"jogos{sufixo_liga}"
    tabela_estat = f"estatisticas_partidas{sufixo_liga}"

    try:
        jogo_resp = supabase.table(tabela_jogos).select("gols_casa,gols_fora,status") \
            .eq("casa_nome", time_casa).eq("fora_nome", time_fora) \
            .eq("data", data_jogo).execute().data
    except Exception as e:
        print(f"[ERRO] _buscar_real_do_jogo{sufixo_liga} (jogos): {e}")
        jogo_resp = []

    if not jogo_resp or jogo_resp[0].get("status") != "encerrado":
        return None, False

    try:
        estat_resp = supabase.table(tabela_estat).select("*") \
            .eq("casa_nome", time_casa).eq("fora_nome", time_fora) \
            .eq("data", data_jogo).execute().data
    except Exception as e:
        print(f"[ERRO] _buscar_real_do_jogo{sufixo_liga} (estatisticas): {e}")
        estat_resp = []

    real = _montar_real(jogo_resp[0], estat_resp[0] if estat_resp else None)
    return real, True


def calcular_taxas_acerto(min_amostras: int = 15, sufixo_liga: str = "") -> dict:
    """
    Varre todas as previsões salvas (da liga indicada) com mercados_json
    preenchido, cruza com o resultado real do jogo (placar + estatística
    de partida) e devolve, por categoria diagnosticável:

        {"acertos": int, "total": int, "taxa": float, "confiavel": bool}

    'confiavel' só vira True quando total >= min_amostras.
    """
    tabela_previsoes = f"previsoes{sufixo_liga}"
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

        real, encerrado = _buscar_real_do_jogo(p["time_casa"], p["time_fora"], p["data"], sufixo_liga)
        if not encerrado:
            continue

        try:
            mercados = json.loads(p["mercados_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        for cat in CATEGORIAS_DIAGNOSTICAVEIS:
            mercado = mercados.get(cat)
            if not mercado:
                continue
            acerto = _acertou(cat, mercado, real)
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
    considerando só as que já têm amostra suficiente (confiavel=True)."""
    confiaveis = [(cat, d["taxa"]) for cat, d in taxas.items() if d["confiavel"]]
    confiaveis.sort(key=lambda x: x[1], reverse=True)
    return [cat for cat, _ in confiaveis[:n]]


# ==========================================================
# NOVO — avaliação por JOGO INDIVIDUAL (Medidor de Desempenho)
# ==========================================================

def avaliar_mercados_previstos(mercados_json: str, time_casa: str, time_fora: str,
                                 data_jogo: str, sufixo_liga: str = "") -> list:
    """
    Avalia, jogo a jogo, cada mercado que foi previsto (mercados_json)
    contra o resultado real — devolve uma lista pronta pra exibir ✅/❌
    na aba Medidor de Desempenho.

    Cada item: {"categoria", "nome_categoria", "mercado", "confianca",
                "acerto": True|False|None}
    acerto=None significa "ainda não dá pra avaliar" (jogo não encerrado,
    ou essa categoria específica ainda não tem estatística de partida
    coletada — ex: jogos anteriores a 07/08/2026).
    """
    real, encerrado = _buscar_real_do_jogo(time_casa, time_fora, data_jogo, sufixo_liga)
    if not encerrado:
        return []

    try:
        mercados = json.loads(mercados_json) if mercados_json else {}
    except (json.JSONDecodeError, TypeError):
        return []

    avaliacoes = []
    for cat in CATEGORIAS_DIAGNOSTICAVEIS:
        mercado = mercados.get(cat)
        if not mercado:
            continue
        acerto = _acertou(cat, mercado, real)
        avaliacoes.append({
            "categoria": cat,
            "nome_categoria": NOMES_CATEGORIAS.get(cat, cat),
            "mercado": mercado.get("nome", cat),
            "confianca": mercado.get("prob"),
            "acerto": acerto,
        })
    return avaliacoes


def gerar_relatorio_partida(avaliacoes: list, time_casa: str, time_fora: str) -> str:
    """
    Monta um resumo curto em texto a partir da lista de avaliar_mercados_previstos().
    Ex: "Previstos 7 eventos avaliáveis — 5 acertos, 2 erros (71.4%).
    Destaque: Resultado (1X2 / Dupla Chance) ✅. Atenção: Cartões (Over/Under) ❌."
    """
    avaliaveis = [a for a in avaliacoes if a["acerto"] is not None]
    if not avaliaveis:
        return f"{time_casa} x {time_fora}: ainda sem mercados avaliáveis pra esse jogo."

    acertos = [a for a in avaliaveis if a["acerto"]]
    erros = [a for a in avaliaveis if not a["acerto"]]
    taxa = round(100 * len(acertos) / len(avaliaveis), 1)

    partes = [
        f"{time_casa} x {time_fora}: previstos {len(avaliaveis)} evento(s) avaliável(is) — "
        f"{len(acertos)} acerto(s), {len(erros)} erro(s) ({taxa}%)."
    ]

    if acertos:
        destaque = max(acertos, key=lambda a: a["confianca"] or 0)
        partes.append(f"Destaque: {destaque['nome_categoria']} ✅ ({destaque['mercado']}).")
    if erros:
        atencao = max(erros, key=lambda a: a["confianca"] or 0)
        partes.append(f"Atenção: {atencao['nome_categoria']} ❌ ({atencao['mercado']}, "
                       f"o modelo estava {atencao['confianca']}% confiante).")

    nao_avaliados = len(avaliacoes) - len(avaliaveis)
    if nao_avaliados:
        partes.append(f"({nao_avaliados} mercado(s) ainda sem estatística de partida pra avaliar.)")

    return " ".join(partes)
