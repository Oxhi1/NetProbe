"""
Gerçek Zamanlı Görselleştirme Paneli (Bonus)
============================================

İstemcinin ``--live-status`` ile ürettiği JSON durum dosyasını periyodik olarak
okuyup aktarımın gidişatını canlı olarak çizer:

* Üstte: anlık throughput ve goodput (KB/s) zaman serisi,
* Ortada: ortalama RTT (ms) zaman serisi,
* Altta: kümülatif sayaçlar (gönderilen, ACK, yeniden gönderim, timeout).

Kullanım (istemciyi ayrı bir terminalde --live-status ile başlattıktan sonra):
    python -m src.live_monitor logs/client.live.json

Not: Matplotlib gerektirir. Aktarım bittiğinde (durum dosyasında running=false)
panel son kareyi gösterip beklemeye geçer; pencereyi kapatarak çıkabilirsiniz.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_panel(status_path: str, interval: float = 0.3, idle_exit: float = 5.0) -> int:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib kurulu değil. `pip install matplotlib` çalıştırın.", file=sys.stderr)
        return 2

    path = Path(status_path)
    print(f"[monitor] '{path}' izleniyor... (panel penceresini kapatarak çıkın)")

    t_hist, thr_hist, good_hist, rtt_hist = [], [], [], []

    plt.ion()
    fig, (ax_thr, ax_rtt, ax_cnt) = plt.subplots(3, 1, figsize=(8, 8))
    fig.suptitle("NetProbe — Canlı Aktarım İzleme")

    last_change = time.time()
    last_snapshot = None
    finished_since = None

    while True:
        snap = _read_status(path)
        if snap is not None and snap != last_snapshot:
            last_snapshot = snap
            last_change = time.time()
            t = snap.get("elapsed_s", 0.0)
            t_hist.append(t)
            thr_hist.append(snap.get("throughput_Bps", 0.0) / 1024.0)
            good_hist.append(snap.get("goodput_Bps", 0.0) / 1024.0)
            rtt_hist.append(snap.get("avg_rtt_ms", 0.0))

            # --- Çizim ---
            ax_thr.clear()
            ax_thr.plot(t_hist, thr_hist, label="Throughput (KB/s)", color="tab:blue")
            ax_thr.plot(t_hist, good_hist, label="Goodput (KB/s)", color="tab:green")
            ax_thr.set_ylabel("KB/s")
            ax_thr.legend(loc="upper left")
            ax_thr.grid(True, alpha=0.3)

            ax_rtt.clear()
            ax_rtt.plot(t_hist, rtt_hist, label="Ortalama RTT (ms)", color="tab:orange")
            ax_rtt.set_ylabel("ms")
            ax_rtt.legend(loc="upper left")
            ax_rtt.grid(True, alpha=0.3)

            ax_cnt.clear()
            labels = ["Gönderilen", "ACK", "Yeniden", "Timeout"]
            values = [
                snap.get("packets_sent", 0),
                snap.get("acks_received", 0),
                snap.get("retransmissions", 0),
                snap.get("timeouts", 0),
            ]
            bars = ax_cnt.bar(labels, values,
                              color=["tab:blue", "tab:green", "tab:red", "tab:purple"])
            ax_cnt.set_ylabel("adet")
            ax_cnt.set_title(f"t = {t:.1f} s")
            for b, v in zip(bars, values):
                ax_cnt.text(b.get_x() + b.get_width() / 2, b.get_height(),
                            str(v), ha="center", va="bottom", fontsize=9)

            fig.tight_layout(rect=(0, 0, 1, 0.96))

        # Aktarım bitti mi?
        if last_snapshot is not None and not last_snapshot.get("running", True):
            if finished_since is None:
                finished_since = time.time()
                print("[monitor] Aktarım tamamlandı. Pencere açık kalacak.")

        # Pencere kapatıldıysa çık
        if not plt.fignum_exists(fig.number):
            break

        # Uzun süre güncelleme yoksa (ve bitmişse) panel açık kalsın ama döngü
        # CPU yakmasın diye yavaşlasın.
        plt.pause(interval)

        # İstemci hiç başlamadı / kayboldu kontrolü (yalnızca henüz veri yokken)
        if last_snapshot is None and time.time() - last_change > idle_exit:
            print(f"[monitor] {idle_exit:.0f}s boyunca durum dosyası bulunamadı; çıkılıyor.",
                  file=sys.stderr)
            break

    plt.ioff()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NetProbe canlı izleme paneli")
    parser.add_argument("status", help="İstemcinin yazdığı canlı durum JSON dosyası")
    parser.add_argument("--interval", type=float, default=0.3, help="Yenileme aralığı (sn)")
    args = parser.parse_args(argv)
    return run_panel(args.status, interval=args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
