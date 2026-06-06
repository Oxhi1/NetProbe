"""
NetProbe Uygulama Katmanı Protokolü
===================================

UDP üzerinde güvenilir aktarım için tasarlanmış basit, paket temelli bir
uygulama katmanı protokolü. Tüm sayısal alanlar ağ bayt sırası (big-endian)
ile serileştirilir.

Paket Türleri
-------------
DATA      (1) : Dosya parçası taşır.
ACK       (2) : Bir DATA/META/FIN paketinin alındığını onaylar.
META      (3) : Aktarım başında dosya meta verisini (ad, boyut, hash) taşır.
FIN       (4) : Göndericinin tüm verinin gönderildiğini bildirdiği kapanış paketi.

Paket Yapısı
------------
DATA / META başlığı (15 bayt):
    +--------+------------+--------------+----------------+-----------+
    | type(1)| seq(4)     | total(4)     | payload_len(2) | crc32(4)  |
    +--------+------------+--------------+----------------+-----------+
    | payload (payload_len bayt)                                     |
    +----------------------------------------------------------------+

ACK / FIN paketi (9 bayt):
    +--------+------------+-----------+
    | type(1)| ack(4)     | crc32(4)  |
    +--------+------------+-----------+

Bütünlük
--------
Her paketin CRC32 checksum'ı, checksum alanı sıfırlanmış başlık + payload
üzerinden hesaplanır. Alıcı aynı hesabı tekrarlayıp karşılaştırarak bozuk
paketleri tespit eder ve sessizce yok sayar (zamanaşımı ile yeniden gönderim
tetiklenir). Dosyanın tamamının bütünlüğü ayrıca META içindeki SHA-256
özeti ile doğrulanır.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

# --- Paket türleri --------------------------------------------------------
PKT_DATA = 1
PKT_ACK = 2
PKT_META = 3
PKT_FIN = 4

TYPE_NAMES = {
    PKT_DATA: "DATA",
    PKT_ACK: "ACK",
    PKT_META: "META",
    PKT_FIN: "FIN",
}

# --- ACK numarası için özel (rezerve) değerler ----------------------------
# DATA paketleri 0..N-1 aralığında seq numarası kullanır; aşağıdaki yüksek
# değerler META ve FIN onayları için ayrılmıştır.
ACK_META = 0xFFFFFFFF
ACK_FIN = 0xFFFFFFFE

# --- Başlık formatları ----------------------------------------------------
# !  : network byte order (big-endian)
# B  : 1 bayt  (packet type)
# I  : 4 bayt  (seq / total / ack / crc32)
# H  : 2 bayt  (payload length)
_DATA_HEADER = struct.Struct("!BIIHI")   # type, seq, total, payload_len, crc32
_ACK_FORMAT = struct.Struct("!BII")      # type, ack, crc32

DATA_HEADER_SIZE = _DATA_HEADER.size      # 15 bayt
ACK_PACKET_SIZE = _ACK_FORMAT.size        # 9 bayt

# Önerilen varsayılan payload boyutu (bayt). UDP datagramının IP parçalanması
# olmadan tek seferde gitmesi için makul bir değer.
DEFAULT_PAYLOAD_SIZE = 1024


@dataclass
class Packet:
    """Çözümlenmiş bir paketi temsil eder."""

    type: int
    seq: int = 0          # DATA/META için sequence number; ACK için ack number
    total: int = 0        # toplam DATA paketi sayısı (DATA/META)
    payload: bytes = b""

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, f"UNKNOWN({self.type})")


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# --- Serileştirme ---------------------------------------------------------

def pack_data(seq: int, total: int, payload: bytes, ptype: int = PKT_DATA) -> bytes:
    """DATA veya META paketi oluşturur (META için ptype=PKT_META verin)."""
    length = len(payload)
    # checksum alanı 0 iken başlığı kur, CRC hesapla, sonra gerçek değerle yaz.
    header_zero = _DATA_HEADER.pack(ptype, seq, total, length, 0)
    crc = _crc32(header_zero + payload)
    header = _DATA_HEADER.pack(ptype, seq, total, length, crc)
    return header + payload


def pack_meta(total: int, meta_json: bytes) -> bytes:
    """META paketi: dosya meta verisini JSON payload olarak taşır."""
    return pack_data(seq=0, total=total, payload=meta_json, ptype=PKT_META)


def pack_ack(ack_number: int) -> bytes:
    """ACK paketi oluşturur."""
    header_zero = _ACK_FORMAT.pack(PKT_ACK, ack_number, 0)
    crc = _crc32(header_zero)
    return _ACK_FORMAT.pack(PKT_ACK, ack_number, crc)


def pack_fin() -> bytes:
    """FIN paketi oluşturur (kapanış)."""
    header_zero = _ACK_FORMAT.pack(PKT_FIN, 0, 0)
    crc = _crc32(header_zero)
    return _ACK_FORMAT.pack(PKT_FIN, 0, crc)


# --- Çözümleme ------------------------------------------------------------

class CorruptPacket(Exception):
    """Checksum doğrulaması başarısız olduğunda veya paket bozuk olduğunda."""


def unpack(datagram: bytes) -> Packet:
    """
    Ham datagramı çözümler ve checksum doğrular.

    Raises:
        CorruptPacket: paket çok kısa, bilinmeyen tür veya checksum uyuşmuyorsa.
    """
    if len(datagram) < 1:
        raise CorruptPacket("boş datagram")

    ptype = datagram[0]

    if ptype in (PKT_ACK, PKT_FIN):
        if len(datagram) < ACK_PACKET_SIZE:
            raise CorruptPacket("ACK/FIN paketi çok kısa")
        _t, ack, crc = _ACK_FORMAT.unpack(datagram[:ACK_PACKET_SIZE])
        header_zero = _ACK_FORMAT.pack(ptype, ack, 0)
        if _crc32(header_zero) != crc:
            raise CorruptPacket("ACK/FIN checksum uyuşmuyor")
        return Packet(type=ptype, seq=ack)

    if ptype in (PKT_DATA, PKT_META):
        if len(datagram) < DATA_HEADER_SIZE:
            raise CorruptPacket("DATA/META başlığı çok kısa")
        _t, seq, total, length, crc = _DATA_HEADER.unpack(datagram[:DATA_HEADER_SIZE])
        payload = datagram[DATA_HEADER_SIZE:DATA_HEADER_SIZE + length]
        if len(payload) != length:
            raise CorruptPacket("payload uzunluğu başlıkla uyuşmuyor")
        header_zero = _DATA_HEADER.pack(ptype, seq, total, length, 0)
        if _crc32(header_zero + payload) != crc:
            raise CorruptPacket("DATA/META checksum uyuşmuyor")
        return Packet(type=ptype, seq=seq, total=total, payload=payload)

    raise CorruptPacket(f"bilinmeyen paket türü: {ptype}")
