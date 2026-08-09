# ==========================================================
# Predix Sports — ia_apostas.py
# Usa a API da Anthropic (Claude, com visão) pra ler um print
# de bilhete de aposta (Betano ou similar) e extrair os campos
# estruturados — jogo(s), mercado(s), odd, stake — pra
# pré-preencher o formulário da aba Banca. O usuário sempre
# confere/edita antes de salvar; a IA só acelera a digitação.
# ==========================================================

import base64
import json
import os

import anthropic
import streamlit as st

MODELO = "claude-sonnet-5"

SYSTEM_PROMPT = """Você lê prints de bilhetes de apostas esportivas (Betano ou \
similar) e extrai os dados em JSON estruturado. Responda SOMENTE com um JSON \
válido, sem texto antes ou depois.

Existem dois tipos de bilhete:

1. ÚNICA — uma seleção simples ou uma múltipla só, com um odd total e um \
valor apostado só.

2. SISTEMA — várias sub-apostas dentro do mesmo bilhete, cada uma com seu \
próprio odd e mostrando o formato "N x R$X,XX" no topo (ex: "Múltiplas de \
4-seleções 5 x R$3,00" significa 5 sub-apostas de R$3,00 cada, cada uma com \
seu odd individual mostrado ao lado de cada bloco "Criar Aposta").

Responda SEMPRE no formato exato abaixo — uma LISTA com uma entrada por \
sub-aposta (bilhete ÚNICA = lista com 1 item só; bilhete SISTEMA = lista \
com N itens, um por sub-aposta):

[
  {
    "jogos_envolvidos": <string, nomes dos confrontos envolvidos NESSA \
sub-aposta, separados por " + " se ela mesma combinar mais de um jogo>,
    "mercados": <string, descrição resumida do(s) mercado(s) apostado(s) \
NESSA sub-aposta, separados por " + " se houver mais de um>,
    "categoria_estimada": <uma destas: "resultado", "gols", "btts", \
"casa_marca", "fora_marca", "escanteios", "cartoes", "chutes_casa", \
"chutes_fora", "chutes_gol_casa", "chutes_gol_fora", "faltas_casa", \
"faltas_fora", "mista">,
    "tipo_aposta": <"simples" se só uma seleção nessa sub-aposta, "multipla" \
se duas ou mais>,
    "odd": <float, o odd dessa sub-aposta específica (o número ao lado do \
"Criar Aposta" daquele bloco, ou o odd único se for bilhete ÚNICA)>,
    "stake": <float, o valor apostado nessa sub-aposta específica em reais \
— pra bilhete SISTEMA, divida o valor total pelo número de sub-apostas \
(ex: "5 x R$3,00" = R$3,00 por sub-aposta), sem o símbolo R$>,
    "confianca": <"alta"|"media"|"baixa", quão nítido e completo o print estava>
  },
  ...
]

Se algum campo não estiver visível no print, use null nesse campo em vez de \
inventar um número."""


def _client() -> anthropic.Anthropic:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY não encontrada. Configure em Settings → Secrets no Streamlit Cloud."
        )
    return anthropic.Anthropic(api_key=api_key)


def analisar_print_aposta(imagem_bytes: bytes, media_type: str = "image/png") -> list[dict]:
    """
    Recebe os bytes brutos da imagem do bilhete e devolve uma LISTA de dicts
    — um item por sub-aposta. Bilhete ÚNICA vira lista de 1 item; bilhete
    SISTEMA (várias sub-apostas com odds/stakes diferentes, tipo "5 x R$3,00")
    vira lista com N itens. Nunca salva nada sozinho — só sugere; quem chama
    decide o que fazer com cada item da lista.
    """
    if not imagem_bytes:
        raise ValueError("Nenhuma imagem fornecida.")

    imagem_b64 = base64.b64encode(imagem_bytes).decode("utf-8")
    client = _client()

    mensagem = client.messages.create(
        model=MODELO,
        max_tokens=4096,
        output_config={"effort": "low"},  # tarefa de extração simples, não precisa de raciocínio profundo
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": imagem_b64},
                },
                {"type": "text", "text": "Extraia os dados desse bilhete de aposta. Responda direto, sem pensar muito — é uma tarefa de leitura simples."},
            ],
        }],
    )

    blocos_texto = [bloco.text for bloco in mensagem.content if getattr(bloco, "type", None) == "text"]
    if not blocos_texto:
        raise RuntimeError("A IA não retornou nenhum bloco de texto na resposta.")
    bruto = "".join(blocos_texto).strip()
    if bruto.startswith("```"):
        bruto = bruto.strip("`")
        if bruto.lower().startswith("json"):
            bruto = bruto[4:].strip()

    try:
        sugestoes = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"A IA respondeu em formato inesperado (não-JSON): {bruto[:200]}") from e

    if isinstance(sugestoes, dict):
        sugestoes = [sugestoes]  # compatibilidade caso o modelo devolva objeto solto em vez de lista

    resultado = []
    for sugestao in sugestoes:
        resultado.append({
            "jogos_envolvidos": sugestao.get("jogos_envolvidos") or "",
            "mercados": sugestao.get("mercados") or "",
            "categoria_estimada": sugestao.get("categoria_estimada") or "mista",
            "tipo_aposta": sugestao.get("tipo_aposta") or "simples",
            "odd": float(sugestao["odd"]) if sugestao.get("odd") is not None else None,
            "stake": float(sugestao["stake"]) if sugestao.get("stake") is not None else None,
            "confianca": sugestao.get("confianca", "baixa"),
        })
    return resultado
