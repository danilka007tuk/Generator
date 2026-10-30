from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from github import Auth, Github, GithubException, InputGitTreeElement


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

logger = logging.getLogger(__name__)


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "").strip()

if not GITHUB_TOKEN:
    raise RuntimeError("GITHUB_TOKEN не задан в .env")

if not GITHUB_USERNAME:
    raise RuntimeError("GITHUB_USERNAME не задан в .env")


GITHUB_TIMEOUT = 30
MAX_COMMIT_ATTEMPTS = 3
COMMIT_RETRY_DELAY = 2


github_client = Github(
    auth=Auth.Token(GITHUB_TOKEN),
    timeout=GITHUB_TIMEOUT,
)


def _is_retryable_github_error(error: GithubException) -> bool:
    """
    Определяет, стоит ли повторить GitHub-запрос.

    409 — конфликт при обновлении ветки или файла.
    422 — иногда GitHub возвращает его при конфликтующем
    или повторном Pages-запросе.
    500/502/503/504 — временная ошибка GitHub.
    """
    return error.status in {
        409,
        422,
        500,
        502,
        503,
        504,
    }


def _read_text_file(path: Path) -> str:
    """
    Читает UTF-8-текстовый файл.
    """
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    if not path.is_file():
        raise ValueError(f"Ожидался файл, получен другой объект: {path}")

    return path.read_text(encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    """
    Возвращает SHA-256 содержимого файла.

    Используется для логирования и диагностики.
    """
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha(data: bytes) -> str:
    """
    Вычисляет SHA-1 Git Blob.

    Git считает SHA так:

        SHA1(b"blob " + str(len(data)).encode() + b"\\0" + data)

    Это позволяет корректно сравнивать бинарные файлы с объектами GitHub.
    """
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def _create_blob_for_file(
    repo,
    file_path: Path,
):
    """
    Создаёт Git Blob для бинарного файла.

    GitHub API получает содержимое в base64.
    """
    file_bytes = file_path.read_bytes()
    encoded = base64.b64encode(file_bytes).decode("ascii")

    blob = repo.create_git_blob(
        encoded,
        "base64",
    )

    logger.debug(
        "Создан blob %s для %s; size=%s; sha256=%s",
        blob.sha,
        file_path,
        len(file_bytes),
        _sha256_bytes(file_bytes),
    )

    return blob


def _build_tree_elements(
    repo,
    html_path: Path,
) -> list[InputGitTreeElement]:
    """
    Формирует Git tree для всего сайта.

    В tree попадают:

    - index.html;
    - register.html, если существует;
    - все файлы из images/.

    html_path должен указывать на index.html.
    """
    html_path = html_path.expanduser().resolve()

    if not html_path.exists():
        raise FileNotFoundError(
            f"Основной HTML-файл не найден: {html_path}"
        )

    if not html_path.is_file():
        raise ValueError(
            f"Путь к HTML не является файлом: {html_path}"
        )

    if html_path.name != "index.html":
        logger.warning(
            "В deploy_to_github передан файл %s вместо index.html",
            html_path.name,
        )

    elements: list[InputGitTreeElement] = []

    index_content = _read_text_file(html_path)

    elements.append(
        InputGitTreeElement(
            path="index.html",
            mode="100644",
            type="blob",
            content=index_content,
        )
    )

    register_path = html_path.parent / "register.html"

    if register_path.exists() and register_path.is_file():
        register_content = _read_text_file(register_path)

        elements.append(
            InputGitTreeElement(
                path="register.html",
                mode="100644",
                type="blob",
                content=register_content,
            )
        )

        logger.info(
            "Добавлен register.html: %s",
            register_path,
        )
    else:
        logger.warning(
            "Файл register.html не найден: %s",
            register_path,
        )

    images_dir = html_path.parent / "images"

    if not images_dir.exists():
        logger.warning(
            "Папка изображений не найдена: %s",
            images_dir,
        )
        return elements

    if not images_dir.is_dir():
        logger.warning(
            "Путь images не является каталогом: %s",
            images_dir,
        )
        return elements

    image_files = sorted(
        path
        for path in images_dir.rglob("*")
        if path.is_file()
    )

    logger.info(
        "Найдено изображений для деплоя: %s",
        len(image_files),
    )

    for image_path in image_files:
        relative_path = image_path.relative_to(images_dir)

        # В GitHub Pages используем POSIX-разделители.
        repo_path = (
            Path("images") / relative_path
        ).as_posix()

        if any(part == ".." for part in relative_path.parts):
            logger.warning(
                "Пропущен небезопасный путь изображения: %s",
                image_path,
            )
            continue

        blob = _create_blob_for_file(
            repo,
            image_path,
        )

        elements.append(
            InputGitTreeElement(
                path=repo_path,
                mode="100644",
                type="blob",
                sha=blob.sha,
            )
        )

        logger.info(
            "Добавлено изображение: %s",
            repo_path,
        )

    return elements


def _get_or_create_repository(
    repo_name: str,
):
    """
    Получает репозиторий или создаёт его.
    """
    if not repo_name or not repo_name.strip():
        raise ValueError("repo_name не может быть пустым")

    repo_name = repo_name.strip()

    user = github_client.get_user()

    try:
        repo = user.get_repo(repo_name)
        logger.info(
            "Используется существующий репозиторий: %s/%s",
            GITHUB_USERNAME,
            repo_name,
        )
        return repo

    except GithubException as error:
        if error.status != 404:
            logger.exception(
                "Ошибка при получении репозитория %s: %s",
                repo_name,
                error,
            )
            raise

    logger.info(
        "Репозиторий %s не найден, создаём новый",
        repo_name,
    )

    repo = user.create_repo(
        repo_name,
        auto_init=True,
    )

    # GitHub может не сразу подготовить initial commit и ветку.
    for attempt in range(1, 6):
        try:
            repo = user.get_repo(repo_name)
            default_branch = repo.default_branch

            if default_branch:
                return repo

        except GithubException as error:
            logger.warning(
                "Репозиторий ещё не готов, попытка %s/5: %s",
                attempt,
                error,
            )

        time.sleep(2)

    raise RuntimeError(
        f"Репозиторий {repo_name} создан, но не готов к деплою"
    )


def _get_branch_state(
    repo,
    branch: str,
):
    """
    Возвращает текущую ссылку ветки, commit и base tree.
    """
    ref = repo.get_git_ref(f"heads/{branch}")
    parent_commit = repo.get_git_commit(
        ref.object.sha,
    )
    base_tree = repo.get_git_tree(
        parent_commit.commit.tree.sha,
    )

    return ref, parent_commit, base_tree


def _publish_tree_once(
    repo,
    repo_name: str,
    html_path: Path,
) -> str:
    """
    Создаёт один commit со всеми файлами сайта
    и fast-forward обновляет ветку.

    Возвращает SHA нового коммита.
    """
    branch = repo.default_branch or "main"

    ref, parent_commit, base_tree = _get_branch_state(
        repo,
        branch,
    )

    elements = _build_tree_elements(
        repo,
        html_path,
    )

    if not elements:
        raise RuntimeError(
            "Не сформировано ни одного файла для публикации"
        )

    tree = repo.create_git_tree(
        elements,
        base_tree,
    )

    commit = repo.create_git_commit(
        message=f"Publish site {repo_name}",
        tree=tree,
        parents=[parent_commit],
    )

    # force=False не перезаписывает чужие изменения.
    # Если ветка изменилась между чтением и edit(), GitHub вернёт 409.
    ref.edit(
        commit.sha,
        force=False,
    )

    logger.info(
        "Опубликован commit %s для %s/%s в ветку %s",
        commit.sha,
        GITHUB_USERNAME,
        repo_name,
        branch,
    )

    return commit.sha


def _publish_tree_with_retry(
    repo,
    repo_name: str,
    html_path: Path,
) -> str:
    """
    Повторяет публикацию при конфликте ветки.

    При каждом повторе заново читается текущий HEAD,
    строится новый tree и создаётся новый commit.
    """
    last_error: Exception | None = None

    for attempt in range(1, MAX_COMMIT_ATTEMPTS + 1):
        try:
            return _publish_tree_once(
                repo,
                repo_name,
                html_path,
            )

        except GithubException as error:
            last_error = error

            if not _is_retryable_github_error(error):
                logger.exception(
                    "Неповторяемая ошибка GitHub при публикации "
                    "%s: %s",
                    repo_name,
                    error,
                )
                raise

            if attempt >= MAX_COMMIT_ATTEMPTS:
                break

            logger.warning(
                "Конфликт/временная ошибка публикации %s "
                "(попытка %s/%s): %s",
                repo_name,
                attempt,
                MAX_COMMIT_ATTEMPTS,
                error,
            )

            time.sleep(
                COMMIT_RETRY_DELAY * attempt
            )

        except Exception as error:
            last_error = error
            raise

    raise RuntimeError(
        f"Не удалось опубликовать {repo_name} "
        f"после {MAX_COMMIT_ATTEMPTS} попыток: {last_error}"
    )


def _enable_github_pages(
    repo,
    repo_name: str,
) -> None:
    """
    Включает GitHub Pages для default branch.

    409 обычно означает, что Pages уже включён.
    """
    branch = repo.default_branch or "main"

    try:
        repo.requester.requestJsonAndCheck(
            "POST",
            f"/repos/{GITHUB_USERNAME}/{repo_name}/pages",
            input={
                "source": {
                    "branch": branch,
                    "path": "/",
                },
            },
        )

        logger.info(
            "GitHub Pages включён для %s/%s",
            GITHUB_USERNAME,
            repo_name,
        )

    except GithubException as error:
        if error.status == 409:
            logger.info(
                "GitHub Pages уже включён для %s/%s",
                GITHUB_USERNAME,
                repo_name,
            )
            return

        # Повторный Pages API иногда возвращает 422,
        # если конфигурация уже существует.
        if error.status == 422:
            logger.warning(
                "GitHub Pages вернул 422 для %s; "
                "проверяем текущую конфигурацию",
                repo_name,
            )

            try:
                repo.requester.requestJsonAndCheck(
                    "GET",
                    f"/repos/{GITHUB_USERNAME}/{repo_name}/pages",
                )
                logger.info(
                    "Текущая конфигурация GitHub Pages существует"
                )
                return

            except GithubException:
                logger.exception(
                    "Не удалось проверить GitHub Pages для %s",
                    repo_name,
                )
                raise

        logger.exception(
            "Ошибка при включении GitHub Pages для %s: %s",
            repo_name,
            error,
        )
        raise


def _wait_for_pages_build(
    repo,
    timeout: int = 10,
    interval: int = 3,
) -> bool:
    """
    Ждёт сборку GitHub Pages ограниченное время.

    После timeout публикация не считается неудачной:
    GitHub Pages может продолжить сборку в фоне.
    """
    if timeout <= 0:
        return False

    if interval <= 0:
        interval = 1

    deadline = time.time() + timeout
    last_status: str | None = None

    while time.time() < deadline:
        try:
            build = repo.requester.requestJsonAndCheck(
                "GET",
                f"/repos/{GITHUB_USERNAME}/{repo.name}"
                "/pages/builds/latest",
            )[1]

            status = build.get("status")

            if status != last_status:
                logger.info(
                    "GitHub Pages build status для %s: %s",
                    repo.name,
                    status,
                )
                last_status = status

            if status == "built":
                return True

            if status in {
                "errored",
                "error",
            }:
                logger.error(
                    "GitHub Pages build завершился ошибкой: %s",
                    build.get("error"),
                )
                return False

        except GithubException as error:
            if error.status != 404:
                logger.warning(
                    "Ошибка проверки Pages build для %s: %s",
                    repo.name,
                    error,
                )

        time.sleep(interval)

    logger.warning(
        "Pages build для %s не завершился за %s секунд; "
        "сборка продолжится в фоне",
        repo.name,
        timeout,
    )

    return False


def deploy_to_github(
    repo_name: str,
    html_path: Path,
) -> str:
    """
    Публикует сайт на GitHub Pages.

    Ожидаемая структура рядом с html_path:

        <job_dir>/
            index.html
            register.html
            images/
                hero.png
                gallery.png

    Возвращает URL сайта.
    """
    repo_name = str(repo_name).strip()

    if not repo_name:
        raise ValueError("repo_name не может быть пустым")

    html_path = Path(html_path).expanduser().resolve()

    if not html_path.exists():
        raise FileNotFoundError(
            f"Файл index.html не найден: {html_path}"
        )

    repo = _get_or_create_repository(
        repo_name,
    )

    _publish_tree_with_retry(
        repo,
        repo_name,
        html_path,
    )

    _enable_github_pages(
        repo,
        repo_name,
    )

    _wait_for_pages_build(
        repo,
        timeout=10,
        interval=3,
    )

    site_url = (
        f"https://{GITHUB_USERNAME}.github.io/"
        f"{repo_name}/"
    )

    logger.info(
        "Сайт опубликован: %s",
        site_url,
    )

    return site_url