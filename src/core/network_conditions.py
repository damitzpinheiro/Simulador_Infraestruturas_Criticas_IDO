"""Condicionamento de rede: latência, jitter, perda e corrupção de frames."""

import asyncio
import random
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("scada_trafgen.network")

PRESETS: dict = {
    "ideal":        {"latency_ms": 0.0,   "jitter_ms": 0.0,  "packet_loss_rate": 0.0,  "corruption_rate": 0.0},
    "lan":          {"latency_ms": 1.0,   "jitter_ms": 0.5,  "packet_loss_rate": 0.0,  "corruption_rate": 0.0},
    "wan":          {"latency_ms": 50.0,  "jitter_ms": 10.0, "packet_loss_rate": 0.0,  "corruption_rate": 0.0},
    "wan_degraded": {"latency_ms": 100.0, "jitter_ms": 30.0, "packet_loss_rate": 0.02, "corruption_rate": 0.001},
    "lossy":        {"latency_ms": 200.0, "jitter_ms": 50.0, "packet_loss_rate": 0.10, "corruption_rate": 0.005},
}


@dataclass
class NetworkConditions:
    """Define condições de rede aplicadas a cada frame enviado."""

    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    packet_loss_rate: float = 0.0
    corruption_rate: float = 0.0

    frames_dropped: int = field(default=0, init=False, repr=False)
    frames_corrupted: int = field(default=0, init=False, repr=False)

    @classmethod
    def from_config(cls, cfg: dict) -> "NetworkConditions":
        preset_name = cfg.get("preset", "ideal")
        params = PRESETS.get(preset_name, PRESETS["ideal"]).copy()
        for key in ("latency_ms", "jitter_ms", "packet_loss_rate", "corruption_rate"):
            if key in cfg:
                params[key] = float(cfg[key])
        nc = cls(**params)
        if not nc.is_ideal:
            logger.info(
                f"Condições de rede: preset={preset_name!r} | "
                f"latência={nc.latency_ms:.0f}ms ±{nc.jitter_ms:.0f}ms | "
                f"perda={nc.packet_loss_rate * 100:.1f}% | "
                f"corrupção={nc.corruption_rate * 100:.2f}%"
            )
        return nc

    @property
    def is_ideal(self) -> bool:
        return (
            self.latency_ms == 0.0
            and self.jitter_ms == 0.0
            and self.packet_loss_rate == 0.0
            and self.corruption_rate == 0.0
        )

    async def apply(self, writer: asyncio.StreamWriter, frame: bytes) -> bool:
        """Aplica condições de rede e escreve o frame. Retorna False se descartado."""
        if self.latency_ms > 0 or self.jitter_ms > 0:
            delay = max(0.0, random.gauss(self.latency_ms, self.jitter_ms)) / 1000.0
            if delay > 0:
                await asyncio.sleep(delay)

        if self.packet_loss_rate > 0 and random.random() < self.packet_loss_rate:
            self.frames_dropped += 1
            logger.debug(
                f"  [NetCond] Frame DESCARTADO (simulação de perda, total={self.frames_dropped})"
            )
            return False

        data = frame
        if self.corruption_rate > 0 and random.random() < self.corruption_rate:
            self.frames_corrupted += 1
            ba = bytearray(frame)
            idx = random.randint(0, len(ba) - 1)
            ba[idx] ^= random.randint(1, 255)
            data = bytes(ba)
            logger.debug(
                f"  [NetCond] Frame CORROMPIDO byte[{idx}] (total={self.frames_corrupted})"
            )

        writer.write(data)
        await writer.drain()
        return True
