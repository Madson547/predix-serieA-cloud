# ==========================================================
# Predix Série A — ia_qualitativa.py
# Usa a API da Anthropic (Claude) pra ler um texto de notícia/
# escalação colado pelo usuário e sugerir um ajuste qualitativo
# manual — mesma escala (0.80 a 1.20) e mesma tabela
# (ajustes_qualitativos) que hoje só são alimentadas via
# planilha + importar_qualitativos.py.
#
# A IA só SUGERE — quem decide salvar é o usuário, depois de
# conferir os campos no app. Não substitui o fluxo de planilha,
# só dá um jeito mais rápido de alimentar a mesma tabela direto
# de dentro do app, sem precisar abrir Excel.
# ==========================================================

import json
import os
from datetime import datetime

import anthropic
import streamlit as st
from database import supabase

MODELO = "claude-sonnet-5"

SYSTEM_PROMPT = """Você extrai informações qualitativas de notícias de futebol \
pra alimentar um modelo de previsão estatística. Leia o texto fornecido sobre \
um confronto específico e responda SOMENTE com um JSON válido, sem nenhum texto \
antes ou depois, no formato exato:

{
  "ajuste_casa": <float, 0.80 a 1.20, multiplicador de força do time da casa — \
1.0 é neutro, abaixo de 1.0 indica desfalques/má fase/reservas, acima de 1.0 \
indica reforços/boa fase>,
  "ajuste_fora": <float, 0.80 a 1.20, idem pro time visitante>,
  "desfalques_casa": <int, 0 a 8, número de jogadores importantes desfalcados no time da casa>,
  "desfalques_fora": <int, 0 a 8, idem pro time visitante>,
  "contexto_especial": <string curta ou "", ex: "derby", "decisão de rebaixamento", "">,
  "resumo_observacoes": <string de até 2 frases resumindo o que é relevante pra previsão>,
  "confianca": <"alta"|"media"|"baixa", quão claro e específico é o texto fornecido>
}

Calibre ajuste_casa/ajuste_fora com moderação: um desfalque importante isolado \
costuma valer -0.03 a -0.06; uma crise grave ou vários desfalques, até -0.15. \
Se o texto não mencionar nada de relevante pra um dos times, use 1.0 (neutro) \
pra esse time — não invente informação que não está no texto. Se o texto for \
vago ou genérico, marque confianca como "baixa"."""


def _client() -> anthropic.Anthropic:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY não encontrada (nem em st.secrets, nem em variável de ambiente). "
            "Configure em Settings → Secrets no Streamlit Cloud."
        )
    return anthropic.Anthropic(api_key=api_key)


def analisar_texto_qualitativo(texto: str, casa: str, fora: str) -> dict:
    """
    Recebe o texto colado pelo usuário e os nomes dos times, devolve um
    dict pronto pra pré-preencher o formulário de ajuste manual — na
    mesma escala (0.80-1.20) que calcular_fator_qualitativo já espera
    no parâmetro ajuste_manual.
    """
    if not texto or not texto.strip():
        raise ValueError("Texto vazio — cole a notícia/escalação antes de analisar.")

    client = _client()

    mensagem = client.messages.create(
        model=MODELO,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Confronto: {casa} (casa) x {fora} (visitante)\n\n"
                f"Texto da notícia/escalação:\n{texto.strip()}"
            ),
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
        "ajuste_casa": round(max(0.80, min(1.20, float(sugestao.get("ajuste_casa", 1.0)))), 4),
        "ajuste_fora": round(max(0.80, min(1.20, float(sugestao.get("ajuste_fora", 1.0)))), 4),
        "desfalques_casa": int(sugestao.get("desfalques_casa", 0)),
        "desfalques_fora": int(sugestao.get("desfalques_fora", 0)),
        "contexto_especial": sugestao.get("contexto_especial", "") or "",
        "resumo_observacoes": sugestao.get("resumo_observacoes", "") or "",
        "confianca": sugestao.get("confianca", "baixa"),
    }


def salvar_ajuste_manual(time_casa: str, time_fora: str, data_jogo: str,
                          ajuste_casa: float, ajuste_fora: float,
                          observacoes: str = "") -> bool:
    """
    Grava (ou atualiza) o ajuste manual pra esse confronto+data direto na
    tabela ajustes_qualitativos — mesma tabela que buscar_ajuste_manual()
    (qualitativo.py) já lê, então o efeito é imediato na próxima análise,
    sem precisar rodar importar_qualitativos.py.
    """
    try:
        registro = {
            "time_casa": time_casa,
            "time_fora": time_fora,
            "data": data_jogo,
            "ajuste_manual_casa": ajuste_casa,
            "ajuste_manual_fora": ajuste_fora,
            "observacoes": observacoes,
            "fonte": "ia_qualitativa",
            "atualizado_em": datetime.now().isoformat(),
        }
        existe = supabase.table("ajustes_qualitativos").select("id") \
            .ilike("time_casa", time_casa).ilike("time_fora", time_fora) \
            .eq("data", data_jogo).execute()
        if existe.data:
            supabase.table("ajustes_qualitativos").update(registro) \
                .eq("id", existe.data[0]["id"]).execute()
        else:
            supabase.table("ajustes_qualitativos").insert(registro).execute()
        return True
    except Exception as e:
        print(f"[ERRO] salvar_ajuste_manual: {e}")
        return False
