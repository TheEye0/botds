# app/github_uploader.py

import base64
import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")

# --- CORREÇÃO AQUI ---
# Define a variável HISTORICO_FILE_PATH que main.py espera importar.
# Ela deve conter o caminho como ele deve aparecer no repositório GitHub.
HISTORICO_FILE_PATH = os.getenv("HISTORICO_FILE_PATH", "historico.json")
# --- FIM DA CORREÇÃO ---

# Variáveis internas para clareza (opcional, poderia usar HISTORICO_FILE_PATH diretamente)
# Assume que o arquivo local a ser lido/escrito tem o mesmo nome base
# que o arquivo no repo, mas sem o caminho do repo (ex: 'historico.json')
HISTORICO_FILE_PATH_LOCAL_BASENAME = HISTORICO_FILE_PATH.split('/')[-1]
# Caminho completo usado na URL da API do GitHub
HISTORICO_FILE_PATH_REPO_API = HISTORICO_FILE_PATH


def upload_to_github():
    # Usa o nome base para operações locais
    local_file_to_read = HISTORICO_FILE_PATH_LOCAL_BASENAME
    # Usa o caminho completo do repo para a API
    repo_file_path_api = HISTORICO_FILE_PATH_REPO_API

    local_file_full_path = os.path.abspath(local_file_to_read)
    print(f"[Uploader DEBUG] Tentando ler o arquivo local em: {local_file_full_path}")

    content = b""
    encoded_content = ""
    read_error = None

    try:
        # Tenta ler o arquivo local pelo nome base
        with open(local_file_to_read, "rb") as f:
            content = f.read()

        print(f"[Uploader DEBUG] Bytes lidos do arquivo local: {len(content)}")
        if content:
            print(f"[Uploader DEBUG] Amostra do conteúdo lido (decodificado): {content.decode('utf-8', errors='ignore')[:200]}...")
            encoded_content = base64.b64encode(content).decode()
            print(f"[Uploader DEBUG] Amostra do conteúdo Base64: {encoded_content[:100]}...")
        else:
            print("[Uploader DEBUG] Arquivo local lido estava VAZIO!")

    except FileNotFoundError:
        read_error = f"Arquivo local não encontrado em {local_file_full_path}"
        print(f"[Uploader ERROR] {read_error}")
        return 500, {"message": read_error}
    except Exception as e:
        read_error = f"Erro ao ler arquivo local {local_file_full_path}: {e}"
        print(f"[Uploader ERROR] {read_error}")
        return 500, {"message": read_error}

    # Usa o caminho do REPOSITÓRIO para a URL da API
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_file_path_api}"
    print(f"[Uploader DEBUG] URL da API GitHub: {url}")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    sha = None
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            sha = response.json()["sha"]
            print(f"[Uploader DEBUG] SHA existente encontrado: {sha}")
        elif response.status_code == 404:
            print("[Uploader DEBUG] Arquivo não existe no repositório (SHA=None). Será criado.")
        else:
            print(f"[Uploader WARNING] Erro ao obter SHA (Status: {response.status_code}): {response.text}. Tentando criar o arquivo.")
    except Exception as e:
        print(f"[Uploader WARNING] Exceção ao obter SHA: {e}. Tentando criar o arquivo.")

    payload = {
        "message": "Atualiza histórico gerado pelo bot",
        "content": encoded_content, # Usa o conteúdo lido e codificado
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    print(f"[Uploader DEBUG] Payload a ser enviado (sem content): { {k: v for k, v in payload.items() if k != 'content'} }")
    print(f"[Uploader DEBUG] Payload 'content' está vazio? {'Sim' if not encoded_content else 'Não'}")

    try:
        r = requests.put(url, headers=headers, json=payload)
        print(f"[Uploader INFO] Resposta do GitHub PUT - Status: {r.status_code}")
        try:
            # Tenta imprimir como JSON, senão como texto
            response_data = r.json()
            print(f"[Uploader INFO] Resposta JSON do GitHub: {json.dumps(response_data, indent=2)}")
            # Retorna o status e o JSON decodificado
            return r.status_code, response_data
        except json.JSONDecodeError:
            response_text = r.text
            print(f"[Uploader INFO] Resposta não-JSON do GitHub: {response_text}")
            # Retorna status e o texto como mensagem de erro (ou sucesso não-JSON)
            return r.status_code, {"message": response_text}
    except Exception as e:
        print(f"[Uploader ERROR] Exceção ao enviar para o GitHub: {e}")
        return 500, {"message": f"Exceção na requisição PUT: {e}"}


if __name__ == '__main__':
    print("Este script é feito para ser importado.")
