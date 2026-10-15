import os
import time
from pathlib import Path
from github import Github
from github import GithubException


g = Github(os.getenv("GITHUB_TOKEN"))
USER = os.getenv("GITHUB_USERNAME")


def deploy_to_github(repo_name: str, html_path: Path) -> str:
    user = g.get_user()

    try:
        repo = user.get_repo(repo_name)
    except Exception:
        repo = user.create_repo(repo_name, auto_init=True)
        time.sleep(3)  # даём GitHub время инициализировать репозиторий

    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        existing = repo.get_contents("index.html")
        repo.update_file("index.html", "Update site", content, existing.sha)
    except Exception:
        repo.create_file("index.html", "Initial site", content)

    # Включаем GitHub Pages через прямой вызов API
    try:
        repo.requester.requestJsonAndCheck(
            "POST",
            f"/repos/{USER}/{repo_name}/pages",
            input={"source": {"branch": "main", "path": "/"}}
        )
    except GithubException as e:
        if e.status == 409:
            pass  # Pages уже включён — это нормально, игнорируем
        else:
            raise  # все остальные ошибки пробрасываем наверх

    time.sleep(10)  # GitHub Pages собирается не мгновенно
    return f"https://{USER}.github.io/{repo_name}/"


