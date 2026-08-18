# ==========================================================
# Predix Série B — coletor_b.py
# MIGRADO em 17/08/2026 da Dados Futebol pra API Futebol
# (api-futebol.com.br), depois de dois casos documentados de
# dado permanentemente corrompido/incompleto na fonte antiga
# (Criciúma x Goiás id=3302, Sport x Cuiabá id=3283 — ambos
# confirmados corretos na API Futebol via teste_api_futebol.py).
#
# TABELAS NO SUPABASE NÃO MUDARAM (jogos_b, times_b,
# estatisticas_partidas_b) — mesmo schema de antes, então
# app.py e diagnostico.py não precisam de nenhuma alteração.
#
# MUDANÇA DE ARQUITETURA: a Dados Futebol exigia gerenciar uma
# "janela de rodadas" pra saber quais jogos revisitar — isso já
# causou pelo menos 2 bugs de detecção de rodada no passado. A
# API Futebol devolve a temporada inteira (380 jogos) numa única
# chamada, incluindo status de cada partida. Por isso trocamos
# pra uma JANELA DE DATAS (hoje ± N dias) — mais simples, sem
# depender de detectar "qual rodada está em andamento".
#
# IMPORTANTE — VERSÃO NOVA, AINDA NÃO VALIDADA EM PRODUÇÃO:
# esse arquivo nunca rodou de verdade contra o cron real. Rode
# primeiro via workflow_dispatch manual e confira o log com
# calma antes de deixar no cron automático — é esperado que
# apareçam ajustes, do mesmo jeito que aconteceu quando a Dados
# Futebol foi debugada ao longo de várias sessões.
# ==========================================================

import os
import time
from datetime import datetime, date, timedelta
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from database import supabase

load_dotenv()

API_FUTEBOL_KEY = os.getenv("API_FUTEBOL_KEY")
BASE_URL = "https://api.api-futebol.com.br/v1"

# Confirmado via teste_api_futebol.py em 17/08/2026 — respondeu 200 com a
# tabela real da Série B (Criciúma no topo). Diferente da Dados Futebol,
# esse ID parece ser fixo pra competição (não muda por temporada — a
# temporada fica dentro de "edicao", aninhada no mesmo campeonato_id).
CAMPEONATO_ID_SERIE_B = 14

# Os dois provedores grafam alguns times de forma diferente. Sem isso, o
# mesmo time vira "dois times" em times_b, e o mesmo jogo vira "dois jogos"
# em jogos_b/estatisticas_partidas_b (confirmado em 17/08/2026 — 12 jogos
# duplicados + 4 times duplicados na primeira execução real). Padronizamos
# pro nome que já estava em uso nas previsões/apostas salvas antes da
# migração (nome antigo, da Dados Futebol), pra não quebrar o histórico.
NORMALIZACAO_NOMES_TIMES = {
    "América-MG": "América Mineiro",
    "Athletic Club": "Athletic-MG",
    "Atlético-GO": "Atlético Goianiense",
    "Operário-PR": "Operário Ferroviário",
}


def _normalizar_nome_time(nome: str) -> str:
    return NORMALIZACAO_NOMES_TIMES.get(nome, nome)


TABELA_JOGOS = "jogos_b"
TABELA_TIMES = "times_b"
TABELA_ESTATISTICAS_PARTIDAS = "estatisticas_partidas_b"

# Janela de datas processada a cada execução — jogos fora dessa janela não
# são tocados (nem revisitados, nem re-salvos). Passado curto o suficiente
# pra não gastar cota da API à toa reprocessando jogos antigos que não vão
# mudar mais; futuro suficiente pra alimentar a tela de "próximos jogos".
JANELA_DIAS_PASSADOS = 10
JANELA_DIAS_FUTUROS = 15

# Mesmo piso/teto de sanidade que já usávamos com a Dados Futebol — mantido
# como camada de defesa, mesmo a API Futebol tendo se mostrado mais
# confiável nos 2 casos testados. Nunca é demais desconfiar de dado que
# vem de fora, não importa a fonte.
LIMITE_SANIDADE_CHUTES = 35
LIMITE_SANIDADE_CHUTES_GOL = 20
MINIMO_SANIDADE_CHUTES = 5
MINIMO_SANIDADE_FALTAS = 3


def _headers():
    return {"Authorization": f"Bearer {API_FUTEBOL_KEY}", "Accept": "application/json"}


def _get(endpoint, params=None):
    """Retorna (status_code, corpo_json). Nunca lança exceção pra chamador
    — quem chama decide o que fazer com status != 200."""
    try:
        resp = requests.get(f"{BASE_URL}{endpoint}", headers=_headers(), params=params, timeout=20)
    except requests.RequestException as e:
        print(f"[ERRO API] {endpoint}: {e}")
        return None, {}
    try:
        corpo = resp.json()
    except Exception:
        corpo = {}
    return resp.status_code, corpo


def _normalizar_data(data_str: str):
    """API Futebol devolve 'DD/MM/AAAA'. Postgres exige 'AAAA-MM-DD'."""
    if not data_str:
        return None
    try:
        return datetime.strptime(data_str.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[AVISO] Formato de data não reconhecido: '{data_str}'")
        return None


# ==========================================================
# TABELA -> tabela "times_b"
# ==========================================================

def buscar_tabela() -> list:
    status, data = _get(f"/campeonatos/{CAMPEONATO_ID_SERIE_B}/tabela")
    if status != 200:
        print(f"[ERRO] /campeonatos/{CAMPEONATO_ID_SERIE_B}/tabela retornou status {status}")
        return []

    linhas = data if isinstance(data, list) else data.get("data", [])
    times = []
    for entry in linhas:
        time_info = entry.get("time", {})
        times.append({
            "nome":    _normalizar_nome_time(time_info.get("nome_popular", "")),
            "posicao": entry.get("posicao"),
            "pts":     entry.get("pontos", 0),
            "j":       entry.get("jogos", 0),
            "v":       entry.get("vitorias", 0),
            "e":       entry.get("empates", 0),
            "d":       entry.get("derrotas", 0),
            "gm":      entry.get("gols_pro", 0),
            "gc":      entry.get("gols_contra", 0),
            "sg":      entry.get("saldo_gols", 0),
        })
    print(f"[API] Tabela: {len(times)} times")
    return times


def salvar_times(times: list):
    if not times:
        return
    for row in times:
        if not row.get("nome"):
            continue
        row["data_atualizacao"] = datetime.now().isoformat()
        try:
            resp = supabase.table(TABELA_TIMES).update(row).eq("nome", row["nome"]).execute()
            if not resp.data:
                supabase.table(TABELA_TIMES).insert(row).execute()
        except Exception as e:
            print(f"[ERRO] salvar_times {row.get('nome')}: {e}")
    print(f"[TIMES] {len(times)} times salvos/atualizados.")


# ==========================================================
# PARTIDAS DA TEMPORADA (resumo, sem estatística) — busca tudo
# de uma vez, extração recursiva pra sobreviver a qualquer
# formato de aninhamento que a API usar (confirmado com
# teste_api_futebol.py: a resposta vem aninhada, não como lista
# plana — a extração recursiva por 'time_mandante'+'time_visitante'
# já se mostrou robusta a isso).
# ==========================================================

def _extrair_partidas(obj):
    encontradas = []
    if isinstance(obj, dict):
        if "time_mandante" in obj and "time_visitante" in obj and "partida_id" in obj:
            encontradas.append(obj)
        else:
            for v in obj.values():
                encontradas.extend(_extrair_partidas(v))
    elif isinstance(obj, list):
        for item in obj:
            encontradas.extend(_extrair_partidas(item))
    return encontradas


def buscar_resumo_temporada() -> list:
    status, data = _get(f"/campeonatos/{CAMPEONATO_ID_SERIE_B}/partidas")
    if status != 200:
        print(f"[ERRO] /campeonatos/{CAMPEONATO_ID_SERIE_B}/partidas retornou status {status}")
        return []
    partidas = _extrair_partidas(data)
    print(f"[API] Partidas na temporada inteira: {len(partidas)}")
    return partidas


def buscar_detalhe_partida(partida_id: int) -> dict | None:
    status, data = _get(f"/partidas/{partida_id}")
    if status != 200:
        print(f"[ERRO] /partidas/{partida_id} retornou status {status}")
        return None
    return data


# ==========================================================
# SANIDADE — mesmo padrão já usado com a Dados Futebol
# ==========================================================

def _aplicar_teto_piso(valor, minimo, maximo, nome_time, rotulo, pid):
    if valor is None:
        return None
    if valor > maximo:
        print(f"[AVISO] {nome_time}: {rotulo} suspeito ({valor}, acima do teto) na partida id={pid}, descartado")
        return None
    if valor < minimo:
        print(f"[AVISO] {nome_time}: {rotulo} suspeito ({valor}, abaixo do piso) na partida id={pid}, descartado")
        return None
    return valor


def _parse_posse(valor_str):
    """'40%' -> 40. Retorna None se não der pra converter."""
    if not valor_str:
        return None
    try:
        return int(str(valor_str).replace("%", "").strip())
    except ValueError:
        return None


def _lado_confiavel(bloco: dict) -> bool:
    """Marca como não-confiável só quando o bloco vier ausente/vazio ou
    com posse de bola fisicamente implausível — mesmo critério já usado
    com a Dados Futebol (faixa alargada em 11/08/2026 pra 3-97%)."""
    if not bloco:
        return False
    posse = _parse_posse(bloco.get("posse_de_bola"))
    if posse is not None and not (3 <= posse <= 97):
        return False
    return True


# ==========================================================
# SALVAR ESTATÍSTICA REAL DA PARTIDA -> estatisticas_partidas_b
# (mesma tabela/schema de antes — reaproveitado sem mudança)
# ==========================================================

def salvar_estatisticas_partida(
    fixture_id, casa_nome, fora_nome, data_jogo,
    escanteios_casa, escanteios_fora, cartoes_casa, cartoes_fora,
    chutes_casa, chutes_fora, chutes_gol_casa, chutes_gol_fora,
    faltas_casa, faltas_fora,
):
    """
    CORREÇÃO 17/08/2026: o upsert usava on_conflict='fixture_id', mas o
    fixture_id NUNCA bate entre provedores diferentes (Dados Futebol usava
    uma numeração, API Futebol usa outra) — isso duplicava toda partida já
    coletada antes da migração, em vez de atualizar. A identidade real e
    estável de uma partida é casa_nome+fora_nome+data, que é justamente
    como diagnostico.py já busca o jogo — então usamos os mesmos três
    campos aqui, fazendo select+update/insert manual (o Supabase não tem
    uma constraint de unicidade composta configurada nessas colunas, então
    não dá pra usar on_conflict direto nelas).
    """
    if not casa_nome or not fora_nome or not data_jogo:
        return
    try:
        existe = supabase.table(TABELA_ESTATISTICAS_PARTIDAS).select("fixture_id") \
            .eq("casa_nome", casa_nome).eq("fora_nome", fora_nome).eq("data", data_jogo).execute()

        payload = {
            "fixture_id": fixture_id,
            "casa_nome": casa_nome,
            "fora_nome": fora_nome,
            "data": data_jogo,
            "escanteios_casa": escanteios_casa,
            "escanteios_fora": escanteios_fora,
            "cartoes_casa": cartoes_casa,
            "cartoes_fora": cartoes_fora,
            "chutes_casa": chutes_casa,
            "chutes_fora": chutes_fora,
            "chutes_gol_casa": chutes_gol_casa,
            "chutes_gol_fora": chutes_gol_fora,
            "faltas_casa": faltas_casa,
            "faltas_fora": faltas_fora,
            "data_atualizacao": datetime.now().isoformat(),
        }

        if existe.data:
            supabase.table(TABELA_ESTATISTICAS_PARTIDAS).update(payload) \
                .eq("casa_nome", casa_nome).eq("fora_nome", fora_nome).eq("data", data_jogo).execute()
        else:
            supabase.table(TABELA_ESTATISTICAS_PARTIDAS).insert(payload).execute()
    except Exception as e:
        print(f"[ERRO] salvar_estatisticas_partida ({casa_nome} x {fora_nome}, {data_jogo}): {e}")


def salvar_jogos(jogos: list):
    """
    CORREÇÃO 17/08/2026: mesmo problema do salvar_estatisticas_partida —
    procurar por fixture_id não reconhece jogos já salvos pelo provedor
    antigo (numeração diferente). Passa a identificar o jogo por
    casa_nome+fora_nome+data, que é estável entre provedores.
    """
    if not jogos:
        return
    salvos = 0
    for row in jogos:
        if not row.get("casa_nome") or not row.get("fora_nome") or not row.get("data"):
            continue
        row["data_atualizacao"] = datetime.now().isoformat()
        try:
            existe = supabase.table(TABELA_JOGOS).select("id") \
                .eq("casa_nome", row["casa_nome"]).eq("fora_nome", row["fora_nome"]) \
                .eq("data", row["data"]).execute()
            if existe.data:
                supabase.table(TABELA_JOGOS).update(row) \
                    .eq("casa_nome", row["casa_nome"]).eq("fora_nome", row["fora_nome"]) \
                    .eq("data", row["data"]).execute()
            else:
                supabase.table(TABELA_JOGOS).insert(row).execute()
            salvos += 1
        except Exception as e:
            print(f"[ERRO] salvar_jogos {row.get('casa_nome')} x {row.get('fora_nome')}: {e}")
    print(f"[JOGOS] {salvos} partidas salvas/atualizadas.")


def _media(lista, fallback=0.0):
    return round(sum(lista) / len(lista), 2) if lista else fallback


# ==========================================================
# PROCESSAMENTO PRINCIPAL — janela de datas
# ==========================================================

def processar_janela():
    """
    Busca o resumo da temporada inteira (1 chamada), filtra só os jogos
    dentro da janela de datas (hoje ± N dias), e:
      - Jogos futuros/agendados: salva em jogos_b com o que já vem no
        resumo (sem chamada extra).
      - Jogos já encerrados: busca o detalhe completo (1 chamada por jogo),
        salva placar real em jogos_b, estatística real em
        estatisticas_partidas_b, e acumula pra recalcular a média por time
        em times_b.
    """
    resumo = buscar_resumo_temporada()
    if not resumo:
        return

    hoje = date.today()
    inicio = hoje - timedelta(days=JANELA_DIAS_PASSADOS)
    fim = hoje + timedelta(days=JANELA_DIAS_FUTUROS)

    jogos_pra_salvar = []
    stats_por_time = {}
    processados_encerrados = 0
    sem_dado_esc_cart = []

    for p in resumo:
        data_iso = _normalizar_data(p.get("data_realizacao"))
        if not data_iso:
            continue
        try:
            data_dt = datetime.strptime(data_iso, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (inicio <= data_dt <= fim):
            continue

        partida_id = p.get("partida_id")
        casa_nome = _normalizar_nome_time(p.get("time_mandante", {}).get("nome_popular", ""))
        fora_nome = _normalizar_nome_time(p.get("time_visitante", {}).get("nome_popular", ""))
        status_raw = p.get("status")
        finalizado = status_raw == "finalizado"

        for nome in (casa_nome, fora_nome):
            stats_por_time.setdefault(nome, {
                "escanteios_casa": [], "escanteios_fora": [],
                "cartoes_casa": [], "cartoes_fora": [],
                "chutes_casa": [], "chutes_fora": [],
                "chutes_gol_casa": [], "chutes_gol_fora": [],
                "faltas_casa": [], "faltas_fora": [],
            })

        if not finalizado:
            jogos_pra_salvar.append({
                "fixture_id":  partida_id,
                "casa_nome":   casa_nome,
                "fora_nome":   fora_nome,
                "data":        data_iso,
                "hora":        p.get("hora_realizacao", ""),
                "status":      "agendado",
                "status_desc": "Agendado",
                "gols_casa":   None,
                "gols_fora":   None,
            })
            continue

        # Jogo encerrado -> busca detalhe completo (placar + estatística real)
        detalhe = buscar_detalhe_partida(partida_id)
        time.sleep(0.2)
        if not detalhe:
            continue
        processados_encerrados += 1

        gols_casa = detalhe.get("placar_mandante")
        gols_fora = detalhe.get("placar_visitante")

        jogos_pra_salvar.append({
            "fixture_id":  partida_id,
            "casa_nome":   casa_nome,
            "fora_nome":   fora_nome,
            "data":        data_iso,
            "hora":        p.get("hora_realizacao", ""),
            "status":      "encerrado",
            "status_desc": "Encerrado",
            "gols_casa":   gols_casa,
            "gols_fora":   gols_fora,
        })

        est = detalhe.get("estatisticas", {})
        est_m = est.get("mandante", {})
        est_v = est.get("visitante", {})

        m_confiavel = _lado_confiavel(est_m)
        v_confiavel = _lado_confiavel(est_v)
        if not m_confiavel:
            print(f"[AVISO] {casa_nome}: bloco de estatística ausente/suspeito na partida id={partida_id}")
        if not v_confiavel:
            print(f"[AVISO] {fora_nome}: bloco de estatística ausente/suspeito na partida id={partida_id}")

        esc_m = est_m.get("escanteios") if m_confiavel else None
        esc_v = est_v.get("escanteios") if v_confiavel else None
        falta_m = est_m.get("faltas") if m_confiavel else None
        falta_v = est_v.get("faltas") if v_confiavel else None
        fin_m = est_m.get("finalizacao", {}).get("total") if m_confiavel else None
        fin_v = est_v.get("finalizacao", {}).get("total") if v_confiavel else None
        fing_m = est_m.get("finalizacao", {}).get("no_gol") if m_confiavel else None
        fing_v = est_v.get("finalizacao", {}).get("no_gol") if v_confiavel else None

        cartoes = detalhe.get("cartoes", {}).get("amarelo", {})
        cart_m = len(cartoes.get("mandante", [])) if m_confiavel else None
        cart_v = len(cartoes.get("visitante", [])) if v_confiavel else None

        # Sanidade nos mesmos campos que já tinham histórico de dado ruim
        fin_m = _aplicar_teto_piso(fin_m, MINIMO_SANIDADE_CHUTES, LIMITE_SANIDADE_CHUTES, casa_nome, "finalizacoes_total", partida_id)
        fin_v = _aplicar_teto_piso(fin_v, MINIMO_SANIDADE_CHUTES, LIMITE_SANIDADE_CHUTES, fora_nome, "finalizacoes_total", partida_id)
        fing_m = _aplicar_teto_piso(fing_m, 0, LIMITE_SANIDADE_CHUTES_GOL, casa_nome, "finalizacoes_no_gol", partida_id)
        fing_v = _aplicar_teto_piso(fing_v, 0, LIMITE_SANIDADE_CHUTES_GOL, fora_nome, "finalizacoes_no_gol", partida_id)
        falta_m = _aplicar_teto_piso(falta_m, MINIMO_SANIDADE_FALTAS, 40, casa_nome, "faltas", partida_id)
        falta_v = _aplicar_teto_piso(falta_v, MINIMO_SANIDADE_FALTAS, 40, fora_nome, "faltas", partida_id)

        salvar_estatisticas_partida(
            fixture_id=partida_id,
            casa_nome=casa_nome, fora_nome=fora_nome, data_jogo=data_iso,
            escanteios_casa=esc_m, escanteios_fora=esc_v,
            cartoes_casa=cart_m, cartoes_fora=cart_v,
            chutes_casa=fin_m, chutes_fora=fin_v,
            chutes_gol_casa=fing_m, chutes_gol_fora=fing_v,
            faltas_casa=falta_m, faltas_fora=falta_v,
        )

        if esc_m is None and esc_v is None and cart_m is None and cart_v is None:
            sem_dado_esc_cart.append(f"{casa_nome} x {fora_nome}")

        if esc_m is not None: stats_por_time[casa_nome]["escanteios_casa"].append(esc_m)
        if esc_v is not None: stats_por_time[fora_nome]["escanteios_fora"].append(esc_v)
        if cart_m is not None: stats_por_time[casa_nome]["cartoes_casa"].append(cart_m)
        if cart_v is not None: stats_por_time[fora_nome]["cartoes_fora"].append(cart_v)
        if fin_m is not None: stats_por_time[casa_nome]["chutes_casa"].append(fin_m)
        if fin_v is not None: stats_por_time[fora_nome]["chutes_fora"].append(fin_v)
        if fing_m is not None: stats_por_time[casa_nome]["chutes_gol_casa"].append(fing_m)
        if fing_v is not None: stats_por_time[fora_nome]["chutes_gol_fora"].append(fing_v)
        if falta_m is not None: stats_por_time[casa_nome]["faltas_casa"].append(falta_m)
        if falta_v is not None: stats_por_time[fora_nome]["faltas_fora"].append(falta_v)

    print(f"[STATS] {processados_encerrados} jogo(s) encerrado(s) processado(s) na janela "
          f"({inicio} a {fim})")
    if sem_dado_esc_cart:
        print(f"[AVISO] {len(sem_dado_esc_cart)} jogo(s) sem estatística confiável: {', '.join(sem_dado_esc_cart)}")

    salvar_jogos(jogos_pra_salvar)
    _atualizar_stats_times(stats_por_time)


def _atualizar_stats_times(stats_por_time: dict):
    """Só escreve uma coluna quando essa execução achou dado real pra ela —
    sem dado novo, o time mantém o valor que já tinha (não sobrescreve com
    zero/padrão)."""
    if not stats_por_time:
        return
    atualizados = 0
    for nome_time, dados in stats_por_time.items():
        update = {"data_atualizacao": datetime.now().isoformat()}
        if dados["chutes_casa"]: update["fin_casa"] = _media(dados["chutes_casa"])
        if dados["chutes_fora"]: update["fin_fora"] = _media(dados["chutes_fora"])
        if dados["chutes_gol_casa"]: update["fing_casa"] = _media(dados["chutes_gol_casa"])
        if dados["chutes_gol_fora"]: update["fing_fora"] = _media(dados["chutes_gol_fora"])
        if dados["faltas_casa"]: update["falta_casa"] = _media(dados["faltas_casa"])
        if dados["faltas_fora"]: update["falta_fora"] = _media(dados["faltas_fora"])
        if dados["escanteios_casa"]: update["esc_casa"] = _media(dados["escanteios_casa"])
        if dados["escanteios_fora"]: update["esc_fora"] = _media(dados["escanteios_fora"])
        if dados["cartoes_casa"]: update["cart_casa"] = _media(dados["cartoes_casa"])
        if dados["cartoes_fora"]: update["cart_fora"] = _media(dados["cartoes_fora"])

        if len(update) <= 1:  # só tinha data_atualizacao, nenhum dado novo
            continue
        try:
            supabase.table(TABELA_TIMES).update(update).eq("nome", nome_time).execute()
            atualizados += 1
        except Exception as e:
            print(f"[ERRO] _atualizar_stats_times {nome_time}: {e}")
    print(f"[STATS] Estatísticas atualizadas para {atualizados} times.")


# ==========================================================
# NOTÍCIAS -> tabela "noticias_b" (inalterado — não depende da
# API de dados esportivos, continua raspando o GE)
# ==========================================================

def salvar_noticia(texto: str, fonte: str = "sistema"):
    try:
        supabase.table("noticias_b").insert({
            "texto": texto,
            "fonte": fonte,
            "data_atualizacao": datetime.now().isoformat(),
        }).execute()
    except Exception as e:
        print(f"[ERRO] salvar_noticia: {e}")


def coletar_noticias() -> dict:
    noticias = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(
            "https://ge.globo.com/futebol/brasileirao-serie-b/",
            headers=headers, timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.select("a.feed-post-link, h2.post__title a, .bastian-feed-item a"):
            texto = tag.get_text(strip=True)
            if texto and len(texto) > 10:
                noticias.append(texto)
        print(f"[GE] {len(noticias)} notícias coletadas (fonte genérica GE Brasileirão)")
    except Exception as e:
        print(f"[ERRO] coletar_noticias: {e}")

    salvas = 0
    for texto in noticias:
        salvar_noticia(texto, fonte="GE")
        salvas += 1
    return {"coletadas": len(noticias), "salvas": salvas}


# ==========================================================
# FUNÇÃO PRINCIPAL
# ==========================================================

def atualizar_tudo():
    if not API_FUTEBOL_KEY:
        print("[API] API_FUTEBOL_KEY não encontrada (defina o secret no GitHub/'.env' local)")
        return

    print("[COLETOR-B] Iniciando atualização completa (Série B) — fonte: API Futebol")

    times = buscar_tabela()
    if times:
        salvar_times(times)
    time.sleep(1)

    processar_janela()
    time.sleep(1)

    resultado_noticias = coletar_noticias()
    print(f"[NOTICIAS] {resultado_noticias['coletadas']} coletadas, "
          f"{resultado_noticias['salvas']} salvas.")

    print("[COLETOR-B] Atualização concluída!")


if __name__ == "__main__":
    atualizar_tudo()
