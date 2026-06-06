# NetProbe — Teknik Rapor

**UDP Tabanlı Güvenilir Dosya Aktarımı, Trafik İzleme ve Ağ Performans Analiz Platformu**

Bursa Teknik Üniversitesi · Bilgisayar Mühendisliği Bölümü · Bilgisayar Ağları Dersi

> **Grup Üyeleri ve Görev Dağılımı:** _(doldurunuz)_
> - Abdullah Çelik — Protokol tasarımı ve istemci (gönderici) gerçeklemesi
> - Abdullah Çelik — Sunucu (alıcı), loglama ve bütünlük doğrulama
> - Efekan AKSOY — Deney altyapısı, performans analizi ve rapor
>
> 

---

## 1. Giriş

Bu çalışmada, UDP (User Datagram Protocol) üzerine **uygulama katmanında**
güvenilir bir dosya aktarım protokolü tasarlanmış ve gerçeklenmiştir. UDP;
bağlantısız, sıralama ve teslim garantisi vermeyen, akış/tıkanıklık kontrolü
içermeyen hafif bir taşıma protokolüdür. TCP ise bağlantı temelli, sıralı ve
güvenilir teslim sağlayan bir protokoldür; ancak bu güvenilirliği sağlayan
mekanizmalar (sıra numarası, ACK, yeniden gönderim, akış kontrolü) uygulama
geliştiricisinden gizlidir.

Projenin amacı, TCP'nin sunduğu güvenilirlik mekanizmalarını UDP üzerinde
**elle** inşa ederek bu mekanizmaların iç işleyişini somut biçimde
deneyimlemektir. Sistem; bir dosyayı paketlere bölerek gönderen istemci, bu
paketleri onaylayıp yeniden birleştiren sunucu, aktarım olaylarını kaydeden bir
loglama altyapısı ve toplanan verilerden performans metrikleri üreten bir analiz
modülünden oluşur. Ayrıca kontrollü deneyler için yapay paket kaybı/gecikme
simülasyonu ve gerçek zamanlı bir izleme paneli geliştirilmiştir.

## 2. Problem Tanımı

UDP üzerinde dosya aktarımı yapılırken aşağıdaki problemlerle karşılaşılır:

1. **Paket kaybı:** Datagramlar ağda kaybolabilir; gönderici bunu doğrudan
   öğrenemez.
2. **Sıra bozulması:** Datagramlar gönderildikleri sırada varmayabilir.
3. **Yinelenme (duplication):** Yeniden gönderim sonucu aynı paket birden çok
   kez ulaşabilir.
4. **Bozulma (corruption):** Paket içeriği ağda bozulabilir.
5. **Akış denetimi yokluğu:** Gönderici, alıcıyı/ağı boğabilir.

Hedef, bu koşullar altında bir dosyanın **eksiksiz, doğru sırada ve bütünlüğü
korunarak** karşı tarafa ulaştırılmasını garanti eden bir protokol tasarlamak;
ayrıca protokolün davranışını farklı ağ koşullarında ölçülebilir kılmaktır.

Çözümde benimsenen mekanizmalar: sıra numarası (sequence number), pozitif
onay (ACK), zamanlayıcı tabanlı yeniden gönderim (timeout + retransmission),
yinelenen paket bastırma, paket düzeyinde CRC32 ve dosya düzeyinde SHA-256
bütünlük doğrulaması ve akış denetimi için kayan pencere (sliding window).

## 3. Sistem Mimarisi

Sistem, gevşek bağlı modüllerden oluşur:

```
            ┌──────────────────────────┐         UDP          ┌──────────────────────────┐
            │        İSTEMCİ           │  DATA  ───────────▶  │         SUNUCU          │
            │      (client.py)         │  ◀───────────  ACK   │       (server.py)        │
            │                          │                      │                          │
  dosya ──▶ │ • parçalama + seq no     │                      │ • checksum doğrulama     │ ──▶ dosya
            │ • sliding window         │                      │ • duplicate bastırma     │
            │ • timeout/retransmission │                      │ • sıralı reassembly      │
            │ • RTT ölçümü             │                      │ • SHA-256 doğrulama      │
            └────────────┬─────────────┘                      └────────────┬─────────────┘
                         │                                                  │
                  ┌──────▼───────┐    (netsim: loss/delay enjeksiyonu)      │
                  │  netsim.py   │◀─────────────────────────────────────────┘
                  └──────┬───────┘
                         │
                  ┌──────▼─────────┐      ┌──────────────┐      ┌──────────────┐
                  │ event_logger   │ ───▶ │ logs/*.csv    │ ───▶ │  analyze.py  │ ──▶ results/figures/*.png
                  │  (olay kaydı)  │      │ *.summary.json│      │ run_experiments
                  └────────────────┘      └──────────────┘      └──────────────┘
```

- **protocol.py** — Paket formatı, serileştirme ve CRC32 bütünlük katmanı. Tüm
  bileşenlerin ortak sözleşmesi.
- **client.py** — Gönderen taraf. Dosyayı parçalar, sliding window ile gönderir,
  ACK'leri işler, zamanaşımında yeniden gönderir.
- **server.py** — Alan taraf. Paketleri doğrular, onaylar, yinelenenleri eler,
  sıralı birleştirir ve bütünlüğü doğrular.
- **netsim.py** — Gerçek soketi saran, yapay kayıp/gecikme enjekte eden katman.
- **event_logger.py** — Olayları zaman damgalı CSV'ye yazar, metrik özetini
  JSON olarak üretir.
- **live_monitor.py / analyze.py / run_experiments.py** — Görselleştirme ve
  deney/analiz araçları.

## 4. Protokol Tasarımı

### 4.1. Paket Türleri

| Tür | Kod | Amaç |
|---|---|---|
| `DATA` | 1 | Dosya parçası taşır |
| `ACK`  | 2 | Bir paketin alındığını onaylar |
| `META` | 3 | Aktarım başında dosya meta verisi (ad, boyut, SHA-256, paket sayısı) |
| `FIN`  | 4 | Aktarımın bittiğini bildiren kapanış paketi |

### 4.2. Paket Biçimi

Tüm sayısal alanlar ağ bayt sırasıyla (big-endian) kodlanır.

**DATA / META başlığı (15 bayt) + payload:**

```
 0        1            5            9             11          15
 +--------+------------+------------+-------------+-----------+============+
 | type(1)|  seq (4)   | total (4)  | p_len (2)   | crc32 (4) |  payload   |
 +--------+------------+------------+-------------+-----------+============+
```

**ACK / FIN paketi (9 bayt):**

```
 0        1            5
 +--------+------------+-----------+
 | type(1)|  ack (4)   | crc32 (4) |
 +--------+------------+-----------+
```

`ack` alanında özel değerler: `0xFFFFFFFF` → META onayı, `0xFFFFFFFE` → FIN
onayı; diğer değerler ilgili DATA paketinin sıra numarasını onaylar.

### 4.3. Bütünlük

İki katmanlı doğrulama uygulanır:

1. **Paket düzeyi (CRC32):** Her paketin checksum'ı, checksum alanı sıfırlanmış
   başlık + payload üzerinden hesaplanır. Alıcı aynı hesabı tekrarlar; uyuşmayan
   paket **sessizce atılır** ve gönderici tarafında zamanaşımıyla yeniden
   gönderilir.
2. **Dosya düzeyi (SHA-256):** Gönderici, dosyanın SHA-256 özetini META içinde
   iletir. Alıcı, birleştirdiği dosyanın özetini hesaplayıp karşılaştırır ve
   sonucu (`integrity_ok`) raporlar.

### 4.4. Güvenilirlik Mekanizması (Selective Repeat)

Gönderici, aynı anda en fazla `window` kadar paketi onaylanmamış olarak "havada"
tutar. Her paket için bağımsız bir zamanlayıcı ve deneme sayacı bulunur:

- Bir paket gönderildiğinde gönderim zamanı kaydedilir.
- `timeout` süresi içinde ACK gelmezse paket yeniden gönderilir (`RESEND`),
  zamanaşımı olayı (`TIMEOUT`) loglanır.
- Bir paket için toplam gönderim sayısı `1 + max_retries`'i (varsayılan 1+5=6)
  aşarsa paket **başarısız** (`FAIL`) kabul edilir ve aktarım o paket için
  başarısız sayılır.
- `window = 1` özel durumu klasik **Stop-and-Wait** protokolüne indirgenir; bu
  sayede iki yaklaşım tek bir kod tabanıyla ve aynı parametreyle (`--window`)
  karşılaştırılabilir.

**Yinelenen paket yönetimi:** Alıcı her DATA için ACK üretir. Daha önce alınmış
bir sıra numarası tekrar gelirse veriyi **ikinci kez yazmaz**; yalnızca ilgili
ACK'i yeniden göndererek paketi yok sayar (`DUPLICATE`). Bu, kaybolan ACK'lerin
yol açtığı gereksiz yeniden gönderimleri doğru biçimde sonlandırır.

**RTT ölçümü (Karn algoritması):** RTT örneği yalnızca **ilk denemede**
onaylanan paketlerden alınır. Yeniden gönderilmiş bir paketin ACK'inin hangi
gönderime ait olduğu belirsiz olduğundan bu örnekler RTT tahminine katılmaz.

### 4.5. El Sıkışma ve Kapanış

1. **Açılış:** İstemci META gönderir, sunucu META-ACK ile yanıtlar (gerekirse
   yeniden denenir).
2. **Aktarım:** DATA/ACK döngüsü, pencere ilerledikçe sürer.
3. **Kapanış:** Tüm paketler onaylanınca istemci FIN gönderir, sunucu FIN-ACK
   ile yanıtlar. Dosya, FIN'den bağımsız olarak **tüm benzersiz paketler
   alındığında** yazıldığı için son paket kayıpları kapanışı bozmaz.

## 5. Gerçekleme Detayları

- **Dil/Kütüphaneler:** Python (standart kütüphane: `socket`, `struct`,
  `zlib`, `hashlib`, `threading`, `csv`, `json`). Yalnızca analiz/görselleştirme
  için `matplotlib` ve `pandas`. Hazır dosya aktarım kütüphanesi
  **kullanılmamıştır**.
- **Olay döngüsü:** İstemci tek bir döngüde hem pencereyi doldurur, hem ACK
  dinler, hem de zamanaşımı denetimi yapar. `recvfrom` zamanaşımı, havadaki en
  yakın paketin kalan süresine göre dinamik ayarlanır; böylece hem CPU
  meşguliyeti düşük tutulur hem de yeniden gönderimler zamanında tetiklenir.
- **Eşzamanlılık:** Yapay gecikme, `threading.Timer` ile **asenkron** uygulanır;
  gönderen bloke olmaz ve paketler gerçek ağdaki gibi yeniden sıralanabilir.
- **Loglama:** Tüm olaylar `wall_time, elapsed_ms, role, event, seq, detail`
  kolonlarıyla CSV'ye yazılır; aktarım sonunda metrik özeti JSON olarak üretilir.
- **Taşınabilirlik:** Windows konsolunun varsayılan cp1252 kodlaması Türkçe
  karakterlerde hata verdiğinden, paket yüklenirken stdout/stderr UTF-8'e
  geçirilir.

### 5.1. Performans Metrikleri (Tanımlar)

- **Throughput** = hat üzerindeki toplam payload baytı (yeniden gönderimler
  dahil) / süre.
- **Goodput** = yalnızca faydalı (dosya) baytı / süre. (Goodput ≤ Throughput;
  fark, yeniden gönderim israfını yansıtır.)
- **Completion time** = META gönderiminden FIN onayına kadar geçen süre.
- **Retransmission rate** = yeniden gönderim sayısı / toplam gönderim sayısı.
- **RTT** = ilk denemede onaylanan paketlerin gönderim–ACK gecikmesi
  (ortalama/min/maks).

## 6. Deney Ortamı

- **Donanım/OS:** Tek makine, Windows 11, Python 3.14. 

- **Ağ:** Loopback (`127.0.0.1`). Loopback üzerinde doğal paket kaybı ~0
  olduğundan, protokolün kayıp davranışını gözlemlemek için kayıp **yapay olarak**
  `netsim` ile üretilmiştir. Bu, tekrarlanabilir ve kontrollü ölçüm sağlar
  (`--seed` ile aynı kayıp deseni yeniden üretilebilir).
- **Yöntem:** `run_experiments.py`, her noktada sunucuyu ayrı bir thread'te,
  istemciyi ana thread'te gerçek UDP soketleriyle çalıştırır. Bir senaryoda tek
  bir değişken taranır, diğer parametreler sabit tutulur (aşağıdaki taban
  değerler): dosya 200 KB, payload 1024 B, window 8, timeout 200 ms, kayıp 0.05.

> Aşağıdaki sayısal sonuçlar tam parametre kümesiyle ve her nokta **3 tekrar**
> ortalanarak (`python -m src.run_experiments --repeat 3`, tekrarlar arasında
> tohum `12345..12347`) üretilmiştir; toplam 90 aktarım koşusu. Grafiklerin
> tamamı `results/figures/` altındadır.

## 7. Performans Metrikleri ve Sonuçlar

### 7.1. Senaryo 1 — Payload Boyutunun Etkisi

| Payload (B) | Goodput (Mbps) | Yeniden gönderim | Süre (s) |
|---:|---:|---:|---:|
| 256  |   0.210 | 51.0 | 7.87 |
| 512  |   0.382 | 28.0 | 4.43 |
| 1024 |   0.709 | 15.3 | 2.33 |
| 2048 |   1.495 |  7.3 | 1.16 |
| 4096 |   3.562 |  2.7 | 0.48 |
| 8192 | 176.605 |  1.0 | 0.28 |

Payload büyüdükçe aynı 200 KB'lık dosya daha az pakete bölünür; bu hem başlık
ek yükünü (15 B/paket) hem de toplam kayıp **olay** sayısını azaltır. 256 B'de
dosya ~800 pakete bölünürken 8192 B'de ~25 pakete iner; kayıp olasılığı paket
başına sabit (%5) olduğundan toplam yeniden gönderim (51 → 1) ve dolayısıyla
tamamlanma süresi belirgin düşer. 8192 B noktasında goodput'un sıçraması
(~177 Mbps), paket sayısı kritik biçimde azaldığında neredeyse hiç kayıp olayı
yaşanmamasından ve aktarımın loopback hızına yaklaşmasından kaynaklanır. Pratik
sınır: payload çok büyürse tek datagram IP parçalanmasına/MTU sınırına
takılabilir — bu yüzden 1–4 KB güvenli ve dengeli bir aralıktır.
**Grafik:** `figures/size.png`.

### 7.2. Senaryo 2 — Timeout Değerinin Etkisi

| Timeout (ms) | Yeniden gönderim | Süre (s) | Goodput (Mbps) |
|---:|---:|---:|---:|
|  25 | 15.3 | 0.32 | 5.149 |
|  50 | 15.3 | 0.68 | 2.458 |
| 100 | 15.3 | 1.22 | 1.351 |
| 200 | 15.3 | 2.35 | 0.705 |
| 400 | 15.3 | 4.59 | 0.359 |
| 800 | 15.3 | 9.15 | 0.180 |

Sabit kayıp deseninde yeniden gönderim sayısı sabit kalırken (~15.3), tamamlanma
süresi timeout ile neredeyse **doğrusal** artar (25 ms → 0.32 s, 800 ms →
9.15 s). Bunun nedeni, kaybolan bir paketin ancak timeout dolduktan sonra tespit
edilip yeniden gönderilmesidir; her kayıp olayı, sisteme yaklaşık bir timeout
süresi gecikme ekler. RTT bu ortamda ~0.3 ms olduğundan 25 ms bile RTT'nin çok
üstündedir ve **erken (spurious) yeniden gönderim** gözlenmez (yeniden gönderim
sayısı düşmüyor). Bu, "timeout, RTT'ye yakın ama üstünde seçilmelidir" ilkesini
doğrular: gereksiz büyük timeout, kayıp telafisini yavaşlatarak goodput'u
düşürür. **Grafik:** `figures/timeout.png`.

### 7.3. Senaryo 3 — Yapay Kayıp Oranının Etkisi

| Kayıp | Yeniden gönderim | Yen. gönd. oranı | Goodput (Mbps) | Süre (s) |
|---:|---:|---:|---:|---:|
| 0.00 |  0.0 | 0.000 | 104.23 | 0.016 |
| 0.02 |  4.7 | 0.023 |   1.924 | 0.90 |
| 0.05 | 15.3 | 0.071 |   0.707 | 2.33 |
| 0.10 | 28.0 | 0.122 |   0.465 | 3.70 |
| 0.20 | 54.3 | 0.212 |   0.307 | 5.68 |
| 0.30 | 97.3 | 0.327 |   0.206 | 8.19 |

Kayıpsız durumda loopback'in gerçek tavanı görülür (~104 Mbps). Kayıp arttıkça
yeniden gönderim sayısı neredeyse doğrusal büyür (yeniden gönderim oranı kayıp
oranını yakından izler: %30 kayıpta ~0.33) ve goodput hızla düşer; çünkü her
kayıp, bir timeout süresi bekleme + tekrar gönderim maliyeti getirir. Goodput
ile throughput arasındaki açıklık (yeniden gönderim israfı) kayıpla birlikte
açılır. Tüm kayıp seviyelerinde dosya bütünlüğü korunmuştur
(`integrity_ok=True`) — yani protokol, yüksek kayıpta **yavaşlar ama bozulmaz**.
**Grafik:** `figures/loss.png`.

### 7.4. Senaryo 4 — Dosya Boyutunun Etkisi

| Dosya | Yeniden gönderim | Goodput (Mbps) | Süre (s) |
|---:|---:|---:|---:|
| 10 KB  |  0.3 | 32.283 | 0.07 |
| 50 KB  |  2.7 |  0.873 | 0.49 |
| 100 KB |  7.3 |  0.741 | 1.16 |
| 500 KB | 33.7 |  0.811 | 5.14 |
| 1 MB   | 64.0 |  0.856 | 9.91 |

50 KB ve üzerinde goodput dosya boyutundan büyük ölçüde **bağımsız**
(~0.74–0.86 Mbps) kalırken, tamamlanma süresi boyutla doğrusal artar; bu
protokolün ölçeklenebilir olduğunu gösterir. 10 KB noktasındaki aykırı yüksek
goodput (~32 Mbps), bu dosyanın yalnızca ~10 pakete bölünmesi ve %5 kayıpta
çoğu koşuda neredeyse hiç kayıp olayı yaşanmamasındandır (ortalama yeniden
gönderim 0.3); çok kısa aktarımda tek bir kaybın olup olmaması sonucu büyük
ölçüde belirler. Küçük dosyalarda META el sıkışmasının göreli maliyeti de daha
belirgindir. **Grafik:** `figures/filesize.png`.

### 7.5. Bonus — Window Boyutu (Stop-and-Wait ↔ Sliding Window)

| Window | Goodput (Mbps) | Süre (s) | Açıklama |
|---:|---:|---:|---|
|  1 | 0.530 | 3.17 | Stop-and-Wait |
|  2 | 0.552 | 3.03 | — |
|  4 | 0.580 | 2.85 | — |
|  8 | 0.700 | 2.36 | — |
| 16 | 1.077 | 1.53 | Sliding Window (Selective Repeat) |
| 32 | 1.402 | 1.18 | — |
| 64 | 2.585 | 0.63 | — |

Stop-and-Wait (window=1) her pakette bir tur beklediği için en yavaş yöntemdir.
Pencere büyüdükçe aynı anda birden çok paket havada tutulur; özellikle kayıp
telafisi sırasında bekleme süreleri örtüşür ve goodput artarken tamamlanma
süresi düşer. window=64'te goodput, Stop-and-Wait'e göre **~4.9 kat** artmış
(0.53 → 2.59 Mbps), süre ~5 kat azalmıştır (3.17 → 0.63 s). Yeniden gönderim
sayısı pencereden bağımsız sabit (~15.3) kalır; yani kazanç daha az yeniden
gönderimden değil, bekleme sürelerinin paralelleştirilmesinden gelir. Bu sonuç,
sliding window yaklaşımının UDP üzerinde güvenilir aktarım için neden tercih
edildiğini somut biçimde gösterir. **Grafik:** `figures/window.png`.

## 8. Sonuçlar ve Tartışma

- **Doğruluk:** Tüm senaryolarda (kayıp %0–%20 dahil) dosya bütünlüğü SHA-256
  ile doğrulanmış; protokol kayıp, yinelenme ve sıra bozulması altında dosyayı
  eksiksiz yeniden oluşturmuştur.
- **Timeout seçimi kritiktir:** RTT'ye göre çok büyük timeout, kayıp telafisini
  yavaşlatarak goodput'u doğrudan düşürür; çok küçük timeout ise gerçek ağlarda
  spurious retransmission'a yol açabilir. Adaptif (RTT-tabanlı) timeout doğal
  bir iyileştirme yönüdür.
- **Payload ve pencere, verimi belirleyen başlıca kaldıraçlardır:** Büyük payload
  başlık/olay ek yükünü, büyük pencere ise bekleme örtüşmesini iyileştirir.
- **Goodput vs. throughput:** İkisi arasındaki fark, yeniden gönderim israfının
  doğrudan ölçüsüdür ve kayıpla birlikte büyür.

## 9. Karşılaşılan Sorunlar ve Çözüm Yaklaşımları

1. **Loopback'te doğal kayıp yok:** Protokolün kayıp davranışı gözlemlenemiyordu.
   → `netsim.py` ile soket düzeyinde, tekrarlanabilir (seed'li) yapay kayıp/gecikme
   enjekte edildi.
2. **Yeniden gönderimde RTT bozulması:** Yeniden gönderilen paketlerin ACK'i RTT
   tahminini şişiriyordu. → Karn algoritması: RTT yalnızca ilk denemede
   onaylanan paketlerden örneklendi.
3. **Kaybolan ACK → gereksiz yeniden gönderim → yinelenme:** → Alıcıda duplicate
   tespiti ve ACK'in yeniden gönderimi; veri ikinci kez yazılmıyor.
4. **Türkçe karakter / konsol kodlaması:** Windows cp1252 konsolunda `print`
   hataları. → Paket yüklenirken stdout/stderr UTF-8'e ayarlandı.
5. **Eşzamanlı gecikme ve bloklama:** Senkron gecikme tüm akışı durduruyordu.
   → `threading.Timer` ile asenkron gecikme uygulandı.
6. **Port yeniden kullanımı (deneyler):** → `SO_REUSEADDR` ve port 0 ile işletim
   sistemine boş port atatma.

## 10. Sonuç ve Gelecekte Yapılabilecek Geliştirmeler

NetProbe, UDP üzerinde sıra numarası, ACK, zamanaşımı, yeniden gönderim,
yinelenme bastırma ve iki katmanlı bütünlük doğrulamasıyla güvenilir bir dosya
aktarımı gerçekler; trafik olaylarını kaydeder ve farklı koşullar altında
performansını ölçüp yorumlar. Deneyler, payload boyutu, timeout, kayıp oranı,
dosya boyutu ve pencere boyutunun verim üzerindeki etkisini niceliksel olarak
ortaya koymuştur.

**Gelecek geliştirmeler:**

- **Adaptif timeout** (Jacobson/Karels RTT tahmini, RTO hesabı).
- **Tıkanıklık denetimi** (AIMD / slow-start benzeri pencere uyarlaması).
- **Cumulative + selective ACK** karışımı ve negatif ACK (NAK).
- **TCP ile karşılaştırmalı deney** ve gerçek ağ (LAN/WAN) ölçümleri.
- **Çoklu istemci** ve eşzamanlı aktarım desteği.
- **Sıkıştırma/şifreleme** ile uçtan uca güvenlik ve veri azaltma.

## 11. Kullanılan Dış Kütüphaneler

- `matplotlib`, `pandas` — yalnızca analiz ve görselleştirme (grafik üretimi,
  canlı panel). Çekirdek protokol tamamen Python standart kütüphanesiyle
  yazılmıştır.

---

_Kaynak kod ve çalıştırma talimatları için bkz. `README.md`._
