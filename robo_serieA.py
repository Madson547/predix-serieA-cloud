# ==========================================================
# Predix Série A — robo_serieA.py
# Job local (Windows Task Scheduler) que mantém "times" e
# "jogos" atualizados no Supabase do predix-serieA-cloud.
# ==========================================================

import sys
import traceback
from datetime import datetime

from coletor import atualizar_tudo


def log(msg: str):
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{agora}] {msg}")


def main():
    inicio = datetime.now()
    log("=== ROBÔ Predix Série A — iniciando ===")

    try:
        atualizar_tudo()
        log("OK — times e jogos atualizados.")
    except Exception as e:
        log(f"ERRO: {e}")
        traceback.print_exc()

    duracao = (datetime.now() - inicio).total_seconds()
    log(f"=== ROBÔ finalizado em {duracao:.1f}s ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
