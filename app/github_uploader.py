import base64
import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
# Garanta que este é o caminho RELATIVO ao script ou absoluto correto no Render
HISTORICO_FILE_PATH_LOCAL = os.getenv("HISTORICO_FILE_PATH", "historico.json")
# Caminho no REPOSITÓRIO GITHUB (pode ser o mesmo ou diferente do local)
HISTORICO_FILE_PATH_REPO = os.getenv("HISTORICO_FILE_PATH", "historico.json")


def upload_to_github():
    # --- DEBUG: Imprime o caminho local que será lido ---
    local_file_full_path = os.path.abspath(HISTORICO_FILE_PATH_LOCAL)
    print(f"[Uploader DEBUG] Tentando ler o arquivo local em: {local_file_full_path}")

    content = b"" # Começa como bytes vazios
    encoded_content = ""
    read_error = None

    try:
        # Tenta ler o arquivo local especificado
        with open(HISTORICO_FILE_PATH_LOCAL, "rb") as f:
            content = f.read()
        # --- DEBUG: Imprime o tamanho e uma amostra do conteúdo lido ---
        print(f"[Uploader DEBUG] Bytes lidos do arquivo local: {len(content)}")
        if content:
            print(f"[Uploader DEBUG] Amostra do conteúdo lido (decodificado): {content.decode('utf-8', errors='ignore')[:200]}...") # Mostra os primeiros 200 caracteres
            encoded_content = base64.b64encode(content).decode()
            print(f"[Uploader DEBUG] Amostra do conteúdo Base64: {encoded_content[:100]}...")
        else:
            print("[Uploader DEBUG] Arquivo local lido estava VAZIO!")

    except FileNotFoundError:
        read_error = f"Arquivo local não encontrado em {local_file_full_path}"
        print(f"[Uploader ERROR] {read_error}")
        # Decide o que fazer: retornar erro ou tentar criar vazio? Por enquanto, retorna erro.
        return 500, {"message": read_error} # Retorna um erro claro
    except Exception as e:
        read_error = f"Erro ao ler arquivo local {local_file_full_path}: {e}"
        print(f"[Uploader ERROR] {read_error}")
        # Decide o que fazer: retornar erro ou tentar criar vazio? Por enquanto, retorna erro.
        return 500, {"message": read_error} # Retorna um erro claro

    # --- Fim do Bloco de Leitura ---

    # Usa o caminho do REPOSITÓRIO para a URL da API
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_FILE_PATH_REPO}"
    print(f"[Uploader DEBUG] URL da API GitHub: {url}")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Verifica SHA (como antes)
    sha = None
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            sha = response.json()["sha"]
            print(f"[Uploader DEBUG] SHA existente encontrado: {sha}")
        elif response.status_code == 404:
            print("[Uploader DEBUG] Arquivo não existe no repositório (SHA=None). Será criado.")
        else:
            # Logar erro ao obter SHA, mas continuar sem SHA (tentará criar)
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

    # --- DEBUG: Imprime o payload ANTES de enviar ---
    # Cuidado: Não imprima o 'content' inteiro se for muito grande ou sensível,
    # mas verificar se ele está vazio ou não é útil.
    print(f"[Uploader DEBUG] Payload a ser enviado (sem content): { {k: v for k, v in payload.items() if k != 'content'} }")
    print(f"[Uploader DEBUG] Payload 'content' está vazio? {'Sim' if not encoded_content else 'Não'}")


    # Envia para o GitHub
    try:
         r = requests.put(url, headers=headers, json=payload)
         print(f"[Uploader INFO] Resposta do GitHub PUT - Status: {r.status_code}")
         # Imprime a resposta do GitHub mesmo em caso de sucesso aparente para análise
         try:
             print(f"[Uploader INFO] Resposta JSON do GitHub: {json.dumps(r.json(), indent=2)}")
         except json.JSONDecodeError:
             print(f"[Uploader INFO] Resposta não-JSON do GitHub: {r.text}")
         return r.status_code, r.json() # Retorna como antes, mas logamos acima
    except Exception as e:
         print(f"[Uploader ERROR] Exceção ao enviar para o GitHub: {e}")
         return 500, {"message": f"Exceção na requisição PUT: {e}"}

# Adicione isto se você importar HISTORICO_FILE_PATH em main.py
# para garantir que a variável esteja disponível para importação
if __name__ == '__main__':
    print("Este script é feito para ser importado, mas pode ser executado para testes.")
    # Você poderia adicionar um teste simples aqui se quisesse
    # status, resp = upload_to_github()
    # print(f"Teste de execução direta - Status: {status}, Resposta: {resp}")
