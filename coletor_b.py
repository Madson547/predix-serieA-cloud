# ==========================================================
# Predix Série B — coletor_b.py
# Adaptado do coletor.py da Série A (mesma lógica, mesma janela
# de rodadas), gravando em tabelas separadas (times_b, jogos_b)
# pra não misturar dado com o pipeline da Série A.
#
# DIFERENÇA-CHAVE: o CAMPEONATO_ID não é fixo aqui. A API não
# documenta uma lista estática de IDs (são gerados no banco dela
# e podem mudar de temporada pra temporada), então descobrimos o
# ID certo em tempo de execução buscando por nome em /v1/campeonatos
# — evita chutar um número errado e também sobrevive caso a API
# renumere os campeonatos no futuro.
# ==========================================================

import os
import re
import time
from datetime import datetime
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from database import supabase

load_dotenv()

DADOS_FUTEBOL_KEY = os.getenv("DADOS_FUTEBOL_KEY")
BASE_URL = "https://api.dadosfutebol.com.br/v1"
CAMPEONATO_NOME_BUSCA = "série b"  # usado pra localizar o campeonato certo por nome
TABELA_JOGOS = "jogos_b"
TABELA_TIMES = "times_b"
TABELA_ESTATISTICAS_PARTIDAS = "estatisticas_partidas_b"

_CAMPEONATO_ID_CACHE = None  # preenchido na primeira chamada de _campeonato_id()


def _normalizar_data(data_str: str) -> str | None:
    """
    A API devolve datas em formatos variados (ex: 'DD/MM/AAAA' ou já ISO).
    O Postgres exige 'AAAA-MM-DD' — sem converter, dias >12 quebram o insert
    (ex: '28/01/2026' é lido como mês=28, erro 22008).
    """
    if not data_str:
        return None
    data_str = data_str.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(data_str[:19] if "T" in fmt else data_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    print(f"[AVISO] Formato de data não reconhecido: '{data_str}'")
    return None


def _headers():
    return {
        "Authorization": f"Bearer {DADOS_FUTEBOL_KEY}",
        "Accept": "application/json",
    }


def _get(endpoint: str, params: dict = None) -> dict:
    try:
        resp = requests.get(f"{BASE_URL}{endpoint}", headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[ERRO API] {endpoint}: {e}")
        return {}


def _campeonato_id() -> int | None:
    """
    Descobre (e cacheia em memória, só durante a execução do processo) o ID
    numérico do campeonato "Série B" da temporada atual, buscando por nome
    em /v1/campeonatos em vez de depender de um número fixo. Casa por
    'nome', 'nome_popular' ou 'slug' contendo "série b"/"serie-b" — a API
    normaliza acentos de forma inconsistente entre esses três campos, então
    checamos os três.
    """
    global _CAMPEONATO_ID_CACHE
    if _CAMPEONATO_ID_CACHE is not None:
        return _CAMPEONATO_ID_CACHE

    ano_atual = datetime.now().strftime("%Y")
    data = _get("/campeonatos", params={"temporada": ano_atual})
    campeonatos = data.get("data", [])

    for c in campeonatos:
        candidatos_nome = [
            (c.get("nome") or "").lower(),
            (c.get("nome_popular") or "").lower(),
            (c.get("slug") or "").lower(),
        ]
        if any("série b" in n or "serie b" in n or "serie-b" in n for n in candidatos_nome):
            _CAMPEONATO_ID_CACHE = c.get("id")
            print(f"[API] Campeonato Série B identificado: id={_CAMPEONATO_ID_CACHE} "
                  f"(nome='{c.get('nome')}')")
            return _CAMPEONATO_ID_CACHE

    print(f"[ERRO] Não encontrei 'Série B' na lista de campeonatos da temporada {ano_atual}. "
          f"Campeonatos disponíveis: {[c.get('nome') for c in campeonatos]}")
    return None


# ==========================================================
# TABELA -> tabela "times"
# ==========================================================

def buscar_tabela() -> list:
    cid = _campeonato_id()
    if cid is None:
        return []
    data = _get(f"/campeonatos/{cid}/tabela")
    classificacao = data.get("data", {}).get("classificacao", [])
    times = []
    for entry in classificacao:
        time_info = entry.get("time", {})
        gp = entry.get("gols_pro", 0)
        gc = entry.get("gols_contra", 0)
        times.append({
            "nome":    time_info.get("nome_popular") or time_info.get("nome", ""),
            "posicao": entry.get("posicao"),
            "pts":     entry.get("pontos", 0),
            "j":       entry.get("jogos", 0),
            "v":       entry.get("vitorias", 0),
            "e":       entry.get("empates", 0),
            "d":       entry.get("derrotas", 0),
            "gm":      gp,
            "gc":      gc,
            "sg":      entry.get("saldo_gols", gp - gc),
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
# RODADA ATUAL
# ==========================================================

def _numero_da_rodada(rodada: dict):
    """
    A API tem um 'id' interno (ex: 176) que NÃO é o número da rodada.
    O número real geralmente vem no campo 'rodada_numero'/'numero', ou
    pode ser extraído do nome (ex: '4ª Rodada' -> 4).
    """
    for campo in ("rodada_numero", "numero", "rodada"):
        val = rodada.get(campo)
        if isinstance(val, int):
            return val
    nome = rodada.get("nome") or ""
    m = re.search(r"(\d+)", nome)
    if m:
        return int(m.group(1))
    return None


def buscar_numero_rodada_atual() -> int | None:
    """
    Identifica a rodada atual do campeonato.

    IMPORTANTE (bug confirmado em 2026-07 via diagnostico_rodada.py):
    a API às vezes deixa uma rodada ANTIGA com status 'andamento'
    desatualizado — ex: a "4ª Rodada" aparecia como 'andamento' mesmo
    com as rodadas 5 a 18 já 'encerrada' depois dela (causa real: jogo
    atrasado daquela rodada, tipo Bahia x Chapecoense). Se confiarmos
    cegamente em status=='andamento', ficamos travados numa rodada
    velha pra sempre.

    Por isso a rodada atual é calculada como "a rodada seguinte à
    última rodada encerrada, em sequência a partir da rodada 1" — não
    depende da flag 'andamento' da API, só da sequência real de rodadas
    já encerradas. Isso é resistente a esse tipo de flag desatualizada.
    """
    cid = _campeonato_id()
    if cid is None:
        return None
    data = _get(f"/campeonatos/{cid}/rodadas")
    rodadas = data.get("data", [])

    numeradas = []
    for r in rodadas:
        numero = _numero_da_rodada(r)
        status = (r.get("status") or "").lower()
        if numero is not None:
            numeradas.append((numero, status, r.get("nome")))

    if not numeradas:
        print("[API] Não foi possível identificar a rodada atual (sem dados numerados).")
        return None

    numeradas.sort(key=lambda item: item[0])

    ultima_encerrada = 0
    for numero, status, _ in numeradas:
        if "encerrada" in status or "encerrado" in status:
            ultima_encerrada = max(ultima_encerrada, numero)

    candidato = ultima_encerrada + 1
    if any(numero == candidato for numero, _, _ in numeradas):
        print(f"[API] Rodada atual calculada: {candidato}ª Rodada (última encerrada: {ultima_encerrada}ª)")
        return candidato

    # Fallback: candidato não existe na lista por algum motivo — usa a
    # primeira rodada 'agendada' encontrada.
    for numero, status, nome in numeradas:
        if "agendada" in status or "agendado" in status:
            print(f"[API] Fallback — primeira rodada agendada encontrada: {nome}")
            return numero

    print("[API] Não foi possível identificar a rodada atual com segurança.")
    return None


# ==========================================================
# PARTIDAS -> tabela "jogos"
# ==========================================================

def buscar_partidas() -> list:
    """
    CORREÇÃO (31/07/2026 — confirmado via consulta direta na tabela `jogos`):
    buscar só a "rodada calculada como atual" tinha o mesmo problema que já
    tínhamos corrigido em buscar_stats_por_time() — a API pode marcar uma
    rodada como 'encerrada' antes de TODOS os jogos dela realmente
    terminarem (jogo atrasado/remarcado no meio da rodada). Quando isso
    acontece, buscar_numero_rodada_atual() pula pra rodada seguinte e a
    rodada anterior — com jogos que ainda estavam 'agendado' no nosso
    banco — nunca mais é revisitada. Resultado confirmado: 10 jogos
    ficaram travados em status='agendado' desde 28/07, mesmo depois de
    vários já terem sido encerrados de verdade (ex: Coritiba x Cruzeiro,
    Corinthians x Athletico Paranaense).

    Agora busca uma JANELA de rodadas (atual + 2 anteriores), igual ao
    princípio já usado em buscar_stats_por_time() — assim qualquer jogo
    "esquecido" numa rodada anterior é reprocessado e tem status/placar
    atualizados nas próximas execuções do coletor.
    """
    cid = _campeonato_id()
    if cid is None:
        return []

    numero_rodada = buscar_numero_rodada_atual()
    rodadas = list(range(max(1, numero_rodada - 2), numero_rodada + 1)) if numero_rodada else None

    partidas_raw = []
    vistos = set()

    if rodadas:
        for r in rodadas:
            data = _get(f"/campeonatos/{cid}/partidas", params={"rodada": r})
            for p in data.get("data", []):
                pid = p.get("id")
                if pid in vistos:
                    continue  # evita duplicar caso a mesma partida apareça em mais de uma rodada consultada
                vistos.add(pid)
                partidas_raw.append(p)
            time.sleep(0.2)
        print(f"[API] Janela de rodadas consultada: {rodadas}")
    else:
        data = _get(f"/campeonatos/{cid}/partidas")
        partidas_raw = data.get("data", [])

    # Fallback: se a janela toda não trouxer nada, tenta sem filtro
    if not partidas_raw:
        print("[API] Nada nas rodadas filtradas, tentando sem filtro...")
        data = _get(f"/campeonatos/{cid}/partidas")
        partidas_raw = data.get("data", [])

    jogos = []
    for p in partidas_raw:
        mandante  = p.get("time_mandante", {})
        visitante = p.get("time_visitante", {})
        nome_m = (mandante.get("nome_popular") or mandante.get("nome", "")) if isinstance(mandante, dict) else str(mandante)
        nome_v = (visitante.get("nome_popular") or visitante.get("nome", "")) if isinstance(visitante, dict) else str(visitante)

        gm = p.get("placar_mandante")
        gv = p.get("placar_visitante")
        status = "encerrado" if gm is not None and gv is not None else "agendado"

        jogos.append({
            "fixture_id":  p.get("id"),
            "casa_nome":   nome_m,
            "fora_nome":   nome_v,
            "data":        _normalizar_data(p.get("data_realizacao", "")),
            "hora":        p.get("hora_realizacao", ""),
            "status":      status,
            "status_desc": "Encerrado" if status == "encerrado" else "Agendado",
            "gols_casa":   gm,
            "gols_fora":   gv,
        })

    print(f"[API] Partidas: {len(jogos)} encontradas")
    return jogos


def salvar_jogos(jogos: list):
    if not jogos:
        return
    salvos = 0
    for row in jogos:
        if not row.get("casa_nome") or not row.get("fora_nome"):
            continue
        row["data_atualizacao"] = datetime.now().isoformat()
        try:
            if row.get("fixture_id"):
                existe = supabase.table(TABELA_JOGOS).select("id") \
                    .eq("fixture_id", row["fixture_id"]).execute()
                if existe.data:
                    supabase.table(TABELA_JOGOS).update(row).eq("fixture_id", row["fixture_id"]).execute()
                    salvos += 1
                    continue
            supabase.table(TABELA_JOGOS).insert(row).execute()
            salvos += 1
        except Exception as e:
            print(f"[ERRO] salvar_jogos {row.get('casa_nome')} x {row.get('fora_nome')}: {e}")
    print(f"[JOGOS] {salvos} partidas salvas/atualizadas.")


# ==========================================================
# ESTATÍSTICAS DE FINALIZAÇÕES -> tabela "times" (eficiência)
# ==========================================================

def _media(lista: list, fallback: float = 0.0) -> float:
    return round(sum(lista) / len(lista), 2) if lista else fallback


def salvar_estatisticas_partida(
    fixture_id, casa_nome, fora_nome, data_jogo,
    escanteios_casa, escanteios_fora, cartoes_casa, cartoes_fora,
    chutes_casa, chutes_fora, chutes_gol_casa, chutes_gol_fora,
    faltas_casa, faltas_fora,
):
    """
    Grava (upsert por fixture_id) o valor REAL de escanteios/cartões/
    chutes/faltas daquela partida específica — não a média móvel do
    time. Mesma lógica do coletor.py (Série A), apontando pra
    estatisticas_partidas_b.
    """
    if not fixture_id:
        return
    try:
        supabase.table(TABELA_ESTATISTICAS_PARTIDAS).upsert({
            "fixture_id": fixture_id,
            "casa_nome": casa_nome,
            "fora_nome": fora_nome,
            "data": data_jogo,
            "escanteios_casa": escanteios_casa or None,
            "escanteios_fora": escanteios_fora or None,
            "cartoes_casa": cartoes_casa or None,
            "cartoes_fora": cartoes_fora or None,
            "chutes_casa": chutes_casa or None,
            "chutes_fora": chutes_fora or None,
            "chutes_gol_casa": chutes_gol_casa or None,
            "chutes_gol_fora": chutes_gol_fora or None,
            "faltas_casa": faltas_casa or None,
            "faltas_fora": faltas_fora or None,
            "data_atualizacao": datetime.now().isoformat(),
        }, on_conflict="fixture_id").execute()
    except Exception as e:
        print(f"[ERRO] salvar_estatisticas_partida (fixture_id={fixture_id}): {e}")


def buscar_stats_por_time(rodadas: list[int] | None = None) -> dict:
    """
    Percorre as partidas das rodadas selecionadas e calcula, por time
    (casa/fora): médias de finalizações, finalizações no gol, escanteios
    e cartões amarelos. Faz uma chamada de API por partida (com
    estatísticas) — cada rodada processada consome ~10 chamadas extras.

    CORREÇÃO 1 (26/07/2026 — diagnosticado via diagnostico_campos_api.py):
    o JSON real da API vem como data -> estatisticas -> mandante/visitante,
    não data -> mandante/visitante direto. Faltava o nível intermediário
    "estatisticas", então est_m/est_v SEMPRE vinham vazios ({}), e todo
    campo (chutes, chutes no gol, cartões — escanteios também, na prática)
    caía no fallback fixo do poisson.py (11.0/9.5/4.0/3.3/2.2/2.3). Também
    corrigido o nome do campo de finalizações: é "finalizacoes_total", não
    "finalizacoes"/"chutes"/"shots".

    CORREÇÃO 2 (26/07/2026 — diagnosticado via teste_stats_isolado.py):
    a chamada SEM filtro de rodada (`/campeonatos/{id}/partidas`) só
    devolve um lote fixo de ~15-20 partidas ANTIGAS (jan/fev, início da
    temporada) — nunca as rodadas atuais. Por isso a função agora busca
    partida por partida, RODADA POR RODADA (`?rodada=N`), do mesmo jeito
    que buscar_partidas() já faz pra pegar a rodada atual.

    Args:
        rodadas: lista de números de rodada a processar.
            - None (padrão) = modo INCREMENTAL: só as últimas 4 rodadas
              a partir da atual. Pensado pra rodar automaticamente todo
              dia sem gastar cota da API refazendo jogos antigos que já
              não mudam mais.
            - lista explícita (ex: list(range(1, 21))) = modo BACKFILL:
              processa exatamente essas rodadas. Usado uma única vez pelo
              backfill_stats_completo.py pra preencher o histórico inteiro.

    NOTA: mesmo com as correções, "cartoes_amarelos" pode vir null da
    própria API em algumas partidas (confirmado no JSON bruto) — isso é
    limitação de dado da fonte, não bug nosso. O código já trata isso
    corretamente (só adiciona à lista se o valor for > 0), então partidas
    sem esse dado simplesmente não entram na média, em vez de contaminar
    com zero falso.
    """
    cid = _campeonato_id()
    if cid is None:
        return {}

    if rodadas is None:
        rodada_atual = buscar_numero_rodada_atual() or 1
        rodadas = list(range(max(1, rodada_atual - 3), rodada_atual + 1))
        print(f"[STATS] Modo incremental — processando rodadas {rodadas}")
    else:
        print(f"[STATS] Modo backfill — processando {len(rodadas)} rodada(s): {rodadas}")

    partidas = []
    for rodada in rodadas:
        data = _get(f"/campeonatos/{cid}/partidas", params={"rodada": rodada})
        partidas_rodada = data.get("data", [])
        # Só partidas já encerradas têm estatística disponível — pular
        # jogos futuros evita chamada desperdiçada.
        encerradas_rodada = [
            p for p in partidas_rodada
            if p.get("placar_mandante") is not None and p.get("placar_visitante") is not None
        ]
        partidas.extend(encerradas_rodada)
        time.sleep(0.2)

    print(f"[STATS] {len(partidas)} partida(s) encerrada(s) encontrada(s) nas rodadas selecionadas")

    stats = {}
    for p in partidas:
        mandante  = p.get("time_mandante", {})
        visitante = p.get("time_visitante", {})
        nome_m = (mandante.get("nome_popular") or mandante.get("nome", "")) if isinstance(mandante, dict) else ""
        nome_v = (visitante.get("nome_popular") or visitante.get("nome", "")) if isinstance(visitante, dict) else ""
        if not nome_m or not nome_v:
            continue

        for nome in (nome_m, nome_v):
            stats.setdefault(nome, {
                "finalizacoes_casa": [], "finalizacoes_fora": [],
                "finalizacoes_gol_casa": [], "finalizacoes_gol_fora": [],
                "escanteios_casa": [], "escanteios_fora": [],
                "cartoes_casa": [], "cartoes_fora": [],
                "faltas_casa": [], "faltas_fora": [],
            })

        pid = p.get("id")
        if not pid:
            continue

        est_data = _get(f"/partidas/{pid}/estatisticas")
        time.sleep(0.3)
        # DEBUG TEMPORÁRIO — remover depois de confirmar o payload
        if pid in (3269, 3281):
            import json
            print(f"[DEBUG-FALTAS] fixture_id={pid} payload cru:\n{json.dumps(est_data, indent=2, ensure_ascii=False)}")

        # CORREÇÃO 1: nível "estatisticas" estava faltando na leitura.
        # Estrutura real: {"data": {"estatisticas": {"mandante": {...}, "visitante": {...}}}}
        est_raiz = est_data.get("data", {})
        est = est_raiz.get("estatisticas", {})
        if not est:
            continue

        est_m = est.get("mandante", {})
        est_v = est.get("visitante", {})

        # CORREÇÃO: nome real do campo é "finalizacoes_total".
        fin_m  = float(est_m.get("finalizacoes_total") or est_m.get("finalizacoes")
                       or est_m.get("chutes") or est_m.get("shots") or 0)
        fin_v  = float(est_v.get("finalizacoes_total") or est_v.get("finalizacoes")
                       or est_v.get("chutes") or est_v.get("shots") or 0)
        fing_m = float(est_m.get("finalizacoes_no_gol") or est_m.get("chutes_a_gol")
                       or est_m.get("shots_on_target") or 0)
        fing_v = float(est_v.get("finalizacoes_no_gol") or est_v.get("chutes_a_gol")
                       or est_v.get("shots_on_target") or 0)
        esc_m  = float(est_m.get("escanteios") or est_m.get("corners") or 0)
        esc_v  = float(est_v.get("escanteios") or est_v.get("corners") or 0)
        cart_m = float(est_m.get("cartoes_amarelos") or est_m.get("yellow_cards") or 0)
        cart_v = float(est_v.get("cartoes_amarelos") or est_v.get("yellow_cards") or 0)
        falta_m = float(est_m.get("faltas") or 0)
        falta_v = float(est_v.get("faltas") or 0)

        # SANIDADE (28/07/2026 — outlier confirmado: Fluminense apareceu com
        # fin_casa=25.0, quase o dobro do range real de ~9-18 chutes/jogo dos
        # outros times). Uma partida raramente passa de ~35 finalizações de
        # um time só, ou ~20 finalizações no gol — valores acima disso são
        # quase certamente erro da própria API (ex: contagem cumulativa em
        # vez de só daquela partida). Descarta em vez de contaminar a média.
        LIMITE_SANIDADE_CHUTES = 35
        LIMITE_SANIDADE_CHUTES_GOL = 20

        if fin_m > LIMITE_SANIDADE_CHUTES:
            print(f"[AVISO] {nome_m}: finalizacoes_total suspeito ({fin_m}) na partida id={pid}, descartado")
            fin_m = 0
        if fin_v > LIMITE_SANIDADE_CHUTES:
            print(f"[AVISO] {nome_v}: finalizacoes_total suspeito ({fin_v}) na partida id={pid}, descartado")
            fin_v = 0
        if fing_m > LIMITE_SANIDADE_CHUTES_GOL:
            fing_m = 0
        if fing_v > LIMITE_SANIDADE_CHUTES_GOL:
            fing_v = 0

        if fin_m  > 0: stats[nome_m]["finalizacoes_casa"].append(fin_m)
        if fin_v  > 0: stats[nome_v]["finalizacoes_fora"].append(fin_v)
        if fing_m > 0: stats[nome_m]["finalizacoes_gol_casa"].append(fing_m)
        if fing_v > 0: stats[nome_v]["finalizacoes_gol_fora"].append(fing_v)
        if esc_m  > 0: stats[nome_m]["escanteios_casa"].append(esc_m)
        if esc_v  > 0: stats[nome_v]["escanteios_fora"].append(esc_v)
        if cart_m > 0: stats[nome_m]["cartoes_casa"].append(cart_m)
        if cart_v > 0: stats[nome_v]["cartoes_fora"].append(cart_v)
        if falta_m > 0: stats[nome_m]["faltas_casa"].append(falta_m)
        if falta_v > 0: stats[nome_v]["faltas_fora"].append(falta_v)

        salvar_estatisticas_partida(
            fixture_id=pid,
            casa_nome=nome_m, fora_nome=nome_v,
            data_jogo=_normalizar_data(p.get("data_realizacao", "")),
            escanteios_casa=esc_m, escanteios_fora=esc_v,
            cartoes_casa=cart_m, cartoes_fora=cart_v,
            chutes_casa=fin_m, chutes_fora=fin_v,
            chutes_gol_casa=fing_m, chutes_gol_fora=fing_v,
            faltas_casa=falta_m, faltas_fora=falta_v,
        )

    print(f"[STATS] Dados de {len(stats)} times processados")
    return stats


def atualizar_stats_times(rodadas: list[int] | None = None):
    """
    Atualiza fin_/fing_/esc_/cart_ por time — MAS só escreve uma coluna se
    essa execução realmente achou dado real pra ela. Antes, quando a API
    não devolvia nada (lista vazia), o código escrevia o valor padrão
    (5.2/4.8/2.2/2.3) mesmo assim — isso apagava qualquer dado bom que já
    estivesse lá (seja de uma coleta anterior bem-sucedida, seja um valor
    que você tenha colocado manualmente no Supabase). Agora, sem dado novo,
    a coluna simplesmente não é tocada — o time mantém o que já tinha.

    Args:
        rodadas: repassado direto pra buscar_stats_por_time(). None = modo
            incremental (últimas 4 rodadas). Lista explícita = backfill.
    """
    stats = buscar_stats_por_time(rodadas=rodadas)
    if not stats:
        return

    atualizados = 0
    sem_esc_cart = []  # times que ficaram sem dado real de escanteios/cartões nesta execução

    for nome_time, dados in stats.items():
        update = {"data_atualizacao": datetime.now().isoformat()}

        if dados["finalizacoes_casa"]:
            update["fin_casa"] = _media(dados["finalizacoes_casa"])
        if dados["finalizacoes_fora"]:
            update["fin_fora"] = _media(dados["finalizacoes_fora"])
        if dados["finalizacoes_gol_casa"]:
            update["fing_casa"] = _media(dados["finalizacoes_gol_casa"])
        if dados["finalizacoes_gol_fora"]:
            update["fing_fora"] = _media(dados["finalizacoes_gol_fora"])
        if dados["faltas_casa"]:
            update["falta_casa"] = _media(dados["faltas_casa"])
        if dados["faltas_fora"]:
            update["falta_fora"] = _media(dados["faltas_fora"])

        tem_esc = bool(dados["escanteios_casa"] or dados["escanteios_fora"])
        tem_cart = bool(dados["cartoes_casa"] or dados["cartoes_fora"])
        if dados["escanteios_casa"]:
            update["esc_casa"] = _media(dados["escanteios_casa"])
        if dados["escanteios_fora"]:
            update["esc_fora"] = _media(dados["escanteios_fora"])
        if dados["cartoes_casa"]:
            update["cart_casa"] = _media(dados["cartoes_casa"])
        if dados["cartoes_fora"]:
            update["cart_fora"] = _media(dados["cartoes_fora"])

        if not (tem_esc and tem_cart):
            sem_esc_cart.append(nome_time)

        try:
            supabase.table(TABELA_TIMES).update(update).eq("nome", nome_time).execute()
            atualizados += 1
        except Exception as e:
            print(f"[ERRO] atualizar_stats_times {nome_time}: {e}")

    print(f"[STATS] Finalizações/escanteios/cartões atualizados para {atualizados} times.")

    if sem_esc_cart:
        print(f"[AVISO] {len(sem_esc_cart)} time(s) SEM dado real de escanteios/cartões "
              f"(usando o que já existia — padrão ou manual): {', '.join(sem_esc_cart)}")


# ==========================================================
# NOTÍCIAS -> tabela "noticias"
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
    if not DADOS_FUTEBOL_KEY:
        print("[API] DADOS_FUTEBOL_KEY não encontrada no .env")
        return

    print("[COLETOR-B] Iniciando atualização completa (Série B)...")

    times = buscar_tabela()
    if times:
        salvar_times(times)
    time.sleep(1)

    jogos = buscar_partidas()
    if jogos:
        salvar_jogos(jogos)
    time.sleep(1)

    atualizar_stats_times()
    time.sleep(1)

    resultado_noticias = coletar_noticias()
    print(f"[NOTICIAS] {resultado_noticias['coletadas']} coletadas, "
          f"{resultado_noticias['salvas']} salvas.")

    print("[COLETOR-B] Atualização concluída!")


if __name__ == "__main__":
    atualizar_tudo()
