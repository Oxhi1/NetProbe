"""
Karşılaştırmalı Deney Çalıştırıcı (Experiment Runner)
=====================================================

Föy Bölüm 7'deki senaryoları otomatik olarak çalıştırır ve sonuçları tek bir
CSV (``results/experiments.csv``) ile JSON dosyasına toplar. Her deneyde
sunucu (alıcı) ayrı bir thread'te, istemci (gönderici) ana thread'te gerçek
UDP soketleri üzerinden çalışır; böylece ölçümler gerçek ağ G/Ç'sini yansıtır.

Senaryolar
----------
* size      : Paket (payload) boyutunun etkisi
* timeout   : Timeout değerinin etkisi
* loss      : Yapay kayıp oranının etkisi
* filesize  : Dosya boyutunun etkisi
* window    : Stop-and-Wait (window=1) ↔ Sliding Window karşılaştırması

Kullanım:
    python -m src.run_experiments               # tüm senaryolar
    python -m src.run_experiments --scenarios loss window
    python -m src.run_experiments --quick       # daha az nokta (hızlı)
    python -m src.run_experiments --repeat 3    # her noktayı 3 kez ortala
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import socket
import tempfile
import threading
from pathlib import Path

from . import protocol as proto
from .client import ReliableSender
from .event_logger import EventLogger
from .netsim import wrap_socket
from .server import receive_file

# Deney varsayılanları (senaryo değişkeni dışında sabit tutulan parametreler)
BASE = {
    "file_size": 200 * 1024,     # 200 KB
    "payload_size": 1024,
    "window": 8,
    "timeout_ms": 200.0,
    "loss": 0.05,
    "delay_ms": 0.0,
    "jitter_ms": 0.0,
    "max_retries": 5,
    "seed": 12345,
}

RESULT_FIELDS = [
    "scenario", "param", "value", "repeat",
    "file_size", "payload_size", "window", "timeout_ms", "loss", "delay_ms",
    "status", "completion_time_s", "throughput_Mbps", "goodput_Mbps",
    "retransmissions", "retransmission_rate", "timeouts", "duplicates",
    "avg_rtt_ms", "failed_packets", "integrity_ok",
]


def _make_test_file(directory: Path, size: int, seed: int) -> Path:
    """Belirtilen boyutta tekrarlanabilir içerikli bir test dosyası üretir."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"test_{size}.bin"
    if not path.exists() or path.stat().st_size != size:
        rng = random.Random(seed)
        path.write_bytes(rng.randbytes(size))
    return path


def run_single(cfg: dict, log_dir: Path, recv_dir: Path) -> dict:
    """
    Tek bir aktarımı (verilen konfigürasyonla) çalıştırır ve birleşik
    sonuç sözlüğünü döndürür.
    """
    test_file = _make_test_file(log_dir / "data", cfg["file_size"], cfg["seed"])

    # --- Sunucu soketi (port 0 → işletim sistemi boş port atar) ----------
    srv_raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv_raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv_raw.bind(("127.0.0.1", 0))
    srv_raw.settimeout(0.5)
    server_port = srv_raw.getsockname()[1]

    srv_logger = EventLogger(log_dir / "server.csv", role="server")
    holder: dict = {}

    def server_worker():
        try:
            holder["result"] = receive_file(srv_raw, recv_dir, srv_logger, idle_timeout=15.0)
        except Exception as e:  # noqa: BLE001 - deney sürerken çökme yutulur
            holder["result"] = {"status": "server_error", "error": str(e)}
        finally:
            holder["summary"] = srv_logger.finalize()
            srv_raw.close()

    server_thread = threading.Thread(target=server_worker, daemon=True)
    server_thread.start()

    # --- İstemci ---------------------------------------------------------
    cli_raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cli_sock = wrap_socket(
        cli_raw,
        loss_rate=cfg["loss"],
        delay_ms=cfg["delay_ms"],
        jitter_ms=cfg["jitter_ms"],
        seed=cfg["seed"],
    )
    cli_logger = EventLogger(log_dir / "client.csv", role="client")
    sender = ReliableSender(
        cli_sock,
        ("127.0.0.1", server_port),
        cli_logger,
        payload_size=cfg["payload_size"],
        window=cfg["window"],
        timeout=cfg["timeout_ms"] / 1000.0,
        max_retries=cfg["max_retries"],
    )
    client_result = sender.send_file(test_file)
    cli_sock.close()

    server_thread.join(timeout=20.0)
    srv_result = holder.get("result", {})

    return {
        "client": client_result,
        "server": srv_result,
    }


def _row_from_result(scenario, param, value, repeat, cfg, combined) -> dict:
    c = combined["client"]
    s = combined["server"]
    return {
        "scenario": scenario,
        "param": param,
        "value": value,
        "repeat": repeat,
        "file_size": cfg["file_size"],
        "payload_size": cfg["payload_size"],
        "window": cfg["window"],
        "timeout_ms": cfg["timeout_ms"],
        "loss": cfg["loss"],
        "delay_ms": cfg["delay_ms"],
        "status": c.get("status"),
        "completion_time_s": c.get("completion_time_s"),
        "throughput_Mbps": c.get("throughput_Mbps"),
        "goodput_Mbps": c.get("goodput_Mbps"),
        "retransmissions": c.get("retransmissions"),
        "retransmission_rate": c.get("retransmission_rate"),
        "timeouts": c.get("timeouts"),
        "duplicates": s.get("duplicates", c.get("duplicates")),
        "avg_rtt_ms": c.get("avg_rtt_ms"),
        "failed_packets": c.get("failed_packets"),
        "integrity_ok": s.get("integrity_ok"),
    }


# --- Senaryo parametre kümeleri ------------------------------------------
def _scenario_values(quick: bool) -> dict:
    if quick:
        return {
            "size": [256, 1024, 4096],
            "timeout": [50, 200, 800],
            "loss": [0.0, 0.05, 0.2],
            "filesize": [20 * 1024, 100 * 1024, 500 * 1024],
            "window": [1, 4, 16],
        }
    return {
        "size": [256, 512, 1024, 2048, 4096, 8192],
        "timeout": [25, 50, 100, 200, 400, 800],
        "loss": [0.0, 0.02, 0.05, 0.10, 0.20, 0.30],
        "filesize": [10 * 1024, 50 * 1024, 100 * 1024, 500 * 1024, 1024 * 1024],
        "window": [1, 2, 4, 8, 16, 32, 64],
    }


_PARAM_LABEL = {
    "size": "payload_size",
    "timeout": "timeout_ms",
    "loss": "loss",
    "filesize": "file_size",
    "window": "window",
}


def _apply_param(cfg: dict, scenario: str, value) -> None:
    cfg[_PARAM_LABEL[scenario]] = value


def run_all(scenarios, quick, repeat, out_dir: Path) -> list[dict]:
    values = _scenario_values(quick)
    rows: list[dict] = []
    work_dir = Path(tempfile.mkdtemp(prefix="netprobe_exp_"))
    recv_dir = work_dir / "received"

    for scenario in scenarios:
        if scenario not in values:
            print(f"[exp] bilinmeyen senaryo atlanıyor: {scenario}")
            continue
        print(f"\n=== Senaryo: {scenario} ({_PARAM_LABEL[scenario]}) ===")
        for value in values[scenario]:
            for r in range(repeat):
                cfg = dict(BASE)
                _apply_param(cfg, scenario, value)
                # tekrarlar arasında farklı tohum → farklı kayıp deseni
                cfg["seed"] = BASE["seed"] + r
                combined = run_single(cfg, work_dir / "logs", recv_dir)
                row = _row_from_result(scenario, _PARAM_LABEL[scenario], value, r, cfg, combined)
                rows.append(row)
                print(f"  {_PARAM_LABEL[scenario]}={value} (#{r}): "
                      f"status={row['status']} "
                      f"goodput={row['goodput_Mbps']}Mbps "
                      f"retx={row['retransmissions']} "
                      f"time={row['completion_time_s']}s "
                      f"bütünlük={row['integrity_ok']}")

    # --- Sonuçları yaz ---------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "experiments.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "experiments.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[exp] {len(rows)} sonuç yazıldı → {csv_path}")
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NetProbe karşılaştırmalı deney çalıştırıcı")
    parser.add_argument("--scenarios", nargs="+",
                        default=["size", "timeout", "loss", "filesize", "window"],
                        help="Çalıştırılacak senaryolar")
    parser.add_argument("--quick", action="store_true", help="Daha az parametre noktası (hızlı)")
    parser.add_argument("--repeat", type=int, default=1, help="Her nokta için tekrar sayısı (ortalama için)")
    parser.add_argument("--out", default="results", help="Sonuç klasörü")
    args = parser.parse_args(argv)

    run_all(args.scenarios, args.quick, args.repeat, Path(args.out))
    print("[exp] Grafikleri üretmek için: python -m src.analyze")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
