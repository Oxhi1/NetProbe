"""Hızlı duman testi: protokol round-trip + tek uçtan uca transfer (kayıplı)."""
from pathlib import Path
import tempfile

from src import protocol as proto
from src.run_experiments import run_single, BASE

# --- 1) Protokol round-trip ---
d = proto.pack_data(seq=5, total=10, payload=b"merhaba dunya")
p = proto.unpack(d)
assert p.type == proto.PKT_DATA and p.seq == 5 and p.total == 10 and p.payload == b"merhaba dunya"
a = proto.unpack(proto.pack_ack(7))
assert a.type == proto.PKT_ACK and a.seq == 7
m = proto.unpack(proto.pack_meta(3, b'{"x":1}'))
assert m.type == proto.PKT_META and m.total == 3
# bozuk paket tespiti
corrupt = bytearray(d); corrupt[-1] ^= 0xFF
try:
    proto.unpack(bytes(corrupt)); raise SystemExit("HATA: bozuk paket tespit edilemedi")
except proto.CorruptPacket:
    pass
print("[1] Protokol round-trip + checksum: OK")

# --- 2) Uçtan uca transfer (%10 kayıp, window=8) ---
cfg = dict(BASE)
cfg.update(file_size=64 * 1024, loss=0.10, window=8, timeout_ms=150, payload_size=1024)
work = Path(tempfile.mkdtemp(prefix="netprobe_smoke_"))
res = run_single(cfg, work / "logs", work / "recv")
c, s = res["client"], res["server"]
print(f"[2] client.status={c['status']} integrity_ok={s.get('integrity_ok')} "
      f"retx={c['retransmissions']} timeouts={c['timeouts']} "
      f"goodput={c['goodput_Mbps']}Mbps time={c['completion_time_s']}s")
assert c["status"] == "ok", "transfer başarısız"
assert s.get("integrity_ok") is True, "bütünlük doğrulanamadı"
print("\nTÜM TESTLER GEÇTİ ✓")
