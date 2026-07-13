# ==========================================================
# Predix Série A — coletor.py
# Busca tabela e partidas na API dadosfutebol.com.br e grava
# no Supabase deste projeto (tabelas: times, jogos)
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
CAMPEONATO_ID = 3  # Campeonato Brasileiro Série A (confirmado via /v1/campeonatos)


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


# ==========================================================
# TABELA -> tabela "times"
# ==========================================================

def buscar_tabela() -> list:
    data = _get(f"/campeonatos/{CAMPEONATO_ID}/tabela")
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
            resp = supabase.table("times").update(row).eq("nome", row["nome"]).execute()
            if not resp.data:
                supabase.table("times").insert(row).execute()
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
    com as rodadas 5 a 18 já 'encerrada' depois dela. Se confiarmos
    cegamente em status=='andamento', ficamos travados numa rodada
    velha pra sempre.

    Por isso a rodada atual é calculada como "a rodada seguinte à
    última rodada encerrada, em sequência a partir da rodada 1" — não
    depende da flag 'andamento' da API, só da sequência real de rodadas
    já encerradas. Isso é resistente a esse tipo de flag desatualizada.
    """
    data = _get(f"/campeonatos/{CAMPEONATO_ID}/rodadas")
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
    numero_rodada = buscar_numero_rodada_atual()
    params = {"rodada": numero_rodada} if numero_rodada else None

    data = _get(f"/campeonatos/{CAMPEONATO_ID}/partidas", params=params)
    partidas_raw = data.get("data", [])

    # Fallback: se filtrar por rodada não trouxer nada, tenta sem filtro
    if not partidas_raw and numero_rodada:
        print("[API] Nada na rodada filtrada, tentando sem filtro...")
        data = _get(f"/campeonatos/{CAMPEONATO_ID}/partidas")
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
                existe = supabase.table("jogos").select("id") \
                    .eq("fixture_id", row["fixture_id"]).execute()
                if existe.data:
                    supabase.table("jogos").update(row).eq("fixture_id", row["fixture_id"]).execute()
                    salvos += 1
                    continue
            supabase.table("jogos").insert(row).execute()
            salvos += 1
        except Exception as e:
            print(f"[ERRO] salvar_jogos {row.get('casa_nome')} x {row.get('fora_nome')}: {e}")
    print(f"[JOGOS] {salvos} partidas salvas/atualizadas.")


# ==========================================================
# ESTATÍSTICAS DE FINALIZAÇÕES -> tabela "times" (eficiência)
# ==========================================================

def _media(lista: list, fallback: float = 0.0) -> float:
    return round(sum(lista) / len(lista), 2) if lista else fallback


def buscar_stats_por_time() -> dict:
    """
    Percorre as partidas do campeonato e calcula a média de finalizações
    e finalizações no gol por time (casa/fora). Faz uma chamada de API
    por partida (com estatísticas) — pode demorar num campeonato cheio.
    """
    print("[STATS] Buscando partidas para calcular estatísticas...")
    data = _get(f"/campeonatos/{CAMPEONATO_ID}/partidas")
    partidas = data.get("data", [])

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
            })

        pid = p.get("id")
        if not pid:
            continue

        est_data = _get(f"/partidas/{pid}/estatisticas")
        time.sleep(0.3)
        est = est_data.get("data", {})
        if not est:
            continue

        est_m = est.get("mandante", {})
        est_v = est.get("visitante", {})

        fin_m  = float(est_m.get("finalizacoes") or est_m.get("chutes") or est_m.get("shots") or 0)
        fin_v  = float(est_v.get("finalizacoes") or est_v.get("chutes") or est_v.get("shots") or 0)
        fing_m = float(est_m.get("finalizacoes_no_gol") or est_m.get("chutes_a_gol")
                       or est_m.get("shots_on_target") or 0)
        fing_v = float(est_v.get("finalizacoes_no_gol") or est_v.get("chutes_a_gol")
                       or est_v.get("shots_on_target") or 0)

        if fin_m  > 0: stats[nome_m]["finalizacoes_casa"].append(fin_m)
        if fin_v  > 0: stats[nome_v]["finalizacoes_fora"].append(fin_v)
        if fing_m > 0: stats[nome_m]["finalizacoes_gol_casa"].append(fing_m)
        if fing_v > 0: stats[nome_v]["finalizacoes_gol_fora"].append(fing_v)

    print(f"[STATS] Dados de {len(stats)} times processados")
    return stats


def atualizar_stats_times():
    stats = buscar_stats_por_time()
    if not stats:
        return
    atualizados = 0
    for nome_time, dados in stats.items():
        update = {
            "fin_casa":  _media(dados["finalizacoes_casa"], 11.0),
            "fin_fora":  _media(dados["finalizacoes_fora"], 9.5),
            "fing_casa": _media(dados["finalizacoes_gol_casa"], 4.0),
            "fing_fora": _media(dados["finalizacoes_gol_fora"], 3.3),
            "data_atualizacao": datetime.now().isoformat(),
        }
        try:
            supabase.table("times").update(update).eq("nome", nome_time).execute()
            atualizados += 1
        except Exception as e:
            print(f"[ERRO] atualizar_stats_times {nome_time}: {e}")
    print(f"[STATS] Finalizações salvas para {atualizados} times.")


# ==========================================================
# NOTÍCIAS -> tabela "noticias"
# ==========================================================

def salvar_noticia(texto: str, fonte: str = "sistema"):
    try:
        supabase.table("noticias").insert({
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
            "https://ge.globo.com/futebol/brasileirao-serie-a/",
            headers=headers, timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.select("a.feed-post-link, h2.post__title a, .bastian-feed-item a"):
            texto = tag.get_text(strip=True)
            if texto and len(texto) > 10:
                noticias.append(texto)
        print(f"[GE] {len(noticias)} notícias Série A")
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

    print("[COLETOR] Iniciando atualização completa (Série A)...")

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

    print("[COLETOR] Atualização concluída!")


if __name__ == "__main__":
    atualizar_tudo()