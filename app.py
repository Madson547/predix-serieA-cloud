import streamlit as st
import pandas as pd
from database import supabase
from poisson import MotorPoisson
from qualitativo import calcular_fator_qualitativo, buscar_noticias_recentes

st.set_page_config(layout="wide", page_title="Predix Sports")

st.markdown("""
    <style>
    .stApp { background-color: #0e1117; color: white; }
    </style>
""", unsafe_allow_html=True)

motor = MotorPoisson()
MEDALHAS = ["🥇", "🥈", "🥉"]


@st.cache_data(ttl=300)
def carregar_partidas():
    resp = (
        supabase.table("jogos").select("*")
        .order("data").execute()
    )
    return resp.data


@st.cache_data(ttl=300)
def carregar_tabela():
    resp = supabase.table("times").select("*").order("pts", desc=True).execute()
    return resp.data


def buscar_time(nome):
    resp = supabase.table("times").select("*").eq("nome", nome).execute().data
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


def analisar_confronto(casa, fora):
    tc = buscar_time(casa)
    tf = buscar_time(fora)
    if not tc or not tf:
        return None

    noticias = buscar_noticias_recentes(50)
    fq_casa = calcular_fator_qualitativo(casa, tc, noticias)
    fq_fora = calcular_fator_qualitativo(fora, tf, noticias)

    resultado = motor.calcular(
        time_casa=casa, time_fora=fora,
        gm_casa=tc["gm"], gc_casa=tc["gc"], j_casa=tc["j"],
        gm_fora=tf["gm"], gc_fora=tf["gc"], j_fora=tf["j"],
        fator_qualitativo_casa=fq_casa,
        fator_qualitativo_fora=fq_fora,
    )
    return resultado


def montar_mercados(resultado, casa, fora):
    """
    Um mercado por categoria (evita combinar mercados redundantes ou
    contraditórios, tipo 'Mais de 1.5' com 'Mais de 2.5' na mesma múltipla).
    """
    mercados = {}

    candidatos_resultado = [
        (f"Vitória {casa}", resultado.prob_casa),
        ("Empate", resultado.prob_empate),
        (f"Vitória {fora}", resultado.prob_fora),
        (f"Dupla Chance: {casa} ou Empate", resultado.prob_dupla_1x),
        (f"Dupla Chance: {fora} ou Empate", resultado.prob_dupla_x2),
    ]
    mercados["resultado"] = dict(zip(("nome", "prob"), max(candidatos_resultado, key=lambda x: x[1])))

    candidatos_gols = [
        ("Mais de 1.5 Gols", resultado.over15_ft),
        ("Menos de 1.5 Gols", 100 - resultado.over15_ft),
        ("Mais de 2.5 Gols", resultado.over25_ft),
        ("Menos de 2.5 Gols", 100 - resultado.over25_ft),
    ]
    mercados["gols"] = dict(zip(("nome", "prob"), max(candidatos_gols, key=lambda x: x[1])))

    if resultado.prob_btts >= 50:
        mercados["btts"] = {"nome": "Ambas Marcam (Sim)", "prob": resultado.prob_btts}
    else:
        mercados["btts"] = {"nome": "Ambas Marcam (Não)", "prob": 100 - resultado.prob_btts}

    if resultado.prob_over_cantos >= 50:
        mercados["escanteios"] = {"nome": f"Mais de {resultado.linha_cantos} Escanteios", "prob": resultado.prob_over_cantos}
    else:
        mercados["escanteios"] = {"nome": f"Menos de {resultado.linha_cantos} Escanteios", "prob": 100 - resultado.prob_over_cantos}

    if resultado.prob_over_cartoes >= 50:
        mercados["cartoes"] = {"nome": f"Mais de {resultado.linha_cartoes} Cartões", "prob": resultado.prob_over_cartoes}
    else:
        mercados["cartoes"] = {"nome": f"Menos de {resultado.linha_cartoes} Cartões", "prob": 100 - resultado.prob_over_cartoes}

    return mercados


def gerar_multiplas(resultado, casa, fora):
    mercados = montar_mercados(resultado, casa, fora)

    combos = [
        ("Múltipla 1 — Mais Segura", ["resultado", "gols"]),
        ("Múltipla 2 — Equilibrada", ["resultado", "gols", "btts"]),
        ("Múltipla 3 — Mercados Alternativos", ["gols", "escanteios", "cartoes"]),
        ("Múltipla 4 — Mais Arriscada", ["resultado", "gols", "btts", "escanteios"]),
    ]

    multiplas = []
    for titulo, categorias in combos:
        pernas = [mercados[c] for c in categorias if c in mercados]
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


partidas = carregar_partidas()
tabela = carregar_tabela()

if not partidas:
    st.warning("⚠️ Nenhum jogo encontrado na janela de datas atual. Verifique se o robô de coleta rodou recentemente.")
    st.stop()

lista_confrontos = [f"{j['casa_nome']} x {j['fora_nome']}" for j in partidas]

aba_painel, aba_multiplas, aba_tabela, aba_performance = st.tabs([
    "📊 Painel Analítico",
    "🎰 Sugestões de Múltiplas",
    "🏆 Tabela de Classificação",
    "📈 Medidor de Desempenho"
])

with aba_painel:
    st.markdown("## ⚽ Predix Sports — Análise Quantitativa Série A 2026")
    col1, col2 = st.columns([1.5, 1.5])

    with col1:
        st.markdown("### 🎛️ Selecione o Confronto")
        confronto_sel = st.selectbox("Escolha a Partida:", lista_confrontos)
        jogo = [j for j in partidas if f"{j['casa_nome']} x {j['fora_nome']}" == confronto_sel][0]

        resultado = analisar_confronto(jogo['casa_nome'], jogo['fora_nome'])

        st.markdown(f"🗓️ **Data:** {jogo.get('data','')} | 🕒 **Hora:** {jogo.get('hora','')} | **Status:** {jogo.get('status_desc','')}")
        st.markdown("---")

        if resultado:
            top_3 = construir_top_apostas(resultado, jogo['casa_nome'], jogo['fora_nome'])
            st.markdown("### 🔥 TOP 3 MELHORES APOSTAS")
            for item in top_3:
                medalha = MEDALHAS[item["posicao"] - 1]
                odd_txt = f" | Odd Justa: {item['odd_justa']}" if item.get("odd_justa") else ""
                st.success(f"{medalha} **{item['mercado']}** — Confiança: {item['confianca']}%{odd_txt}")

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

with aba_multiplas:
    st.markdown("## 🎰 Sugestões de Múltiplas")
    st.caption(f"Confronto: **{jogo['casa_nome']} x {jogo['fora_nome']}**")
    st.caption(
        "⚠️ A probabilidade combinada assume que os mercados são independentes entre si — "
        "na prática alguns se correlacionam (ex: Mais de 2.5 Gols tende a andar junto com "
        "Ambas Marcam), então trate como referência, não como certeza matemática exata."
    )
    st.markdown("---")

    if resultado:
        multiplas = gerar_multiplas(resultado, jogo['casa_nome'], jogo['fora_nome'])
        cores = ["#1b4332", "#123a4a", "#4a3b1b", "#4a1919"]
        for i, m in enumerate(multiplas):
            cor = cores[i % len(cores)]
            pernas_html = "".join(
                f'<div style="padding:3px 0;">✅ {p["nome"]} <span style="opacity:0.7;">({p["prob"]:.1f}%)</span></div>'
                for p in m["pernas"]
            )
            odd_html = f" &nbsp;|&nbsp; Odd Justa: <b>{m['odd_justa']}</b>" if m["odd_justa"] else ""
            st.markdown(f"""
            <div style="background:{cor}; border-radius:10px; padding:16px 20px; margin-bottom:14px;">
              <div style="font-weight:bold; font-size:17px; margin-bottom:10px;">{m['titulo']}</div>
              {pernas_html}
              <div style="margin-top:12px; font-size:15px;">
                📊 Probabilidade de bater: <b>{m['prob_combinada']}%</b>{odd_html}
              </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.warning("Análise não disponível — time não encontrado no banco.")

with aba_tabela:
    st.subheader("🏆 Classificação — Brasileirão Série A 2026")
    st.caption("🟩 Classificação para Libertadores (1º–4º) · 🟥 Zona de rebaixamento à Série B (17º–20º)")
    if tabela:
        df = pd.DataFrame(tabela)
        colunas = ["posicao", "nome", "pts", "j", "v", "e", "d", "gm", "gc", "sg"]
        colunas_exist = [c for c in colunas if c in df.columns]
        df_ordenado = df[colunas_exist].sort_values("pts", ascending=False)
        styled = df_ordenado.style.apply(colorir_tabela, axis=1)
        st.dataframe(styled, use_container_width=True, height=660, hide_index=True)
    else:
        st.info("Tabela não disponível.")

with aba_performance:
    st.subheader("📈 Histórico da Banca")
    st.info("Em breve: histórico completo de apostas com gráfico de evolução.")