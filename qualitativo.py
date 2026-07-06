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

def buscar_noticias_recentes(limite: int = 30) -> list:
    try:
        resp = supabase.table("noticias").select("texto") \
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
# FATOR QUALITATIVO COMBINADO -> MotorPoisson
# ==========================================================

def calcular_fator_qualitativo(nome_time: str, dados_time: dict | None, noticias: list) -> float:
    """
    Combina eficiência de conversão + sentimento de notícias num único
    multiplicador para o MotorPoisson (fator_qualitativo_casa/fora).
    1.0 = neutro (comportamento padrão, quando não há dado nenhum).
    Faixa final: 0.80 a 1.20.
    """
    fator = 1.0

    eficiencia = calcular_eficiencia_conversao(dados_time)
    if eficiencia is not None:
        desvio = (eficiencia - EFICIENCIA_MEDIA_LIGA) / EFICIENCIA_MEDIA_LIGA
        fator += max(-0.10, min(0.10, desvio * 0.5))

    fator += calcular_sentimento_time(nome_time, noticias)  # já limitado a ±0.15

    return round(max(0.80, min(1.20, fator)), 4)
