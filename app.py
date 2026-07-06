import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
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
    hoje = datetime.now().strftime("%Y-%m-%d")
    limite = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    resp = (
        supabase.table("jogos").select("*")
        .gte("data", hoje).lte("data", limite)
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


def registrar_aposta(mercado, probabilidade):
    try:
        supabase.table("apostas").insert({
            "mercado": mercado,
            "probabilidade": float(probabilidade) if probabilidade else None,
            "resultado": "Pendente",
            "data": datetime.now().isoformat(),
        }).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao registrar: {e}")
        return False


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

aba_painel, aba_tabela, aba_performance = st.tabs([
    "📊 Painel Analítico",
    "🏆 Tabela de Classificação",
    "📈 Medidor de Desempenho"
])

with aba_painel:
    st.markdown("## ⚽ Predix Sports — Análise Quantitativa Série B 2026")
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
            st.warning(f"🟫 Escanteios FT (estimado): {resultado.cantos_ft} cantos")
            st.error(f"🟥 Cartões FT (estimado): {resultado.cartoes_ft} cartões")
            st.info(f"🏆 Placar mais provável: **{resultado.placar_mais_provavel}**")
        else:
            st.warning("Análise não disponível — time não encontrado no banco.")

        st.markdown("---")
        st.markdown("### 🚀 Registrar na Banca")
        mercado = st.radio("Mercado:", [
            f"Vitória {jogo['casa_nome']}", "Empate", f"Vitória {jogo['fora_nome']}",
            "Ambas Marcam", "Mais de 1.5 Gols", "Mais de 2.5 Gols"
        ])
        if st.button("💾 Gravar Palpite"):
            if registrar_aposta(f"{confronto_sel} - {mercado}", 0):
                st.success("✅ Palpite registrado!")

    with col2:
        st.markdown("### 📅 Próximos Jogos")
        for i, j in enumerate(partidas):
            status_icon = "🔴" if j.get("status") == "encerrado" else "🟢" if j.get("status") == "ao_vivo" else "⚪"
            st.code(
                f"⚽ CONFRONTO {i+1} | {j.get('data','')} às {j.get('hora','')}\n"
                f"{j['casa_nome']} x {j['fora_nome']}\n"
                f"{status_icon} {j.get('status_desc','Agendado')}"
            )

with aba_tabela:
    st.subheader("🏆 Classificação — Brasileirão Série B 2026")
    st.caption("🟩 Zona de acesso à Série A (1º–4º) · 🟥 Zona de rebaixamento à Série C (17º–20º)")
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