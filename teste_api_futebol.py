# ==========================================================
# TESTE COMPARATIVO — API Futebol (api-futebol.com.br)
# Script isolado, NÃO faz parte do pipeline principal do Predix.
# Objetivo único: buscar o mesmo jogo problemático (Criciúma x
# Goiás, 15/08/2026, Série B) nessa API alternativa e comparar
# com o que a Dados Futebol devolveu, pra decidir se vale migrar.
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

    # O plano Free só libera a Série B — precisamos do campeonato_id certo.
    # Como não conseguimos listar /campeonatos (bloqueado por plano), testamos
    # por tentativa: só um ID vai responder status 200 de verdade — qualquer
    # outro (errado, inexistente, ou fora do plano) responde 401 ou 404.
    print("=== 1. Descobrindo o campeonato_id da Série B por tentativa ===")
    serie_b_id = None
    candidatos = list(range(1, 31))
    for cid in candidatos:
        status, resp = _get(f"/campeonatos/{cid}/tabela")
        if status != 200:
            continue
        print(f"[CANDIDATO VÁLIDO] campeonato_id={cid} respondeu 200:")
        print(json.dumps(resp, indent=2, ensure_ascii=False)[:1500])
        serie_b_id = cid
        break

    if not serie_b_id:
        print("[ERRO] Nenhum ID de 1 a 30 respondeu status 200. "
              "Ampliando a busca pra 31-80...")
        for cid in range(31, 81):
            status, resp = _get(f"/campeonatos/{cid}/tabela")
            if status != 200:
                continue
            print(f"[CANDIDATO VÁLIDO] campeonato_id={cid} respondeu 200:")
            print(json.dumps(resp, indent=2, ensure_ascii=False)[:1500])
            serie_b_id = cid
            break

    if not serie_b_id:
        print("[ERRO] Nenhum ID de 1 a 80 funcionou. Precisa investigar direto "
              "no painel 'Requisições' ou contatar o suporte.")
        return

    print(f"\n[OK] campeonato_id da Série B: {serie_b_id}")

    # 2. Lista partidas do campeonato pra achar Criciúma x Goiás (15/08/2026)
    print("\n=== 2. Buscando partida Criciúma x Goiás ===")
    status, partidas = _get(f"/campeonatos/{serie_b_id}/partidas")
    if status != 200:
        print(f"[ERRO] /campeonatos/{serie_b_id}/partidas retornou status {status}")
        return
    lista = partidas if isinstance(partidas, list) else partidas.get("partidas", partidas.get("data", []))
    alvo = None
    for p in lista:
        casa = str(p.get("time_mandante", {}).get("nome_popular", "")).lower()
        fora = str(p.get("time_visitante", {}).get("nome_popular", "")).lower()
        data = str(p.get("data_realizacao", ""))
        if "criciúma" in casa or "criciuma" in casa:
            if "goiás" in fora or "goias" in fora:
                if "2026-08-15" in data or "15/08/2026" in data:
                    alvo = p
                    break
    if not alvo:
        print("[AVISO] Não achei o jogo exato por nome+data. Mostrando as primeiras 5 partidas pra conferência manual:")
        print(json.dumps(lista[:5], indent=2, ensure_ascii=False))
        return

    partida_id = alvo.get("partida_id")
    print(f"[OK] Partida encontrada: id={partida_id}")
    print(json.dumps(alvo, indent=2, ensure_ascii=False))

    # 3. Busca o detalhe completo da partida (deve incluir estatísticas)
    print(f"\n=== 3. Detalhe completo da partida id={partida_id} ===")
    status, detalhe = _get(f"/partidas/{partida_id}")
    print(json.dumps(detalhe, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
