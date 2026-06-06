"""
Ağ Koşulu Simülasyon Modülü (netsim)
====================================

Gerçek bir UDP soketini saran ve giden (opsiyonel olarak gelen) datagramlara
yapay **paket kaybı** ve **gecikme/jitter** enjekte eden bir sarmalayıcı sınıf.

Kontrollü ve tekrarlanabilir deneyler için kullanılır: localhost üzerinde
paket kaybı pratikte sıfır olduğundan, protokolün retransmission/timeout
davranışını gözlemlemek için kaybı yapay olarak üretmek gerekir.

Tasarım Notları
---------------
* Kayıp, paketin hiç gönderilmemesiyle (sessizce düşürme) modellenir; karşı
  taraf bunu zamanaşımı ile algılar.
* Gecikme **asenkron** uygulanır (threading.Timer): gönderen taraf bloke
  olmaz ve paketler gerçek bir ağdaki gibi sıralarını değiştirebilir.
* Sarmalayıcı, gerçek soketin diğer tüm metotlarını (bind, settimeout,
  getsockname, close ...) __getattr__ ile şeffaf biçimde devreder.
"""

from __future__ import annotations

import random
import socket
import threading


class SimulatedSocket:
    """
    Bir UDP soketini sarmalar ve sendto/recvfrom üzerinde kayıp+gecikme uygular.

    Parametreler
    ------------
    sock        : sarmalanacak gerçek socket.socket nesnesi
    loss_rate   : giden paket kaybı olasılığı [0.0, 1.0]
    delay_ms    : giden pakete eklenecek ortalama tek yönlü gecikme (ms)
    jitter_ms   : gecikmeye eklenecek ± düzgün dağılımlı sapma (ms)
    recv_loss_rate : gelen paket kaybı olasılığı (ACK kaybını test etmek için)
    seed        : tekrarlanabilirlik için RNG tohumu (None = rastgele)
    """

    def __init__(
        self,
        sock: socket.socket,
        loss_rate: float = 0.0,
        delay_ms: float = 0.0,
        jitter_ms: float = 0.0,
        recv_loss_rate: float = 0.0,
        seed: int | None = None,
    ):
        self._sock = sock
        self.loss_rate = float(loss_rate)
        self.delay_ms = float(delay_ms)
        self.jitter_ms = float(jitter_ms)
        self.recv_loss_rate = float(recv_loss_rate)
        self._rng = random.Random(seed)
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()

        # İstatistikler (deney raporu için yararlı)
        self.dropped_out = 0
        self.dropped_in = 0
        self.sent_out = 0

    # --- Giden yön --------------------------------------------------------
    def sendto(self, data: bytes, address) -> int:
        """Kayıp olasılığına göre paketi düşürür, aksi halde (gecikmeli) gönderir."""
        self.sent_out += 1

        if self.loss_rate > 0.0 and self._rng.random() < self.loss_rate:
            self.dropped_out += 1
            # Çağıran açısından paket "gönderilmiş" gibi davranır.
            return len(data)

        delay = self._sample_delay()
        if delay <= 0.0:
            return self._sock.sendto(data, address)

        # Asenkron, gecikmeli gönderim.
        timer = threading.Timer(delay, self._delayed_send, args=(data, address))
        timer.daemon = True
        with self._lock:
            # Tamamlanmış timer'ları ara sıra temizle.
            self._timers = [t for t in self._timers if t.is_alive()]
            self._timers.append(timer)
        timer.start()
        return len(data)

    def _delayed_send(self, data: bytes, address) -> None:
        try:
            self._sock.sendto(data, address)
        except OSError:
            # Soket gecikme dolmadan kapanmış olabilir; sessizce yut.
            pass

    def _sample_delay(self) -> float:
        """Saniye cinsinden gecikme örnekler (delay ± jitter, negatif değer 0'a kırpılır)."""
        if self.delay_ms <= 0.0 and self.jitter_ms <= 0.0:
            return 0.0
        jitter = self._rng.uniform(-self.jitter_ms, self.jitter_ms) if self.jitter_ms else 0.0
        return max(0.0, (self.delay_ms + jitter) / 1000.0)

    # --- Gelen yön --------------------------------------------------------
    def recvfrom(self, bufsize: int):
        """
        Datagram alır. recv_loss_rate > 0 ise gelen paketleri de yapay olarak
        düşürebilir (paketi alıp atar; çağırana tekrar bekleme yaptırır).
        """
        while True:
            data, addr = self._sock.recvfrom(bufsize)
            if self.recv_loss_rate > 0.0 and self._rng.random() < self.recv_loss_rate:
                self.dropped_in += 1
                continue  # paketi yok say, bir sonrakini bekle
            return data, addr

    # --- Kapatma / temizlik ----------------------------------------------
    def close(self) -> None:
        with self._lock:
            for t in self._timers:
                t.cancel()
            self._timers.clear()
        self._sock.close()

    # --- Diğer tüm socket metotlarını şeffaf biçimde devret ---------------
    def __getattr__(self, name):
        # __getattr__ yalnızca normal arama başarısız olunca çağrılır,
        # bu yüzden yalnızca gerçek soketin metotlarına düşer.
        return getattr(self._sock, name)


def wrap_socket(sock: socket.socket, **kwargs) -> SimulatedSocket | socket.socket:
    """
    Verilen parametrelerden en az biri etkin (>0) ise soketi SimulatedSocket
    ile sarmalar; aksi halde soketi olduğu gibi döndürür (sıfır ek yük).
    """
    loss = kwargs.get("loss_rate", 0.0)
    delay = kwargs.get("delay_ms", 0.0)
    jitter = kwargs.get("jitter_ms", 0.0)
    recv_loss = kwargs.get("recv_loss_rate", 0.0)
    if loss <= 0 and delay <= 0 and jitter <= 0 and recv_loss <= 0:
        return sock
    return SimulatedSocket(sock, **kwargs)
