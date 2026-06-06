# NetProbe

**UDP Tabanlı Güvenilir Dosya Aktarımı, Trafik İzleme ve Ağ Performans Analiz Platformu**

Bursa Teknik Üniversitesi — Bilgisayar Mühendisliği — Bilgisayar Ağları Dersi Dönem Projesi

NetProbe, UDP üzerinde **uygulama katmanında** güvenilir bir dosya aktarım
protokolü gerçekler; aktarım sırasındaki ağ olaylarını kayıt altına alır ve
toplanan verilerle performans analizi (throughput, goodput, RTT, kayıp,
yeniden gönderim) yapar. UDP'nin sağlamadığı güvenilirlik (sıra numarası, ACK,
zamanaşımı, yeniden gönderim, bütünlük doğrulama) tamamen kendi protokolümüzle
sağlanır — hazır bir dosya aktarım kütüphanesi kullanılmaz.

---
##!!!Tablo ekran görüntüleri dizindeki results/figures klasöründe yer almaktadır!!!

## Özellikler

| Föy Gereksinimi | Karşılığı |
|---|---|
| UDP istemci-sunucu (4.1) | `src/client.py`, `src/server.py` (`socket`, `SOCK_DGRAM`) |
| Sequence number, ACK, timeout, retransmission (4.2) | `src/client.py` (Selective Repeat) |
| Maks. 5 yeniden gönderim, fail raporlama, duplicate yok sayma | `--max-retries` (vars. 5), `EV_FAIL`, sunucuda duplicate ACK |
| Dosya bütünlüğü (4.3) | SHA-256 (META) + her pakette CRC32 checksum |
| Olay kayıtları (4.4) | `src/event_logger.py` → CSV + JSON özet |
| Performans metrikleri (4.5) | throughput, goodput, completion time, retransmission rate, RTT |
| Karşılaştırmalı deneyler (4.6 / 7) | `src/run_experiments.py` (5 senaryo) |
| **Ek olarak:** Sliding Window | `--window N` (1 = Stop-and-Wait, N>1 = Selective Repeat) |
| **Ek olarak:** Loss/Delay simülasyonu | `src/netsim.py` (`--loss`, `--delay`, `--jitter`) |
| **Ek olarak:** Gerçek zamanlı görselleştirme | `src/live_monitor.py` |

---

## Proje Yapısı

```
.
├── src/
│   ├── protocol.py        # Uygulama katmanı paket formatı, CRC32 checksum, (de)serileştirme
│   ├── client.py          # Gönderici: Selective Repeat, timeout, retransmission, RTT
│   ├── server.py          # Alıcı: ACK üretimi, duplicate tespiti, reassembly, SHA-256 doğrulama
│   ├── netsim.py          # Yapay paket kaybı / gecikme simülasyonu (socket sarmalayıcı)
│   ├── event_logger.py    # Olay kaydı (CSV) + metrik toplama (JSON özet)
│   ├── live_monitor.py    # Gerçek zamanlı görselleştirme paneli (matplotlib)
│   ├── run_experiments.py # Karşılaştırmalı deney çalıştırıcı (5 senaryo)
│   └── analyze.py         # Sonuç CSV'sinden performans grafikleri (PNG)
├── tests/
│   └── smoke_test.py      # Protokol round-trip + uçtan uca transfer testi
├── data/                  # Gönderilecek örnek/test dosyaları
├── received/              # Sunucunun aldığı dosyalar (üretilir)
├── logs/                  # Olay logları + özetler (üretilir)
├── results/               # Deney sonuçları (CSV/JSON) ve figures/ grafikleri (üretilir)
├── docs/
│   └── teknik_rapor.md    # Teknik rapor (PDF'e dönüştürülecek)
├── requirements.txt
└── README.md
```

---

## Kurulum

Python **3.9+** gerekir (geliştirme/test: 3.14).

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

> `matplotlib` ve `pandas` yalnızca grafik üretimi (`analyze`) ve canlı panel
> (`live_monitor`) için gerekir. Çekirdek aktarım (client/server) yalnızca
> Python standart kütüphanesini kullanır.

Tüm komutlar **proje kök dizininden** ve `python -m src.<modül>` biçiminde
çalıştırılmalıdır (modüller `src` paketinin parçasıdır).

---

## Hızlı Başlangıç

### 1) Sunucuyu başlat (alıcı)

```bash
python -m src.server --port 9000 --out received
```

### 2) İstemci ile dosya gönder (gönderici)

```bash
# Stop-and-Wait (window=1), %5 yapay kayıp
python -m src.client --host 127.0.0.1 --port 9000 --file data/ornek.bin \
    --window 1 --timeout 200 --loss 0.05

# Sliding Window (Selective Repeat, window=16), 20ms gecikme + jitter
python -m src.client --host 127.0.0.1 --port 9000 --file data/ornek.bin \
    --window 16 --timeout 150 --delay 20 --jitter 5 --loss 0.05
```

Aktarım bitince hem istemci hem sunucu bir JSON özet (metrikler + bütünlük
sonucu) yazdırır; ayrıntılı olay logları `logs/*.csv` altında oluşur.

### 3) Canlı izleme paneli

İstemciyi `--live-status` ile başlatın, **ayrı bir terminalde** paneli açın:

```bash
# Terminal A
python -m src.client --host 127.0.0.1 --port 9000 --file data/ornek.bin \
    --window 8 --loss 0.1 --live-status logs/client.live.json

# Terminal B
python -m src.live_monitor logs/client.live.json
```

### 4) Karşılaştırmalı deneyler + grafikler

```bash
python -m src.run_experiments --quick     # hızlı; tüm noktalar için --quick'i kaldırın
python -m src.analyze                      # results/figures/*.png üretir
```

`run_experiments` sunucu ve istemciyi gerçek UDP soketleriyle (ayrı thread'ler)
çalıştırır, sonuçları `results/experiments.csv` dosyasına yazar.

---

## İstemci Parametreleri

| Parametre | Açıklama | Varsayılan |
|---|---|---|
| `--file` | Gönderilecek dosya (zorunlu) | — |
| `--host`, `--port` | Sunucu adresi | `127.0.0.1`, `9000` |
| `--payload-size` | Paket başına payload (bayt) | `1024` |
| `--window` | Sliding window boyutu (**1 = Stop-and-Wait**) | `1` |
| `--timeout` | Retransmission zamanaşımı (ms) | `200` |
| `--max-retries` | Paket başına maks. yeniden gönderim | `5` |
| `--loss` | Yapay paket kayıp oranı [0–1] | `0.0` |
| `--delay` / `--jitter` | Yapay gecikme / jitter (ms) | `0.0` |
| `--seed` | Simülasyon RNG tohumu (tekrarlanabilirlik) | yok |
| `--live-status` | Canlı metrikleri yazacağı JSON dosyası | yok |

---

## Test

```bash
python -m tests.smoke_test
```

Protokol serileştirme/checksum'ını ve %10 yapay kayıp altında uçtan uca
transferi (bütünlük dahil) doğrular.

---

## Bütünlük ve Güvenilirlik Notları (Föy "Teknik Netleştirme")

- **Maks. yeniden gönderim:** Varsayılan **5** (`--max-retries`). Bir paket bu
  sınıra rağmen iletilemezse `FAIL` olarak loglanır ve aktarım o paket için
  başarısız sayılır (`status="failed"`).
- **Duplicate paket:** Alıcı, daha önce aldığı bir sıra numarasını tekrar
  yazmaz; yalnızca ilgili ACK'i tekrar göndererek paketi yok sayar.
- **Bütünlük:** Paket düzeyinde CRC32 (bozuk paket → sessizce atılır, zamanaşımı
  ile yeniden gönderilir), dosya düzeyinde SHA-256 karşılaştırması.

---

## GitHub

Depo bağlantısı: `https://github.com/Oxhi1/NetProbe`  
