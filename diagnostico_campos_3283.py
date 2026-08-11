# diagnostico_campos_3291.py — roda uma vez, só printa o JSON bruto
import os, json, requests
from dotenv import load_dotenv
load_dotenv()

DADOS_FUTEBOL_KEY = os.getenv("DADOS_FUTEBOL_KEY")
BASE_URL = "https://api.dadosfutebol.com.br/v1"
FIXTURE_ID = 3283  # Goiás x Londrina

resp = requests.get(
    f"{BASE_URL}/partidas/{FIXTURE_ID}/estatisticas",
    headers={"Authorization": f"Bearer {DADOS_FUTEBOL_KEY}", "Accept": "application/json"},
    timeout=15,
)
print(f"Status: {resp.status_code}")
print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
