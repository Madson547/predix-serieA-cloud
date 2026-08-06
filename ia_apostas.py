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
válido, sem texto antes ou depois, no formato exato:

{
  "jogos_envolvidos": <string, nomes dos confrontos envolvidos, separados por \
" + " se for múltipla com mais de um jogo, ex: "Corinthians x Athletico-PR" ou \
"Corinthians x Athletico-PR + Coritiba x Cruzeiro">,
  "mercados": <string, descrição resumida do(s) mercado(s) apostado(s), \
separados por " + " se houver mais de um, ex: "Menos de 10.5 Escanteios + 1X">,
  "categoria_estimada": <uma destas: "resultado", "gols", "btts", "casa_marca", \
"fora_marca", "escanteios", "cartoes", "chutes_casa", "chutes_fora", \
"chutes_gol_casa", "chutes_gol_fora", "faltas_casa", "faltas_fora", "mista" \
(se a múltipla combina categorias diferentes)>,
  "tipo_aposta": <"simples" se só uma seleção, "multipla" se duas ou mais>,
  "odd": <float, a odd total/combinada mostrada no bilhete>,
  "stake": <float, o valor apostado em reais, sem o símbolo R$>,
  "confianca": <"alta"|"media"|"baixa", quão nítido e completo o print estava>
}

Se algum campo não estiver visível no print (ex: valor apostado cortado), \
use null nesse campo em vez de inventar um número."""


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


def analisar_print_aposta(imagem_bytes: bytes, media_type: str = "image/png") -> dict:
    """
    Recebe os bytes brutos da imagem do bilhete (do st.file_uploader) e
    devolve um dict com os campos pra pré-preencher o formulário de
    "Nova aposta". Nunca salva nada sozinho — só sugere.
    """
    if not imagem_bytes:
        raise ValueError("Nenhuma imagem fornecida.")

    imagem_b64 = base64.b64encode(imagem_bytes).decode("utf-8")
    client = _client()

    mensagem = client.messages.create(
        model=MODELO,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": imagem_b64},
                },
                {"type": "text", "text": "Extraia os dados desse bilhete de aposta."},
            ],
        }],
    )

    bruto = mensagem.content[0].text.strip()
    if bruto.startswith("```"):
        bruto = bruto.strip("`")
        if bruto.lower().startswith("json"):
            bruto = bruto[4:].strip()

    try:
        sugestao = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"A IA respondeu em formato inesperado (não-JSON): {bruto[:200]}") from e

    return {
        "jogos_envolvidos": sugestao.get("jogos_envolvidos") or "",
        "mercados": sugestao.get("mercados") or "",
        "categoria_estimada": sugestao.get("categoria_estimada") or "mista",
        "tipo_aposta": sugestao.get("tipo_aposta") or "simples",
        "odd": float(sugestao["odd"]) if sugestao.get("odd") is not None else None,
        "stake": float(sugestao["stake"]) if sugestao.get("stake") is not None else None,
        "confianca": sugestao.get("confianca", "baixa"),
    }
