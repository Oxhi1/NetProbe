"""
NetProbe İstemci (Gönderici)
============================

UDP üzerinden güvenilir dosya aktarımının gönderen tarafı. Güvenilirlik
mekanizmaları uygulama katmanında elle gerçekleştirilir:

* Dosya, ``payload_size`` baytlık parçalara bölünür ve her parçaya bir
  sequence number atanır.
* Her gönderilen paket için zamanlayıcı tutulur; ``timeout`` süresi içinde
  ACK gelmezse paket yeniden gönderilir (en fazla ``max_retries`` kez).
* **Selective Repeat** sliding window: aynı anda ``window`` kadar paket
  havada (in-flight) tutulabilir. ``window=1`` özel durumu klasik
  **Stop-and-Wait** protokolüne karşılık gelir.
* RTT örnekleri yalnızca ilk denemede ACK'lenen paketlerden alınır
  (Karn algoritması) — yeniden gönderim belirsizliği RTT ölçümünü bozmaz.

Kullanım:
    python -m src.client --host 127.0.0.1 --port 9000 --file data/test.bin \
        --window 8 --timeout 200 --payload-size 1024 --loss 0.05
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import threading
import time
from pathlib import Path

from . import protocol as proto
from .event_logger import EventLogger
from .netsim import wrap_socket


class _LiveStatusWriter(threading.Thread):
    """Belirli aralıklarla logger anlık görüntüsünü JSON dosyasına yazar
    (canlı izleme paneli bu dosyayı okur)."""

    def __init__(self, logger: EventLogger, path: Path, interval: float = 0.2):
        super().__init__(daemon=True)
        self._logger = logger
        self._path = Path(path)
        self._interval = interval
        self._stop = threading.Event()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        while not self._stop.is_set():
            self._dump()
            self._stop.wait(self._interval)
        self._dump()  # son durum

    def _dump(self) -> None:
        try:
            snap = self._logger.snapshot()
            snap["running"] = not self._stop.is_set()
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap), encoding="utf-8")
            tmp.replace(self._path)
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        self._stop.set()


class ReliableSender:
    """Selective Repeat tabanlı güvenilir UDP dosya göndericisi."""

    def __init__(
        self,
        sock,
        server_addr,
        logger: EventLogger,
        payload_size: int = proto.DEFAULT_PAYLOAD_SIZE,
        window: int = 1,
        timeout: float = 0.2,      # saniye
        max_retries: int = 5,
    ):
        self.sock = sock
        self.server_addr = server_addr
        self.log = logger
        self.payload_size = payload_size
        self.window = max(1, window)
        self.timeout = timeout
        self.max_retries = max_retries

    # --- Yardımcı el sıkışma adımları ------------------------------------
    def _handshake(self, meta_bytes: bytes, total: int) -> bool:
        """META gönderir, META-ACK gelene kadar (sınırlı denemeyle) yeniden dener."""
        packet = proto.pack_meta(total, meta_bytes)
        for attempt in range(1, self.max_retries + 2):
            self.sock.sendto(packet, self.server_addr)
            self.log.event("META_SEND", 0, f"deneme={attempt}")
            self.sock.settimeout(self.timeout)
            deadline = time.perf_counter() + self.timeout
            while time.perf_counter() < deadline:
                try:
                    data, _ = self.sock.recvfrom(65535)
                except socket.timeout:
                    break
                except OSError:
                    return False
                try:
                    pkt = proto.unpack(data)
                except proto.CorruptPacket:
                    continue
                if pkt.type == proto.PKT_ACK and pkt.seq == proto.ACK_META:
                    self.log.event("META_ACK", 0)
                    return True
            self.log.log_timeout("META", attempt)
        return False

    def _teardown(self) -> None:
        """FIN gönderir ve FIN-ACK bekler (best-effort kapanış)."""
        packet = proto.pack_fin()
        for attempt in range(1, self.max_retries + 2):
            self.sock.sendto(packet, self.server_addr)
            self.log.event("FIN_SEND", "", f"deneme={attempt}")
            self.sock.settimeout(self.timeout)
            try:
                data, _ = self.sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            try:
                pkt = proto.unpack(data)
            except proto.CorruptPacket:
                continue
            if pkt.type == proto.PKT_FIN:
                self.log.event("FIN_ACK", "")
                return
        self.log.info("FIN-ACK alınamadı (kapanış yine de tamamlandı)")

    # --- Ana aktarım döngüsü ---------------------------------------------
    def send_file(self, path: Path) -> dict:
        data = Path(path).read_bytes()
        filesize = len(data)
        sha = hashlib.sha256(data).hexdigest()

        # Parçalara böl
        chunks = [data[i:i + self.payload_size] for i in range(0, filesize, self.payload_size)]
        if not chunks:                       # boş dosya
            chunks = [b""]
        total = len(chunks)

        meta = {
            "filename": Path(path).name,
            "filesize": filesize,
            "total_packets": total,
            "payload_size": self.payload_size,
            "sha256": sha,
        }

        self.log.start()
        self.log.set_good_bytes(filesize)
        self.log.info(
            f"Aktarım başlıyor: {meta['filename']} ({filesize}B, {total} paket, "
            f"window={self.window}, timeout={self.timeout*1000:.0f}ms)"
        )

        if not self._handshake(json.dumps(meta).encode("utf-8"), total):
            self.log.info("El sıkışma başarısız (META-ACK yok); aktarım iptal")
            return {"status": "handshake_failed", **meta}

        # Per-paket durum
        acked = [False] * total
        abandoned = [False] * total
        attempts = [0] * total
        send_time = [0.0] * total

        base = 0          # en küçük tamamlanmamış (ack'lenmemiş/terk edilmemiş) seq
        next_seq = 0      # gönderilecek bir sonraki seq

        def _is_done(i: int) -> bool:
            return acked[i] or abandoned[i]

        def _transmit(seq: int, first: bool) -> None:
            packet = proto.pack_data(seq, total, chunks[seq])
            self.sock.sendto(packet, self.server_addr)
            send_time[seq] = time.perf_counter()
            attempts[seq] += 1
            if first:
                self.log.log_send(seq, len(chunks[seq]), first_time=True)
            else:
                self.log.log_resend(seq, len(chunks[seq]), attempts[seq])

        while base < total:
            # 1) Pencereyi yeni paketlerle doldur
            while next_seq < total and next_seq < base + self.window:
                _transmit(next_seq, first=True)
                next_seq += 1

            # 2) En yakın zamanaşımına kadar ACK dinle
            poll = self._poll_timeout(base, next_seq, acked, abandoned, send_time)
            self.sock.settimeout(poll)
            try:
                resp, _ = self.sock.recvfrom(65535)
                self._handle_ack(resp, acked, abandoned, attempts, send_time)
            except socket.timeout:
                pass
            except OSError:
                break

            # 3) Süresi dolan paketleri yeniden gönder / terk et
            now = time.perf_counter()
            for seq in range(base, next_seq):
                if _is_done(seq):
                    continue
                if now - send_time[seq] >= self.timeout:
                    self.log.log_timeout(seq, attempts[seq])
                    if attempts[seq] >= self.max_retries + 1:
                        # 1 ilk gönderim + max_retries yeniden gönderim tükendi
                        abandoned[seq] = True
                        self.log.log_fail(seq)
                    else:
                        _transmit(seq, first=False)

            # 4) base'i ilerlet
            while base < total and _is_done(base):
                base += 1

        # Tüm paketler tamamlandıysa düzgün kapanış yap
        failed = sum(abandoned)
        if failed == 0:
            self._teardown()

        summary = self.log.finalize()
        status = "ok" if failed == 0 else "failed"
        result = {"status": status, "failed_packets": failed, **meta, **summary}
        return result

    def _handle_ack(self, datagram, acked, abandoned, attempts, send_time) -> None:
        try:
            pkt = proto.unpack(datagram)
        except proto.CorruptPacket:
            return
        if pkt.type != proto.PKT_ACK:
            return
        seq = pkt.seq
        if seq in (proto.ACK_META, proto.ACK_FIN):
            return  # gecikmiş el sıkışma onayları; yok say
        if 0 <= seq < len(acked) and not acked[seq]:
            acked[seq] = True
            # RTT yalnızca ilk denemede ACK'lenenden alınır (Karn algoritması)
            rtt_ms = None
            if attempts[seq] == 1:
                rtt_ms = (time.perf_counter() - send_time[seq]) * 1000.0
            self.log.log_ack(seq, rtt_ms)

    def _poll_timeout(self, base, next_seq, acked, abandoned, send_time) -> float:
        """Havadaki paketler arasında en yakın zamanaşımına kalan süre (sn)."""
        now = time.perf_counter()
        nearest = self.timeout
        in_flight = False
        for seq in range(base, next_seq):
            if acked[seq] or abandoned[seq]:
                continue
            in_flight = True
            remaining = self.timeout - (now - send_time[seq])
            nearest = min(nearest, remaining)
        if not in_flight:
            return 0.001
        return min(self.timeout, max(0.001, nearest))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NetProbe UDP istemci (gönderici)")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu IP/host")
    parser.add_argument("--port", type=int, default=9000, help="Sunucu UDP portu")
    parser.add_argument("--file", required=True, help="Gönderilecek dosya yolu")
    parser.add_argument("--payload-size", type=int, default=proto.DEFAULT_PAYLOAD_SIZE,
                        help="Paket başına payload boyutu (bayt)")
    parser.add_argument("--window", type=int, default=1,
                        help="Sliding window boyutu (1 = Stop-and-Wait)")
    parser.add_argument("--timeout", type=float, default=200.0,
                        help="Retransmission zamanaşımı (ms)")
    parser.add_argument("--max-retries", type=int, default=5,
                        help="Paket başına maksimum yeniden gönderim sayısı (varsayılan 5)")
    # Bonus: ağ koşulu simülasyonu
    parser.add_argument("--loss", type=float, default=0.0, help="Yapay paket kayıp oranı [0-1]")
    parser.add_argument("--delay", type=float, default=0.0, help="Ortalama tek yön gecikme (ms)")
    parser.add_argument("--jitter", type=float, default=0.0, help="Gecikme jitter'ı ± (ms)")
    parser.add_argument("--seed", type=int, default=None, help="Simülasyon RNG tohumu")
    parser.add_argument("--log", default="logs/client.csv", help="Olay log CSV yolu")
    parser.add_argument("--live-status", default=None,
                        help="Verilirse canlı metrikleri bu JSON dosyasına yazar")
    args = parser.parse_args(argv)

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock = wrap_socket(
        raw_sock,
        loss_rate=args.loss,
        delay_ms=args.delay,
        jitter_ms=args.jitter,
        seed=args.seed,
    )

    logger = EventLogger(args.log, role="client")
    live_writer = None
    if args.live_status:
        live_writer = _LiveStatusWriter(logger, Path(args.live_status))
        live_writer.start()

    sender = ReliableSender(
        sock,
        (args.host, args.port),
        logger,
        payload_size=args.payload_size,
        window=args.window,
        timeout=args.timeout / 1000.0,
        max_retries=args.max_retries,
    )

    print(f"[client] {args.host}:{args.port} → '{args.file}' gönderiliyor "
          f"(window={args.window}, timeout={args.timeout:.0f}ms, loss={args.loss}, delay={args.delay}ms)")

    try:
        result = sender.send_file(Path(args.file))
    finally:
        if live_writer:
            live_writer.stop()
        sock.close()

    print("[client] Sonuç:", json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
