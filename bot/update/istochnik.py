# -*- coding: utf-8 -*-
"""Этап 17 — источник свежего прайса: FTP Форы или локальная папка (единый интерфейс).

Фора раз в сутки заливает `.xls` на наш приёмный vsftpd (папка `price`, пассивный режим).
Источник «дай свежий прайс» имеет две реализации с одним методом `poluchit_svezhiy() -> путь`:
  - `FtpIstochnik` — берёт самый свежий файл по дате в имени (`…YYYY-MM-DDThh-mm-ss….xls`,
    fallback — mtime сервера), проверяет размер ДО скачивания (`SIZE` — отсекает пустышку
    51 200 Б без загрузки трафика), качает во временную папку;
  - `LokalnyyIstochnik` — файл или папка на диске (для разработки без FTP).

Полную валидацию (открыть xls, число строк/колонок) делает уже `run.py` через `validate.proverit`
после получения пути — здесь только доставка + дешёвый размер-гейт.

Переносимость (Ubuntu-ready): пути через `os.path`/`tempfile`, никаких Windows-специфик.
Креды — из `.env` (`PRICE_FTP_*`), в коде не хардкодятся.
"""
import ftplib
import os
import tempfile

from ..logger import logger
from . import meta, validate


class IstochnikError(Exception):
    """Не удалось получить годный файл прайса из источника (сеть/пусто/пустышка)."""


def _svezhiy_po_imeni(names: list[str]) -> str:
    """Самый свежий из имён: по дате в имени (…2026-07-18T11-29-30…), иначе лексикографически."""
    def klyuch(n: str):
        d = meta.data_iz_faila(n)  # парсит таймстамп из имени; для чужого имени вернёт None
        return (d is not None, d.isoformat() if d else "", n)
    return max(names, key=klyuch)


class LokalnyyIstochnik:
    """Локальный файл или папка — тот же интерфейс, что FTP (разработка без сети)."""

    def __init__(self, path: str):
        self.path = path

    def svezhee_imya(self) -> str:
        """Имя свежего файла без «скачивания» (для skip-если-уже-применён, этап 24)."""
        if os.path.isfile(self.path):
            return os.path.basename(self.path)
        if os.path.isdir(self.path):
            files = [f for f in os.listdir(self.path) if f.lower().endswith((".xls", ".xlsx"))]
            if not files:
                raise IstochnikError(f"в папке нет .xls: {self.path}")
            return _svezhiy_po_imeni(files)
        raise IstochnikError(f"путь не найден: {self.path}")

    def poluchit_svezhiy(self) -> str:
        if os.path.isfile(self.path):
            return self.path
        svezhiy = self.svezhee_imya()  # для папки
        logger.info(f"Локальный источник: свежий файл {svezhiy}")
        return os.path.join(self.path, svezhiy)


class FtpIstochnik:
    """FTP-приём: свежий .xls из папки, размер-гейт до скачивания, загрузка во временную папку."""

    def __init__(self, host: str, port: int, user: str, password: str, directory: str):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.directory = directory

    def _spisok_xls(self, ftp: ftplib.FTP) -> list[str]:
        names = [n for n in ftp.nlst() if n.lower().endswith((".xls", ".xlsx"))]
        # nlst иногда отдаёт путь с папкой — оставляем только имя файла
        return [os.path.basename(n) for n in names]

    def _connect(self) -> ftplib.FTP:
        ftp = ftplib.FTP()
        ftp.connect(self.host, self.port, timeout=30)
        ftp.login(self.user, self.password)
        ftp.set_pasv(True)  # пассивный режим (vsftpd, диапазон 40000-40100)
        ftp.cwd(self.directory)
        ftp.voidcmd("TYPE I")  # бинарный режим — иначе SIZE не работает
        return ftp

    def svezhee_imya(self) -> str:
        """Имя свежего файла БЕЗ скачивания (для skip-если-уже-применён, этап 24)."""
        logger.info(f"FTP: смотрю свежий файл в {self.host}:{self.port}/{self.directory}…")
        ftp = None
        try:
            ftp = self._connect()
            names = self._spisok_xls(ftp)
            if not names:
                raise IstochnikError(f"на FTP нет .xls в папке {self.directory}")
            return _svezhiy_po_imeni(names)
        except ftplib.all_errors as e:
            raise IstochnikError(f"ошибка FTP: {e}")
        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except ftplib.all_errors:
                    pass

    def poluchit_svezhiy(self) -> str:
        logger.info(f"FTP: подключаюсь к {self.host}:{self.port} (папка {self.directory})…")
        ftp = None
        try:
            ftp = self._connect()
            names = self._spisok_xls(ftp)
            if not names:
                raise IstochnikError(f"на FTP нет .xls в папке {self.directory}")
            name = _svezhiy_po_imeni(names)

            # размер ДО скачивания — отсекаем пустышку без трафика
            try:
                size = ftp.size(name)
            except ftplib.all_errors:
                size = None
            logger.info(f"FTP: свежий файл {name} (размер {size if size is not None else '?'} Б)")
            if size is not None and size < validate.MIN_RAZMER:
                raise IstochnikError(
                    f"файл на FTP мал ({size} Б < {validate.MIN_RAZMER}) — пустышка, не скачиваю"
                )

            # скачиваем во временную папку (детерминированное имя — не плодим мусор по дням)
            tmp_dir = os.path.join(tempfile.gettempdir(), "telewin_price")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp = os.path.join(tmp_dir, name)
            with open(tmp, "wb") as f:
                ftp.retrbinary(f"RETR {name}", f.write)
            logger.info(f"FTP: скачан → {tmp} ({os.path.getsize(tmp)} Б)")
            return tmp
        except ftplib.all_errors as e:
            raise IstochnikError(f"ошибка FTP: {e}")
        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except ftplib.all_errors:
                    pass


def sozdat_ftp_istochnik(cfg) -> FtpIstochnik:
    """Собирает FTP-источник из конфига. Нет FTP-кредов → понятная ошибка."""
    if not cfg.ftp or not cfg.ftp.host:
        raise IstochnikError(
            "FTP не настроен: задай PRICE_FTP_HOST/USER/PASSWORD/DIR в .env (см. .env.example)"
        )
    return FtpIstochnik(cfg.ftp.host, cfg.ftp.port, cfg.ftp.user,
                        cfg.ftp.password, cfg.ftp.directory)
