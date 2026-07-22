import streamlit as st
import pandas as pd
from datetime import date
from database import supabase
from poisson import MotorPoisson
from qualitativo import calcular_fator_qualitativo, buscar_noticias_recentes, buscar_ajuste_manual
from previsoes import salvar_previsao, buscar_previsoes

import streamlit as st

import streamlit as st

# 1. Definição do usuário e senha
USUARIO_CORRETO = "admin"
SENHA_CORRETA = "seriea2026"

# 2. Inicializa o estado de login
if "logado" not in st.session_state:
    st.session_state.logado = False

# 3. Função da tela de login
def tela_login():
    st.title("⚽ Previsor Esportivo - Série A")
    st.subheader("Área Restrita")
    
    usuario = st.text_input("Usuário")
    senha = st.text_input("Senha", type="password")
    
    if st.button("Entrar"):
        if usuario == USUARIO_CORRETO and senha == SENHA_CORRETA:
            st.session_state.logado = True
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")

# 4. Controle de Fluxo Bloqueante
if not st.session_state.logado:
    tela_login()
else:
    # Botão para deslogar na barra lateral
    if st.sidebar.button("Sair do App"):
        st.session_state.logado = False
        st.rerun()
        
    # ---------------------------------------------------------
    # TUDO ABAIXO DAQUI SÓ APARECE DEPOIS DE LOGAR
    # ---------------------------------------------------------
    st.title("⚽ Predix Sports — Análise Quantitativa Série A 2026")
    
    # [ATENÇÃO: Cole aqui todo o resto do seu código atual]
    # Certifique-se de aplicar o recuo (Tab ou 4 espaços) 
    # em todas as linhas do seu código original que colar aqui.



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
    hoje = date.today().isoformat()
    resp = (
        supabase.table("jogos").select("*")
        .gte("data", hoje)
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


def _valor_ou_padrao(dados_time, chave, padrao):
    """dados_time.get(chave) pode existir mas vir None (time ainda sem
    partidas com estatística coletada) — nesses casos cai pro padrão do
    MotorPoisson em vez de quebrar ou virar 0."""
    valor = dados_time.get(chave)
    return valor if valor is not None else padrao


def analisar_confronto(casa, fora, data_jogo=None):
    tc = buscar_time(casa)
    tf = buscar_time(fora)
    if not tc or not tf:
        return None

    noticias = buscar_noticias_recentes(50)

    ajuste = buscar_ajuste_manual(casa, fora, data_jogo)
    ajuste_manual_casa = ajuste.get("ajuste_manual_casa") if ajuste else None
    ajuste_manual_fora = ajuste.get("ajuste_manual_fora") if ajuste else None

    fq_casa = calcular_fator_qualitativo(casa, tc, noticias, ajuste_manual_casa)
    fq_fora = calcular_fator_qualitativo(fora, tf, noticias, ajuste_manual_fora)

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
    )
    # Anexado à resposta só pra UI conseguir avisar quando um ajuste manual
    # (planilha) entrou no cálculo, sem precisar buscar de novo.
    resultado.ajuste_manual_aplicado = ajuste is not None
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

    if resultado.prob_casa_marca >= 50:
        mercados["casa_marca"] = {"nome": f"{casa} Marca", "prob": resultado.prob_casa_marca}
    else:
        mercados["casa_marca"] = {"nome": f"{casa} Não Marca", "prob": 100 - resultado.prob_casa_marca}

    if resultado.prob_fora_marca >= 50:
        mercados["fora_marca"] = {"nome": f"{fora} Marca", "prob": resultado.prob_fora_marca}
    else:
        mercados["fora_marca"] = {"nome": f"{fora} Não Marca", "prob": 100 - resultado.prob_fora_marca}

    if resultado.prob_over_cantos >= 50:
        mercados["escanteios"] = {"nome": f"Mais de {resultado.linha_cantos} Escanteios", "prob": resultado.prob_over_cantos}
    else:
        mercados["escanteios"] = {"nome": f"Menos de {resultado.linha_cantos} Escanteios", "prob": 100 - resultado.prob_over_cantos}

    if resultado.prob_over_cartoes >= 50:
        mercados["cartoes"] = {"nome": f"Mais de {resultado.linha_cartoes} Cartões", "prob": resultado.prob_over_cartoes}
    else:
        mercados["cartoes"] = {"nome": f"Menos de {resultado.linha_cartoes} Cartões", "prob": 100 - resultado.prob_over_cartoes}

    # Chutes/chutes no gol são POR TIME, então cada um vira sua própria
    # categoria — dá pra combinar "chutes do mandante" com "chutes no gol
    # do visitante" na mesma múltipla sem repetir a mesma perna duas vezes.
    if resultado.prob_over_chutes_casa >= 50:
        mercados["chutes_casa"] = {"nome": f"Mais de {resultado.linha_chutes_casa} Chutes ({casa})", "prob": resultado.prob_over_chutes_casa}
    else:
        mercados["chutes_casa"] = {"nome": f"Menos de {resultado.linha_chutes_casa} Chutes ({casa})", "prob": 100 - resultado.prob_over_chutes_casa}

    if resultado.prob_over_chutes_fora >= 50:
        mercados["chutes_fora"] = {"nome": f"Mais de {resultado.linha_chutes_fora} Chutes ({fora})", "prob": resultado.prob_over_chutes_fora}
    else:
        mercados["chutes_fora"] = {"nome": f"Menos de {resultado.linha_chutes_fora} Chutes ({fora})", "prob": 100 - resultado.prob_over_chutes_fora}

    if resultado.prob_over_chutes_gol_casa >= 50:
        mercados["chutes_gol_casa"] = {"nome": f"Mais de {resultado.linha_chutes_gol_casa} Chutes no Gol ({casa})", "prob": resultado.prob_over_chutes_gol_casa}
    else:
        mercados["chutes_gol_casa"] = {"nome": f"Menos de {resultado.linha_chutes_gol_casa} Chutes no Gol ({casa})", "prob": 100 - resultado.prob_over_chutes_gol_casa}

    if resultado.prob_over_chutes_gol_fora >= 50:
        mercados["chutes_gol_fora"] = {"nome": f"Mais de {resultado.linha_chutes_gol_fora} Chutes no Gol ({fora})", "prob": resultado.prob_over_chutes_gol_fora}
    else:
        mercados["chutes_gol_fora"] = {"nome": f"Menos de {resultado.linha_chutes_gol_fora} Chutes no Gol ({fora})", "prob": 100 - resultado.prob_over_chutes_gol_fora}

    return mercados


def gerar_multiplas(resultado, casa, fora):
    """
    Distribui os mercados disponíveis entre 5 múltiplas tentando:
    1. Nunca colocar duas categorias de CONFIABILIDADE BAIXA na mesma
       múltipla (evita que 1 jogo ruim de cartões/chutes no gol quebre
       2+ pernas de uma vez — foi exatamente isso que aconteceu na
       aposta perdida do Atlético-MG x Bahia em 21/07/2026).
    2. Espalhar as categorias entre as 5 múltiplas em vez de reciclar
       sempre os mesmos 3-4 "campeões" — cada categoria aparece no
       máximo em 2 das 5 múltiplas.

    Classificação de confiabilidade (observada nos jogos analisados
    nesta conversa, não é estatística formal — reavaliar com o
    diagnostico_calibracao.py conforme mais jogos acumularem):
      ALTA:  resultado, gols            (acerto consistente em vários jogos)
      MÉDIA: escanteios, btts, chutes_casa, chutes_fora, casa_marca, fora_marca
      BAIXA: cartoes, chutes_gol_casa, chutes_gol_fora  (erros repetidos)
    """
    mercados = montar_mercados(resultado, casa, fora)

    combos = [
        ("Múltipla 1 — Mais Segura", ["resultado", "gols", "escanteios"]),
        ("Múltipla 2 — Foco no Mandante", ["casa_marca", "chutes_casa", "chutes_gol_casa"]),
        ("Múltipla 3 — Foco no Visitante", ["fora_marca", "chutes_fora", "chutes_gol_fora"]),
        ("Múltipla 4 — Ambas Marcam + Cartões", ["btts", "cartoes", "resultado"]),
        ("Múltipla 5 — Mais Arriscada (Mix)", ["casa_marca", "fora_marca", "cartoes"]),
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

        resultado = analisar_confronto(jogo['casa_nome'], jogo['fora_nome'], jogo.get('data'))

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
                if salvar_previsao(resultado, jogo['casa_nome'], jogo['fora_nome'], jogo.get('data'), top_3):
                    st.success("Previsão salva! Confira depois na aba 📈 Medidor de Desempenho.")
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
        st.dataframe(styled, width='stretch', height=660, hide_index=True)
    else:
        st.info("Tabela não disponível.")

with aba_performance:
    st.subheader("📈 Medidor de Desempenho")
    st.caption("Busque previsões salvas pra comparar com o resultado real depois do jogo.")

    termo_busca = st.text_input("Buscar por time (ou deixe vazio pra ver as mais recentes):", "")
    previsoes_salvas = buscar_previsoes(termo_busca)

    if not previsoes_salvas:
        st.info("Nenhuma previsão salva ainda. Use o botão '💾 Salvar previsão' na aba Painel Analítico.")
    else:
        for p in previsoes_salvas:
            with st.expander(f"{p['time_casa']} x {p['time_fora']} — {p.get('data','')}"):
                st.write(f"Vitória {p['time_casa']}: {p['prob_casa']}% | Empate: {p['prob_empate']}% | Vitória {p['time_fora']}: {p['prob_fora']}%")
                st.write(f"Ambas Marcam: {p['prob_btts']}% | Gols >1.5: {p['over15_ft']}% | Gols >2.5: {p['over25_ft']}%")
                st.write(f"Escanteios: média {p['cantos_ft']} | linha {p['linha_cantos']} | over: {p['prob_over_cantos']}%")
                st.write(f"Cartões: média {p['cartoes_ft']} | linha {p['linha_cartoes']} | over: {p['prob_over_cartoes']}%")
                st.write(f"Placar mais provável: {p['placar_mais_provavel']}")

                jogo_real = supabase.table("jogos").select("gols_casa,gols_fora,status") \
                    .eq("casa_nome", p['time_casa']).eq("fora_nome", p['time_fora']) \
                    .eq("data", p['data']).execute().data
                if jogo_real and jogo_real[0].get("status") == "encerrado":
                    gc, gf = jogo_real[0]["gols_casa"], jogo_real[0]["gols_fora"]
                    st.success(f"✅ Resultado real: {p['time_casa']} {gc} x {gf} {p['time_fora']}")
                else:
                    st.caption("⏳ Jogo ainda não encerrado (ou sem placar salvo).")
