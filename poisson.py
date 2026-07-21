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
    linha_cantos: float
    prob_over_cantos: float
    linha_cartoes: float
    prob_over_cartoes: float

    # Chutes / Chutes no gol — POR TIME (não combinado), pra diversificar
    # Múltiplas sem repetir a mesma perna em categorias diferentes
    chutes_casa: float
    chutes_fora: float
    linha_chutes_casa: float
    prob_over_chutes_casa: float
    linha_chutes_fora: float
    prob_over_chutes_fora: float

    chutes_gol_casa: float
    chutes_gol_fora: float
    linha_chutes_gol_casa: float
    prob_over_chutes_gol_casa: float
    linha_chutes_gol_fora: float
    prob_over_chutes_gol_fora: float

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

    REVERTIDO (07/2026) — a versão com gols separados por casa/fora e
    médias de liga por time (testada por algumas horas) foi comparada
    com esta fórmula via diagnostico_calibracao_v2.py contra 26 jogos
    já encerrados da temporada. Resultado: a fórmula com FATOR_CASA/
    FATOR_FORA fixo teve log-loss e Brier MELHORES em 1x2 (0.923/0.546
    vs 0.954/0.572) e em over/under 2.5 (0.653/0.232 vs 0.727/0.263).
    A versão nova só ganhou em acurácia bruta do over/under, que é uma
    métrica mais fraca (não pune erro confiante como log-loss/brier).
    Hipótese: separar por casa/fora reduz o tamanho da amostra por time
    pela metade, aumentando ruído mais do que ganha em precisão teórica.
    Decisão do usuário: reverter. FATOR_CASA/FATOR_FORA/empate mantidos
    como estavam (calibração portada da Série B, 07/2026).

    ÚNICA MELHORIA MANTIDA da investigação: media_gols_liga passou de
    um valor chutado (1.30) pra um valor calculado a partir da média
    real dos 20 times da Série A coletados manualmente (1.3135) — troca
    de baixíssimo risco, sem reintroduzir a complexidade que não se
    mostrou melhor no backtest.
    """

    MAX_GOLS = 9
    FATOR_CASA = 1.25   # Vantagem histórica do mandante — calibração portada da Série B
    FATOR_FORA = 0.75

    # Ajuste aditivo de gols esperados (xG) — DESATIVADO (0.0). Testado em
    # 0.5 e 0.25 e nunca mostrou melhora real. Deixe > 0.0 só se for
    # testar de novo com diagnostico_calibracao.py.
    AJUSTE_GOLS_CASA = 0.0
    AJUSTE_GOLS_FORA = 0.0

    def __init__(self, fator_ajuste_empate: float = 1.00):
        self.fator_ajuste_empate = fator_ajuste_empate

    def calcular(
        self,
        time_casa: str,
        time_fora: str,
        gm_casa: float, gc_casa: float, j_casa: int,
        gm_fora: float, gc_fora: float, j_fora: int,
        media_gols_liga: float = 1.3135,
        fator_qualitativo_casa: float = 1.0,
        fator_qualitativo_fora: float = 1.0,
        cantos_casa: float = 5.2,
        cantos_fora: float = 4.8,
        cartoes_casa: float = 2.2,
        cartoes_fora: float = 2.3,
        chutes_casa: float = 11.0,
        chutes_fora: float = 9.5,
        chutes_gol_casa: float = 4.0,
        chutes_gol_fora: float = 3.3,
    ) -> ResultadoPoisson:
        """
        Calcula probabilidades completas para um confronto.

        Args:
            time_casa / time_fora: Nomes dos times
            gm_*, gc_*, j_*: Gols marcados, sofridos e jogos (temporada
                inteira, sem separar mando de campo)
            media_gols_liga: Média real de gols/jogo da liga (calculada
                a partir dos 20 times coletados em 2026-07)
            fator_qualitativo_*: Multiplicador por desfalques/notícias (0.80–1.20)
            cantos_*, cartoes_*: Médias de escanteios e cartões por jogo
        """
        media_casa = media_gols_liga * self.FATOR_CASA
        media_fora = media_gols_liga * self.FATOR_FORA

        # Forças normalizadas pela média da LIGA (sem fator casa embutido).
        atk_casa = (gm_casa / j_casa) / media_gols_liga
        def_casa = (gc_casa / j_casa) / media_gols_liga
        atk_fora = (gm_fora / j_fora) / media_gols_liga
        def_fora = (gc_fora / j_fora) / media_gols_liga

        xg_casa = atk_casa * def_fora * media_casa * fator_qualitativo_casa
        xg_fora = atk_fora * def_casa * media_fora * fator_qualitativo_fora

        xg_casa += self.AJUSTE_GOLS_CASA
        xg_fora += self.AJUSTE_GOLS_FORA

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
        # CORREÇÃO (07/2026): removido o bônus baseado em xG que era somado
        # em cima da média real de escanteios de cada time. A média real já
        # reflete o perfil ofensivo do time, então somar o bônus de xG em
        # cima inflava a estimativa (mesmo padrão do bug de dupla contagem
        # já corrigido nos gols — identificado comparando com a Série B,
        # que já tinha corrigido isso após validar contra jogo real: previa
        # 11.6 escanteios vs <8.5 reais). cantos_ft agora é só a soma das
        # médias reais por time.
        cantos_ft = round(cantos_casa + cantos_fora, 1)
        cantos_ht = round(cantos_ft * 0.44, 1)
        equilibrio = (p_emp / 100) * 1.8
        cartoes_ft = round(
            min(max(cartoes_casa + cartoes_fora + equilibrio, 3.0), 7.0), 1
        )

        # Probabilidade de "mais de X.5" escanteios/cartões, tratando a
        # estimativa (cantos_ft/cartoes_ft) como média de uma Poisson.
        # Aproximação: escanteios e cartões não seguem Poisson tão bem
        # quanto gols, mas serve como referência de mercado razoável.
        linha_cantos = self._linha_media(cantos_ft)
        prob_over_cantos = self._prob_over_poisson(cantos_ft, linha_cantos) * 100

        linha_cartoes = self._linha_media(cartoes_ft)
        prob_over_cartoes = self._prob_over_poisson(cartoes_ft, linha_cartoes) * 100

        # Chutes / Chutes no gol — por time, não combinado. Usa a média
        # real coletada (fin_casa/fin_fora/fing_casa/fing_fora) e a mesma
        # aproximação Poisson já usada em escanteios/cartões.
        linha_chutes_casa = self._linha_media(chutes_casa)
        prob_over_chutes_casa = self._prob_over_poisson(chutes_casa, linha_chutes_casa) * 100
        linha_chutes_fora = self._linha_media(chutes_fora)
        prob_over_chutes_fora = self._prob_over_poisson(chutes_fora, linha_chutes_fora) * 100

        linha_chutes_gol_casa = self._linha_media(chutes_gol_casa)
        prob_over_chutes_gol_casa = self._prob_over_poisson(chutes_gol_casa, linha_chutes_gol_casa) * 100
        linha_chutes_gol_fora = self._linha_media(chutes_gol_fora)
        prob_over_chutes_gol_fora = self._prob_over_poisson(chutes_gol_fora, linha_chutes_gol_fora) * 100

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
            linha_cantos=linha_cantos,
            prob_over_cantos=round(prob_over_cantos, 1),
            linha_cartoes=linha_cartoes,
            prob_over_cartoes=round(prob_over_cartoes, 1),
            chutes_casa=round(chutes_casa, 1),
            chutes_fora=round(chutes_fora, 1),
            linha_chutes_casa=linha_chutes_casa,
            prob_over_chutes_casa=round(prob_over_chutes_casa, 1),
            linha_chutes_fora=linha_chutes_fora,
            prob_over_chutes_fora=round(prob_over_chutes_fora, 1),
            chutes_gol_casa=round(chutes_gol_casa, 1),
            chutes_gol_fora=round(chutes_gol_fora, 1),
            linha_chutes_gol_casa=linha_chutes_gol_casa,
            prob_over_chutes_gol_casa=round(prob_over_chutes_gol_casa, 1),
            linha_chutes_gol_fora=linha_chutes_gol_fora,
            prob_over_chutes_gol_fora=round(prob_over_chutes_gol_fora, 1),
            placar_mais_provavel=placar,
            prob_placar_mais_provavel=round(prob_placar * 100, 1),
            odd_casa=_odd_justa(p_casa),
            odd_empate=_odd_justa(p_emp),
            odd_fora=_odd_justa(p_fora),
            odd_btts=_odd_justa(p_btts),
            odd_over25=_odd_justa(over25_ft),
        )

    def _linha_media(self, media: float) -> float:
        """Converte uma média (ex: 9.3) na linha de mercado mais próxima,
        sempre terminada em .5 (ex: 9.5) — como as casas de apostas fazem,
        pra nunca ter empate exato na linha. floor(media)+0.5 é sempre a
        linha .5 mais próxima da média (nunca a de baixo)."""
        return int(np.floor(media)) + 0.5

    def _prob_over_poisson(self, media: float, linha: float) -> float:
        """P(X > linha), tratando X ~ Poisson(media)."""
        limite = int(np.floor(linha))
        return float(1.0 - poisson.cdf(limite, max(media, 0.1)))

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
