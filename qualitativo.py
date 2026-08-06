# ==========================================================
# Predix Série A — qualitativo.py
# Ajustes qualitativos que alimentam o MotorPoisson via
# fator_qualitativo_casa / fator_qualitativo_fora:
#   1. Eficiência de conversão (gols / finalizações no gol)
#   2. Sentimento de notícias recentes
# ==========================================================

import unicodedata
from database import supabase


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

# Referência de liga: em média, times convertem ~30% das finalizações
# no gol em gols de fato (aprox. 3 gols a cada 10 chutes no alvo).
EFICIENCIA_MEDIA_LIGA = 0.30


def _normalizar(nome: str) -> str:
    nome = unicodedata.normalize("NFD", nome or "")
    nome = "".join(c for c in nome if unicodedata.category(c) != "Mn")
    return nome.lower().strip()


# ==========================================================
# EFICIÊNCIA DE CONVERSÃO
# ==========================================================

def calcular_eficiencia_conversao(dados_time: dict | None) -> float | None:
    """
    Eficiência = gols por jogo / finalizações no gol por jogo (média
    entre casa e fora). Retorna None se o coletor ainda não rodou a
    etapa de estatísticas por partida (colunas fing_casa/fing_fora
    vazias) — assim o chamador sabe que deve tratar como neutro.
    """
    if not dados_time:
        return None

    fing_casa = float(dados_time.get("fing_casa") or 0)
    fing_fora = float(dados_time.get("fing_fora") or 0)
    gm = float(dados_time.get("gm") or 0)
    j  = float(dados_time.get("j") or 0)

    if (fing_casa + fing_fora) <= 0 or j <= 0:
        return None

    gols_por_jogo   = gm / j
    fing_media_jogo = (fing_casa + fing_fora) / 2
    eficiencia = gols_por_jogo / fing_media_jogo

    return round(max(0.10, min(0.60, eficiencia)), 4)


# ==========================================================
# SENTIMENTO DE NOTÍCIAS
# ==========================================================

def buscar_noticias_recentes(limite: int = 30, sufixo_liga: str = "") -> list:
    try:
        resp = supabase.table(f"noticias{sufixo_liga}").select("texto") \
            .order("data_atualizacao", desc=True).limit(limite).execute()
        return [r["texto"] for r in (resp.data or []) if r.get("texto")]
    except Exception as e:
        print(f"[ERRO] buscar_noticias_recentes: {e}")
        return []


def calcular_sentimento_time(nome: str, noticias: list) -> float:
    """Retorna ajuste entre -0.15 e +0.15 baseado nas notícias que citam o time."""
    if not noticias or not nome:
        return 0.0

    nome_norm = _normalizar(nome)
    partes_nome = [p for p in nome_norm.split() if len(p) > 3]

    score = 0
    mencionado = 0
    for noticia in noticias:
        texto = _normalizar(noticia)
        menciona = nome_norm in texto or any(p in texto for p in partes_nome)
        if not menciona:
            continue
        mencionado += 1
        pos = sum(1 for p in PALAVRAS_POSITIVAS if p in texto)
        neg = sum(1 for p in PALAVRAS_NEGATIVAS if p in texto)
        score += (pos - neg)

    if mencionado == 0:
        return 0.0

    return max(-0.15, min(0.15, score * 0.03))


# ==========================================================
# FORMA RECENTE (histórico real de jogos, não estimativa)
# ==========================================================

def calcular_forma_recente(nome_time: str, data_referencia: str | None, sufixo_liga: str = "", n: int = 5) -> str | None:
    """
    Calcula a forma recente de um time com base no aproveitamento de
    pontos nos últimos N jogos ENCERRADOS (histórico real da tabela
    'jogos'). Retorna None se não houver amostra suficiente (< 3 jogos)
    ou se data_referencia não for informada — nesses casos o chamador
    deve tratar como sinal ausente (não contribui pro fator qualitativo),
    igual ao comportamento de calcular_eficiencia_conversao quando falta
    dado.

    NOVO sinal, adicional aos três que já existiam (eficiência de
    conversão, sentimento de notícia, ajuste manual) — não substitui
    nenhum deles.
    """
    if not data_referencia:
        return None

    try:
        resp = (
            supabase.table(f"jogos{sufixo_liga}")
            .select("casa_nome, fora_nome, gols_casa, gols_fora, data")
            .or_(f"casa_nome.eq.{nome_time},fora_nome.eq.{nome_time}")
            .eq("status", "encerrado")
            .lt("data", data_referencia)
            .order("data", desc=True)
            .limit(n)
            .execute()
        )
        jogos = resp.data or []
    except Exception as e:
        print(f"[ERRO] calcular_forma_recente: {e}")
        return None

    jogos_validos = [j for j in jogos if j.get("gols_casa") is not None and j.get("gols_fora") is not None]
    if len(jogos_validos) < 3:
        return None

    pontos = 0
    for j in jogos_validos:
        if j["casa_nome"] == nome_time:
            gm, gc = j["gols_casa"], j["gols_fora"]
        else:
            gm, gc = j["gols_fora"], j["gols_casa"]
        if gm > gc:
            pontos += 3
        elif gm == gc:
            pontos += 1

    aproveitamento = pontos / (len(jogos_validos) * 3)

    if aproveitamento >= 0.80:
        return "otima"
    elif aproveitamento >= 0.55:
        return "boa"
    elif aproveitamento >= 0.35:
        return "neutra"
    elif aproveitamento >= 0.15:
        return "ruim"
    else:
        return "pessima"


# ==========================================================
# AJUSTE QUALITATIVO MANUAL (planilha -> tabela ajustes_qualitativos)
# ==========================================================

def buscar_ajuste_manual(time_casa: str, time_fora: str, data_jogo: str | None = None, sufixo_liga: str = "") -> dict | None:
    """
    Busca o registro de ajuste qualitativo manual (preenchido na planilha
    predix_dados_qualitativos.xlsx e importado via importar_qualitativos.py,
    ou salvo direto pela IA via ia_qualitativa.py) para o confronto exato
    entre time_casa e time_fora.

    Casamento por nome (case-insensitive) + data quando informada. Sem data,
    ou se não achar pela data exata, cai para o registro mais recente
    cadastrado para esse confronto — evita ficar sem ajuste só porque a
    data no Supabase não bateu 100% com a da tabela 'jogos'.
    """
    try:
        base = supabase.table(f"ajustes_qualitativos{sufixo_liga}").select("*") \
            .ilike("time_casa", time_casa).ilike("time_fora", time_fora)

        if data_jogo:
            resp = base.eq("data", data_jogo).order("data", desc=True).limit(1).execute()
            if resp.data:
                return resp.data[0]

        resp = base.order("data", desc=True).limit(1).execute()
        return resp.data[0] if resp.data else None
    except Exception as e:
        print(f"[ERRO] buscar_ajuste_manual: {e}")
        return None


# ==========================================================
# FATOR QUALITATIVO COMBINADO -> MotorPoisson
# ==========================================================

def calcular_fator_qualitativo(
    nome_time: str,
    dados_time: dict | None,
    noticias: list,
    ajuste_manual: float | None = None,
    forma_recente: str | None = None,
) -> float:
    """
    Combina eficiência de conversão + sentimento de notícias + ajuste
    qualitativo manual (planilha) + forma recente real (NOVO — histórico
    de jogos, opcional) num único multiplicador para o MotorPoisson
    (fator_qualitativo_casa/fora). 1.0 = neutro.

    Os quatro componentes SOMAM desvios em torno de 1.0 (não se
    multiplicam entre si) — assim cada sinal complementa os outros em
    vez de compor exponencialmente. forma_recente é o único argumento
    novo e tem peso propositalmente menor que ajuste_manual (que reflete
    julgamento humano específico do confronto): serve pra reforçar ou
    contrariar levemente os outros sinais, não pra dominar sozinho.
    Faixa final: 0.75 a 1.25 (mantida igual, mesmo com 4 sinais).
    """
    desvio = 0.0

    eficiencia = calcular_eficiencia_conversao(dados_time)
    if eficiencia is not None:
        desvio_eficiencia = (eficiencia - EFICIENCIA_MEDIA_LIGA) / EFICIENCIA_MEDIA_LIGA
        desvio += max(-0.10, min(0.10, desvio_eficiencia * 0.5))

    desvio += calcular_sentimento_time(nome_time, noticias)  # já limitado a ±0.15

    if ajuste_manual is not None:
        desvio += (ajuste_manual - 1.0)  # ajuste_manual já vem na escala 0.80–1.20

    ajuste_forma = {
        "otima": 0.06,
        "boa": 0.03,
        "neutra": 0.0,
        "ruim": -0.04,
        "pessima": -0.07,
    }
    if forma_recente is not None:
        desvio += ajuste_forma.get(forma_recente, 0.0)

    fator = 1.0 + desvio
    return round(max(0.75, min(1.25, fator)), 4)
