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


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(
    dotenv_path=BASE_DIR / ".env"
)

logger = logging.getLogger(__name__)


GITHUB_TOKEN = os.getenv(
    "GITHUB_TOKEN",
    "",
).strip()

GITHUB_USERNAME = os.getenv(
    "GITHUB_USERNAME",
    "",
).strip()

GITHUB_TIMEOUT = int(
    os.getenv(
        "GITHUB_TIMEOUT",
        "30",
    )
)

MAX_COMMIT_ATTEMPTS = int(
    os.getenv(
        "MAX_COMMIT_ATTEMPTS",
        "3",
    )
)

COMMIT_RETRY_DELAY = int(
    os.getenv(
        "COMMIT_RETRY_DELAY",
        "2",
    )
)

PAGES_BUILD_TIMEOUT = int(
    os.getenv(
        "PAGES_BUILD_TIMEOUT",
        "30",
    )
)

PAGES_BUILD_INTERVAL = int(
    os.getenv(
        "PAGES_BUILD_INTERVAL",
        "3",
    )
)


if not GITHUB_TOKEN:
    raise RuntimeError(
        "GITHUB_TOKEN не задан в .env"
    )

if not GITHUB_USERNAME:
    raise RuntimeError(
        "GITHUB_USERNAME не задан в .env"
    )


github_client = Github(
    auth=Auth.Token(GITHUB_TOKEN),
    timeout=GITHUB_TIMEOUT,
)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _github_error_details(
    error: GithubException,
) -> str:
    """
    Возвращает подробное описание ошибки GitHub API.
    """
    status = getattr(
        error,
        "status",
        "unknown",
    )

    data = getattr(
        error,
        "data",
        None,
    )

    if isinstance(data, dict):
        message = data.get(
            "message",
            "",
        )

        errors = data.get(
            "errors",
            [],
        )

        documentation_url = data.get(
            "documentation_url",
            "",
        )

        return (
            f"status={status}; "
            f"message={message}; "
            f"errors={errors}; "
            f"documentation_url={documentation_url}"
        )

    return (
        f"status={status}; "
        f"error={error}"
    )


def _is_retryable_github_error(
    error: GithubException,
) -> bool:
    """
    Определяет, можно ли безопасно повторить GitHub-запрос.

    409 — конфликт при обновлении ветки.

    500/502/503/504 — временные ошибки GitHub.

    422 намеренно не включён:
    чаще всего это ошибка параметров, прав или состояния API.
    """
    return getattr(
        error,
        "status",
        None,
    ) in {
        409,
        500,
        502,
        503,
        504,
    }


def _read_text_file(
    path: Path,
) -> str:
    """
    Читает UTF-8-текстовый файл.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Файл не найден: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Ожидался файл, получен другой объект: {path}"
        )

    return path.read_text(
        encoding="utf-8",
    )


def _sha256_bytes(
    data: bytes,
) -> str:
    """
    Возвращает SHA-256 содержимого файла.
    """
    return hashlib.sha256(
        data,
    ).hexdigest()


def _git_blob_sha(
    data: bytes,
) -> str:
    """
    Вычисляет SHA-1 Git Blob.

    Формула Git:

        SHA1(
            b"blob "
            + str(len(data)).encode()
            + b"\\0"
            + data
        )
    """
    header = (
        f"blob {len(data)}\0"
        .encode("utf-8")
    )

    return hashlib.sha1(
        header + data,
    ).hexdigest()


def _normalize_repo_name(
    repo_name: str,
) -> str:
    """
    Проверяет и нормализует имя репозитория.
    """
    value = str(
        repo_name or "",
    ).strip()

    if not value:
        raise ValueError(
            "repo_name не может быть пустым"
        )

    if "/" in value:
        raise ValueError(
            "repo_name должен содержать только имя репозитория, "
            "без owner и URL"
        )

    if value in {".", ".."}:
        raise ValueError(
            "Недопустимое имя репозитория"
        )

    return value


def _normalize_html_path(
    html_path: Path,
) -> Path:
    """
    Проверяет и нормализует путь к index.html.
    """
    path = (
        Path(html_path)
        .expanduser()
        .resolve()
    )

    if not path.exists():
        raise FileNotFoundError(
            f"HTML-файл не найден: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Путь к HTML не является файлом: {path}"
        )

    return path


def _repo_api_path(
    repo_name: str,
    suffix: str = "",
) -> str:
    """
    Формирует путь GitHub API для репозитория.
    """
    owner = GITHUB_USERNAME.strip("/")
    suffix = suffix.lstrip("/")

    base = (
        f"/repos/{owner}/{repo_name}"
    )

    return (
        f"{base}/{suffix}"
        if suffix
        else base
    )


# ---------------------------------------------------------------------------
# Работа с Git Blob
# ---------------------------------------------------------------------------

def _create_blob_for_file(
    repo: Any,
    file_path: Path,
):
    """
    Создаёт Git Blob для бинарного файла.

    GitHub API получает содержимое в base64.
    """
    file_bytes = file_path.read_bytes()

    encoded = base64.b64encode(
        file_bytes,
    ).decode("ascii")

    blob = repo.create_git_blob(
        encoded,
        "base64",
    )

    logger.debug(
        "Создан blob: sha=%s; file=%s; size=%s; sha256=%s",
        blob.sha,
        file_path,
        len(file_bytes),
        _sha256_bytes(file_bytes),
    )

    return blob


# ---------------------------------------------------------------------------
# Формирование Git tree
# ---------------------------------------------------------------------------

def _build_tree_elements(
    repo: Any,
    html_path: Path,
) -> list[InputGitTreeElement]:
    """
    Формирует Git tree для сайта.

    В публикацию входят:

    - index.html;
    - register.html, если существует;
    - все файлы из images/.
    """
    html_path = _normalize_html_path(
        html_path,
    )

    elements: list[InputGitTreeElement] = []

    if html_path.name != "index.html":
        logger.warning(
            "В deploy_to_github передан файл %s вместо index.html",
            html_path.name,
        )

    index_content = _read_text_file(
        html_path,
    )

    elements.append(
        InputGitTreeElement(
            path="index.html",
            mode="100644",
            type="blob",
            content=index_content,
        )
    )

    logger.info(
        "Добавлен index.html: %s",
        html_path,
    )

    register_path = (
        html_path.parent / "register.html"
    )

    if register_path.exists() and register_path.is_file():
        register_content = _read_text_file(
            register_path,
        )

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
        logger.info(
            "register.html не найден: %s",
            register_path,
        )

    images_dir = (
        html_path.parent / "images"
    )

    if not images_dir.exists():
        logger.info(
            "Папка images отсутствует: %s",
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
        "Найдено изображений для публикации: %s",
        len(image_files),
    )

    for image_path in image_files:
        relative_path = image_path.relative_to(
            images_dir,
        )

        if any(
            part == ".."
            for part in relative_path.parts
        ):
            logger.warning(
                "Пропущен небезопасный путь: %s",
                image_path,
            )
            continue

        repo_path = (
            Path("images") / relative_path
        ).as_posix()

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


# ---------------------------------------------------------------------------
# Репозиторий
# ---------------------------------------------------------------------------

def _get_or_create_repository(
    repo_name: str,
):
    """
    Получает репозиторий или создаёт его.

    После создания ждёт появления default branch.
    """
    repo_name = _normalize_repo_name(
        repo_name,
    )

    user = github_client.get_user()

    try:
        repo = user.get_repo(
            repo_name,
        )

        logger.info(
            "Используется существующий репозиторий: %s/%s",
            GITHUB_USERNAME,
            repo_name,
        )

        return repo

    except GithubException as error:
        status = getattr(
            error,
            "status",
            None,
        )

        if status != 404:
            logger.error(
                "Ошибка получения репозитория %s/%s: %s",
                GITHUB_USERNAME,
                repo_name,
                _github_error_details(error),
            )
            raise

    logger.info(
        "Репозиторий %s/%s не найден. Создаём новый.",
        GITHUB_USERNAME,
        repo_name,
    )

    try:
        repo = user.create_repo(
            name=repo_name,
            auto_init=True,
            private=False,
        )

    except GithubException as error:
        logger.error(
            "Ошибка создания репозитория %s/%s: %s",
            GITHUB_USERNAME,
            repo_name,
            _github_error_details(error),
        )
        raise

    deadline = time.time() + 30
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            repo = user.get_repo(
                repo_name,
            )

            branch = repo.default_branch

            if not branch:
                time.sleep(2)
                continue

            repo.get_git_ref(
                f"heads/{branch}",
            )

            logger.info(
                "Репозиторий готов: %s/%s; branch=%s",
                GITHUB_USERNAME,
                repo_name,
                branch,
            )

            return repo

        except GithubException as error:
            last_error = error

            logger.warning(
                "Репозиторий ещё не готов: %s",
                _github_error_details(error),
            )

            time.sleep(2)

    raise RuntimeError(
        f"Репозиторий {GITHUB_USERNAME}/{repo_name} "
        f"создан, но default branch не появилась. "
        f"Последняя ошибка: {last_error}"
    )


# ---------------------------------------------------------------------------
# Ветка и Git tree
# ---------------------------------------------------------------------------

def _get_branch_state(
    repo: Any,
    branch: str,
):
    """
    Возвращает ref, родительский commit и base tree.
    """
    ref = repo.get_git_ref(
        f"heads/{branch}",
    )

    parent_commit = repo.get_git_commit(
        ref.object.sha,
    )

    # ИСПРАВЛЕНО: GitCommit имеет атрибут .tree, а не .commit.tree
    base_tree = repo.get_git_tree(
        parent_commit.tree.sha,
    )

    return (
        ref,
        parent_commit,
        base_tree,
    )


def _publish_tree_once(
    repo: Any,
    repo_name: str,
    html_path: Path,
) -> str:
    """
    Создаёт один commit со всеми файлами сайта
    и обновляет ветку без force push.
    """
    branch = (
        repo.default_branch
        or "main"
    )

    logger.info(
        "Подготовка публикации: repo=%s; branch=%s",
        repo_name,
        branch,
    )

    ref, parent_commit, base_tree = (
        _get_branch_state(
            repo,
            branch,
        )
    )

    elements = _build_tree_elements(
        repo,
        html_path,
    )

    if not elements:
        raise RuntimeError(
            "Не сформировано ни одного файла для публикации"
        )

    logger.info(
        "Файлов в новом tree: %s",
        len(elements),
    )

    tree = repo.create_git_tree(
        elements,
        base_tree,
    )

    logger.info(
        "Создан Git tree: %s",
        tree.sha,
    )

    commit = repo.create_git_commit(
        message=f"Publish site {repo_name}",
        tree=tree,
        parents=[parent_commit],
    )

    logger.info(
        "Создан commit: %s",
        commit.sha,
    )

    # force=False защищает от перезаписи чужих изменений.
    ref.edit(
        commit.sha,
        force=False,
    )

    logger.info(
        "Ветка %s обновлена до commit %s",
        branch,
        commit.sha,
    )

    return commit.sha


def _publish_tree_with_retry(
    repo: Any,
    repo_name: str,
    html_path: Path,
) -> str:
    """
    Повторяет публикацию только при конфликте ref
    или временной ошибке GitHub.
    """
    last_error: Exception | None = None

    attempts = max(
        1,
        MAX_COMMIT_ATTEMPTS,
    )

    for attempt in range(1, attempts + 1):
        try:
            return _publish_tree_once(
                repo,
                repo_name,
                html_path,
            )

        except GithubException as error:
            last_error = error

            logger.error(
                "Ошибка публикации %s; попытка %s/%s: %s",
                repo_name,
                attempt,
                attempts,
                _github_error_details(error),
            )

            if not _is_retryable_github_error(
                error,
            ):
                raise

            if attempt >= attempts:
                break

            delay = (
                COMMIT_RETRY_DELAY * attempt
            )

            logger.warning(
                "Повтор публикации через %s секунд",
                delay,
            )

            time.sleep(delay)

        except Exception as error:
            last_error = error
            logger.exception(
                "Непредвиденная ошибка публикации %s",
                repo_name,
            )
            raise

    raise RuntimeError(
        f"Не удалось опубликовать {repo_name} "
        f"после {attempts} попыток: {last_error}"
    )


# ---------------------------------------------------------------------------
# GitHub Pages
# ---------------------------------------------------------------------------

def _get_pages_config(
    repo: Any,
    repo_name: str,
):
    """
    Получает текущую конфигурацию GitHub Pages.

    Возвращает словарь конфигурации либо None,
    если Pages ещё не включён.
    """
    pages_url = _repo_api_path(
        repo_name,
        "pages",
    )

    try:
        response = repo.requester.requestJsonAndCheck(
            "GET",
            pages_url,
        )

        if isinstance(response, tuple):
            return response[1]

        return response

    except GithubException as error:
        if getattr(error, "status", None) == 404:
            return None

        logger.error(
            "Ошибка получения Pages config для %s: %s",
            repo_name,
            _github_error_details(error),
        )
        raise


def _enable_github_pages(
    repo: Any,
    repo_name: str,
) -> None:
    """
    Включает или обновляет GitHub Pages.
    """
    branch = (
        repo.default_branch
        or "main"
    )

    pages_url = _repo_api_path(
        repo_name,
        "pages",
    )

    payload = {
        "source": {
            "branch": branch,
            "path": "/",
        }
    }

    current_config = _get_pages_config(
        repo,
        repo_name,
    )

    if current_config is not None:
        logger.info(
            "GitHub Pages уже настроен для %s/%s: %s",
            GITHUB_USERNAME,
            repo_name,
            current_config,
        )

        try:
            repo.requester.requestJsonAndCheck(
                "PUT",
                pages_url,
                input=payload,
            )

            logger.info(
                "Конфигурация GitHub Pages обновлена: "
                "%s/%s; branch=%s",
                GITHUB_USERNAME,
                repo_name,
                branch,
            )

            return

        except GithubException as error:
            logger.error(
                "Не удалось обновить GitHub Pages для %s/%s: %s",
                GITHUB_USERNAME,
                repo_name,
                _github_error_details(error),
            )
            raise

    logger.info(
        "GitHub Pages ещё не включён для %s/%s",
        GITHUB_USERNAME,
        repo_name,
    )

    try:
        repo.requester.requestJsonAndCheck(
            "POST",
            pages_url,
            input=payload,
        )

        logger.info(
            "GitHub Pages включён для %s/%s; branch=%s",
            GITHUB_USERNAME,
            repo_name,
            branch,
        )

    except GithubException as error:
        status = getattr(
            error,
            "status",
            None,
        )

        if status == 409:
            logger.info(
                "GitHub Pages уже включён для %s/%s",
                GITHUB_USERNAME,
                repo_name,
            )
            return

        logger.error(
            "Ошибка включения GitHub Pages для %s/%s: %s",
            GITHUB_USERNAME,
            repo_name,
            _github_error_details(error),
        )
        raise


def _wait_for_pages_build(
    repo: Any,
    repo_name: str,
    timeout: int = PAGES_BUILD_TIMEOUT,
    interval: int = PAGES_BUILD_INTERVAL,
) -> bool:
    """
    Ожидает завершения последней сборки GitHub Pages.

    Если сборка не успела завершиться за timeout,
    это не считается ошибкой публикации:
    GitHub может продолжить сборку в фоне.
    """
    timeout = max(
        int(timeout),
        0,
    )

    interval = max(
        int(interval),
        1,
    )

    if timeout == 0:
        return False

    builds_url = _repo_api_path(
        repo_name,
        "pages/builds/latest",
    )

    deadline = time.time() + timeout
    last_status: str | None = None

    while time.time() < deadline:
        try:
            response = repo.requester.requestJsonAndCheck(
                "GET",
                builds_url,
            )

            if isinstance(response, tuple):
                build_data = response[1]
            else:
                build_data = response

            if not isinstance(build_data, dict):
                logger.warning(
                    "GitHub вернул неожиданный ответ Pages build: %r",
                    build_data,
                )
                time.sleep(interval)
                continue

            status = build_data.get(
                "status",
            )

            if status != last_status:
                logger.info(
                    "Статус Pages build для %s: %s",
                    repo_name,
                    status,
                )
                last_status = status

            if status == "built":
                logger.info(
                    "GitHub Pages успешно собран для %s",
                    repo_name,
                )
                return True

            if status in {
                "errored",
                "error",
            }:
                logger.error(
                    "Сборка GitHub Pages завершилась ошибкой: %s",
                    build_data,
                )
                return False

        except GithubException as error:
            status = getattr(
                error,
                "status",
                None,
            )

            # Сразу после включения Pages build может ещё отсутствовать.
            if status != 404:
                logger.warning(
                    "Ошибка проверки Pages build для %s: %s",
                    repo_name,
                    _github_error_details(error),
                )

        time.sleep(interval)

    logger.warning(
        "Pages build для %s не завершился за %s секунд. "
        "Сборка продолжится в фоне.",
        repo_name,
        timeout,
    )

    return False


# ---------------------------------------------------------------------------
# Публичная функция деплоя
# ---------------------------------------------------------------------------

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

    Возвращает URL опубликованного сайта.
    """
    repo_name = _normalize_repo_name(
        repo_name,
    )

    html_path = _normalize_html_path(
        html_path,
    )

    logger.info(
        "Начало публикации: owner=%s; repo=%s; html=%s",
        GITHUB_USERNAME,
        repo_name,
        html_path,
    )

    try:
        repo = _get_or_create_repository(
            repo_name,
        )

        commit_sha = _publish_tree_with_retry(
            repo,
            repo_name,
            html_path,
        )

        logger.info(
            "Файлы опубликованы в commit %s",
            commit_sha,
        )

        _enable_github_pages(
            repo,
            repo_name,
        )

        _wait_for_pages_build(
            repo,
            repo_name,
            timeout=PAGES_BUILD_TIMEOUT,
            interval=PAGES_BUILD_INTERVAL,
        )

        site_url = (
            f"https://{GITHUB_USERNAME}.github.io/"
            f"{repo_name}/"
        )

        logger.info(
            "Публикация завершена: %s",
            site_url,
        )

        return site_url

    except GithubException as error:
        details = _github_error_details(
            error,
        )

        logger.error(
            "GitHub API error при публикации %s: %s",
            repo_name,
            details,
            exc_info=True,
        )

        raise RuntimeError(
            f"GitHub не завершил публикацию "
            f"{repo_name}: {details}"
        ) from error

    except Exception:
        logger.exception(
            "Непредвиденная ошибка публикации %s",
            repo_name,
        )
        raise