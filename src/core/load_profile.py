"""Perfis de geração de carga: controla o intervalo entre eventos simulados."""

import math
import random
import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("scada_trafgen.load")


class LoadProfile:
    """Controla o intervalo entre eventos simulados."""

    def next_interval(self, base_interval: float) -> float:
        raise NotImplementedError

    @classmethod
    def from_config(cls, cfg: dict) -> "LoadProfile":
        profile_type = cfg.get("type", "constant").lower()
        if profile_type == "poisson":
            lp: LoadProfile = PoissonProfile()
        elif profile_type == "burst":
            lp = BurstProfile(
                burst_count=int(cfg.get("burst_count", 10)),
                burst_duration_s=float(cfg.get("burst_duration_s", 2.0)),
                burst_every_s=float(cfg.get("burst_every_s", 60.0)),
            )
        elif profile_type == "sinusoidal":
            lp = SinusoidalProfile(period_s=float(cfg.get("period_s", 300.0)))
        else:
            lp = ConstantProfile()
        if profile_type != "constant":
            logger.info(f"Perfil de carga: {profile_type}")
        return lp


class ConstantProfile(LoadProfile):
    """Intervalo fixo — comportamento padrão."""
    def next_interval(self, base_interval: float) -> float:
        return base_interval


class PoissonProfile(LoadProfile):
    """
    Chegadas de Poisson: intervalos exponencialmente distribuídos.
    O valor médio coincide com base_interval.
    """
    def next_interval(self, base_interval: float) -> float:
        if base_interval <= 0:
            return 0.0
        return random.expovariate(1.0 / base_interval)


@dataclass
class BurstProfile(LoadProfile):
    """
    Rajadas periódicas: burst_count eventos concentrados em burst_duration_s
    segundos, seguidos de uma pausa até o próximo ciclo de burst_every_s.
    """
    burst_count: int = 10
    burst_duration_s: float = 2.0
    burst_every_s: float = 60.0
    _events_in_burst: int = field(default=0, init=False, repr=False)
    _next_burst: float = field(default_factory=time.time, init=False, repr=False)

    def next_interval(self, base_interval: float) -> float:
        now = time.time()
        if now >= self._next_burst:
            self._events_in_burst = 0
            self._next_burst = now + self.burst_every_s
            logger.debug("BurstProfile: início de rajada")

        if self._events_in_burst < self.burst_count:
            self._events_in_burst += 1
            return self.burst_duration_s / max(1, self.burst_count)

        remaining = self._next_burst - time.time()
        return max(0.1, remaining)


@dataclass
class SinusoidalProfile(LoadProfile):
    """
    Taxa varia com uma senoide de período configurável.
    No pico (factor=1.5), intervalo = base * 0.67 (mais rápido).
    No vale (factor=0.5), intervalo = base * 2.0 (mais lento).
    Simula ciclos de carga ao longo do dia.
    """
    period_s: float = 300.0

    def next_interval(self, base_interval: float) -> float:
        t = time.time()
        factor = 1.0 + 0.5 * math.sin(2.0 * math.pi * t / self.period_s)
        return max(0.05, base_interval * factor)
