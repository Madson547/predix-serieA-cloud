# ==========================================================
# TESTE COMPARATIVO — API Futebol (api-futebol.com.br)
# Script isolado, NÃO faz parte do pipeline principal do Predix.
# Objetivo: buscar jogos problemáticos já documentados (Sport x
# Cuiabá, Série B) nessa API alternativa e comparar com o que a
# Dados Futebol devolveu, pra decidir se vale migrar.
# ==========================================================

import os
import json
import requests

API_KEY = os.getenv("API_FUTEBOL_KEY")
BASE_URL = "https://api.api-futebol.com.br/v1"


def _headers():
    return {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}


def _get(endpoint, params=None):
    resp = requests.get(f"{BASE_URL}{endpoint}", headers=_headers(), params=params, timeout=15)
    print(f"[GET] {endpoint} -> status {resp.status_code}")
    try:
        corpo = resp.json()
    except Exception:
        corpo = {}
        print(f"[AVISO] resposta não-JSON: {resp.text[:300]}")
    return resp.status_code, corpo


def main():
    if not API_KEY:
        print("[ERRO] API_FUTEBOL_KEY não encontrada (defina como secret/env).")
        return

    # campeonato_id da Série B já confirmado numa execução anterior (14 —
    # tabela retornou Criciúma no topo). Fixando aqui pra não gastar cota
    # testando de novo 1-30 a cada execução.
    serie_b_id = 14
    print(f"=== campeonato_id da Série B: {serie_b_id} (já confirmado) ===")

    # 2. Lista partidas do campeonato pra achar Criciúma x Goiás (15/08/2026)
    print("\n=== 2. Buscando partida Criciúma x Goiás ===")
    status, partidas = _get(f"/campeonatos/{serie_b_id}/partidas")
    if status != 200:
        print(f"[ERRO] /campeonatos/{serie_b_id}/partidas retornou status {status}")
        return

    # A estrutura de retorno desse endpoint não é garantida (pode vir como
    # lista simples, dict com 'partidas'/'data', ou agrupado por fase/rodada
    # como vimos acontecer em outro endpoint da mesma API). Em vez de assumir
    # um formato e quebrar, inspecionamos primeiro.
    print(f"[DEBUG] Tipo da resposta: {type(partidas).__name__}")
    if isinstance(partidas, dict):
        print(f"[DEBUG] Chaves de primeiro nível: {list(partidas.keys())[:20]}")

    def _extrair_partidas(obj):
        """Extrai recursivamente todo dict que pareça ser uma partida
        (tem 'time_mandante' e 'time_visitante'), não importa o nível
        de aninhamento (fase -> rodada -> partidas, etc.)."""
        encontradas = []
        if isinstance(obj, dict):
            if "time_mandante" in obj and "time_visitante" in obj:
                encontradas.append(obj)
            else:
                for v in obj.values():
                    encontradas.extend(_extrair_partidas(v))
        elif isinstance(obj, list):
            for item in obj:
                encontradas.extend(_extrair_partidas(item))
        return encontradas

    lista = _extrair_partidas(partidas)
    print(f"[DEBUG] Total de partidas extraídas (todos os formatos): {len(lista)}")

    if not lista:
        print("[AVISO] Não consegui extrair nenhuma partida da estrutura. "
              "JSON bruto (primeiros 3000 caracteres) pra inspeção manual:")
        print(json.dumps(partidas, indent=2, ensure_ascii=False)[:3000])
        return

    alvo_lista = []
    for p in lista:
        casa = str(p.get("time_mandante", {}).get("nome_popular", "")).lower()
        fora = str(p.get("time_visitante", {}).get("nome_popular", "")).lower()
        if "sport" in casa and "cuiab" in fora:
            alvo_lista.append(p)
        elif "cuiab" in casa and "sport" in fora:
            alvo_lista.append(p)

    if not alvo_lista:
        print("[AVISO] Não achei nenhum jogo Sport x Cuiabá. Mostrando as primeiras 5 partidas pra conferência manual:")
        print(json.dumps(lista[:5], indent=2, ensure_ascii=False))
        return

    print(f"[OK] {len(alvo_lista)} jogo(s) Sport x Cuiabá encontrado(s):")
    for p in alvo_lista:
        print(f"  - id={p.get('partida_id')} | {p.get('data_realizacao')} | status={p.get('status')} | "
              f"{p.get('time_mandante',{}).get('nome_popular')} x {p.get('time_visitante',{}).get('nome_popular')}")

    # Busca o detalhe completo de CADA jogo encontrado (normalmente 1 ou 2 —
    # turno e returno)
    for p in alvo_lista:
        partida_id = p.get("partida_id")
        print(f"\n=== 3. Detalhe completo da partida id={partida_id} ({p.get('data_realizacao')}) ===")
        status, detalhe = _get(f"/partidas/{partida_id}")
        print(json.dumps(detalhe, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
