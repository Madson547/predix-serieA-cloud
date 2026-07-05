# ==========================================================
# Predictor Pro 3.0
# raspador.py (CORRIGIDO - endpoints validados)
# ==========================================================

import os
import time
import requests
from datetime import datetime, date
from dotenv import load_dotenv
from supabase import create_client
from bs4 import BeautifulSoup

load_dotenv(dotenv_path=".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("SUPABASE_URL ou SUPABASE_KEY não encontrados no .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

DADOS_FUTEBOL_KEY = os.getenv("DADOS_FUTEBOL_KEY")
BASE_URL = "https://api.dadosfutebol.com.br/v1"
CAMPEONATO_ID = 1  # Campeonato Brasileiro Série A (confirmado na doc oficial da API)

def _headers():
    return {
        "Authorization": f"Bearer {DADOS_FUTEBOL_KEY}",
        "Accept": "application/json"
    }

def _get(endpoint: str, params: dict = None) -> dict:
    try:
        resp = requests.get(
            f"{BASE_URL}{endpoint}",
            headers=_headers(),
            params=params,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[ERRO API] {endpoint}: {e}")
        return {}

# ==========================================================
# BUSCAR RODADA ATUAL
# ==========================================================

def buscar_numero_rodada_atual() -> int:
    """
    Busca as rodadas e encontra a atual (status=andamento ou proxima).
    """
    data = _get(f"/campeonatos/{CAMPEONATO_ID}/rodadas")
    rodadas = data.get("data", [])

    # Procura rodada em andamento
    for r in rodadas:
        status = r.get("status", "").lower()
        if "andamento" in status or "atual" in status:
            print(f"[API] Rodada atual: {r.get('nome')} (ID={r.get('id')})")
            return r.get("rodada_numero") or r.get("id")

    # Se não encontrar, pega a próxima
    for r in rodadas:
        status = r.get("status", "").lower()
        if "proxima" in status or "agendada" in status:
            print(f"[API] Próxima rodada: {r.get('nome')}")
            return r.get("rodada_numero") or r.get("id")

    # Fallback: última rodada com partidas
    print("[API] Usando última rodada disponível")
    return len(rodadas)

# ==========================================================
# BUSCAR TABELA
# ==========================================================

def buscar_tabela() -> list:
    data = _get(f"/campeonatos/{CAMPEONATO_ID}/tabela")
    classificacao = data.get("data", {}).get("classificacao", [])
    tabela = []
    for entry in classificacao:
        time_info = entry.get("time", {})
        tabela.append({
            "posicao":     entry.get("posicao"),
            "time":        time_info.get("nome", ""),
            "time_id":     time_info.get("id"),
            "sigla":       time_info.get("sigla", ""),
            "pontos":      entry.get("pontos", 0),
            "jogos":       entry.get("jogos", 0),
            "vitorias":    entry.get("vitorias", 0),
            "empates":     entry.get("empates", 0),
            "derrotas":    entry.get("derrotas", 0),
            "gols_pro":    entry.get("gols_pro", 0),
            "gols_contra": entry.get("gols_contra", 0),
            "saldo":       entry.get("saldo_gols", 0),
        })
    print(f"[API] Tabela: {len(tabela)} times")
    return tabela

# ==========================================================
# BUSCAR PARTIDAS DA RODADA
# ==========================================================

def buscar_rodada(numero: int = None) -> dict:
    """
    Busca partidas de uma rodada específica.
    Se numero=None, busca a rodada atual.
    """
    if numero is None:
        numero = buscar_numero_rodada_atual()

    data = _get(f"/campeonatos/{CAMPEONATO_ID}/partidas", params={"rodada": numero})
    partidas_raw = data.get("data", [])

    # Se não trouxer, tenta sem filtro (últimas partidas)
    if not partidas_raw:
        data = _get(f"/campeonatos/{CAMPEONATO_ID}/partidas")
        partidas_raw = data.get("data", [])

    partidas = []
    for p in partidas_raw:
        mandante  = p.get("time_mandante", {})
        visitante = p.get("time_visitante", {})

        nome_m = mandante.get("nome_popular") or mandante.get("nome", "") if isinstance(mandante, dict) else str(mandante)
        nome_v = visitante.get("nome_popular") or visitante.get("nome", "") if isinstance(visitante, dict) else str(visitante)

        gm = p.get("placar_mandante")
        gv = p.get("placar_visitante")

        if gm is not None and gv is not None:
            status = "encerrada"
        else:
            status = "agendada"

        partidas.append({
            "id":             p.get("id"),
            "rodada":         p.get("rodada_numero", numero),
            "status":         status,
            "data":           p.get("data_realizacao", ""),
            "hora":           p.get("hora_realizacao", ""),
            "estadio":        p.get("estadio", {}).get("nome", "") if isinstance(p.get("estadio"), dict) else "",
            "mandante":       nome_m,
            "mandante_id":    mandante.get("id") if isinstance(mandante, dict) else None,
            "visitante":      nome_v,
            "visitante_id":   visitante.get("id") if isinstance(visitante, dict) else None,
            "gols_mandante":  gm,
            "gols_visitante": gv,
        })

    print(f"[API] Rodada {numero}: {len(partidas)} partidas")
    return {"numero": numero, "partidas": partidas}

# ==========================================================
# BUSCAR ESTATÍSTICAS REAIS POR TIME
# ==========================================================

def buscar_stats_por_time() -> dict:
    """
    Busca todas as partidas disponíveis e calcula
    médias reais de escanteios e cartões por time.
    """
    print("[STATS] Buscando partidas para calcular estatísticas...")

    data = _get(f"/campeonatos/{CAMPEONATO_ID}/partidas")
    partidas = data.get("data", [])

    if not partidas:
        print("[STATS] Nenhuma partida encontrada.")
        return {}

    stats = {}

    for p in partidas:
        mandante  = p.get("time_mandante", {})
        visitante = p.get("time_visitante", {})

        nome_m = mandante.get("nome_popular") or mandante.get("nome", "") if isinstance(mandante, dict) else ""
        nome_v = visitante.get("nome_popular") or visitante.get("nome", "") if isinstance(visitante, dict) else ""

        if not nome_m or not nome_v:
            continue

        for nome in [nome_m, nome_v]:
            if nome not in stats:
                stats[nome] = {
                    "escanteios_casa": [], "escanteios_fora": [],
                    "cartoes_casa":    [], "cartoes_fora": [],
                    "gols_casa":       [], "gols_fora": [],
                    "finalizacoes_casa": [], "finalizacoes_fora": [],
                    "finalizacoes_gol_casa": [], "finalizacoes_gol_fora": [],
                }

        gm = p.get("placar_mandante")
        gv = p.get("placar_visitante")

        # Gols
        if gm is not None:
            stats[nome_m]["gols_casa"].append(float(gm))
        if gv is not None:
            stats[nome_v]["gols_fora"].append(float(gv))

        # Estatísticas detalhadas da partida
        pid = p.get("id")
        if pid:
            est_data = _get(f"/partidas/{pid}/estatisticas")
            est = est_data.get("data", {})
            time.sleep(0.3)

            if est:
                est_m = est.get("mandante", {})
                est_v = est.get("visitante", {})

                esc_m  = float(est_m.get("escanteios") or est_m.get("corners") or 0)
                esc_v  = float(est_v.get("escanteios") or est_v.get("corners") or 0)
                cart_m = float(est_m.get("cartoes_amarelos") or est_m.get("yellow_cards") or 0)
                cart_v = float(est_v.get("cartoes_amarelos") or est_v.get("yellow_cards") or 0)

                # Finalizações (chutes totais) e finalizações no gol (chutes a gol)
                fin_m  = float(est_m.get("finalizacoes") or est_m.get("chutes") or est_m.get("shots") or 0)
                fin_v  = float(est_v.get("finalizacoes") or est_v.get("chutes") or est_v.get("shots") or 0)
                fing_m = float(est_m.get("finalizacoes_no_gol") or est_m.get("chutes_a_gol")
                               or est_m.get("shots_on_target") or 0)
                fing_v = float(est_v.get("finalizacoes_no_gol") or est_v.get("chutes_a_gol")
                               or est_v.get("shots_on_target") or 0)

                if esc_m > 0: stats[nome_m]["escanteios_casa"].append(esc_m)
                if esc_v > 0: stats[nome_v]["escanteios_fora"].append(esc_v)
                if cart_m > 0: stats[nome_m]["cartoes_casa"].append(cart_m)
                if cart_v > 0: stats[nome_v]["cartoes_fora"].append(cart_v)
                if fin_m > 0: stats[nome_m]["finalizacoes_casa"].append(fin_m)
                if fin_v > 0: stats[nome_v]["finalizacoes_fora"].append(fin_v)
                if fing_m > 0: stats[nome_m]["finalizacoes_gol_casa"].append(fing_m)
                if fing_v > 0: stats[nome_v]["finalizacoes_gol_fora"].append(fing_v)

    print(f"[STATS] Dados de {len(stats)} times processados")
    return stats

def media(lista: list, fallback: float = 0.0) -> float:
    return round(sum(lista) / len(lista), 2) if lista else fallback

def atualizar_stats_supabase():
    stats = buscar_stats_por_time()
    if not stats:
        return

    for nome_time, dados in stats.items():
        try:
            update = {
                "c_casa":    media(dados["escanteios_casa"], 5.0),
                "c_fora":    media(dados["escanteios_fora"], 4.5),
                "cart_casa": media(dados["cartoes_casa"],    2.0),
                "cart_fora": media(dados["cartoes_fora"],    2.0),
                "fin_casa":  media(dados["finalizacoes_casa"], 11.0),
                "fin_fora":  media(dados["finalizacoes_fora"], 9.5),
                "fing_casa": media(dados["finalizacoes_gol_casa"], 4.0),
                "fing_fora": media(dados["finalizacoes_gol_fora"], 3.3),
                "data_atualizacao": datetime.now().isoformat()
            }

            # Gols também se disponível
            if dados["gols_casa"]:
                update["g_casa"] = media(dados["gols_casa"])
            if dados["gols_fora"]:
                update["g_fora"] = media(dados["gols_fora"])

            resp = supabase.table("estatisticas_times") \
                .update(update).eq("time", nome_time).execute()

            if not resp.data:
                update["time"] = nome_time
                supabase.table("estatisticas_times").insert(update).execute()

            print(f"[STATS] {nome_time}: esc={update['c_casa']}/{update['c_fora']} cart={update['cart_casa']}/{update['cart_fora']} "
                  f"fin={update['fin_casa']}/{update['fin_fora']} fing={update['fing_casa']}/{update['fing_fora']}")

        except Exception as e:
            print(f"[ERRO] {nome_time}: {e}")

    print("[STATS] Estatísticas salvas!")

# ==========================================================
# SALVAR TABELA
# ==========================================================

def salvar_tabela(tabela: list):
    if not tabela:
        return
    try:
        supabase.table("tabela_serie_a").delete().neq("posicao", 0).execute()
    except Exception:
        pass
    for row in tabela:
        row["data_atualizacao"] = datetime.now().isoformat()
        supabase.table("tabela_serie_a").insert(row).execute()
    print(f"[TABELA] {len(tabela)} times salvos.")

# ==========================================================
# SALVAR RODADA
# ==========================================================

def salvar_rodada(rodada: dict):
    partidas = rodada.get("partidas", [])
    if not partidas:
        print("[RODADA] Nenhuma partida para salvar.")
        return
    try:
        supabase.table("rodada_atual").delete().neq("id", 0).execute()
    except Exception:
        pass
    for p in partidas:
        p["data_atualizacao"] = datetime.now().isoformat()
        supabase.table("rodada_atual").insert(p).execute()
    print(f"[RODADA] {len(partidas)} partidas salvas.")

# ==========================================================
# NOTÍCIAS SÉRIE A
# ==========================================================

def salvar_noticia(texto: str, fonte: str = "sistema"):
    try:
        supabase.table("noticias_bastidores").insert({
            "texto": texto,
            "fonte": fonte,
            "data_atualizacao": datetime.now().isoformat()
        }).execute()
    except Exception as e:
        print(f"[ERRO] salvar_noticia: {e}")

def coletar_ge() -> list:
    noticias = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(
            "https://ge.globo.com/futebol/brasileirao-serie-a/",
            headers=headers, timeout=10
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.select("a.feed-post-link, h2.post__title a, .bastian-feed-item a"):
            texto = tag.get_text(strip=True)
            if texto and len(texto) > 10:
                noticias.append(texto)
        print(f"[GE] {len(noticias)} notícias Série A")
    except Exception as e:
        print(f"[ERRO] coletar_ge: {e}")
    return noticias

def atualizar_feed():
    print("[FEED] Buscando notícias Série A...")
    coletadas = coletar_ge()
    salvas = 0
    for texto in coletadas:
        salvar_noticia(texto, fonte="GE")
        salvas += 1
    print(f"[FEED] {salvas} notícias salvas.")
    return {"coletadas": len(coletadas), "salvas": salvas}

# ==========================================================
# FUNÇÃO PRINCIPAL
# ==========================================================

def atualizar_dados_api():
    if not DADOS_FUTEBOL_KEY:
        print("[API] DADOS_FUTEBOL_KEY não encontrada no .env")
        return

    print("[API] Iniciando atualização completa...")

    # 1. Tabela
    tabela = buscar_tabela()
    if tabela:
        salvar_tabela(tabela)
    time.sleep(1)

    # 2. Rodada atual
    rodada = buscar_rodada()
    if rodada.get("partidas"):
        salvar_rodada(rodada)
    time.sleep(1)

    # 3. Estatísticas reais
    atualizar_stats_supabase()

    print("[API] Atualização completa concluída!")