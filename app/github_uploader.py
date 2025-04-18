import base64
import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
HISTORICO_FILE_PATH = os.getenv("HISTORICO_FILE_PATH", "historico.json")

def upload_to_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_FILE_PATH}"

    with open(HISTORICO_FILE_PATH, "rb") as f:
        content = f.read()
        encoded_content = base64.b64encode(content).decode()

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Verifica se o arquivo já existe (para obter o SHA)
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        sha = response.json()["sha"]
    else:
        sha = None

    payload = {
        "message": "Atualiza histórico gerado pelo bot",
        "content": encoded_content,
        "branch": "main",  # Altere se usar outra branch
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=headers, json=payload)
    return r.status_code, r.json()
