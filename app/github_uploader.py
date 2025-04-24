# -*- coding: utf-8 -*-
"""
app/github_uploader.py — leitura e upload de historico.json para GitHub
"""
import base64
import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
# Caminho relativo no repositório
HISTORICO_FILE_PATH = os.getenv("HISTORICO_FILE_PATH", "historico.json")


def upload_to_github(local_file_path: str):
    """
    Lê o arquivo local em local_file_path e envia para o GitHub no caminho HISTORICO_FILE_PATH.
    """
    # Determina caminho absoluto para leitura
    local_file_full_path = os.path.abspath(local_file_path)
    print(f"[Uploader DEBUG] Lendo arquivo local: {local_file_full_path}")

    try:
        with open(local_file_full_path, "rb") as f:
            content = f.read()
        if not content:
            print("[Uploader ERROR] Arquivo está vazio.")
            return 400, {"message": "Arquivo vazio."}
        encoded_content = base64.b64encode(content).decode()
    except Exception as e:
        print(f"[Uploader ERROR] Falha ao ler arquivo: {e}")
        return 500, {"message": str(e)}

    # URL da API GitHub
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Obtém SHA existente, se houver
    sha = None
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            sha = resp.json().get("sha")
        elif resp.status_code != 404:
            print(f"[Uploader WARNING] GET SHA retornou {resp.status_code}")
    except Exception as e:
        print(f"[Uploader WARNING] Erro ao obter SHA: {e}")

    payload = {
        "message": "Atualiza historico.json pelo bot",
        "content": encoded_content,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(url, headers=headers, json=payload)
        print(f"[Uploader INFO] PUT status: {r.status_code}")
        data = r.json()
        print(f"[Uploader INFO] Response: {json.dumps(data, indent=2)}")
        return r.status_code, data
    except Exception as e:
        print(f"[Uploader ERROR] Exceção no PUT: {e}")
        return 500, {"message": str(e)}


if __name__ == '__main__':
    print("Este módulo é para ser usado via import, não executado diretamente.")
