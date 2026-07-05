# ==========================================================
# Predictor Pro 3.0
# dashboard/app.py — Interface Streamlit
# ==========================================================

import streamlit as st
from banco import (
    banco_online, conectar,
    carregar_times, carregar_tabela_serie_a,
    carregar_rodada_atual, carregar_noticias
)
from previsao import prever_jogo
from raspador import atualizar_dados_api, atualizar_feed

# ==========================================================
# CONFIGURAÇÃO DA PÁGINA
# ==========================================================

st.set_page_config(
    page_title="Predictor Pro 3.0",
    page_icon="⚽",
    layout="wide",
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .prob-card {
        background: #1e2130;
        border-radius: 12px;
        padding: 20px 10px;
        text-align: center;
    }
    .prob-label { color: #aaa; font-size: 13px; margin-bottom: 4px; }
    .prob-time  { font-weight: bold; font-size: 15px; margin-bottom: 8px; }
    .prob-value { font-size: 26px; font-weight: bold; }
    .verde   { color: #00e676; }
    .amarelo { color: #ffd600; }
    .vermelho{ color: #ff5252; }
    .noticia-item { padding: 6px 0; border-bottom: 1px solid #2a2d3e; font-size: 14px; }
    .tag-fonte { background:#2a2d3e; color:#aaa; border-radius:4px;
                 padding:1px 6px; font-size:11px; margin-left:6px; }
</style>
""", unsafe_allow_html=True)

# ==========================================================
# HEADER
# ==========================================================

st.title("Predictor Pro 3.0")

online = banco_online()
if online:
    st.success("Supabase conectado")
else:
    st.error("Supabase offline — verifique as credenciais")

# ==========================================================
# ABAS
# ==========================================================

aba1, aba2, aba3, aba4 = st.tabs([
    "🎯 Preditor",
    "📅 Jogos da Rodada",
    "📊 Tabela Série B",
    "📰 Últimas Notícias",
])


# ----------------------------------------------------------
# ABA 1 — PREDITOR
# ----------------------------------------------------------
with aba1:

    ok_times, lista_times = carregar_times()
    times_disponiveis = lista_times if ok_times else []

    if not times_disponiveis:
        st.warning("Nenhum time encontrado. Atualize os dados na aba Tabela.")
        times_disponiveis = ["Flamengo", "Palmeiras"]   # fallback visual

    col_casa, col_fora = st.columns(2)
    with col_casa:
        st.caption("Time da Casa")
        time_casa = st.selectbox("Casa", times_disponiveis,
                                 label_visibility="collapsed", key="sel_casa")
    with col_fora:
        st.caption("Time de Fora")
        time_fora = st.selectbox("Fora", times_disponiveis,
                                 index=min(1, len(times_disponiveis) - 1),
                                 label_visibility="collapsed", key="sel_fora")

    st.divider()

    if time_casa == time_fora:
        st.warning("Selecione times diferentes.")
    else:
        with st.spinner("Calculando previsão..."):
            resultado = prever_jogo(time_casa, time_fora)

        prob = resultado.get("probabilidades", {})
        p_casa   = prob.get("casa", 0)
        p_empate = prob.get("empate", 0)
        p_fora   = prob.get("fora", 0)

        # Cards de probabilidade
        st.subheader("Probabilidades 1X2")
        c1, c2, c3 = st.columns(3)

        with c1:
            cor = "verde" if p_casa > p_fora else "vermelho"
            st.markdown(f"""
            <div class="prob-card">
              <div class="prob-label">Casa</div>
              <div class="prob-time">{time_casa}</div>
              <div class="prob-value {cor}">{p_casa}%</div>
            </div>""", unsafe_allow_html=True)

        with c2:
            st.markdown(f"""
            <div class="prob-card">
              <div class="prob-label">Empate</div>
              <div class="prob-time">—</div>
              <div class="prob-value amarelo">{p_empate}%</div>
            </div>""", unsafe_allow_html=True)

        with c3:
            cor = "verde" if p_fora > p_casa else "vermelho"
            st.markdown(f"""
            <div class="prob-card">
              <div class="prob-label">Fora</div>
              <div class="prob-time">{time_fora}</div>
              <div class="prob-value {cor}">{p_fora}%</div>
            </div>""", unsafe_allow_html=True)

        st.divider()

        # Detalhes do modelo
        with st.expander("🔍 Detalhes do cálculo"):
            forca = resultado.get("forca", {})
            tabela_pos = resultado.get("tabela", {})
            n_noticias = resultado.get("noticias_analisadas", 0)

            col_d1, col_d2 = st.columns(2)
            with col_d1:
                st.markdown(f"**{time_casa}**")
                st.write(f"📍 Posição na tabela: **{tabela_pos.get('posicao_casa', '?')}º**")
                st.write(f"💪 Força base (tabela): `{forca.get('casa_base', 0):.4f}`")
                st.write(f"🎯 Eficiência de conversão: `{forca.get('eficiencia_casa', 'sem dado')}`")
                st.write(f"📰 Ajuste notícias: `{forca.get('ajuste_casa', 0):+.3f}`")
                st.write(f"⚡ Força final: `{forca.get('casa_final', 0):.4f}`")
            with col_d2:
                st.markdown(f"**{time_fora}**")
                st.write(f"📍 Posição na tabela: **{tabela_pos.get('posicao_fora', '?')}º**")
                st.write(f"💪 Força base (tabela): `{forca.get('fora_base', 0):.4f}`")
                st.write(f"🎯 Eficiência de conversão: `{forca.get('eficiencia_fora', 'sem dado')}`")
                st.write(f"📰 Ajuste notícias: `{forca.get('ajuste_fora', 0):+.3f}`")
                st.write(f"⚡ Força final: `{forca.get('fora_final', 0):.4f}`")

            st.caption(f"Modelo: dados reais da tabela + eficiência de conversão + sentimento de {n_noticias} notícias | Fator casa: +25% (recalibrado)")

        # Resultado mais provável
        vencedor = resultado.get("vencedor_provavel", "?")
        confianca = resultado.get("confianca", 0)
        st.info(f"🏆 Resultado mais provável: **{vencedor}** ({confianca:.1f}% de probabilidade)")


# ----------------------------------------------------------
# ABA 2 — JOGOS DA RODADA
# ----------------------------------------------------------
with aba2:
    ok_rodada, partidas = carregar_rodada_atual()

    col_r1, col_r2 = st.columns([3, 1])
    with col_r1:
        if partidas:
            rodada_num = partidas[0].get("rodada", "?")
            st.subheader(f"Rodada {rodada_num}")
        else:
            st.subheader("Rodada Atual")

    with col_r2:
        if st.button("🔄 Atualizar dados", key="btn_rodada"):
            with st.spinner("Buscando dados da API..."):
                atualizar_dados_api()
            st.rerun()

    if ok_rodada and partidas:
        for p in partidas:
            status = p.get("status", "agendada")
            data   = p.get("data", "")[:10] if p.get("data") else ""
            hora   = p.get("hora", "")[:5]  if p.get("hora") else ""
            mandante  = p.get("mandante", "?")
            visitante = p.get("visitante", "?")
            gm = p.get("gols_mandante")
            gv = p.get("gols_visitante")

            if gm is not None and gv is not None:
                placar_str = f"**{gm} x {gv}**"
                badge = "🟢" if status == "encerrada" else "🟡"
            else:
                placar_str = f"*{hora}*" if hora else "—"
                badge = "⚪"

            st.markdown(
                f"{badge} `{data}` — {mandante} {placar_str} {visitante} "
                f"&nbsp;&nbsp;<small>📍 {p.get('estadio', '')}</small>",
                unsafe_allow_html=True
            )
    else:
        st.info("Nenhuma partida encontrada. Clique em **Atualizar dados** para buscar da API.")


# ----------------------------------------------------------
# ABA 3 — TABELA SÉRIE B
# ----------------------------------------------------------
with aba3:
    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        st.subheader("Classificação — Série A 2026")
    with col_t2:
        if st.button("🔄 Atualizar tabela", key="btn_tabela"):
            with st.spinner("Buscando tabela da API..."):
                atualizar_dados_api()
            st.rerun()

    ok_tab, tabela = carregar_tabela_serie_a()

    if ok_tab and tabela:
        import pandas as pd

        df = pd.DataFrame(tabela)

        # Seleciona e renomeia colunas para exibição
        colunas_map = {
            "posicao":     "Pos",
            "time":        "Time",
            "pontos":      "Pts",
            "jogos":       "J",
            "vitorias":    "V",
            "empates":     "E",
            "derrotas":    "D",
            "gols_pro":    "GP",
            "gols_contra": "GC",
            "saldo":       "SG",
        }
        colunas_exibir = [c for c in colunas_map if c in df.columns]
        df_exibir = df[colunas_exibir].rename(columns=colunas_map)

        # Destaque zona de acesso (top 4) e rebaixamento (bottom 4)
        def highlight_zona(row):
            pos = int(row["Pos"]) if str(row["Pos"]).isdigit() else 99
            if pos <= 4:
                return ["background-color: #1a3a1a"] * len(row)
            if pos >= len(tabela) - 3:
                return ["background-color: #3a1a1a"] * len(row)
            return [""] * len(row)

        st.dataframe(
            df_exibir.style.apply(highlight_zona, axis=1),
            use_container_width=True,
            hide_index=True,
        )
        st.caption("🟢 Libertadores (top 4)  |  🔴 Zona de rebaixamento (bottom 4)")
    else:
        st.info("Tabela não encontrada. Clique em **Atualizar tabela** para buscar da API.")


# ----------------------------------------------------------
# ABA 4 — NOTÍCIAS
# ----------------------------------------------------------
with aba4:
    col_n1, col_n2 = st.columns([3, 1])
    with col_n1:
        st.subheader("Últimas Notícias — Série A 2026")
    with col_n2:
        if st.button("🔄 Atualizar notícias", key="btn_noticias"):
            with st.spinner("Coletando notícias..."):
                resultado_feed = atualizar_feed()
            st.success(
                f"Coletadas: {resultado_feed['coletadas']} | "
                f"Relevantes: {resultado_feed['salvas']}"
            )
            st.rerun()

    noticias = carregar_noticias(limite=20)

    if noticias:
        for n in noticias:
            texto = n.get("texto", "")
            fonte = n.get("fonte", "")
            st.markdown(
                f'<div class="noticia-item">— {texto}'
                f'<span class="tag-fonte">{fonte}</span></div>',
                unsafe_allow_html=True
            )
    else:
        st.info("Nenhuma notícia encontrada. Clique em **Atualizar notícias**.")

    st.caption(
        "ℹ️ Apenas notícias da Série A 2026 são salvas. "
        "As notícias são usadas como dado qualitativo no cálculo de probabilidade."
    )