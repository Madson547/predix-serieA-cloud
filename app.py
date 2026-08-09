import streamlit as st
import pandas as pd
import json
from datetime import date
from database import supabase
from poisson import MotorPoisson
from qualitativo import calcular_fator_qualitativo, buscar_noticias_recentes, buscar_ajuste_manual, calcular_forma_recente
from ia_qualitativa import analisar_texto_qualitativo, salvar_ajuste_manual
from previsoes import salvar_previsao, buscar_previsoes, buscar_previsoes_do_dia
from diagnostico import calcular_taxas_acerto, melhores_categorias, NOMES_CATEGORIAS, avaliar_mercados_previstos, gerar_relatorio_partida
from banca import salvar_aposta, buscar_apostas, atualizar_resultado_aposta, excluir_aposta, calcular_roi, buscar_evolucao_lucro, avaliar_aposta_automaticamente
from ia_apostas import analisar_print_aposta

st.set_page_config(layout="wide", page_title="Predix Sports", page_icon="🇧🇷")

# ==========================================================
# SELETOR DE LIGA — Série A e Série B rodando no mesmo app,
# mesmo repositório, mesmo Supabase. Cada liga tem seu próprio
# conjunto de tabelas (jogos/jogos_b, times/times_b, previsoes/
# previsoes_b, ajustes_qualitativos/ajustes_qualitativos_b,
# noticias/noticias_b) — sufixo_liga escolhe qual conjunto usar
# em toda chamada ao Supabase daqui pra baixo.
# ==========================================================
LIGAS = {
    "🇧🇷 Série A": {
        "sufixo": "",
        "nome_exibicao": "Série A",
        "campeonato_id": 3,
        "legenda_tabela": "🟩 Classificação para Libertadores (1º–4º) · 🟥 Zona de rebaixamento à Série B (17º–20º)",
    },
    "🇧🇷 Série B": {
        "sufixo": "_b",
        "nome_exibicao": "Série B",
        "campeonato_id": 4,
        "legenda_tabela": "🟩 Acesso à Série A (1º–4º) · 🟥 Zona de rebaixamento à Série C (17º–20º)",
    },
}

liga_selecionada = st.radio(
    "Liga:", list(LIGAS.keys()), horizontal=True, label_visibility="collapsed"
)
liga = LIGAS[liga_selecionada]
SUFIXO = liga["sufixo"]

motor = MotorPoisson()
MEDALHAS = ["🥇", "🥈", "🥉"]


@st.cache_data(ttl=300)
def carregar_partidas(sufixo_liga):
    resp = (
        supabase.table(f"jogos{sufixo_liga}").select("*")
        .neq("status", "encerrado")
        .order("data").execute()
    )
    return resp.data

@st.cache_data(ttl=300)
def carregar_tabela(sufixo_liga):
    resp = supabase.table(f"times{sufixo_liga}").select("*").order("pts", desc=True).execute()
    return resp.data


def buscar_time(nome, sufixo_liga):
    resp = supabase.table(f"times{sufixo_liga}").select("*").eq("nome", nome).execute().data
    return resp[0] if resp else None


def construir_top_apostas(resultado, casa, fora, top_n=3):
    candidatos = [
        (f"Vitória {casa}", resultado.prob_casa, resultado.odd_casa),
        ("Empate", resultado.prob_empate, resultado.odd_empate),
        (f"Vitória {fora}", resultado.prob_fora, resultado.odd_fora),
        (f"Dupla Chance: {casa} ou Empate", resultado.prob_dupla_1x, None),
        (f"Dupla Chance: {fora} ou Empate", resultado.prob_dupla_x2, None),
        ("Ambas Marcam (BTTS)", resultado.prob_btts, resultado.odd_btts),
        (f"{casa} Marca", resultado.prob_casa_marca, None),
        (f"{fora} Marca", resultado.prob_fora_marca, None),
        ("Mais de 1.5 Gols", resultado.over15_ft, None),
        ("Mais de 2.5 Gols", resultado.over25_ft, resultado.odd_over25),
    ]
    ordenados = sorted(candidatos, key=lambda x: x[1], reverse=True)[:top_n]
    return [
        {"posicao": i + 1, "mercado": m, "confianca": c, "odd_justa": o}
        for i, (m, c, o) in enumerate(ordenados)
    ]


def _valor_ou_padrao(dados_time, chave, padrao):
    """dados_time.get(chave) pode existir mas vir None (time ainda sem
    partidas com estatística coletada) — nesses casos cai pro padrão do
    MotorPoisson em vez de quebrar ou virar 0."""
    valor = dados_time.get(chave)
    return valor if valor is not None else padrao


def analisar_confronto(casa, fora, data_jogo=None, sufixo_liga=""):
    tc = buscar_time(casa, sufixo_liga)
    tf = buscar_time(fora, sufixo_liga)
    if not tc or not tf:
        return None

    noticias = buscar_noticias_recentes(50, sufixo_liga)

    ajuste = buscar_ajuste_manual(casa, fora, data_jogo, sufixo_liga)
    ajuste_manual_casa = ajuste.get("ajuste_manual_casa") if ajuste else None
    ajuste_manual_fora = ajuste.get("ajuste_manual_fora") if ajuste else None

    forma_casa = calcular_forma_recente(casa, data_jogo, sufixo_liga)
    forma_fora = calcular_forma_recente(fora, data_jogo, sufixo_liga)

    fq_casa = calcular_fator_qualitativo(casa, tc, noticias, ajuste_manual_casa, forma_casa)
    fq_fora = calcular_fator_qualitativo(fora, tf, noticias, ajuste_manual_fora, forma_fora)

    resultado = motor.calcular(
        time_casa=casa, time_fora=fora,
        # Gols temporada inteira (gm/gc/j) — fórmula validada como melhor
        # no diagnostico_calibracao_v2.py (log-loss/Brier menores que a
        # versão com splits casa/fora testada em 2026-07).
        gm_casa=tc["gm"], gc_casa=tc["gc"], j_casa=tc["j"],
        gm_fora=tf["gm"], gc_fora=tf["gc"], j_fora=tf["j"],
        fator_qualitativo_casa=fq_casa,
        fator_qualitativo_fora=fq_fora,
        # Escanteios/cartões reais por time (coletor.py). Fallback pros
        # defaults do MotorPoisson (5.2/4.8/2.2/2.3) se o time ainda não
        # tiver estatística coletada (colunas NULL no Supabase).
        cantos_casa=_valor_ou_padrao(tc, "esc_casa", 5.2),
        cantos_fora=_valor_ou_padrao(tf, "esc_fora", 4.8),
        cartoes_casa=_valor_ou_padrao(tc, "cart_casa", 2.2),
        cartoes_fora=_valor_ou_padrao(tf, "cart_fora", 2.3),
        # Chutes/chutes no gol reais por time (coletor.py já coletava,
        # só não era exposto como mercado até agora).
        chutes_casa=_valor_ou_padrao(tc, "fin_casa", 11.0),
        chutes_fora=_valor_ou_padrao(tf, "fin_fora", 9.5),
        chutes_gol_casa=_valor_ou_padrao(tc, "fing_casa", 4.0),
        chutes_gol_fora=_valor_ou_padrao(tf, "fing_fora", 3.3),
        # Faltas reais por time (coletor.py) — fallback pros defaults do
        # MotorPoisson se o time ainda não tiver dado coletado.
        faltas_casa=_valor_ou_padrao(tc, "falta_casa", 10.0),
        faltas_fora=_valor_ou_padrao(tf, "falta_fora", 11.0),
    )
    # Anexado à resposta só pra UI conseguir avisar quando um ajuste manual
    # (planilha) entrou no cálculo, sem precisar buscar de novo.
    resultado.ajuste_manual_aplicado = ajuste is not None
    return resultado


def montar_mercados(resultado, casa, fora):
    """
    Um mercado por categoria (evita combinar mercados redundantes ou
    contraditórios, tipo 'Mais de 1.5' com 'Mais de 2.5' na mesma múltipla).

    Cada mercado carrega, além de nome/prob (usados na UI e nas múltiplas):
    - tipo: chave da categoria (igual à chave do dict, redundante de propósito
      pra sobreviver ao dict virar JSON solto)
    - linha: valor numérico da linha (1.5, 2.5, linha de escanteios...) ou
      None quando não se aplica (resultado, btts, time marca)
    - direcao: lado escolhido, usado pelo diagnostico.py pra comparar com o
      resultado real do jogo (gols_casa/gols_fora) sem precisar re-parsear
      o texto do "nome"

    direcao possíveis por tipo:
      resultado    -> vitoria_casa | empate | vitoria_fora | dupla_1x | dupla_x2
      gols         -> mais | menos      (junto com linha 1.5 ou 2.5)
      btts         -> sim | nao
      casa_marca   -> sim | nao
      fora_marca   -> sim | nao
      escanteios/cartoes/chutes_*/faltas_* -> mais | menos (linha correspondente)
    """
    mercados = {}

    candidatos_resultado = [
        (f"Vitória {casa}", resultado.prob_casa, "vitoria_casa"),
        ("Empate", resultado.prob_empate, "empate"),
        (f"Vitória {fora}", resultado.prob_fora, "vitoria_fora"),
        (f"Dupla Chance: {casa} ou Empate", resultado.prob_dupla_1x, "dupla_1x"),
        (f"Dupla Chance: {fora} ou Empate", resultado.prob_dupla_x2, "dupla_x2"),
    ]
    nome, prob, direcao = max(candidatos_resultado, key=lambda x: x[1])
    mercados["resultado"] = {"tipo": "resultado", "nome": nome, "prob": prob, "linha": None, "direcao": direcao}

    candidatos_gols = [
        ("Mais de 1.5 Gols", resultado.over15_ft, 1.5, "mais"),
        ("Menos de 1.5 Gols", 100 - resultado.over15_ft, 1.5, "menos"),
        ("Mais de 2.5 Gols", resultado.over25_ft, 2.5, "mais"),
        ("Menos de 2.5 Gols", 100 - resultado.over25_ft, 2.5, "menos"),
    ]
    nome, prob, linha, direcao = max(candidatos_gols, key=lambda x: x[1])
    mercados["gols"] = {"tipo": "gols", "nome": nome, "prob": prob, "linha": linha, "direcao": direcao}

    if resultado.prob_btts >= 50:
        mercados["btts"] = {"tipo": "btts", "nome": "Ambas Marcam (Sim)", "prob": resultado.prob_btts, "linha": None, "direcao": "sim"}
    else:
        mercados["btts"] = {"tipo": "btts", "nome": "Ambas Marcam (Não)", "prob": 100 - resultado.prob_btts, "linha": None, "direcao": "nao"}

    if resultado.prob_casa_marca >= 50:
        mercados["casa_marca"] = {"tipo": "casa_marca", "nome": f"{casa} Marca", "prob": resultado.prob_casa_marca, "linha": None, "direcao": "sim"}
    else:
        mercados["casa_marca"] = {"tipo": "casa_marca", "nome": f"{casa} Não Marca", "prob": 100 - resultado.prob_casa_marca, "linha": None, "direcao": "nao"}

    if resultado.prob_fora_marca >= 50:
        mercados["fora_marca"] = {"tipo": "fora_marca", "nome": f"{fora} Marca", "prob": resultado.prob_fora_marca, "linha": None, "direcao": "sim"}
    else:
        mercados["fora_marca"] = {"tipo": "fora_marca", "nome": f"{fora} Não Marca", "prob": 100 - resultado.prob_fora_marca, "linha": None, "direcao": "nao"}

    # A partir daqui: categorias ainda NÃO diagnosticáveis historicamente
    # (coletor.py só salva a média móvel do time, não o valor real da
    # partida) — mas já ficam estruturadas com tipo/linha/direcao pra não
    # precisar mexer de novo quando o coletor passar a salvar o valor real.
    if resultado.prob_over_cantos >= 50:
        mercados["escanteios"] = {"tipo": "escanteios", "nome": f"Mais de {resultado.linha_cantos} Escanteios", "prob": resultado.prob_over_cantos, "linha": resultado.linha_cantos, "direcao": "mais"}
    else:
        mercados["escanteios"] = {"tipo": "escanteios", "nome": f"Menos de {resultado.linha_cantos} Escanteios", "prob": 100 - resultado.prob_over_cantos, "linha": resultado.linha_cantos, "direcao": "menos"}

    if resultado.prob_over_cartoes >= 50:
        mercados["cartoes"] = {"tipo": "cartoes", "nome": f"Mais de {resultado.linha_cartoes} Cartões", "prob": resultado.prob_over_cartoes, "linha": resultado.linha_cartoes, "direcao": "mais"}
    else:
        mercados["cartoes"] = {"tipo": "cartoes", "nome": f"Menos de {resultado.linha_cartoes} Cartões", "prob": 100 - resultado.prob_over_cartoes, "linha": resultado.linha_cartoes, "direcao": "menos"}

    # Chutes/chutes no gol são POR TIME, então cada um vira sua própria
    # categoria — dá pra combinar "chutes do mandante" com "chutes no gol
    # do visitante" na mesma múltipla sem repetir a mesma perna duas vezes.
    if resultado.prob_over_chutes_casa >= 50:
        mercados["chutes_casa"] = {"tipo": "chutes_casa", "nome": f"Mais de {resultado.linha_chutes_casa} Chutes ({casa})", "prob": resultado.prob_over_chutes_casa, "linha": resultado.linha_chutes_casa, "direcao": "mais"}
    else:
        mercados["chutes_casa"] = {"tipo": "chutes_casa", "nome": f"Menos de {resultado.linha_chutes_casa} Chutes ({casa})", "prob": 100 - resultado.prob_over_chutes_casa, "linha": resultado.linha_chutes_casa, "direcao": "menos"}

    if resultado.prob_over_chutes_fora >= 50:
        mercados["chutes_fora"] = {"tipo": "chutes_fora", "nome": f"Mais de {resultado.linha_chutes_fora} Chutes ({fora})", "prob": resultado.prob_over_chutes_fora, "linha": resultado.linha_chutes_fora, "direcao": "mais"}
    else:
        mercados["chutes_fora"] = {"tipo": "chutes_fora", "nome": f"Menos de {resultado.linha_chutes_fora} Chutes ({fora})", "prob": 100 - resultado.prob_over_chutes_fora, "linha": resultado.linha_chutes_fora, "direcao": "menos"}

    if resultado.prob_over_chutes_gol_casa >= 50:
        mercados["chutes_gol_casa"] = {"tipo": "chutes_gol_casa", "nome": f"Mais de {resultado.linha_chutes_gol_casa} Chutes no Gol ({casa})", "prob": resultado.prob_over_chutes_gol_casa, "linha": resultado.linha_chutes_gol_casa, "direcao": "mais"}
    else:
        mercados["chutes_gol_casa"] = {"tipo": "chutes_gol_casa", "nome": f"Menos de {resultado.linha_chutes_gol_casa} Chutes no Gol ({casa})", "prob": 100 - resultado.prob_over_chutes_gol_casa, "linha": resultado.linha_chutes_gol_casa, "direcao": "menos"}

    if resultado.prob_over_chutes_gol_fora >= 50:
        mercados["chutes_gol_fora"] = {"tipo": "chutes_gol_fora", "nome": f"Mais de {resultado.linha_chutes_gol_fora} Chutes no Gol ({fora})", "prob": resultado.prob_over_chutes_gol_fora, "linha": resultado.linha_chutes_gol_fora, "direcao": "mais"}
    else:
        mercados["chutes_gol_fora"] = {"tipo": "chutes_gol_fora", "nome": f"Menos de {resultado.linha_chutes_gol_fora} Chutes no Gol ({fora})", "prob": 100 - resultado.prob_over_chutes_gol_fora, "linha": resultado.linha_chutes_gol_fora, "direcao": "menos"}

    # Faltas — por time, mesmo padrão de chutes/escanteios.
    if resultado.prob_over_faltas_casa >= 50:
        mercados["faltas_casa"] = {"tipo": "faltas_casa", "nome": f"Mais de {resultado.linha_faltas_casa} Faltas ({casa})", "prob": resultado.prob_over_faltas_casa, "linha": resultado.linha_faltas_casa, "direcao": "mais"}
    else:
        mercados["faltas_casa"] = {"tipo": "faltas_casa", "nome": f"Menos de {resultado.linha_faltas_casa} Faltas ({casa})", "prob": 100 - resultado.prob_over_faltas_casa, "linha": resultado.linha_faltas_casa, "direcao": "menos"}

    if resultado.prob_over_faltas_fora >= 50:
        mercados["faltas_fora"] = {"tipo": "faltas_fora", "nome": f"Mais de {resultado.linha_faltas_fora} Faltas ({fora})", "prob": resultado.prob_over_faltas_fora, "linha": resultado.linha_faltas_fora, "direcao": "mais"}
    else:
        mercados["faltas_fora"] = {"tipo": "faltas_fora", "nome": f"Menos de {resultado.linha_faltas_fora} Faltas ({fora})", "prob": 100 - resultado.prob_over_faltas_fora, "linha": resultado.linha_faltas_fora, "direcao": "menos"}

    return mercados


FALLBACK_CLASSIFICACAO_ESTATICA_SERIE_A = {
    # Baseado na observação manual dos primeiros jogos desta conversa
    # (ex: caso Atlético-MG x Bahia em 21/07/2026) — só vale pra Série A,
    # não deve contaminar a Série B, que tem padrão de jogo próprio.
    "resultado": "ALTA", "gols": "ALTA",
    "escanteios": "MEDIA", "btts": "MEDIA", "chutes_casa": "MEDIA", "chutes_fora": "MEDIA",
    "casa_marca": "MEDIA", "fora_marca": "MEDIA",
    "cartoes": "BAIXA", "chutes_gol_casa": "BAIXA", "chutes_gol_fora": "BAIXA",
    "faltas_casa": "MEDIA", "faltas_fora": "MEDIA",
}

FALLBACK_CLASSIFICACAO_ESTATICA_SERIE_B = {
    # Neutro de propósito — a Série B não herda o viés observado na
    # Série A (não é a mesma liga, nem o mesmo padrão de jogo). Some
    # categorias podem ser boas ou ruins de forma completamente
    # diferente da A; melhor não presumir. Some ~15 jogos avaliados
    # (pouco mais de 1 rodada) já bastam pra virar taxa real.
    cat: "MEDIA" for cat in FALLBACK_CLASSIFICACAO_ESTATICA_SERIE_A
}

_RANK_TIER = {"ALTA": 3, "MEDIA": 2, "BAIXA": 1}


@st.cache_data(ttl=600)
def _taxas_cacheadas(sufixo_liga):
    """calcular_taxas_acerto varre TODAS as previsões salvas — pode ficar
    pesado se rodar em toda interação do usuário. Cacheado por 10 min."""
    return calcular_taxas_acerto(min_amostras=15, sufixo_liga=sufixo_liga)


def classificar_categorias_dinamico(sufixo_liga=""):
    """
    Classifica cada categoria em ALTA/MEDIA/BAIXA com base na taxa de
    acerto REAL medida (calcular_taxas_acerto) quando já houver amostra
    confiável (min. 15 avaliações); category ainda sem amostra suficiente
    cai no fallback estático manual. Retorna (classificacao: dict,
    taxas: dict) — taxas é devolvido junto pra exibir "(78% em 42 jogos)"
    na interface.
    """
    taxas = _taxas_cacheadas(sufixo_liga)
    fallback = FALLBACK_CLASSIFICACAO_ESTATICA_SERIE_B if sufixo_liga == "_b" else FALLBACK_CLASSIFICACAO_ESTATICA_SERIE_A
    classificacao = {}
    for cat, fallback_tier in fallback.items():
        d = taxas.get(cat)
        if d and d.get("confiavel"):
            if d["taxa"] >= 65:
                classificacao[cat] = "ALTA"
            elif d["taxa"] >= 50:
                classificacao[cat] = "MEDIA"
            else:
                classificacao[cat] = "BAIXA"
        else:
            classificacao[cat] = fallback_tier
    return classificacao, taxas


def gerar_multiplas(resultado, casa, fora, sufixo_liga=""):
    """
    Distribui os mercados disponíveis entre 5 múltiplas tentando nunca
    colocar duas categorias de CONFIABILIDADE BAIXA na mesma múltipla —
    evita que 1 jogo ruim de cartões/chutes no gol quebre 2+ pernas de
    uma vez (foi o que aconteceu no Atlético-MG x Bahia em 21/07/2026).

    A classificação ALTA/MÉDIA/BAIXA agora é DINÂMICA: usa a taxa de
    acerto real medida por calcular_taxas_acerto() assim que a categoria
    tiver amostra suficiente (min. 15 avaliações) pra essa liga — antes
    disso, cai no fallback manual (mesma lista que já existia). Ou seja,
    se "cartões" continuar errando muito na Série B, o próprio sistema
    vai reclassificar essa categoria pra BAIXA sozinho, sem precisar
    editar código de novo — e se ela virar confiável, sobe de tier
    automaticamente também.
    """
    mercados = montar_mercados(resultado, casa, fora)
    classificacao, taxas = classificar_categorias_dinamico(sufixo_liga)

    combos = [
        ("Múltipla 1 — Mais Segura", ["resultado", "gols", "escanteios"]),
        ("Múltipla 2 — Foco no Mandante", ["casa_marca", "chutes_casa", "faltas_fora"]),
        ("Múltipla 3 — Foco no Visitante", ["fora_marca", "chutes_fora", "faltas_casa"]),
        ("Múltipla 4 — Ambas Marcam + Cartões", ["btts", "cartoes", "faltas_casa"]),
        ("Múltipla 5 — Mais Arriscada (Mix)", ["casa_marca", "fora_marca", "faltas_fora"]),
    ]

    multiplas = []
    for titulo, categorias in combos:
        categorias_disponiveis = [c for c in categorias if c in mercados]

        # Se 2+ categorias desse combo estão classificadas BAIXA agora
        # (dinamicamente), troca as excedentes por uma alternativa
        # melhor disponível, evitando empilhar risco correlacionado.
        baixas_no_combo = [c for c in categorias_disponiveis if classificacao.get(c) == "BAIXA"]
        if len(baixas_no_combo) >= 2:
            for excedente in baixas_no_combo[1:]:
                candidatos = [
                    c for c in mercados
                    if c not in categorias_disponiveis and classificacao.get(c) != "BAIXA"
                ]
                if not candidatos:
                    continue
                candidatos.sort(
                    key=lambda c: (_RANK_TIER.get(classificacao.get(c), 0), mercados[c]["prob"]),
                    reverse=True,
                )
                substituto = candidatos[0]
                categorias_disponiveis = [substituto if x == excedente else x for x in categorias_disponiveis]

        pernas = []
        for c in categorias_disponiveis:
            perna = dict(mercados[c])
            tier = classificacao.get(c, "?")
            d = taxas.get(c, {})
            perna["classificacao"] = tier
            perna["amostra_info"] = f"{d['taxa']}% em {d['total']} jogos" if d.get("confiavel") else "amostra pequena"
            pernas.append(perna)

        if not pernas:
            continue
        prob_combinada = 1.0
        for p in pernas:
            prob_combinada *= (p["prob"] / 100)
        prob_combinada *= 100
        odd_justa = round(100 / prob_combinada, 2) if prob_combinada > 0 else None
        multiplas.append({
            "titulo": titulo,
            "pernas": pernas,
            "prob_combinada": round(prob_combinada, 1),
            "odd_justa": odd_justa,
        })

    return multiplas


def colorir_tabela(row):
    pos = row["posicao"]
    if pos <= 4:
        return ['background-color: #1b4332; color: white'] * len(row)
    elif pos >= 17:
        return ['background-color: #4a1919; color: white'] * len(row)
    return [''] * len(row)


partidas = carregar_partidas(SUFIXO)
tabela = carregar_tabela(SUFIXO)

lista_confrontos = [f"{j['casa_nome']} x {j['fora_nome']}" for j in partidas] if partidas else []

aba_painel, aba_multiplas, aba_bingo, aba_tabela, aba_performance, aba_banca = st.tabs([
    "📊 Painel Analítico",
    "🎰 Sugestões de Múltiplas",
    "🎯 Bingo",
    "🏆 Tabela de Classificação",
    "📈 Medidor de Desempenho",
    "🏦 Banca"
])

with aba_painel:
    st.markdown(f"## ⚽ Predix Sports — Análise Quantitativa {liga['nome_exibicao']} 2026")

    if not partidas:
        st.warning("⚠️ Nenhum jogo encontrado na janela de datas atual. Verifique se o robô de coleta rodou recentemente.")
        st.caption("As outras abas (Tabela e Medidor de Desempenho) continuam funcionando normalmente.")
    else:
        col1, col2 = st.columns([1.5, 1.5])

        with col1:
            st.markdown("### 🎛️ Selecione o Confronto")
            confronto_sel = st.selectbox("Escolha a Partida:", lista_confrontos, key=f"confronto_sel{SUFIXO}")
            jogo = [j for j in partidas if f"{j['casa_nome']} x {j['fora_nome']}" == confronto_sel][0]

            resultado = analisar_confronto(jogo['casa_nome'], jogo['fora_nome'], jogo.get('data'), SUFIXO)

            st.markdown(f"🗓️ **Data:** {jogo.get('data','')} | 🕒 **Hora:** {jogo.get('hora','')} | **Status:** {jogo.get('status_desc','')}")
            st.markdown("---")

            if resultado:
                if getattr(resultado, "ajuste_manual_aplicado", False):
                    st.caption("📝 Ajuste qualitativo manual (planilha) aplicado neste confronto.")

                top_3 = construir_top_apostas(resultado, jogo['casa_nome'], jogo['fora_nome'])
                st.markdown("### 🔥 TOP 3 MELHORES APOSTAS")
                for item in top_3:
                    medalha = MEDALHAS[item["posicao"] - 1]
                    odd_txt = f" | Odd Justa: {item['odd_justa']}" if item.get("odd_justa") else ""
                    st.success(f"{medalha} **{item['mercado']}** — Confiança: {item['confianca']}%{odd_txt}")

                if st.button("💾 Salvar previsão", key=f"salvar_{jogo['casa_nome']}_{jogo['fora_nome']}_{jogo.get('data','')}"):
                    mercados_pra_salvar = montar_mercados(resultado, jogo['casa_nome'], jogo['fora_nome'])
                    multiplas_pra_salvar = gerar_multiplas(resultado, jogo['casa_nome'], jogo['fora_nome'], sufixo_liga=SUFIXO)
                    if salvar_previsao(resultado, jogo['casa_nome'], jogo['fora_nome'], jogo.get('data'), top_3, multiplas_pra_salvar, mercados_pra_salvar, sufixo_liga=SUFIXO):
                        st.success("Previsão + mercados + múltiplas salvos! Confira depois nas abas 🎯 Bingo e 📈 Medidor de Desempenho.")
                    else:
                        st.error("Não consegui salvar — confira o log do Streamlit Cloud.")

                with st.expander("🤖 Analisar notícia/escalação com IA (opcional)"):
                    st.caption(
                        "Cole abaixo o texto de uma notícia, escalação ou resumo de site "
                        "esportivo sobre este confronto. A IA sugere um ajuste qualitativo — "
                        "confira antes de salvar, o valor só entra pra valer depois que você "
                        "clicar em salvar aqui embaixo."
                    )
                    chave_ia = f"texto_ia_{jogo['casa_nome']}_{jogo['fora_nome']}_{jogo.get('data','')}"
                    texto_ia = st.text_area(
                        "Cole aqui o texto da notícia/escalação",
                        key=chave_ia, height=100,
                    )
                    if st.button("🤖 Analisar com IA", key=f"btn_ia_{chave_ia}"):
                        try:
                            with st.spinner("Analisando com IA..."):
                                sugestao = analisar_texto_qualitativo(
                                    texto_ia, jogo['casa_nome'], jogo['fora_nome']
                                )
                            st.session_state[f"ajuste_casa_{chave_ia}"] = sugestao["ajuste_casa"]
                            st.session_state[f"ajuste_fora_{chave_ia}"] = sugestao["ajuste_fora"]
                            st.session_state[f"obs_{chave_ia}"] = sugestao["resumo_observacoes"]
                            st.success(
                                f"✅ Sugestão: {jogo['casa_nome']} = {sugestao['ajuste_casa']} | "
                                f"{jogo['fora_nome']} = {sugestao['ajuste_fora']} "
                                f"(confiança da IA: {sugestao['confianca']}). "
                                f"Contexto: {sugestao['contexto_especial'] or '—'}"
                            )
                        except Exception as e:
                            st.error(f"Erro ao analisar com IA: {e}")

                    ajuste_casa_ia = st.number_input(
                        f"Ajuste {jogo['casa_nome']}", min_value=0.80, max_value=1.20, step=0.01,
                        key=f"ajuste_casa_{chave_ia}", value=st.session_state.get(f"ajuste_casa_{chave_ia}", 1.0)
                    )
                    ajuste_fora_ia = st.number_input(
                        f"Ajuste {jogo['fora_nome']}", min_value=0.80, max_value=1.20, step=0.01,
                        key=f"ajuste_fora_{chave_ia}", value=st.session_state.get(f"ajuste_fora_{chave_ia}", 1.0)
                    )
                    obs_ia = st.text_input(
                        "Observações", key=f"obs_{chave_ia}",
                        value=st.session_state.get(f"obs_{chave_ia}", "")
                    )
                    if st.button("💾 Salvar ajuste manual (via IA)", key=f"btn_salvar_ia_{chave_ia}"):
                        if salvar_ajuste_manual(
                            jogo['casa_nome'], jogo['fora_nome'], jogo.get('data'),
                            ajuste_casa_ia, ajuste_fora_ia, obs_ia, sufixo_liga=SUFIXO
                        ):
                            st.success(f"✅ Ajuste salvo em ajustes_qualitativos{SUFIXO}! Recarregue a página pra ver refletido na análise.")
                        else:
                            st.error("Não consegui salvar — confira o log do Streamlit Cloud.")

                st.markdown("---")
                st.markdown("### 🎯 Todas as Probabilidades")
                st.info(f"🟦 Vitória {jogo['casa_nome']}: {resultado.prob_casa}%")
                st.info(f"🟨 Empate: {resultado.prob_empate}%")
                st.info(f"🟦 Vitória {jogo['fora_nome']}: {resultado.prob_fora}%")
                st.info(f"🤝 Ambas Marcam: {resultado.prob_btts}%")
                st.success(f"🟩 Gols FT (>1.5): {resultado.over15_ft}%")
                st.success(f"🟩 Gols FT (>2.5): {resultado.over25_ft}%")
                st.warning(f"🟫 Escanteios FT: média {resultado.cantos_ft} | Mais de {resultado.linha_cantos}: {resultado.prob_over_cantos}%")
                st.error(f"🟥 Cartões FT: média {resultado.cartoes_ft} | Mais de {resultado.linha_cartoes}: {resultado.prob_over_cartoes}%")
                st.info(f"🎯 Chutes {jogo['casa_nome']}: média {resultado.chutes_casa} | Mais de {resultado.linha_chutes_casa}: {resultado.prob_over_chutes_casa}%")
                st.info(f"🎯 Chutes {jogo['fora_nome']}: média {resultado.chutes_fora} | Mais de {resultado.linha_chutes_fora}: {resultado.prob_over_chutes_fora}%")
                st.success(f"🥅 Chutes no gol {jogo['casa_nome']}: média {resultado.chutes_gol_casa} | Mais de {resultado.linha_chutes_gol_casa}: {resultado.prob_over_chutes_gol_casa}%")
                st.success(f"🥅 Chutes no gol {jogo['fora_nome']}: média {resultado.chutes_gol_fora} | Mais de {resultado.linha_chutes_gol_fora}: {resultado.prob_over_chutes_gol_fora}%")
                st.warning(f"🟨 Faltas {jogo['casa_nome']}: média {resultado.faltas_casa} | Mais de {resultado.linha_faltas_casa}: {resultado.prob_over_faltas_casa}%")
                st.warning(f"🟨 Faltas {jogo['fora_nome']}: média {resultado.faltas_fora} | Mais de {resultado.linha_faltas_fora}: {resultado.prob_over_faltas_fora}%")
                st.info(f"🏆 Placar mais provável: **{resultado.placar_mais_provavel}**")
            else:
                st.warning("Análise não disponível — time não encontrado no banco.")

        with col2:
            st.markdown("### 📅 Próximos Jogos")
            for i, j in enumerate(partidas):
                status_icon = "🔴" if j.get("status") == "encerrado" else "🟢" if j.get("status") == "ao_vivo" else "⚪"
                st.code(
                    f"⚽ CONFRONTO {i+1} | {j.get('data','')} às {j.get('hora','')}\n"
                    f"{j['casa_nome']} x {j['fora_nome']}\n"
                    f"{status_icon} {j.get('status_desc','Agendado')}"
                )

with aba_bingo:
    st.markdown("## 🎯 Bingo do Dia")
    st.caption(
        "Critério: 2 mercados das categorias com maior taxa de acerto histórica "
        "+ 1 mercado com probabilidade acima de 75% na análise de hoje. "
        "Cobre todos os jogos analisados e salvos no dia."
    )

    hoje = date.today().isoformat()
    previsoes_hoje = buscar_previsoes_do_dia(hoje, sufixo_liga=SUFIXO)

    if not previsoes_hoje:
        st.info("Nenhuma previsão salva ainda hoje. Analise os jogos na aba 📊 Painel Analítico e clique em '💾 Salvar previsão' — o Bingo cobre todos os jogos salvos no dia.")
    else:
        taxas = calcular_taxas_acerto(min_amostras=15, sufixo_liga=SUFIXO)
        top_categorias = melhores_categorias(taxas, n=2)

        with st.expander("📐 Taxas de acerto histórico por categoria (usadas no Bingo)"):
            for cat in ["resultado", "gols", "btts", "casa_marca", "fora_marca"]:
                d = taxas[cat]
                status = "✅ confiável" if d["confiavel"] else f"⏳ coletando (mín. 15 amostras)"
                st.write(f"**{NOMES_CATEGORIAS[cat]}**: {d['acertos']}/{d['total']} — {d['taxa']}% ({status})")
            st.caption(
                "Escanteios, cartões, chutes e faltas ainda não entram nesse diagnóstico: o coletor.py "
                "só salva a média móvel do time, não o valor real de cada partida específica — sem isso "
                "não dá pra comparar a previsão daquele jogo com o que realmente saiu naquele jogo."
            )

        usar_fallback = len(top_categorias) < 2
        if usar_fallback:
            st.warning(
                "Ainda não há dados históricos suficientes (mínimo 15 avaliações por categoria) pra "
                "confiar na taxa de acerto. Por enquanto o Bingo usa só o critério de probabilidade "
                "acima de 75% em até 3 mercados por jogo — sem esconder isso de você."
            )

        st.markdown("---")

        pernas_estruturadas_dia = []

        for p in previsoes_hoje:
            if not p.get("mercados_json"):
                continue
            try:
                mercados_jogo = json.loads(p["mercados_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            titulo_jogo = f"{p['time_casa']} x {p['time_fora']}"

            if not usar_fallback:
                pernas_historicas = [mercados_jogo[c] for c in top_categorias if c in mercados_jogo]
                categorias_usadas = set(top_categorias)
                candidatos_prob = [
                    (chave, m) for chave, m in mercados_jogo.items()
                    if chave not in categorias_usadas and m.get("prob", 0) > 75
                ]
                candidatos_prob.sort(key=lambda x: x[1]["prob"], reverse=True)

                if len(pernas_historicas) < 2 or not candidatos_prob:
                    st.markdown(f"**{titulo_jogo}**")
                    st.caption("Sem card completo hoje (faltou mercado >75% além dos 2 históricos).")
                    st.markdown("---")
                    continue

                _, perna_prob = candidatos_prob[0]
                pernas_bingo = pernas_historicas + [perna_prob]
                legenda_pernas = [f"🏆 {NOMES_CATEGORIAS.get(c, c)}" for c in top_categorias] + ["🎯 Probabilidade >75%"]
            else:
                candidatos_prob = sorted(
                    [(chave, m) for chave, m in mercados_jogo.items() if m.get("prob", 0) > 75],
                    key=lambda x: x[1]["prob"], reverse=True
                )[:3]
                if not candidatos_prob:
                    st.markdown(f"**{titulo_jogo}**")
                    st.caption("Sem card completo hoje (nenhum mercado passou de 75%).")
                    st.markdown("---")
                    continue
                pernas_bingo = [m for _, m in candidatos_prob]
                legenda_pernas = ["🎯 Probabilidade >75%"] * len(pernas_bingo)

            prob_combinada = 1.0
            for perna in pernas_bingo:
                prob_combinada *= (perna["prob"] / 100)
            prob_combinada = round(prob_combinada * 100, 1)

            st.markdown(f"### {titulo_jogo}")
            for legenda, perna in zip(legenda_pernas, pernas_bingo):
                st.success(f"{legenda} — **{perna['nome']}** ({perna['prob']}%)")
                pernas_estruturadas_dia.append({
                    "casa": p["time_casa"], "fora": p["time_fora"], "data": p["data"],
                    "sufixo_liga": SUFIXO, "categoria": perna.get("tipo"),
                    "direcao": perna.get("direcao"), "linha": perna.get("linha"),
                    "nome": perna.get("nome"), "prob": perna.get("prob"),
                })
            st.info(f"📊 Probabilidade combinada do Bingo: **{prob_combinada}%**")
            st.markdown("---")

        if pernas_estruturadas_dia:
            st.markdown("#### 💾 Registrar cartela na Banca")
            st.caption(
                "Junta todas as pernas mostradas acima (de todos os jogos do dia) numa "
                "aposta pendente só, já com os dados estruturados — permite sugestão "
                "automática de resultado depois que os jogos encerrarem."
            )
            if st.button("💾 Registrar cartela do Bingo como aposta pendente", key=f"btn_registrar_bingo{SUFIXO}"):
                texto_mercados = " + ".join(perna["nome"] for perna in pernas_estruturadas_dia)
                jogos_unicos = sorted(set(f"{pp['casa']} x {pp['fora']}" for pp in pernas_estruturadas_dia))
                st.session_state["nova_jogos"] = " + ".join(jogos_unicos)
                st.session_state["nova_mercados"] = texto_mercados
                st.session_state["bingo_pernas_estruturadas"] = pernas_estruturadas_dia
                st.success("Cartela pronta! Vai na aba '🏦 Banca → ➕ Nova Aposta' — os campos já vêm pré-preenchidos.")


with aba_multiplas:
    st.markdown("## 🎰 Sugestões de Múltiplas")

    if not partidas:
        st.info("Sem jogos na janela atual — nada pra gerar múltiplas ainda. Confira a aba Painel Analítico.")
    else:
        st.caption(f"Confronto: **{jogo['casa_nome']}** x **{jogo['fora_nome']}**")
        st.caption(
            "⚠️ A probabilidade combinada assume que os mercados são independentes entre si — "
            "na prática alguns se correlacionam (ex: Mais de 2.5 Gols tende a andar junto com "
            "Ambas Marcam), então trate como referência, não como certeza matemática exata."
        )
        st.markdown("---")

        if resultado:
            multiplas = gerar_multiplas(resultado, jogo['casa_nome'], jogo['fora_nome'], sufixo_liga=SUFIXO)
            cores = ["#1b4332", "#123a4a", "#4a3b1b", "#4a1919"]
            emoji_tier = {"ALTA": "🟢", "MEDIA": "🟡", "BAIXA": "🔴"}
            for i, m in enumerate(multiplas):
                cor = cores[i % len(cores)]
                pernas_html = "".join(
                    f'<div style="padding:3px 0; color:#ffffff;">'
                    f'{emoji_tier.get(p.get("classificacao"), "⚪")} {p["nome"]} '
                    f'<span style="opacity:0.85;">({p["prob"]:.1f}% | {p.get("amostra_info","")})</span></div>'
                    for p in m["pernas"]
                )
                odd_html = f" &nbsp;|&nbsp; Odd Justa: <b>{m['odd_justa']}</b>" if m["odd_justa"] else ""
                st.markdown(f"""
                <div style="background:{cor}; border-radius:10px; padding:16px 20px; margin-bottom:14px; color:#ffffff;">
                  <div style="font-weight:bold; font-size:17px; margin-bottom:10px; color:#ffffff;">{m['titulo']}</div>
                  {pernas_html}
                  <div style="margin-top:12px; font-size:15px; color:#ffffff;">
                    📊 Probabilidade de bater: <b>{m['prob_combinada']}%</b>{odd_html}
                  </div>
                </div>
                """, unsafe_allow_html=True)
            st.caption("🟢 confiabilidade alta · 🟡 média · 🔴 baixa — calculado com base na taxa de acerto real "
                       "quando já há amostra suficiente (mín. 15 jogos avaliados); senão usa a classificação manual inicial.")
        else:
            st.warning("Análise não disponível — time não encontrado no banco.")

with aba_tabela:
    st.subheader(f"🏆 Classificação — Brasileirão {liga['nome_exibicao']} 2026")
    st.caption(liga["legenda_tabela"])
    if tabela:
        df = pd.DataFrame(tabela)
        colunas = ["posicao", "nome", "pts", "j", "v", "e", "d", "gm", "gc", "sg"]
        colunas_exist = [c for c in colunas if c in df.columns]
        df_ordenado = df[colunas_exist].sort_values("pts", ascending=False)
        styled = df_ordenado.style.apply(colorir_tabela, axis=1)
        st.dataframe(styled, width='stretch', height=660, hide_index=True)
    else:
        st.info("Tabela não disponível.")

with aba_performance:
    st.subheader("📈 Medidor de Desempenho")
    st.caption("Busque previsões salvas pra comparar com o resultado real depois do jogo.")

    col_busca, col_filtro = st.columns([2, 1])
    with col_busca:
        termo_busca = st.text_input("Buscar por time (ou deixe vazio pra ver as mais recentes):", "")
    with col_filtro:
        filtro_exibicao = st.radio(
            "Exibir:", ["Só pendentes", "Tudo"], horizontal=True, key="filtro_medidor"
        )
    previsoes_salvas = buscar_previsoes(termo_busca, sufixo_liga=SUFIXO)

    if not previsoes_salvas:
        st.info("Nenhuma previsão salva ainda. Use o botão '💾 Salvar previsão' na aba Painel Analítico.")
    else:
        # Busca o status real de cada previsão ANTES de decidir se exibe —
        # isso é só um filtro visual, nada é apagado do banco. A amostra
        # continua intacta pra calcular_taxas_acerto()/Bingo mesmo pros
        # jogos escondidos daqui.
        pendentes_ocultados = 0
        for p in previsoes_salvas:
            jogo_real = supabase.table(f"jogos{SUFIXO}").select("gols_casa,gols_fora,status") \
                .eq("casa_nome", p['time_casa']).eq("fora_nome", p['time_fora']) \
                .eq("data", p['data']).execute().data
            encerrado = bool(jogo_real and jogo_real[0].get("status") == "encerrado")

            if filtro_exibicao == "Só pendentes" and encerrado:
                pendentes_ocultados += 1
                continue

            with st.expander(f"{p['time_casa']} x {p['time_fora']} — {p.get('data','')}"):
                st.write(f"Vitória {p['time_casa']}: {p['prob_casa']}% | Empate: {p['prob_empate']}% | Vitória {p['time_fora']}: {p['prob_fora']}%")
                st.write(f"Ambas Marcam: {p['prob_btts']}% | Gols >1.5: {p['over15_ft']}% | Gols >2.5: {p['over25_ft']}%")
                st.write(f"Escanteios: média {p['cantos_ft']} | linha {p['linha_cantos']} | over: {p['prob_over_cantos']}%")
                st.write(f"Cartões: média {p['cartoes_ft']} | linha {p['linha_cartoes']} | over: {p['prob_over_cartoes']}%")
                st.write(f"Placar mais provável: {p['placar_mais_provavel']}")

                multiplas_salvas = json.loads(p["multiplas_json"]) if p.get("multiplas_json") else []
                if multiplas_salvas:
                    st.markdown("**Múltiplas geradas nessa previsão:**")
                    for m in multiplas_salvas:
                        pernas_txt = " + ".join(f"{leg['nome']} ({leg['prob']:.1f}%)" for leg in m["pernas"])
                        st.caption(f"{m['titulo']}: {pernas_txt} → combinada {m['prob_combinada']}%")

                jogo_real = supabase.table(f"jogos{SUFIXO}").select("gols_casa,gols_fora,status") \
                    .eq("casa_nome", p['time_casa']).eq("fora_nome", p['time_fora']) \
                    .eq("data", p['data']).execute().data
                if jogo_real and jogo_real[0].get("status") == "encerrado":
                    gc, gf = jogo_real[0]["gols_casa"], jogo_real[0]["gols_fora"]
                    st.success(f"✅ Resultado real: {p['time_casa']} {gc} x {gf} {p['time_fora']}")

                    avaliacoes = avaliar_mercados_previstos(
                        p.get("mercados_json"), p['time_casa'], p['time_fora'], p['data'], sufixo_liga=SUFIXO
                    )
                    if avaliacoes:
                        st.markdown("**Mercados previstos x resultado real:**")
                        for a in avaliacoes:
                            if a["acerto"] is True:
                                st.markdown(f"✅ {a['nome_categoria']}: **{a['mercado']}** (previsto {a['confianca']}%)")
                            elif a["acerto"] is False:
                                st.markdown(f"❌ {a['nome_categoria']}: **{a['mercado']}** (previsto {a['confianca']}%)")
                            else:
                                st.caption(f"⏳ {a['nome_categoria']}: sem estatística de partida coletada ainda")

                        relatorio = gerar_relatorio_partida(avaliacoes, p['time_casa'], p['time_fora'])
                        st.info(f"📋 {relatorio}")
                else:
                    st.caption("⏳ Jogo ainda não encerrado (ou sem placar salvo).")

        if pendentes_ocultados:
            st.caption(f"({pendentes_ocultados} previsão(ões) já encerrada(s) oculta(s) — nada foi apagado, "
                       f"muda pra 'Tudo' acima pra ver. Todas continuam contando pro Bingo e pro ROI.)")

with aba_banca:
    st.subheader("🏦 Controle de Banca")
    st.caption(
        "Registro das apostas reais feitas fora do app (Betano ou outra casa), "
        "cruzado com o que o Predix já previu — pra medir retorno financeiro "
        "de verdade, não só acerto estatístico."
    )

    sub_nova, sub_pendentes, sub_roi = st.tabs(["➕ Nova Aposta", "⏳ Pendentes", "📊 ROI"])

    # ------------------------------------------------------------
    # NOVA APOSTA — print do bilhete (opcional, via IA) + formulário
    # ------------------------------------------------------------
    with sub_nova:
        st.markdown("#### 📸 Opção 1 — Sobe o print do bilhete confirmado")
        st.caption(
            "Print de LOGO DEPOIS de apostar (antes do jogo acontecer). A IA "
            "pré-preenche os campos abaixo — confira e ajuste antes de salvar."
        )
        print_bilhete = st.file_uploader(
            "Print(s) do bilhete", type=["png", "jpg", "jpeg"], key="upload_bilhete",
            accept_multiple_files=True,
        )
        if print_bilhete and st.button("🤖 Analisar print(s) com IA", key="btn_analisar_bilhete"):
            lote = []
            erros = []
            with st.spinner(f"Lendo {len(print_bilhete)} bilhete(s)..."):
                for arquivo in print_bilhete:
                    try:
                        media_type = arquivo.type or "image/png"
                        sugestao_aposta = analisar_print_aposta(arquivo.getvalue(), media_type)
                        sugestao_aposta["_nome_arquivo"] = arquivo.name
                        lote.append(sugestao_aposta)
                    except Exception as e:
                        erros.append(f"{arquivo.name}: {e}")
            st.session_state["bilhetes_lote"] = lote
            if lote:
                st.success(f"✅ {len(lote)} bilhete(s) lido(s). Escolhe abaixo qual carregar no formulário.")
            if erros:
                st.error("Erro ao ler: " + " | ".join(erros))

        lote = st.session_state.get("bilhetes_lote") or []
        if lote:
            opcoes = [f"{i+1}. {b['_nome_arquivo']} — {b['jogos_envolvidos'] or '(sem jogo identificado)'}" for i, b in enumerate(lote)]
            escolha = st.selectbox("Qual bilhete carregar no formulário abaixo?", opcoes, key="sel_bilhete_lote")
            idx_escolhido = opcoes.index(escolha)
            if st.button("⬇️ Carregar este bilhete no formulário", key="btn_carregar_bilhete_lote"):
                sugestao_aposta = lote[idx_escolhido]
                st.session_state["nova_jogos"] = sugestao_aposta["jogos_envolvidos"]
                st.session_state["nova_mercados"] = sugestao_aposta["mercados"]
                st.session_state["nova_categoria"] = sugestao_aposta["categoria_estimada"]
                st.session_state["nova_tipo"] = sugestao_aposta["tipo_aposta"]
                if sugestao_aposta["odd"] is not None:
                    st.session_state["nova_odd"] = sugestao_aposta["odd"]
                if sugestao_aposta["stake"] is not None:
                    st.session_state["nova_stake"] = sugestao_aposta["stake"]
                st.success(f"Carregado (confiança da IA: {sugestao_aposta['confianca']}). Confira os campos abaixo e salva quando estiver certo.")

        st.markdown("#### ✍️ Opção 2 — Ou preenche direto")
        col_a, col_b = st.columns(2)
        with col_a:
            nova_data = st.date_input("Data da aposta", key="nova_data")
            nova_liga = st.selectbox("Liga", ["Série A", "Série B", "Outra"], key="nova_liga_sel")
            nova_jogos = st.text_input(
                "Jogo(s) envolvido(s)", key="nova_jogos",
                placeholder="ex: Corinthians x Athletico-PR"
            )
            nova_mercados = st.text_input(
                "Mercado(s) apostado(s)", key="nova_mercados",
                placeholder="ex: Menos de 10.5 Escanteios + 1X"
            )
        with col_b:
            nova_categoria = st.selectbox(
                "Categoria (pro ROI por categoria)",
                ["resultado", "gols", "btts", "casa_marca", "fora_marca", "escanteios",
                 "cartoes", "chutes_casa", "chutes_fora", "chutes_gol_casa", "chutes_gol_fora",
                 "faltas_casa", "faltas_fora", "mista"],
                key="nova_categoria"
            )
            nova_tipo = st.selectbox("Tipo", ["simples", "multipla"], key="nova_tipo")
            nova_odd = st.number_input("Odd", min_value=1.01, step=0.01, key="nova_odd")
            nova_stake = st.number_input("Valor apostado (R$)", min_value=0.0, step=1.0, key="nova_stake")

        nova_obs = st.text_input("Observações (opcional)", key="nova_obs")

        if st.button("💾 Salvar aposta como pendente", key="btn_salvar_aposta"):
            if not nova_jogos or not nova_mercados or not nova_odd or not nova_stake:
                st.error("Preenche pelo menos jogo, mercado, odd e valor apostado.")
            else:
                pernas_estruturadas = st.session_state.get("bingo_pernas_estruturadas")
                ok = salvar_aposta(
                    data_aposta=nova_data.isoformat(), liga=nova_liga,
                    jogos_envolvidos=nova_jogos, mercados=nova_mercados,
                    categoria_estimada=nova_categoria, tipo_aposta=nova_tipo,
                    odd=nova_odd, stake=nova_stake, observacoes=nova_obs,
                    fonte_print=bool(print_bilhete),
                    mercados_estruturado_json=json.dumps(pernas_estruturadas, ensure_ascii=False) if pernas_estruturadas else None,
                )
                if ok:
                    st.session_state.pop("bingo_pernas_estruturadas", None)
                    if pernas_estruturadas:
                        st.success("✅ Aposta salva como pendente, com dados estruturados do Bingo — vai ter sugestão automática de resultado na aba '⏳ Pendentes'.")
                    else:
                        st.success("✅ Aposta salva como pendente! Marca o resultado na aba '⏳ Pendentes' depois que o jogo acabar.")
                else:
                    st.error("Não consegui salvar — confira o log do Streamlit Cloud.")

    # ------------------------------------------------------------
    # PENDENTES — marcar ganhou/perdeu sem precisar de novo print
    # ------------------------------------------------------------
    with sub_pendentes:
        pendentes = buscar_apostas(status="pendente")
        if not pendentes:
            st.info("Nenhuma aposta pendente. Registre uma na aba '➕ Nova Aposta'.")
        else:
            st.caption(f"{len(pendentes)} aposta(s) aguardando resultado.")
            for ap in pendentes:
                with st.expander(f"{ap['jogos_envolvidos']} — {ap['mercados']} (odd {ap['odd']}, R$ {ap['stake']:.2f})"):
                    st.write(f"Data: {ap['data_aposta']} | Liga: {ap.get('liga','—')} | Tipo: {ap['tipo_aposta']}")
                    st.caption(
                        f"Se ganhou, retorno automático: R$ {ap['stake'] * ap['odd']:.2f} | "
                        f"Se perdeu: -R$ {ap['stake']:.2f}"
                    )

                    sugestao = avaliar_aposta_automaticamente(ap)
                    if sugestao["status"] == "ganhou":
                        st.markdown("🤖 **Sugestão automática: ✅ Ganhou** — confira e clica em Ganhou abaixo pra confirmar.")
                    elif sugestao["status"] == "perdeu":
                        st.markdown("🤖 **Sugestão automática: ❌ Perdeu** — confira e clica em Perdeu abaixo pra confirmar.")
                    elif sugestao["status"] == "aguardando":
                        st.caption("🤖 Sugestão automática: aguardando jogo(s) encerrar(em).")
                    elif sugestao["status"] == "indeterminado":
                        st.caption("🤖 Sugestão automática: parcialmente avaliável — confira o detalhamento.")
                        with st.expander("Ver detalhamento por perna"):
                            for d in sugestao["detalhes"]:
                                icone = "✅" if d["acerto"] is True else "❌" if d["acerto"] is False else "❔"
                                st.write(f"{icone} {d['jogo']} — {d['nome']}")
                    # status "sem_estrutura" = aposta digitada na mão, sem sugestão — segue 100% manual

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        if st.button("✅ Ganhou", key=f"ganhou_{ap['id']}"):
                            if atualizar_resultado_aposta(ap["id"], ganhou=True):
                                st.success("Marcado como ganho!")
                                st.rerun()
                    with col2:
                        if st.button("❌ Perdeu", key=f"perdeu_{ap['id']}"):
                            if atualizar_resultado_aposta(ap["id"], ganhou=False):
                                st.success("Marcado como perda.")
                                st.rerun()
                    with col3:
                        if st.button("🗑️ Excluir (erro de digitação)", key=f"excluir_{ap['id']}"):
                            if excluir_aposta(ap["id"]):
                                st.success("Excluída.")
                                st.rerun()

    # ------------------------------------------------------------
    # ROI — painel agregado
    # ------------------------------------------------------------
    with sub_roi:
        roi = calcular_roi()
        geral = roi["geral"]

        if geral["total_apostas"] == 0:
            st.info("Ainda não há apostas resolvidas (ganhou/perdeu) pra calcular ROI.")
        else:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Investido", f"R$ {geral['investido']:.2f}")
            col2.metric("Retorno", f"R$ {geral['retorno']:.2f}")
            col3.metric("Lucro/Prejuízo", f"R$ {geral['lucro']:.2f}", delta=f"{geral['roi']}% ROI")
            col4.metric("Taxa de acerto", f"{geral['taxa_acerto']}%", f"{geral['vitorias']}/{geral['total_apostas']}")

            evolucao = buscar_evolucao_lucro()
            if len(evolucao) >= 2:
                st.markdown("---")
                st.markdown("#### 📈 Evolução do lucro acumulado")
                df_evolucao = pd.DataFrame(evolucao).set_index("data")
                st.line_chart(df_evolucao["lucro_acumulado"])
                st.caption("Cada ponto é uma aposta resolvida, em ordem cronológica — mostra tendência, não só a foto de agora.")
            elif len(evolucao) == 1:
                st.caption("Só 1 aposta resolvida até agora — o gráfico de evolução aparece a partir da 2ª.")

            st.markdown("---")
            st.markdown("#### Por liga")
            for liga_nome, dados in roi["por_liga"].items():
                st.write(f"**{liga_nome}**: {dados['total_apostas']} apostas | "
                         f"ROI {dados['roi']}% | Lucro R$ {dados['lucro']:.2f} | "
                         f"Acerto {dados['taxa_acerto']}%")

            st.markdown("#### Por categoria de mercado")
            for cat_nome, dados in sorted(roi["por_categoria"].items(), key=lambda x: x[1]["roi"], reverse=True):
                nome_exib = NOMES_CATEGORIAS.get(cat_nome, cat_nome)
                cor_fn = st.success if dados["roi"] > 0 else st.error if dados["roi"] < 0 else st.info
                cor_fn(f"{nome_exib}: {dados['total_apostas']} apostas | ROI {dados['roi']}% | "
                       f"Lucro R$ {dados['lucro']:.2f}")

            st.markdown("#### Por tipo de aposta")
            for tipo_nome, dados in roi["por_tipo"].items():
                st.write(f"**{tipo_nome.capitalize()}**: {dados['total_apostas']} apostas | "
                         f"ROI {dados['roi']}% | Lucro R$ {dados['lucro']:.2f}")
