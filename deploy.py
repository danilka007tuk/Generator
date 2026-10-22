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
        time.sleep(3)

    # === 1. Заливаем index.html ===
    content = html_path.read_text(encoding="utf-8")
    try:
        existing = repo.get_contents("index.html")
        repo.update_file("index.html", "Update site", content, existing.sha)
    except Exception:
        repo.create_file("index.html", "Initial site", content)

    # === 2. Заливаем register.html (если есть) ===
    register_path = html_path.parent / "register.html"
    if register_path.exists():
        register_content = register_path.read_text(encoding="utf-8")
        try:
            existing = repo.get_contents("register.html")
            repo.update_file("register.html", "Update register", register_content, existing.sha)
        except Exception:
            try:
                repo.create_file("register.html", "Add register", register_content)
            except Exception as e:
                print(f"Не удалось залить register.html: {e}")

    # === 3. Заливаем картинки ===
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
            except Exception:
                try:
                    repo.create_file(img_path_in_repo, f"Add {img_file.name}", img_bytes)
                except Exception as e:
                    print(f"Не удалось залить {img_file.name}: {e}")

    # === 4. Включаем GitHub Pages ===
    try:
        repo.requester.requestJsonAndCheck(
            "POST",
            f"/repos/{USER}/{repo_name}/pages",
            input={"source": {"branch": "main", "path": "/"}}
        )
    except GithubException as e:
        if e.status == 409:
            pass
        else:
            raise

    time.sleep(10)
    return f"https://{USER}.github.io/{repo_name}/"