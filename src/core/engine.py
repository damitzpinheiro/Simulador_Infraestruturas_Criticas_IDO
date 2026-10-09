"""Orquestra os geradores de trafego dos protocolos industriais."""

import asyncio
import statistics
import time
import logging
from typing import Dict, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("scada_trafgen.engine")


class SessionState(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ACTIVE = "active"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class TrafficStats:
    protocol: str
    packets_sent: int = 0
    packets_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    errors: int = 0
    frames_dropped: int = 0        # descartados por simulacao de perda
    window_blocks: int = 0         # vezes que _send_i_frame bloqueou por janela k cheia
    t1_timeouts: int = 0           # desconexoes por T1 timeout
    start_time: float = field(default_factory=time.time)
    _rtt_samples: List[float] = field(default_factory=list, repr=False)
    _asdu_type_counts: Dict[int, int] = field(default_factory=dict, repr=False)

    @property
    def duration(self) -> float:
        return time.time() - self.start_time

    @property
    def pps_sent(self) -> float:
        d = self.duration
        return self.packets_sent / d if d > 0 else 0

    @property
    def pps_received(self) -> float:
        d = self.duration
        return self.packets_received / d if d > 0 else 0

    @property
    def rtt_mean(self) -> float:
        return statistics.mean(self._rtt_samples) if self._rtt_samples else 0.0

    @property
    def rtt_p50(self) -> float:
        return statistics.median(self._rtt_samples) if self._rtt_samples else 0.0

    @property
    def rtt_p95(self) -> float:
        if len(self._rtt_samples) < 2:
            return self._rtt_samples[0] if self._rtt_samples else 0.0
        qs = statistics.quantiles(self._rtt_samples, n=100)
        return qs[94]  # indice 94 = P95

    @property
    def rtt_p99(self) -> float:
        if len(self._rtt_samples) < 2:
            return self._rtt_samples[0] if self._rtt_samples else 0.0
        qs = statistics.quantiles(self._rtt_samples, n=100)
        return qs[98]  # indice 98 = P99

    @property
    def packet_loss_rate(self) -> float:
        total = self.packets_sent + self.frames_dropped
        return self.frames_dropped / total if total > 0 else 0.0

    def record_sent(self, nbytes: int):
        self.packets_sent += 1
        self.bytes_sent += nbytes

    def record_received(self, nbytes: int):
        self.packets_received += 1
        self.bytes_received += nbytes

    def record_error(self):
        self.errors += 1

    def record_dropped(self):
        self.frames_dropped += 1

    def record_rtt(self, rtt_ms: float):
        self._rtt_samples.append(rtt_ms)

    def record_window_block(self):
        self.window_blocks += 1

    def record_t1_timeout(self):
        self.t1_timeouts += 1

    def record_asdu_type(self, type_id: int):
        self._asdu_type_counts[type_id] = self._asdu_type_counts.get(type_id, 0) + 1

    def summary(self) -> dict:
        s: dict = {
            "protocol": self.protocol,
            "duration_s": round(self.duration, 1),
            "sent": {"packets": self.packets_sent, "bytes": self.bytes_sent},
            "received": {"packets": self.packets_received, "bytes": self.bytes_received},
            "pps_sent": round(self.pps_sent, 2),
            "pps_received": round(self.pps_received, 2),
            "errors": self.errors,
            "frames_dropped": self.frames_dropped,
            "packet_loss_rate_pct": round(self.packet_loss_rate * 100, 2),
            "window_blocks": self.window_blocks,
            "t1_timeouts": self.t1_timeouts,
        }
        if self._rtt_samples:
            s["rtt_ms"] = {
                "n": len(self._rtt_samples),
                "mean": round(self.rtt_mean, 2),
                "p50": round(self.rtt_p50, 2),
                "p95": round(self.rtt_p95, 2),
                "p99": round(self.rtt_p99, 2),
            }
        if self._asdu_type_counts:
            s["asdu_types"] = dict(sorted(self._asdu_type_counts.items()))
        return s


class BaseProtocolGenerator:
    def __init__(self, config: dict):
        self.config = config
        self.state = SessionState.IDLE
        self.stats = TrafficStats(protocol=self.protocol_name)
        self._tasks: List[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    @property
    def protocol_name(self) -> str:
        raise NotImplementedError

    async def start(self):
        raise NotImplementedError

    async def stop(self):
        logger.info(f"[{self.protocol_name}] Parando gerador...")
        self._stop_event.set()
        self.state = SessionState.STOPPING
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.state = SessionState.STOPPED
        logger.info(f"[{self.protocol_name}] Gerador parado.")

    def is_running(self) -> bool:
        return self.state in (SessionState.CONNECTED, SessionState.ACTIVE)


class MultiTargetGenerator(BaseProtocolGenerator):
    """Um master/client que mantem N conexoes independentes, uma por alvo.

    Modela um centro de controle (SCADA) polando varios RTUs a partir de um
    mesmo processo. Cada alvo e um sub-gerador de ALVO UNICO (a classe single-
    target ja testada); todos compartilham as estatisticas (visao agregada) e
    cada conexao tem reconexao propria: a queda de um alvo nao derruba os outros.

    Config: `targets: [{host, port, ...}, ...]`. Sem `targets`, cai no alvo unico
    a partir do proprio config (retrocompativel).
    """

    SINGLE_CLASS = None        # sobrescrever: a classe de master/client single-target
    _PROTO_NAME = "MultiTarget"

    def __init__(self, config: dict):
        self._subs: List[BaseProtocolGenerator] = []
        self._forced_state = SessionState.IDLE
        super().__init__(config)
        base = {k: v for k, v in config.items() if k != "targets"}
        for t in (config.get("targets") or [{}]):
            sub = self.SINGLE_CLASS({**base, **t})
            sub.stats = self.stats            # estatisticas compartilhadas (agregado)
            self._subs.append(sub)
        self._retry_delay = int(config.get("retry_delay", 5))

    @property
    def protocol_name(self) -> str:
        return self._PROTO_NAME

    @property
    def state(self) -> SessionState:
        if self._subs:
            estados = [s.state for s in self._subs]
            if any(e == SessionState.ACTIVE for e in estados):
                return SessionState.ACTIVE
            if any(e in (SessionState.CONNECTED, SessionState.CONNECTING) for e in estados):
                return SessionState.CONNECTING
            return estados[0]
        return self._forced_state

    @state.setter
    def state(self, value):
        self._forced_state = value

    @property
    def last_values(self) -> dict:
        """Agrega o last_values dos sub-masters (usado por 'dp master'/'watch')."""
        merged: dict = {}
        for s in self._subs:
            lv = getattr(s, "last_values", None)
            if lv:
                merged.update(lv)
        return merged

    async def _run_sub(self, sub: BaseProtocolGenerator):
        """Roda um alvo com reconexao propria, independente dos demais."""
        alvo = getattr(sub, "host", None) or getattr(sub, "url", "?")
        while not self._stop_event.is_set():
            try:
                await sub.start()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.stats.record_error()
                logger.error(f"[{self._PROTO_NAME} -> {alvo}] {e}")
            if self._stop_event.is_set():
                break
            try:
                await asyncio.sleep(self._retry_delay)
            except asyncio.CancelledError:
                break

    async def start(self):
        self._stop_event.clear()
        self._tasks = [asyncio.create_task(self._run_sub(s)) for s in self._subs]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self._stop_event.set()
        for s in self._subs:
            try:
                await s.stop()
            except Exception:
                pass
        for t in self._tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


class Engine:
    def __init__(self, config: dict):
        self.config = config
        self.generators: Dict[str, BaseProtocolGenerator] = {}
        self._running = False
        # controle por gerador: cada um pode ser iniciado/pausado individualmente
        self._gen_tasks: Dict[str, asyncio.Task] = {}
        self._gen_enabled: Dict[str, bool] = {}
        self._stats_task = None
        self._stats_interval = config.get("stats_interval", 10)

    def register_generator(self, name: str, generator: BaseProtocolGenerator):
        self.generators[name] = generator
        self._gen_enabled[name] = False
        logger.info(f"Gerador registrado: {name} ({generator.protocol_name})")

    def replace_generator(self, name: str, generator: BaseProtocolGenerator):
        """Troca um gerador ja registrado por outra instancia (deve estar pausado)."""
        self.generators[name] = generator
        self._gen_enabled.setdefault(name, False)

    async def start(self, autostart: bool = True):
        """Sobe o reporter de stats e, se autostart, inicia todos os geradores.

        Retorna logo apos lancar as tasks (nao bloqueia) - o ciclo de vida
        passa a ser controlado por quem chamou (console interativo ou duracao).
        """
        self._running = True
        self._stats_task = asyncio.create_task(self._stats_reporter())
        if autostart:
            logger.info(f"Iniciando engine com {len(self.generators)} gerador(es)...")
            for name in self.generators:
                self.start_generator(name)
        else:
            logger.info(f"Engine pronta ({len(self.generators)} geradores registrados, "
                        f"nenhum iniciado)")

    def _is_alive(self, name: str) -> bool:
        t = self._gen_tasks.get(name)
        return t is not None and not t.done()

    def start_generator(self, name: str) -> bool:
        """Inicia um gerador individual. Retorna False se ja estava rodando."""
        if name not in self.generators:
            return False
        if self._gen_enabled.get(name) and self._is_alive(name):
            return False
        self._gen_enabled[name] = True
        gen = self.generators[name]
        gen.state = SessionState.IDLE
        logger.info(f"  -> Iniciando {name}...")
        self._gen_tasks[name] = asyncio.create_task(self._run_generator(name, gen))
        return True

    async def pause_generator(self, name: str) -> bool:
        """Pausa um gerador individual sem afetar os demais."""
        if name not in self.generators or not self._gen_enabled.get(name):
            return False
        self._gen_enabled[name] = False
        task = self._gen_tasks.get(name)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.generators[name].stop()
        return True

    def status_rows(self) -> List[dict]:
        """Estado de cada gerador, para o console."""
        rows = []
        for name, gen in self.generators.items():
            rows.append({
                "name": name,
                "role": self.role_of(name),
                "state": gen.state.value,
                "enabled": bool(self._gen_enabled.get(name)),
                "alive": self._is_alive(name),
            })
        return rows

    @staticmethod
    def role_of(name: str) -> str:
        n = name.lower()
        if any(k in n for k in ("slave", "outstation", "server")):
            return "slave/servidor"
        if any(k in n for k in ("master", "client")):
            return "master/cliente"
        return "-"

    async def stop(self):
        self._running = False
        logger.info("Parando engine...")
        if self._stats_task and not self._stats_task.done():
            self._stats_task.cancel()
            try:
                await self._stats_task
            except (asyncio.CancelledError, Exception):
                pass
        for name in list(self.generators):
            self._gen_enabled[name] = False
            task = self._gen_tasks.get(name)
            if task and not task.done():
                task.cancel()
        for name in list(self.generators):
            task = self._gen_tasks.get(name)
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            await self.generators[name].stop()
        logger.info("Engine parada.")
        self._print_final_stats()

    async def _run_generator(self, name: str, gen: BaseProtocolGenerator):
        retry_delay = self.config.get("retry_delay", 5)
        max_retries = self.config.get("max_retries", -1)
        retries = 0

        while self._running and self._gen_enabled.get(name):
            try:
                await gen.start()
                # retorno normal: aguarda antes de reiniciar (reconexao)
                if self._running and self._gen_enabled.get(name):
                    await asyncio.sleep(min(retry_delay, 2))
            except asyncio.CancelledError:
                break
            except Exception as e:
                retries += 1
                gen.stats.record_error()
                logger.error(f"[{name}] Erro: {e}")
                if 0 < max_retries <= retries:
                    logger.error(f"[{name}] Max retries atingido ({max_retries})")
                    break
                logger.info(f"[{name}] Reconectando em {retry_delay}s (retry {retries})...")
                try:
                    await asyncio.sleep(retry_delay)
                except asyncio.CancelledError:
                    break

    async def _stats_reporter(self):
        while self._running:
            await asyncio.sleep(self._stats_interval)
            if not self._running:
                break
            self._print_stats()

    def _print_stats(self):
        logger.info("=" * 65)
        logger.info("  ESTATISTICAS DE TRAFEGO")
        logger.info("-" * 65)
        for name, gen in self.generators.items():
            s = gen.stats
            plr = f"PLR={s.packet_loss_rate * 100:.1f}% " if s.frames_dropped > 0 else ""
            rtt = f"RTT={s.rtt_mean:.1f}ms " if s._rtt_samples else ""
            logger.info(
                f"  [{name}] TX: {s.packets_sent} pkts ({s.bytes_sent} B) | "
                f"RX: {s.packets_received} pkts ({s.bytes_received} B) | "
                f"PPS: {s.pps_sent:.1f}/{s.pps_received:.1f} | "
                f"{plr}{rtt}Erros: {s.errors} | Estado: {gen.state.value}"
            )
        logger.info("=" * 65)

    def _report_generator(self, name: str, emit):
        gen = self.generators[name]
        s = gen.stats.summary()
        emit("")
        emit(f"  [{name}] ({s['protocol']})")
        emit(f"    Estado:       {gen.state.value}")
        emit(f"    Duracao:      {s['duration_s']}s")
        emit(f"    Enviados:     {s['sent']['packets']} pacotes ({s['sent']['bytes']} bytes)")
        emit(f"    Recebidos:    {s['received']['packets']} pacotes ({s['received']['bytes']} bytes)")
        emit(f"    Taxa:         {s['pps_sent']:.2f} pps TX / {s['pps_received']:.2f} pps RX")
        emit(f"    Perdidos:     {s['frames_dropped']} frames (PLR={s['packet_loss_rate_pct']}%)")
        emit(f"    Erros:        {s['errors']}")
        emit(f"    Bloq. janela: {s['window_blocks']}")
        emit(f"    T1 timeouts:  {s['t1_timeouts']}")
        if "rtt_ms" in s:
            r = s["rtt_ms"]
            emit(
                f"    RTT (n={r['n']}):  "
                f"mean={r['mean']}ms | P50={r['p50']}ms | "
                f"P95={r['p95']}ms | P99={r['p99']}ms"
            )
        if "asdu_types" in s:
            emit(f"    ASDU por tipo: {s['asdu_types']}")

    def print_report(self, names=None, titulo: str = "RELATORIO", emit=None):
        """Imprime o relatorio de um ou mais geradores (mesmo formato do relatorio final).

        names=None imprime todos; uma lista imprime so os informados.
        emit e a funcao de saida: por padrao logger.info; o console interativo
        passa 'print' porque nesse modo o log fica em WARNING e info nao apareceria.
        """
        if emit is None:
            emit = logger.info
        if names is None:
            names = list(self.generators)
        emit("")
        emit("=" * 65)
        emit(f"  {titulo}")
        emit("=" * 65)
        for name in names:
            if name in self.generators:
                self._report_generator(name, emit)
        emit("=" * 65)

    def _print_final_stats(self):
        self.print_report(None, titulo="RELATORIO FINAL")
