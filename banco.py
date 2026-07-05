# ==========================================================
# Predictor Pro 3.0
# banco.py — Supabase: tabela real + rodada + notícias
# ==========================================================

import os
import streamlit as st
from supabase import create_client, Client
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")


# ==========================================================
# CONEXÃO COM O SUPABASE
# ==========================================================

@st.cache_resource
def conectar() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise Exception("SUPABASE_URL ou SUPABASE_KEY não encontrados")

    return create_client(url, key)


# ==========================================================
# TABELA SÉRIE A (dados reais da API)
# ==========================================================

def carregar_tabela_serie_a() -> tuple[bool, list]:
    """
    Retorna a tabela de classificação da Série A salva no Supabase.
    Calcula o saldo de gols na hora (gols_pro - gols_contra).
    Formato: (sucesso: bool, lista_de_times: list)
    """
    try:
        supabase = conectar()
        resp = supabase.table("tabela_serie_a") \
            .select("*") \
            .order("posicao") \
            .execute()

        if resp.data:
            for row in resp.data:
                gp = int(row.get("gols_pro") or 0)
                gc = int(row.get("gols_contra") or 0)
                row["saldo"] = gp - gc
            return True, resp.data

        return False, []

    except Exception as e:
        print(f"[ERRO] carregar_tabela_serie_a: {e}")
        return False, []


def carregar_times_tabela() -> tuple[bool, dict]:
    """
    Retorna dicionário {nome_time: dados} para uso no preditor.
    """
    ok, tabela = carregar_tabela_serie_a()
    if not ok:
        return False, {}
    dados = {row["time"]: row for row in tabela if row.get("time")}
    return True, dados


# ==========================================================
# TIMES (para dropdown do preditor)
# ==========================================================

def carregar_times() -> tuple[bool, list]:
    """
    Retorna lista de nomes de times da tabela Série A.
    Fallback: estatisticas_times (legado).
    """
    try:
        supabase = conectar()

        # Tenta primeiro a tabela real
        resp = supabase.table("tabela_serie_a") \
            .select("time, posicao") \
            .order("posicao") \
            .execute()

        if resp.data:
            times = [row["time"] for row in resp.data if row.get("time")]
            if times:
                return True, times

        # Fallback: tabela legada
        resp2 = supabase.table("estatisticas_times") \
            .select("time") \
            .order("time") \
            .execute()

        if resp2.data:
            times = [row["time"] for row in resp2.data if row.get("time")]
            return True, times

        return False, []

    except Exception as e:
        print(f"[ERRO] carregar_times: {e}")
        return False, []


# ==========================================================
# RODADA ATUAL
# ==========================================================

def carregar_rodada_atual() -> tuple[bool, list]:
    """
    Retorna as partidas da rodada atual salvas no Supabase.
    """
    try:
        supabase = conectar()
        resp = supabase.table("rodada_atual") \
            .select("*") \
            .order("data") \
            .execute()

        if resp.data:
            return True, resp.data

        return False, []

    except Exception as e:
        print(f"[ERRO] carregar_rodada_atual: {e}")
        return False, []


# ==========================================================
# NOTÍCIAS
# ==========================================================

def carregar_noticias(limite: int = 15) -> list:
    """
    Retorna as últimas N notícias da Série B do Supabase.
    """
    try:
        supabase = conectar()
        resp = supabase.table("noticias_bastidores") \
            .select("texto, fonte, data_atualizacao") \
            .order("data_atualizacao", desc=True) \
            .limit(limite) \
            .execute()

        if resp.data:
            return resp.data

        return []

    except Exception as e:
        print(f"[ERRO] carregar_noticias: {e}")
        return []


def salvar_noticia(texto: str, fonte: str = "manual"):
    try:
        supabase = conectar()
        supabase.table("noticias_bastidores").insert({
            "texto": texto,
            "fonte": fonte,
            "data_atualizacao": datetime.now().isoformat()
        }).execute()
    except Exception as e:
        print(f"[ERRO] salvar_noticia: {e}")


# ==========================================================
# STATUS DO BANCO
# ==========================================================

def banco_online() -> bool:
    try:
        supabase = conectar()
        supabase.table("tabela_serie_a").select("posicao").limit(1).execute()
        return True
    except Exception:
        try:
            supabase = conectar()
            supabase.table("estatisticas_times").select("time").limit(1).execute()
            return True
        except Exception:
            return False