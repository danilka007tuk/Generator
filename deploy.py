import os
import time
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

from github import Github, GithubException, Auth

logger = logging.getLogger(__name__)

g = Github(auth=Auth.Token(os.getenv("GITHUB_TOKEN")))
USER = os.getenv("GITHUB_USERNAME")


def _wait_for_pages_build(repo, timeout: int = 60, interval: int = 3) -> bool:
    """
    Ждёт, пока GitHub Pages соберёт сайт.
    Возвращает True, если сборка завершилась успешно, False — если таймаут.
    """
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        try:
            build = repo.requester.requestJsonAndCheck(
                "GET",
                f"/repos/{USER}/{repo.name}/pages/builds/latest"
            )[1]
            status = build.get("status")
            if status != last_status:
                logger.info(f"Pages build status: {status}")
                last_status = status
            if status == "built":
                return True
            if status == "errored":
                logger.error(f"Pages build errored: {build.get('error')}")
                return False
        except GithubException as e:
            # 404 — Pages ещё не инициализирован, ждём
            if e.status == 404:
                pass
            else:
                logger.warning(f"Ошибка при проверке Pages build: {e}")
        time.sleep(interval)
    logger.warning(f"Pages build не завершился за {timeout} секунд")
    return False


def deploy_to_github(repo_name: str, html_path: Path) -> str:
    user = g.get_user()

    # 1. Получаем или создаём репозиторий
    try:
        repo = user.get_repo(repo_name)
    except GithubException as e:
        if e.status == 404:
            repo = user.create_repo(repo_name, auto_init=True)
            time.sleep(3)
        else:
            logger.error(f"Ошибка GitHub при получении репозитория {repo_name}: {e}")
            raise e

    # 2. Заливаем index.html
    content = html_path.read_text(encoding="utf-8")
    try:
        existing = repo.get_contents("index.html")
        repo.update_file("index.html", "Update site", content, existing.sha)
    except GithubException as e:
        if e.status == 404:
            repo.create_file("index.html", "Initial site", content)
        else:
            logger.error(f"Ошибка при заливке index.html: {e}")
            raise e

    # 3. Заливаем register.html (если есть)
    register_path = html_path.parent / "register.html"
    if register_path.exists():
        register_content = register_path.read_text(encoding="utf-8")
        try:
            existing = repo.get_contents("register.html")
            repo.update_file("register.html", "Update register", register_content, existing.sha)
        except GithubException as e:
            if e.status == 404:
                try:
                    repo.create_file("register.html", "Add register", register_content)
                except GithubException as ex:
                    logger.error(f"Не удалось залить register.html: {ex}")
            else:
                logger.error(f"Ошибка при получении register.html: {e}")
    else:
        logger.warning(f"Файл register.html не найден по пути {register_path}")

    # 4. Заливаем картинки
    images_dir = html_path.parent / "images"
    if images_dir.exists() and images_dir.is_dir():
        for img_file in images_dir.iterdir():
            if not img_file.is_file():
                continue
            img_bytes = img_file.read_bytes()
            img_path_in_repo = f"images/{img_file.name}"
            try:
                existing = repo.get_contents(img_path_in_repo)
                repo.update_file(img_path_in_repo, f"Update {img_file.name}", img_bytes, existing.sha)
            except GithubException as e:
                if e.status == 404:
                    try:
                        repo.create_file(img_path_in_repo, f"Add {img_file.name}", img_bytes)
                    except GithubException as ex:
                        logger.error(f"Не удалось залить {img_file.name}: {ex}")
                else:
                    logger.error(f"Ошибка при получении {img_path_in_repo}: {e}")

    # 5. Включаем GitHub Pages (если ещё не включён)
    try:
        repo.requester.requestJsonAndCheck(
            "POST",
            f"/repos/{USER}/{repo_name}/pages",
            input={"source": {"branch": "main", "path": "/"}}
        )
    except GithubException as e:
        if e.status == 409:
            # Pages уже включён — это нормально
            pass
        else:
            logger.error(f"Ошибка при включении GitHub Pages: {e}")
            raise e

    # 6. Ждём сборки Pages через polling вместо sleep(10)
    if _wait_for_pages_build(repo, timeout=60):
        logger.info(f"Сайт {repo_name} собран и доступен")
    else:
        logger.warning(f"Сайт {repo_name} задеплоен, но сборка не подтверждена за отведённое время")

    return f"https://{USER}.github.io/{repo_name}/"