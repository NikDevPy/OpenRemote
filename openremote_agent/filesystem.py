"""Sandbox для имён файлов без абсолютных путей, .. и переходов через symlink."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import secrets
import stat


class SafeFiles:
    MAX_TEXT = 65536

    def __init__(self, root: Path, private: bool = True):
        root = root.absolute()
        if private:
            root.mkdir(parents=True, mode=0o700, exist_ok=True)
        st = root.lstat()
        if (root.is_symlink() or not stat.S_ISDIR(st.st_mode) or
                st.st_uid != os.getuid() or st.st_mode & 0o002 or
                (private and st.st_mode & 0o077)):
            raise ValueError("Корень файлов должен принадлежать пользователю, быть каталогом и не допускать чужой записи")
        self.root = root
        self.fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def close(self) -> None:
        os.close(self.fd)

    @staticmethod
    def parts(path: str) -> tuple[str, ...]:
        if not isinstance(path, str) or len(path) > 1024 or not path or path.startswith("/"):
            raise ValueError("Нужен относительный путь внутри sandbox")
        parts = tuple(segment for segment in path.split("/") if segment != ".")
        if not parts or any(not p or p == ".." or "\x00" in p or "\\" in p for p in parts):
            raise ValueError("Недопустимые компоненты пути")
        return parts

    def parent_fd(self, parts: tuple[str, ...]) -> int:
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _directory_fd(self, path: str) -> int:
        if path == ".":
            return os.dup(self.fd)
        parts = self.parts(path)
        fd = self.parent_fd(parts)
        try:
            directory = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            return directory
        finally:
            os.close(fd)

    def list_files(self, path: str = ".") -> dict:
        fd = self._directory_fd(path)
        try:
            with os.scandir(fd) as entries:
                names = []
                for entry in entries:
                    # Не следуем ссылкам при определении типа.
                    names.append({"name": entry.name, "type": (
                        "symlink" if entry.is_symlink() else
                        "dir" if entry.is_dir(follow_symlinks=False) else
                        "file" if entry.is_file(follow_symlinks=False) else "other"
                    )})
                    if len(names) > 100:
                        break
            return {"entries": sorted(names[:100], key=lambda x: x["name"]), "truncated": len(names) > 100}
        finally:
            os.close(fd)

    def read_text(self, path: str) -> dict:
        parts = self.parts(path)
        parent_fd = self.parent_fd(parts)
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise ValueError("Допускаются только обычные файлы")
                if st.st_size > self.MAX_TEXT:
                    raise ValueError("Файл превышает предел 64 КиБ")
                content = os.read(fd, self.MAX_TEXT + 1)
                if len(content) > self.MAX_TEXT:
                    raise ValueError("Файл превышает предел 64 КиБ")
                return {"content": content.decode("utf-8"), "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest()}
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)

    def write_text(self, path: str, content: str) -> dict:
        if not isinstance(content, str):
            raise ValueError("Требуется UTF-8 текст")
        payload = content.encode("utf-8")
        if len(payload) > self.MAX_TEXT:
            raise ValueError("Превышен предел 64 КиБ")
        parts = self.parts(path)
        parent_fd = self.parent_fd(parts)
        created = False
        try:
            fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
            created = True
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                if created:
                    os.unlink(parts[-1], dir_fd=parent_fd)
                raise
            return {"created": True, "size": len(payload)}
        finally:
            os.close(parent_fd)


    def replace_text(self, path: str, content: str, expected_sha256: str) -> dict:
        """Сменить содержимое одним rename после проверки хэша и типа файла.

        Проверка хэша оптимистическая: посторонние процессы, не использующие
        advisory locks, могут менять файл между последней проверкой и rename.
        """
        if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or
                any(c not in "0123456789abcdef" for c in expected_sha256)):
            raise ValueError("Требуется корректный SHA-256 текущего содержимого")
        if not isinstance(content, str):
            raise ValueError("Требуется UTF-8 текст")
        payload = content.encode("utf-8")
        if len(payload) > self.MAX_TEXT:
            raise ValueError("Размер текста превышает допустимый предел")
        parts = self.parts(path)
        parent_fd = self.parent_fd(parts)
        temp_name = None
        try:
            src_fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_fd,
            )
            try:
                current = os.fstat(src_fd)
                if not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid():
                    raise ValueError("Допускается заменять только собственные обычные файлы")
                if current.st_size > self.MAX_TEXT:
                    raise ValueError("Файл слишком велик")
                data = os.read(src_fd, self.MAX_TEXT + 1)
                if hashlib.sha256(data).hexdigest() != expected_sha256:
                    raise ValueError("Конфликт: файл изменился, требуется перечитать")
                if len(data) > self.MAX_TEXT:
                    raise ValueError("Файл слишком велик")
                # Запрещаем замену, если файл или метаданные поменялись с момента чтения.
                latest = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if ((latest.st_ino, latest.st_mtime_ns, latest.st_size, latest.st_dev) !=
                        (current.st_ino, current.st_mtime_ns, current.st_size, current.st_dev)):
                    raise ValueError("Конфликт изменения файла")
                temp_name = ".openremote-" + secrets.token_hex(16)
                tmp_fd = os.open(
                    temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=parent_fd,
                )
                try:
                    os.fchmod(tmp_fd, stat.S_IMODE(current.st_mode) & 0o777)
                    with os.fdopen(tmp_fd, "wb") as out:
                        out.write(payload)
                        out.flush()
                        os.fsync(out.fileno())
                except BaseException:
                    # fdopen() владеет файловым дескриптором после создания.
                    raise
                latest = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if ((latest.st_ino, latest.st_mtime_ns, latest.st_size, latest.st_dev) !=
                        (current.st_ino, current.st_mtime_ns, current.st_size, current.st_dev)):
                    raise ValueError("Конфликт изменения файла")
                os.replace(temp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                temp_name = None
                os.fsync(parent_fd)
                return {"replaced": True, "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()}
            finally:
                os.close(src_fd)
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)
