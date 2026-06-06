"""NetProbe - UDP Tabanlı Güvenilir Dosya Aktarımı, Trafik İzleme ve Performans Analiz Platformu."""

__version__ = "1.0.0"

# Windows konsolu varsayılan olarak cp1252 kullanır ve Türkçe karakterleri
# (ğ, ş, İ, ı ...) kodlayamadığı için print çağrıları UnicodeEncodeError
# verebilir. Paket yüklendiğinde stdout/stderr'i UTF-8'e geçirerek bunu
# kökten çözüyoruz (Python 3.7+).
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
