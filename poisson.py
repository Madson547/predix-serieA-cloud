"""
Predix SerieA — Motor de Poisson
Calcula probabilidades preditivas para partidas de futebol.
"""

from dataclasses import dataclass
import numpy as np
from scipy.stats import poisson


@dataclass
class ResultadoPoisson:
    """Resultado completo de uma análise preditiva."""
    time_casa: str
    time_fora: str
    xg_casa: float
    xg_fora: float

    # Mercado 1x2
    prob_casa: float
    prob_empate: float
    prob_fora: float

    # Dupla Chance
    prob_dupla_1x: float
    prob_dupla_x2: float
    prob_dupla_12: float

    # Ambas Marcam / Time marca
    prob_btts: float
    prob_casa_marca: float
    prob_fora_marca: float

    # Mercados de gols
    over05_ht: float
    over15_ht: float
    over05_ft: float
    over15_ft: float
    over25_ft: float
    over35_ft: float

    # Linhas especiais
    cantos_ht: float
    cantos_ft: float
    cartoes_ft: float

    # Placar mais provável
    placar_mais_provavel: str
    prob_placar_mais_provavel: float

    # Odds justas (1 / probabilidade)
    odd_casa: float
    odd_empate: float
    odd_fora: float
    odd_btts: float
    odd_over25: float


class MotorPoisson:
    """
    Motor quantitativo baseado em Distribuição de Poisson.

    Metodologia Diego Godin (Dixon & Coles adaptado):
    - Força de ataque e defesa normalizadas pela média da liga
    - Ajuste de fator casa (home advantage)
    - Ajuste qualitativo por desfalques e notícias (quando disponível)
    """

    MAX_GOLS = 9
    FATOR_CASA = 1.15   # Vantagem histórica do mandante no futebol brasileiro
    FATOR_FORA = 0.85

    def __init__(self, fator_ajuste_empate: float = 1.08):
        self.fator_ajuste_empate = fator_ajuste_empate

    def calcular(
        self,
        time_casa: str,
        time_fora: str,
        gm_casa: float, gc_casa: float, j_casa: int,
        gm_fora: float, gc_fora: float, j_fora: int,
        media_gols_liga: float = 1.30,
        fator_qualitativo_casa: float = 1.0,
        fator_qualitativo_fora: float = 1.0,
        cantos_casa: float = 5.2,
        cantos_fora: float = 4.8,
        cartoes_casa: float = 2.2,
        cartoes_fora: float = 2.3,
    ) -> ResultadoPoisson:
        """
        Calcula probabilidades completas para um confronto.

        Args:
            time_casa / time_fora: Nomes dos times
            gm_*, gc_*, j_*: Gols marcados, sofridos e jogos de cada time
            media_gols_liga: Média de gols por jogo da liga inteira
            fator_qualitativo_*: Multiplicador por desfalques/notícias (0.80–1.0)
            cantos_*, cartoes_*: Médias de escanteios e cartões por jogo
        """
        media_casa = media_gols_liga * self.FATOR_CASA
        media_fora = media_gols_liga * self.FATOR_FORA

        # Forças normalizadas pela média da LIGA (sem fator casa embutido).
        # Bug anterior: normalizar por media_casa/media_fora fazia o fator
        # casa entrar duas vezes (uma na normalização, outra na multiplicação
        # final) e se cancelar — o mandante podia até sair como azarão contra
        # um time estatisticamente idêntico.
        atk_casa = (gm_casa / j_casa) / media_gols_liga
        def_casa = (gc_casa / j_casa) / media_gols_liga
        atk_fora = (gm_fora / j_fora) / media_gols_liga
        def_fora = (gc_fora / j_fora) / media_gols_liga

        # Expected Goals ajustados — fator casa aplicado uma única vez aqui
        xg_casa = atk_casa * def_fora * media_casa * fator_qualitativo_casa
        xg_fora = atk_fora * def_casa * media_fora * fator_qualitativo_fora

        xg_casa = max(xg_casa, 0.1)
        xg_fora = max(xg_fora, 0.1)

        # Matriz de probabilidades
        matriz = self._build_matriz(xg_casa, xg_fora)

        # Probabilidades 1x2 com ajuste de empate
        p_casa_raw = float(np.sum(np.tril(matriz, -1)))
        p_emp_raw  = float(np.sum(np.diag(matriz)))
        p_fora_raw = float(np.sum(np.triu(matriz, 1)))

        p_emp = p_emp_raw * self.fator_ajuste_empate
        sobra = 1.0 - p_emp
        total_raw = p_casa_raw + p_fora_raw
        p_casa = (p_casa_raw / total_raw) * sobra * 100
        p_fora = (p_fora_raw / total_raw) * sobra * 100
        p_emp  = p_emp * 100

        # Dupla Chance (derivada direto do 1x2 já ajustado)
        p_dupla_1x = round(p_casa + p_emp, 1)
        p_dupla_x2 = round(p_emp + p_fora, 1)
        p_dupla_12 = round(p_casa + p_fora, 1)

        # BTTS e Time marca (direto da matriz, antes de normalizar 1x2)
        n = self.MAX_GOLS
        p_casa_marca = float(np.sum(matriz[1:, :])) * 100
        p_fora_marca = float(np.sum(matriz[:, 1:])) * 100
        p_btts = float(np.sum(matriz[1:, 1:])) * 100

        # Mercados de gols
        over05_ft = self._over(matriz, 0.5)
        over15_ft = self._over(matriz, 1.5)
        over25_ft = self._over(matriz, 2.5)
        over35_ft = self._over(matriz, 3.5)

        over05_ht = round(over05_ft * 0.62, 1)
        over15_ht = round(over15_ft * 0.38, 1)

        # Linhas especiais
        cantos_ft = round(cantos_casa + cantos_fora + (xg_casa * 1.1) + (xg_fora * 0.9), 1)
        cantos_ht = round(cantos_ft * 0.44, 1)
        equilibrio = (p_emp / 100) * 1.8
        cartoes_ft = round(
            min(max(cartoes_casa + cartoes_fora + equilibrio, 3.0), 7.0), 1
        )

        # Placar mais provável
        placar, prob_placar = self._placar_mais_provavel(matriz)

        # Odds justas (protegidas contra divisão por zero)
        def _odd_justa(prob_pct):
            return round(100 / prob_pct, 2) if prob_pct > 0 else None

        return ResultadoPoisson(
            time_casa=time_casa,
            time_fora=time_fora,
            xg_casa=round(xg_casa, 2),
            xg_fora=round(xg_fora, 2),
            prob_casa=round(p_casa, 1),
            prob_empate=round(p_emp, 1),
            prob_fora=round(p_fora, 1),
            prob_dupla_1x=p_dupla_1x,
            prob_dupla_x2=p_dupla_x2,
            prob_dupla_12=p_dupla_12,
            prob_btts=round(p_btts, 1),
            prob_casa_marca=round(p_casa_marca, 1),
            prob_fora_marca=round(p_fora_marca, 1),
            over05_ht=over05_ht,
            over15_ht=over15_ht,
            over05_ft=round(over05_ft, 1),
            over15_ft=round(over15_ft, 1),
            over25_ft=round(over25_ft, 1),
            over35_ft=round(over35_ft, 1),
            cantos_ht=cantos_ht,
            cantos_ft=cantos_ft,
            cartoes_ft=cartoes_ft,
            placar_mais_provavel=placar,
            prob_placar_mais_provavel=round(prob_placar * 100, 1),
            odd_casa=_odd_justa(p_casa),
            odd_empate=_odd_justa(p_emp),
            odd_fora=_odd_justa(p_fora),
            odd_btts=_odd_justa(p_btts),
            odd_over25=_odd_justa(over25_ft),
        )

    def _build_matriz(self, xg_casa: float, xg_fora: float) -> np.ndarray:
        n = self.MAX_GOLS
        matriz = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                matriz[i, j] = poisson.pmf(i, xg_casa) * poisson.pmf(j, xg_fora)
        return matriz

    def _over(self, matriz: np.ndarray, linha: float) -> float:
        under = 0.0
        corte = int(np.ceil(linha))
        n = self.MAX_GOLS
        for i in range(n):
            for j in range(n):
                if (i + j) < corte:
                    under += matriz[i, j]
        return round((1.0 - under) * 100, 1)

    def _placar_mais_provavel(self, matriz: np.ndarray):
        idx = np.unravel_index(np.argmax(matriz), matriz.shape)
        return f"{idx[0]}x{idx[1]}", float(matriz[idx])