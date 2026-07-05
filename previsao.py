# ==========================================================
# Predictor Pro 3.0
# previsao.py — Motor baseado na tabela real + peso de notícias
# ==========================================================

import re
from raspador import supabase


# ==========================================================
# PALAVRAS-CHAVE PARA ANÁLISE DE NOTÍCIAS
# ==========================================================

PALAVRAS_POSITIVAS = [
    "vence", "venceu", "ganha", "ganhou", "vitória", "vitoria",
    "invicto", "boa fase", "bom momento", "recuperação", "recuperacao",
    "retorna", "reforço", "reforco", "artilheiro", "destaque",
    "100%", "lider", "líder", "confiante", "motivado", "motivação",
    "sequência positiva", "sequencia positiva",
]

PALAVRAS_NEGATIVAS = [
    "derrota", "perdeu", "perde", "lesão", "lesao", "contundido",
    "suspenso", "suspensão", "crise", "má fase", "ma fase",
    "eliminado", "rebaixamento", "rebaixado", "desfalque",
    "expulso", "punido", "punição", "desfalcado", "baixa",
    "sem vencer", "jejum", "instabilidade", "cansaço",
]


# ==========================================================
# BUSCAR TABELA DO SUPABASE
# ==========================================================

def buscar_tabela() -> dict:
    """
    Retorna um dicionário {nome_time: dados_tabela}
    com dados reais da tabela Série A no Supabase.
    """
    try:
        resp = supabase.table("tabela_serie_a").select("*").execute()
        tabela = {}
        for row in (resp.data or []):
            nome = (row.get("time") or "").strip()
            if nome:
                tabela[nome] = row
        return tabela
    except Exception as e:
        print(f"[ERRO] buscar_tabela: {e}")
        return {}


def _normalizar(nome: str) -> str:
    """Remove acentos e converte para minúsculo para comparação."""
    import unicodedata
    nome = unicodedata.normalize("NFD", nome)
    nome = "".join(c for c in nome if unicodedata.category(c) != "Mn")
    return nome.lower().strip()


def encontrar_time(nome: str, tabela: dict) -> dict | None:
    """
    Localiza o time na tabela ignorando maiúsculas/acentos.
    """
    alvo = _normalizar(nome)
    for chave, dados in tabela.items():
        if _normalizar(chave) == alvo:
            return dados
        sigla = _normalizar(dados.get("sigla", ""))
        if sigla and sigla == alvo:
            return dados
        # correspondência parcial (ex: "América" → "America-MG")
        if alvo in _normalizar(chave) or _normalizar(chave) in alvo:
            return dados
    return None


# ==========================================================
# ESTATÍSTICAS DE FINALIZAÇÕES (para eficiência de conversão)
# ==========================================================

def buscar_estatisticas_times() -> dict:
    """
    Retorna {nome_time: dados} com as médias de finalizações,
    finalizações no gol, escanteios e cartões salvas pelo raspador.
    """
    try:
        resp = supabase.table("estatisticas_times").select("*").execute()
        stats = {}
        for row in (resp.data or []):
            nome = (row.get("time") or "").strip()
            if nome:
                stats[nome] = row
        return stats
    except Exception as e:
        print(f"[ERRO] buscar_estatisticas_times: {e}")
        return {}


# ==========================================================
# CALCULAR FORÇA A PARTIR DA TABELA REAL
# ==========================================================

def calcular_forca_tabela(dados: dict, eficiencia: float = None) -> float:
    """
    Calcula índice de força com base nos dados reais da tabela:
      - Aproveitamento (pontos / jogos máximos)
      - Saldo de gols normalizado
      - Gols pró por jogo
      - Eficiência de conversão (gols / finalizações no gol)
    Retorna valor entre 0.0 e 1.0 aproximadamente.

    A eficiência entrou depois de observarmos, em partidas reais, times
    dominantes em posse/finalizações perderem para adversários mais
    eficientes com menos chances. Sem esse fator, o modelo superestimava
    times "estatisticamente bonitos" mas pouco decisivos.
    """
    jogos     = int(dados.get("jogos") or 1)
    pontos    = float(dados.get("pontos") or 0)
    vitorias  = float(dados.get("vitorias") or 0)
    gols_pro  = float(dados.get("gols_pro") or 0)
    gols_con  = float(dados.get("gols_contra") or 0)
    saldo     = float(dados.get("saldo") or (gols_pro - gols_con))

    aproveitamento = pontos / (jogos * 3) if jogos > 0 else 0
    gols_por_jogo  = gols_pro / jogos if jogos > 0 else 0
    saldo_norm     = max(-1.0, min(1.0, saldo / max(jogos, 1) / 3))

    if eficiencia is None:
        eficiencia = 0.30  # média-padrão quando não há dado de finalizações

    forca = (
        aproveitamento * 0.45 +   # peso maior: pontuação real
        gols_por_jogo  * 0.25 +   # ataque (volume)
        saldo_norm     * 0.15 +   # saldo de gols
        eficiencia     * 0.15     # eficiência de conversão (qualidade do ataque)
    )

    return round(max(0.0, min(1.5, forca)), 4)


def calcular_eficiencia_conversao(stats: dict | None) -> float:
    """
    Eficiência de conversão = gols marcados / finalizações no gol (fing).
    Usa a média combinada casa+fora salva pelo raspador em estatisticas_times.
    Retorna um valor tipicamente entre 0.10 e 0.60 (ligas de futebol costumam
    girar em torno de 0.30 — 3 gols a cada ~10 finalizações no alvo).

    Se não houver dados de finalizações, retorna None para o chamador usar
    o valor-padrão (evita distorcer o modelo com dado ausente).
    """
    if not stats:
        return None

    fin_casa  = float(stats.get("fin_casa")  or 0)
    fin_fora  = float(stats.get("fin_fora")  or 0)
    fing_casa = float(stats.get("fing_casa") or 0)
    fing_fora = float(stats.get("fing_fora") or 0)
    g_casa    = float(stats.get("g_casa")    or 0)
    g_fora    = float(stats.get("g_fora")    or 0)

    fing_total = fing_casa + fing_fora
    gols_total = g_casa + g_fora

    if fing_total <= 0:
        return None

    eficiencia = gols_total / fing_total
    return round(max(0.10, min(0.60, eficiencia)), 4)


# ==========================================================
# ANÁLISE QUALITATIVA DE NOTÍCIAS
# ==========================================================

def buscar_noticias_recentes(limite: int = 30) -> list:
    """Busca as últimas N notícias do Supabase."""
    try:
        resp = supabase.table("noticias_bastidores") \
            .select("texto") \
            .order("data_atualizacao", desc=True) \
            .limit(limite) \
            .execute()
        return [r["texto"] for r in (resp.data or []) if r.get("texto")]
    except Exception as e:
        print(f"[ERRO] buscar_noticias: {e}")
        return []


def calcular_sentimento_time(nome: str, noticias: list) -> float:
    """
    Varre as notícias e calcula um score de sentimento para o time.
    Retorna valor entre -0.15 e +0.15 (ajuste qualitativo).
    Positivo = boas notícias, Negativo = más notícias.
    """
    if not noticias or not nome:
        return 0.0

    nome_norm   = _normalizar(nome)
    # variações comuns do nome
    partes_nome = [p for p in nome_norm.split() if len(p) > 3]

    score = 0
    mencionado = 0

    for noticia in noticias:
        texto = _normalizar(noticia)

        # Verifica se a notícia menciona o time
        menciona = nome_norm in texto or any(p in texto for p in partes_nome)
        if not menciona:
            continue

        mencionado += 1

        pos = sum(1 for p in PALAVRAS_POSITIVAS if p in texto)
        neg = sum(1 for p in PALAVRAS_NEGATIVAS if p in texto)
        score += (pos - neg)

    if mencionado == 0:
        return 0.0

    # Normaliza: cada notícia vale no máximo ±0.05
    ajuste = max(-0.15, min(0.15, score * 0.03))
    print(f"[NOTICIAS] {nome}: {mencionado} menções, score={score}, ajuste={ajuste:+.3f}")
    return ajuste


# ==========================================================
# CONVERTER FORÇA → PROBABILIDADES 1X2
# ==========================================================

def forcas_para_probabilidades(forca_casa: float, forca_fora: float) -> tuple:
    """
    Converte os índices de força em probabilidades 1X2 (casa, empate, fora).

    FATOR CASA — recalibrado em jul/2026. O valor antigo (0.08) subestimava
    o mandante em 17-25 pontos percentuais nas partidas reais analisadas.
    Referência de calibração: no Brasileirão 2026, mandantes venceram ~50%
    dos jogos contra ~24-29% dos visitantes (dado real da temporada).
    Esse novo valor aproxima o modelo desse padrão para confrontos
    equilibrados; ainda assim, recomenda-se validar com a tabela de
    previsões x resultados reais (`previsoes`) e reajustar se necessário —
    fator casa varia por competição e não é uma constante universal.
    """
    FATOR_CASA = 0.25  # vantagem do mandante (recalibrado — era 0.08)

    # Diferença de qualidade calculada ANTES do boost de mando: o empate deve
    # refletir o equilíbrio real entre os times, não ser "engolido" pelo
    # próprio fator casa (esse acoplamento também distorcia o modelo antigo).
    diff_qualidade = abs(forca_casa - forca_fora)
    p_empate = max(0.15, 0.30 - diff_qualidade * 0.8)
    restante = 1.0 - p_empate

    forca_casa_adj = forca_casa + FATOR_CASA
    total = forca_casa_adj + forca_fora + 0.0001  # evita divisão por zero

    bruto_casa = forca_casa_adj / total
    bruto_fora = forca_fora / total

    p_casa = bruto_casa / (bruto_casa + bruto_fora) * restante
    p_fora = bruto_fora / (bruto_casa + bruto_fora) * restante

    # Garante soma = 100%
    p_casa   = round(p_casa   * 100, 1)
    p_empate = round(p_empate * 100, 1)
    p_fora   = round(p_fora   * 100, 1)

    # Corrige arredondamento residual
    diff_r = round(100.0 - p_casa - p_empate - p_fora, 1)
    p_casa += diff_r

    return p_casa, p_empate, p_fora


# ==========================================================
# PREVISÃO PRINCIPAL
# ==========================================================

def prever_jogo(time_casa: str, time_fora: str) -> dict:
    """
    Previsão completa usando:
      1. Dados reais da tabela Série A (45% aproveitamento + 25% ataque
         + 15% saldo + 15% eficiência de conversão)
      2. Análise qualitativa das últimas notícias (ajuste ±0.15)
      3. Fator casa recalibrado (+0.25, ver forcas_para_probabilidades)
    """
    tabela  = buscar_tabela()
    stats   = buscar_estatisticas_times()

    dados_casa = encontrar_time(time_casa, tabela)
    dados_fora = encontrar_time(time_fora, tabela)
    stats_casa = encontrar_time(time_casa, stats)
    stats_fora = encontrar_time(time_fora, stats)

    eficiencia_casa = calcular_eficiencia_conversao(stats_casa)
    eficiencia_fora = calcular_eficiencia_conversao(stats_fora)

    # Força base pela tabela
    if dados_casa:
        forca_casa_base = calcular_forca_tabela(dados_casa, eficiencia_casa)
        pos_casa = dados_casa.get("posicao", "?")
    else:
        print(f"[AVISO] {time_casa} não encontrado na tabela, usando força padrão.")
        forca_casa_base = 0.33
        pos_casa = "?"

    if dados_fora:
        forca_fora_base = calcular_forca_tabela(dados_fora, eficiencia_fora)
        pos_fora = dados_fora.get("posicao", "?")
    else:
        print(f"[AVISO] {time_fora} não encontrado na tabela, usando força padrão.")
        forca_fora_base = 0.33
        pos_fora = "?"

    # Ajuste pelas notícias
    noticias = buscar_noticias_recentes(50)
    ajuste_casa = calcular_sentimento_time(time_casa, noticias)
    ajuste_fora = calcular_sentimento_time(time_fora, noticias)

    forca_casa = round(max(0.05, forca_casa_base + ajuste_casa), 4)
    forca_fora = round(max(0.05, forca_fora_base + ajuste_fora), 4)

    # Probabilidades
    p_casa, p_empate, p_fora = forcas_para_probabilidades(forca_casa, forca_fora)

    # Determina resultado mais provável
    if p_casa >= p_fora and p_casa >= p_empate:
        resultado = time_casa
        confianca = p_casa
    elif p_fora >= p_casa and p_fora >= p_empate:
        resultado = time_fora
        confianca = p_fora
    else:
        resultado = "Empate"
        confianca = p_empate

    return {
        "vencedor_provavel": resultado,
        "confianca": confianca,
        "probabilidades": {
            "casa":   p_casa,
            "empate": p_empate,
            "fora":   p_fora,
        },
        "forca": {
            "casa_base":    forca_casa_base,
            "fora_base":    forca_fora_base,
            "ajuste_casa":  ajuste_casa,
            "ajuste_fora":  ajuste_fora,
            "casa_final":   forca_casa,
            "fora_final":   forca_fora,
            "eficiencia_casa": eficiencia_casa if eficiencia_casa is not None else "sem dado",
            "eficiencia_fora": eficiencia_fora if eficiencia_fora is not None else "sem dado",
        },
        "tabela": {
            "posicao_casa": pos_casa,
            "posicao_fora": pos_fora,
        },
        "noticias_analisadas": len(noticias),
    }


# ==========================================================
# TESTE LOCAL
# ==========================================================

if __name__ == "__main__":
    import json
    resultado = prever_jogo("Flamengo", "Palmeiras")
    print(json.dumps(resultado, indent=2, ensure_ascii=False))