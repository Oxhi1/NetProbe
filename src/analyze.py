"""
Performans Analizi ve Grafik Üretimi
====================================

``results/experiments.csv`` dosyasını okuyup her senaryo için performans
grafikleri üretir ve ``results/figures/`` altına PNG olarak kaydeder. Aynı
parametre noktası birden fazla kez (repeat) çalıştırıldıysa değerler ortalanır.

Üretilen grafikler (föy 2.3 / 4.5 metrikleri):
* Payload boyutu → goodput & tamamlanma süresi
* Timeout → yeniden gönderim & tamamlanma süresi
* Kayıp oranı → yeniden gönderim oranı, goodput & tamamlanma süresi
* Dosya boyutu → goodput & tamamlanma süresi
* Window → goodput & tamamlanma süresi (Stop-and-Wait ↔ Sliding Window)

Kullanım:
    python -m src.analyze                       # results/experiments.csv
    python -m src.analyze --csv path/to.csv --out results/figures
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Her senaryo için: x ekseni etiketi + çizilecek (kolon, eksen başlığı) listesi
_SCENARIO_PLOTS = {
    "size": ("Payload Boyutu (bayt)", [
        ("goodput_Mbps", "Goodput (Mbps)"),
        ("completion_time_s", "Tamamlanma Süresi (s)"),
    ]),
    "timeout": ("Timeout (ms)", [
        ("retransmissions", "Yeniden Gönderim Sayısı"),
        ("completion_time_s", "Tamamlanma Süresi (s)"),
    ]),
    "loss": ("Yapay Kayıp Oranı", [
        ("retransmission_rate", "Yeniden Gönderim Oranı"),
        ("goodput_Mbps", "Goodput (Mbps)"),
        ("completion_time_s", "Tamamlanma Süresi (s)"),
    ]),
    "filesize": ("Dosya Boyutu (bayt)", [
        ("goodput_Mbps", "Goodput (Mbps)"),
        ("completion_time_s", "Tamamlanma Süresi (s)"),
    ]),
    "window": ("Window Boyutu (1 = Stop-and-Wait)", [
        ("goodput_Mbps", "Goodput (Mbps)"),
        ("completion_time_s", "Tamamlanma Süresi (s)"),
    ]),
}


def make_plots(csv_path: Path, out_dir: Path) -> int:
    try:
        import matplotlib
        matplotlib.use("Agg")  # başsız (GUI'siz) ortamda PNG üretimi
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError as e:
        print(f"Gerekli kütüphane eksik ({e}). `pip install matplotlib pandas`.", file=sys.stderr)
        return 2

    if not csv_path.exists():
        print(f"Sonuç dosyası yok: {csv_path}. Önce `python -m src.run_experiments` çalıştırın.",
              file=sys.stderr)
        return 1

    df = pd.read_csv(csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    produced = []
    for scenario, (xlabel, metrics) in _SCENARIO_PLOTS.items():
        sub = df[df["scenario"] == scenario]
        if sub.empty:
            continue

        # Tekrarları (repeat) ortala
        grouped = sub.groupby("value", as_index=False).mean(numeric_only=True).sort_values("value")
        x = grouped["value"]

        n = len(metrics)
        fig, axes = plt.subplots(1, n, figsize=(5.5 * n, 4.2))
        if n == 1:
            axes = [axes]
        fig.suptitle(f"NetProbe — {scenario} senaryosu", fontsize=13)

        for ax, (col, ylabel) in zip(axes, metrics):
            ax.plot(x, grouped[col], marker="o", color="tab:blue")
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)
            # window ve filesize senaryolarında log ölçek okunabilirliği artırır
            if scenario in ("window", "filesize", "size"):
                ax.set_xscale("log", base=2)

        fig.tight_layout(rect=(0, 0, 1, 0.94))
        fig_path = out_dir / f"{scenario}.png"
        fig.savefig(fig_path, dpi=130)
        plt.close(fig)
        produced.append(fig_path)
        print(f"[analyze] üretildi: {fig_path}")

    # --- Özet karşılaştırma tablosu (konsola) ----------------------------
    if produced:
        print(f"\n[analyze] {len(produced)} grafik → {out_dir}")
    else:
        print("[analyze] Çizilecek veri bulunamadı.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NetProbe performans grafiği üretici")
    parser.add_argument("--csv", default="results/experiments.csv", help="Deney sonuç CSV'si")
    parser.add_argument("--out", default="results/figures", help="Grafiklerin yazılacağı klasör")
    args = parser.parse_args(argv)
    return make_plots(Path(args.csv), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
