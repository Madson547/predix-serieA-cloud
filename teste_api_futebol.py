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
        return resp.json()
    except Exception:
        print(f"[AVISO] resposta não-JSON: {resp.text[:300]}")
        return {}


def main():
    if not API_KEY:
        print("[ERRO] API_FUTEBOL_KEY não encontrada (defina como secret/env).")
        return

    # O plano Free só libera a Série B — o endpoint /campeonatos (lista geral)
    # retorna 401 mesmo com chave válida, porque listar TODAS as competições
    # não está incluso no plano. Por isso descobrimos o campeonato_id certo
    # por tentativa: cada ID errado também retorna 401 (mesma mensagem
    # "não faz parte do seu plano"), e o ID certo é o único que responde 200.
    # Sabemos que Série A = 10 e Copa do Brasil = 2 (documentação oficial),
    # então testamos uma faixa razoável ao redor desses números.
    print("=== 1. Descobrindo o campeonato_id da Série B por tentativa ===")
    serie_b_id = None
    candidatos = list(range(1, 31))
    for cid in candidatos:
        resp = _get(f"/campeonatos/{cid}/tabela")
        if isinstance(resp, dict) and resp.get("code") == 401:
            continue
        # Se chegou aqui, essa chamada não caiu no erro de plano — confirma
        # olhando se tem cara de tabela de classificação (lista com "pontos")
        print(f"[CANDIDATO] campeonato_id={cid} respondeu algo além do erro 401:")
        print(json.dumps(resp, indent=2, ensure_ascii=False)[:1500])
        serie_b_id = cid
        break

    if not serie_b_id:
        print("[ERRO] Nenhum ID na faixa testada (1-30) funcionou. "
              "Pode ser que o campeonato_id da Série B esteja fora dessa faixa — "
              "ajuste o range de 'candidatos' e rode de novo.")
        return

    print(f"\n[OK] campeonato_id da Série B parece ser: {serie_b_id}")

    # 2. Lista partidas do campeonato pra achar Criciúma x Goiás (15/08/2026)
    print("\n=== 2. Buscando partida Criciúma x Goiás ===")
    partidas = _get(f"/campeonatos/{serie_b_id}/partidas")
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
    detalhe = _get(f"/partidas/{partida_id}")
    print(json.dumps(detalhe, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
