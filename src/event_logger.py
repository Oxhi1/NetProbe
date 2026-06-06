"""
Trafik İzleme / Olay Kayıt ve Metrik Toplama Modülü
===================================================

Aktarım sırasında oluşan ağ olaylarını (gönderim, ACK, timeout, yeniden
gönderim, duplicate ...) hem zaman damgalı bir CSV log dosyasına yazar hem de
özet sayaçları biriktirir. Aktarım sonunda performans metriklerini hesaplayıp
JSON özet dosyası üretir.

Föy karşılığı:
* 2.2 / 4.4 Olay kayıtları: SEND/ACK/TIMEOUT/RESEND/DUPLICATE olayları + sayaçlar
* 2.3 / 4.5 Performans metrikleri: throughput, goodput, loss rate, RTT, completion time
"""

from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path

# Olay türleri (CSV "event" kolonunda görünür)
EV_SEND = "SEND"            # bir DATA paketinin ilk gönderimi
EV_RESEND = "RESEND"        # zamanaşımı sonrası yeniden gönderim
EV_ACK = "ACK"             # ACK alındı
EV_TIMEOUT = "TIMEOUT"      # bir paket için zamanaşımı oluştu
EV_DUPLICATE = "DUPLICATE"  # aynı paket tekrar alındı (alıcı tarafı)
EV_RECV = "RECV"           # yeni DATA paketi alındı (alıcı tarafı)
EV_FAIL = "FAIL"           # paket maksimum denemeye rağmen iletilemedi
EV_INFO = "INFO"           # genel bilgi/durum mesajı


class EventLogger:
    """Thread-safe olay kaydedici ve metrik toplayıcı."""

    def __init__(self, log_path: str | Path, role: str = "client"):
        self.role = role
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._t0: float | None = None
        self._t_end: float | None = None

        # --- Sayaçlar -----------------------------------------------------
        self.packets_sent = 0          # benzersiz DATA paketi (ilk gönderim)
        self.total_transmissions = 0   # tüm DATA gönderimleri (yeniden dahil)
        self.retransmissions = 0       # yeniden gönderim sayısı
        self.acks_received = 0
        self.timeouts = 0
        self.failed_packets = 0
        self.duplicates = 0            # alıcıda yinelenen paket
        self.packets_received = 0      # alıcıda benzersiz DATA paketi
        self.bytes_sent = 0            # gönderilen toplam payload (yeniden dahil)
        self.good_bytes = 0            # başarıyla teslim edilen faydalı dosya baytı

        self._rtt_samples: list[float] = []

        # CSV dosyasını aç
        self._fh = open(self.log_path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(["wall_time", "elapsed_ms", "role", "event", "seq", "detail"])

    # --- Yaşam döngüsü ----------------------------------------------------
    def start(self) -> None:
        self._t0 = time.perf_counter()

    def _elapsed_ms(self) -> float:
        if self._t0 is None:
            return 0.0
        return (time.perf_counter() - self._t0) * 1000.0

    # --- Çekirdek olay yazımı --------------------------------------------
    def event(self, event_type: str, seq: int | str = "", detail: str = "") -> None:
        with self._lock:
            self._writer.writerow([
                f"{time.time():.6f}",
                f"{self._elapsed_ms():.3f}",
                self.role,
                event_type,
                seq,
                detail,
            ])

    # --- Üst düzey yardımcılar (sayaç + log birlikte) ---------------------
    def log_send(self, seq: int, nbytes: int, first_time: bool = True) -> None:
        with self._lock:
            self.total_transmissions += 1
            self.bytes_sent += nbytes
            if first_time:
                self.packets_sent += 1
        self.event(EV_SEND if first_time else EV_RESEND, seq, f"{nbytes}B")

    def log_resend(self, seq: int, nbytes: int, attempt: int) -> None:
        with self._lock:
            self.total_transmissions += 1
            self.retransmissions += 1
            self.bytes_sent += nbytes
        self.event(EV_RESEND, seq, f"deneme={attempt} {nbytes}B")

    def log_timeout(self, seq: int, attempt: int) -> None:
        with self._lock:
            self.timeouts += 1
        self.event(EV_TIMEOUT, seq, f"deneme={attempt}")

    def log_ack(self, seq: int, rtt_ms: float | None = None) -> None:
        with self._lock:
            self.acks_received += 1
            if rtt_ms is not None:
                self._rtt_samples.append(rtt_ms)
        self.event(EV_ACK, seq, f"rtt={rtt_ms:.2f}ms" if rtt_ms is not None else "")

    def log_fail(self, seq: int) -> None:
        with self._lock:
            self.failed_packets += 1
        self.event(EV_FAIL, seq, "maksimum yeniden deneme aşıldı")

    def log_recv(self, seq: int, nbytes: int) -> None:
        with self._lock:
            self.packets_received += 1
            self.good_bytes += nbytes
        self.event(EV_RECV, seq, f"{nbytes}B")

    def log_duplicate(self, seq: int) -> None:
        with self._lock:
            self.duplicates += 1
        self.event(EV_DUPLICATE, seq, "yinelenen paket yok sayıldı")

    def info(self, detail: str, seq: int | str = "") -> None:
        self.event(EV_INFO, seq, detail)

    def set_good_bytes(self, nbytes: int) -> None:
        """Gönderici tarafında faydalı (dosya) bayt miktarını doğrudan ayarlar."""
        with self._lock:
            self.good_bytes = nbytes

    # --- Anlık görüntü (canlı izleme paneli için) -------------------------
    def snapshot(self) -> dict:
        """O ana kadarki sayaçların thread-safe anlık görüntüsü."""
        with self._lock:
            elapsed = self._elapsed_ms() / 1000.0
            good_through = (self.good_bytes / elapsed) if elapsed > 0 else 0.0
            raw_through = (self.bytes_sent / elapsed) if elapsed > 0 else 0.0
            avg_rtt = (sum(self._rtt_samples) / len(self._rtt_samples)) if self._rtt_samples else 0.0
            return {
                "elapsed_s": elapsed,
                "packets_sent": self.packets_sent,
                "acks_received": self.acks_received,
                "retransmissions": self.retransmissions,
                "timeouts": self.timeouts,
                "throughput_Bps": raw_through,
                "goodput_Bps": good_through,
                "avg_rtt_ms": avg_rtt,
            }

    # --- Sonlandırma ve özet ---------------------------------------------
    def finalize(self) -> dict:
        self._t_end = time.perf_counter()
        summary = self.summary()
        # JSON özetini logun yanına yaz
        summary_path = self.log_path.with_suffix(".summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        with self._lock:
            self._fh.flush()
            self._fh.close()
        return summary

    def summary(self) -> dict:
        with self._lock:
            if self._t0 is None:
                duration = 0.0
            else:
                end = self._t_end if self._t_end is not None else time.perf_counter()
                duration = end - self._t0

            avg_rtt = (sum(self._rtt_samples) / len(self._rtt_samples)) if self._rtt_samples else 0.0
            min_rtt = min(self._rtt_samples) if self._rtt_samples else 0.0
            max_rtt = max(self._rtt_samples) if self._rtt_samples else 0.0

            # Retransmission oranı: yeniden gönderimler / toplam gönderimler
            retx_rate = (self.retransmissions / self.total_transmissions) if self.total_transmissions else 0.0
            # Gözlemlenen kayıp oranı: timeout sayısı / toplam gönderim
            obs_loss = (self.timeouts / self.total_transmissions) if self.total_transmissions else 0.0

            # Throughput: hat üzerindeki tüm payload baytları (yeniden dahil)
            # Goodput: yalnızca faydalı dosya baytları
            throughput_Bps = (self.bytes_sent / duration) if duration > 0 else 0.0
            goodput_Bps = (self.good_bytes / duration) if duration > 0 else 0.0

            return {
                "role": self.role,
                "completion_time_s": round(duration, 4),
                "packets_sent": self.packets_sent,
                "packets_received": self.packets_received,
                "total_transmissions": self.total_transmissions,
                "retransmissions": self.retransmissions,
                "timeouts": self.timeouts,
                "acks_received": self.acks_received,
                "duplicates": self.duplicates,
                "failed_packets": self.failed_packets,
                "bytes_sent": self.bytes_sent,
                "good_bytes": self.good_bytes,
                "throughput_Bps": round(throughput_Bps, 2),
                "throughput_Mbps": round(throughput_Bps * 8 / 1e6, 4),
                "goodput_Bps": round(goodput_Bps, 2),
                "goodput_Mbps": round(goodput_Bps * 8 / 1e6, 4),
                "retransmission_rate": round(retx_rate, 4),
                "observed_loss_rate": round(obs_loss, 4),
                "avg_rtt_ms": round(avg_rtt, 3),
                "min_rtt_ms": round(min_rtt, 3),
                "max_rtt_ms": round(max_rtt, 3),
            }
