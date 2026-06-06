"""
NetProbe Sunucu (Alıcı)
=======================

UDP üzerinden güvenilir dosya aktarımını alan taraf. Görevleri:

* META paketinden dosya meta verisini okuyup onaylamak,
* gelen DATA paketlerinin checksum'ını doğrulamak,
* her alınan paket için ACK üretmek (yinelenenler için ACK'i tekrarlamak),
* paketleri sıra numarasına göre yeniden birleştirmek,
* yinelenen (duplicate) paketleri ikinci kez dosyaya yazmamak,
* aktarım sonunda SHA-256 ile dosya bütünlüğünü doğrulamak,
* tüm olayları event_logger ile kayıt altına almak.

Kullanım:
    python -m src.server --host 0.0.0.0 --port 9000 --out received/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
from pathlib import Path

from . import protocol as proto
from .event_logger import EventLogger
from .netsim import wrap_socket


def receive_file(
    sock,
    out_dir: Path,
    logger: EventLogger,
    idle_timeout: float = 10.0,
) -> dict:
    """
    Tek bir dosya aktarımını alır ve sonucu özet dict olarak döndürür.

    Akış: META → DATA* → FIN. Soket üzerinde settimeout ayarlı olmalıdır.
    """
    client_addr = None
    meta: dict | None = None
    total_packets = 0
    received: dict[int, bytes] = {}   # seq -> payload (yalnızca benzersiz)
    file_written = False
    result: dict = {"status": "incomplete"}
    last_activity = time.perf_counter()

    logger.start()
    logger.info("Sunucu aktarım bekliyor")

    while True:
        # Boşta kalma zamanaşımı kontrolü
        if time.perf_counter() - last_activity > idle_timeout:
            logger.info("Boşta kalma zamanaşımı; aktarım yarıda kesildi")
            result["status"] = "timeout"
            break

        try:
            datagram, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break

        last_activity = time.perf_counter()

        try:
            pkt = proto.unpack(datagram)
        except proto.CorruptPacket as e:
            # Bozuk paket: sessizce yok say (gönderici zamanaşımıyla yeniden yollar)
            logger.event("CORRUPT", "", str(e))
            continue

        if client_addr is None:
            client_addr = addr

        # --- META paketi -------------------------------------------------
        if pkt.type == proto.PKT_META:
            try:
                meta = json.loads(pkt.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.event("CORRUPT", "", "META JSON çözümlenemedi")
                continue
            total_packets = int(meta["total_packets"])
            if not received:  # ilk META
                logger.info(
                    f"META alındı: dosya='{meta['filename']}' boyut={meta['filesize']}B "
                    f"paketler={total_packets} payload={meta.get('payload_size')}B"
                )
            # META'yı her alışta onayla (önceki META-ACK kaybolmuş olabilir)
            sock.sendto(proto.pack_ack(proto.ACK_META), client_addr)
            continue

        # --- DATA paketi -------------------------------------------------
        if pkt.type == proto.PKT_DATA:
            if meta is None:
                # META gelmeden DATA geldi; yine de ACK'le ki gönderici ilerlesin
                # (META retransmit edilecektir). Veriyi tamponlamak için total
                # paket bilgisini DATA başlığından da alabiliriz.
                total_packets = pkt.total
            if pkt.seq in received:
                # Yinelenen paket: dosyaya tekrar yazma, sadece ACK'i tekrarla.
                logger.log_duplicate(pkt.seq)
            else:
                received[pkt.seq] = pkt.payload
                logger.log_recv(pkt.seq, len(pkt.payload))
            # Her durumda ACK gönder.
            sock.sendto(proto.pack_ack(pkt.seq), client_addr)

            # Tüm paketler tamamlandıysa dosyayı yaz ve doğrula.
            if not file_written and total_packets > 0 and len(received) == total_packets:
                result = _finalize_file(received, total_packets, meta, out_dir, logger)
                file_written = True
            continue

        # --- FIN paketi --------------------------------------------------
        if pkt.type == proto.PKT_FIN:
            logger.info("FIN alındı")
            # Henüz tüm paketler gelmediyse FIN'i de onaylayıp beklemeye devam et.
            if not file_written and total_packets > 0 and len(received) == total_packets:
                result = _finalize_file(received, total_packets, meta, out_dir, logger)
                file_written = True
            sock.sendto(proto.pack_fin(), client_addr)  # FIN-ACK
            if file_written:
                # Olası FIN retransmitlerine kısa süre yanıt verip kapan.
                result["status"] = result.get("status", "ok")
                break
            else:
                # Eksik paket var; göndericinin retransmitlerini beklemeye devam et.
                continue

    return result


def _finalize_file(
    received: dict[int, bytes],
    total_packets: int,
    meta: dict | None,
    out_dir: Path,
    logger: EventLogger,
) -> dict:
    """Paketleri sıraya göre birleştirir, diske yazar ve bütünlüğü doğrular."""
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = meta["filename"] if meta else f"received_{int(time.time())}.bin"
    out_path = out_dir / Path(filename).name

    data = b"".join(received[i] for i in range(total_packets))
    out_path.write_bytes(data)

    sha = hashlib.sha256(data).hexdigest()
    expected = meta.get("sha256") if meta else None
    integrity_ok = (expected is None) or (sha == expected)

    logger.info(
        f"Dosya yazıldı: {out_path} ({len(data)}B) | SHA-256 {'DOĞRU' if integrity_ok else 'HATALI'}"
    )

    return {
        "status": "ok" if integrity_ok else "integrity_error",
        "output_path": str(out_path),
        "received_bytes": len(data),
        "sha256": sha,
        "expected_sha256": expected,
        "integrity_ok": integrity_ok,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NetProbe UDP sunucu (alıcı)")
    parser.add_argument("--host", default="0.0.0.0", help="Dinlenecek arayüz (varsayılan 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9000, help="Dinlenecek UDP portu")
    parser.add_argument("--out", default="received", help="Alınan dosyaların yazılacağı klasör")
    parser.add_argument("--log", default="logs/server.csv", help="Olay log CSV yolu")
    parser.add_argument("--idle-timeout", type=float, default=10.0,
                        help="Bu kadar saniye paket gelmezse aktarım iptal edilir")
    parser.add_argument("--sock-timeout", type=float, default=0.5,
                        help="recvfrom soket zamanaşımı (saniye)")
    # Bonus: ACK kaybını test etmek için sunucu tarafı kayıp simülasyonu
    parser.add_argument("--ack-loss", type=float, default=0.0,
                        help="Sunucudan giden ACK'ler için yapay kayıp oranı [0-1]")
    parser.add_argument("--seed", type=int, default=None, help="Simülasyon RNG tohumu")
    args = parser.parse_args(argv)

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw_sock.bind((args.host, args.port))
    raw_sock.settimeout(args.sock_timeout)

    sock = wrap_socket(raw_sock, loss_rate=args.ack_loss, seed=args.seed)

    logger = EventLogger(args.log, role="server")
    print(f"[server] {args.host}:{args.port} üzerinde dinleniyor → çıktı klasörü: {args.out}")

    try:
        result = receive_file(sock, Path(args.out), logger, idle_timeout=args.idle_timeout)
    finally:
        summary = logger.finalize()
        sock.close()

    print("[server] Aktarım sonucu:", json.dumps(result, ensure_ascii=False, indent=2))
    print("[server] Özet metrikler:", json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
