# -*- coding: utf-8 -*-
"""
main_corrigido.py - BotDS Discord Bot com integração Groq, SerpApi e histórico no GitHub (Correções aplicadas)
"""
import os
import json
import datetime
import traceback
import base64
import requests
import discord
from discord.ext import commands, tasks
from collections import defaultdict, deque
from threading import Thread
from flask import Flask
from dotenv import load_dotenv
import asyncio # Importado para sleep e to_thread
import re # Importado para extração mais robusta

# --- Carrega variáveis de ambiente ---
load_dotenv()
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
SERPAPI_KEY     = os.getenv("SERPAPI_KEY")
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN")
GITHUB_REPO     = os.getenv("GITHUB_REPO")
# Define um padrão mais seguro se as variáveis não forem números
try:
    ALLOWED_GUILD_ID  = int(os.getenv("ALLOWED_GUILD_ID", "0"))
except ValueError:
    print("AVISO: ALLOWED_GUILD_ID inválido no .env, usando 0.")
    ALLOWED_GUILD_ID = 0
try:
    ALLOWED_USER_ID   = int(os.getenv("ALLOWED_USER_ID", "0"))
except ValueError:
    print("AVISO: ALLOWED_USER_ID inválido no .env, usando 0.")
    ALLOWED_USER_ID = 0
try:
    CANAL_DESTINO_ID  = int(os.getenv("CANAL_DESTINO_ID", "0"))
except ValueError:
    print("AVISO: CANAL_DESTINO_ID inválido no .env, usando 0.")
    CANAL_DESTINO_ID = 0

LLAMA_MODEL       = os.getenv("LLAMA_MODEL", "llama3-8b-8192") # Modelo padrão Groq atualizado
HISTORICO_PATH    = os.getenv("HISTORICO_FILE_PATH", "historico.json") # Nome do arquivo no repo/local

# --- Inicializa clientes ---
from groq import Groq
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
from serpapi import GoogleSearch

# --- Configuração Discord Bot ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
conversas = defaultdict(lambda: deque(maxlen=10)) # Histórico para !ask (por canal)

# --- Helpers ---
async def send_long_message(ctx_or_channel, text: str, limit: int = 1990):
    """Envia mensagens longas divididas em partes."""
    if not text: # Não envia nada se o texto for vazio
        return
    parts = [text[i:i+limit] for i in range(0, len(text), limit)]
    for part in parts:
        if isinstance(ctx_or_channel, (discord.TextChannel, discord.DMChannel)):
            await ctx_or_channel.send(part)
        elif hasattr(ctx_or_channel, 'send'): # Assume que é um contexto de comando
            await ctx_or_channel.send(part)
        await asyncio.sleep(0.5) # Pequena pausa para evitar rate limit

def autorizado(ctx):
    """Verifica se o comando foi invocado por usuário/guild autorizado."""
    user_ok = ctx.author.id == ALLOWED_USER_ID
    guild_ok = False
    if isinstance(ctx.channel, discord.DMChannel):
        print(f"DEBUG (autorizado): Verificando DM - User {ctx.author.id} OK? {user_ok}")
        return user_ok
    elif ctx.guild:
        guild_ok = ctx.guild.id == ALLOWED_GUILD_ID
        print(f"DEBUG (autorizado): Verificando Guild {ctx.guild.id} OK? {guild_ok} | User {ctx.author.id} OK? {user_ok}")
        # Permite OU o usuário OU a guild
        return user_ok or guild_ok
    else:
        print(f"DEBUG (autorizado): Contexto desconhecido (não é DM nem Guild).")
        return False # Não autorizado em contextos desconhecidos

# --- Histórico GitHub ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_HISTORY_FILE = os.path.join(BASE_DIR, HISTORICO_PATH.split('/')[-1]) # Garante apenas nome do arquivo local

# Tenta importar a função de upload. Se falhar, define uma função dummy.
try:
    from github_uploader import upload_to_github
    print("INFO: Função 'upload_to_github' importada com sucesso.")
    # Verifica se é coroutine (assíncrona)
    is_upload_async = asyncio.iscoroutinefunction(upload_to_github)
    print(f"DEBUG: 'upload_to_github' é assíncrona? {is_upload_async}")
except ImportError:
    print("ERRO CRÍTICO: Módulo 'github_uploader.py' não encontrado. Upload para GitHub desabilitado.")
    async def upload_to_github(*args, **kwargs): # Define função dummy async
        print("ERRO: Upload para GitHub não pode ser executado (módulo não encontrado).")
        return 500, {"error": "Upload module not found"} # Simula falha
    is_upload_async = True # Assume async para a dummy
except Exception as e:
     print(f"ERRO CRÍTICO: Erro ao importar 'github_uploader': {e}")
     traceback.print_exc()
     async def upload_to_github(*args, **kwargs):
        print(f"ERRO: Upload para GitHub não pode ser executado (erro na importação: {e}).")
        return 500, {"error": f"Upload import error: {e}"}
     is_upload_async = True

def carregar_historico():
    """Tenta carregar o histórico do GitHub, senão do arquivo local, senão retorna vazio."""
    historico = {"palavras": [], "frases": []} # Default
    github_content = None

    # 1. Tentar ler do GitHub
    if GITHUB_TOKEN and GITHUB_REPO:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.raw"} # Usar raw para obter conteúdo direto
        print(f"DEBUG (carregar_historico): Tentando buscar de {url}")
        try:
            response = requests.get(url, headers=headers, timeout=15) # Timeout de 15s
            if response.status_code == 200:
                github_content = response.content # Conteúdo em bytes
                print(f"DEBUG: Conteúdo recebido do GitHub ({len(github_content)} bytes).")
                # Salva localmente o que veio do GitHub
                try:
                    with open(LOCAL_HISTORY_FILE, 'wb') as f:
                        f.write(github_content)
                    print(f"DEBUG: Conteúdo do GitHub salvo localmente em '{LOCAL_HISTORY_FILE}'.")
                except Exception as e_write:
                    print(f"ERRO (carregar_historico): Falha ao salvar localmente o conteúdo do GitHub: {e_write}")
                    # Mesmo com erro ao salvar local, tentamos usar o conteúdo do github
            elif response.status_code == 404:
                 print(f"INFO (carregar_historico): Arquivo '{HISTORICO_PATH}' não encontrado no GitHub.")
            else:
                 print(f"ERRO (carregar_historico): Falha ao buscar do GitHub. Status: {response.status_code}, Resposta: {response.text[:200]}")
        except requests.exceptions.RequestException as e_req:
            print(f"ERRO (carregar_historico): Erro de rede ao buscar do GitHub: {e_req}")
        except Exception as e_gh:
            print(f"ERRO (carregar_historico): Erro inesperado ao processar resposta do GitHub: {e_gh}")
            traceback.print_exc()
    else:
        print("AVISO (carregar_historico): GITHUB_TOKEN ou GITHUB_REPO não configurados. Pulando busca no GitHub.")

    # 2. Processar conteúdo (prioriza GitHub, senão tenta local)
    content_to_parse = None
    if github_content:
        print("DEBUG: Usando conteúdo do GitHub para parse.")
        content_to_parse = github_content
    elif os.path.exists(LOCAL_HISTORY_FILE):
        print(f"DEBUG: Tentando ler do arquivo local '{LOCAL_HISTORY_FILE}' como fallback.")
        try:
            with open(LOCAL_HISTORY_FILE, 'rb') as f: # Ler como bytes
                 content_to_parse = f.read()
        except Exception as e_read_local:
            print(f"ERRO (carregar_historico): Falha ao ler arquivo local '{LOCAL_HISTORY_FILE}': {e_read_local}")
    else:
         print(f"INFO (carregar_historico): Arquivo local '{LOCAL_HISTORY_FILE}' também não encontrado.")

    # 3. Parse do JSON (se tivermos conteúdo)
    if content_to_parse:
        try:
            # Decodifica bytes para string (assume UTF-8) e faz o parse
            data = json.loads(content_to_parse.decode('utf-8'))
            # Validação básica
            if isinstance(data, dict) and "palavras" in data and "frases" in data and \
               isinstance(data["palavras"], list) and isinstance(data["frases"], list):
                historico = data
                print(f"DEBUG: Histórico carregado e validado com {len(historico['palavras'])} palavras, {len(historico['frases'])} frases.")
            else:
                print("ERRO (carregar_historico): Estrutura do JSON inválida. Usando histórico vazio.")
        except json.JSONDecodeError as e_json:
            print(f"ERRO (carregar_historico): Falha ao decodificar JSON ({e_json}). Conteúdo (bytes iniciais): {content_to_parse[:100]}")
        except UnicodeDecodeError as e_unicode:
             print(f"ERRO (carregar_historico): Falha ao decodificar conteúdo como UTF-8 ({e_unicode}). Conteúdo (bytes iniciais): {content_to_parse[:100]}")
        except Exception as e_parse:
            print(f"ERRO (carregar_historico): Erro inesperado ao fazer parse do JSON: {e_parse}")
            traceback.print_exc()

    return historico

async def salvar_historico(hist):
    """Salva o histórico localmente e tenta fazer upload para o GitHub."""
    if not isinstance(hist, dict) or "palavras" not in hist or "frases" not in hist:
        print("ERRO (salvar_historico): Tentativa de salvar histórico inválido.")
        return

    # 1. Salvar localmente primeiro
    try:
        with open(LOCAL_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
        print(f"DEBUG (salvar_historico): Histórico salvo localmente em '{LOCAL_HISTORY_FILE}'.")
    except Exception as e_save_local:
        print(f"ERRO CRÍTICO (salvar_historico): Falha ao salvar histórico localmente: {e_save_local}")
        traceback.print_exc()
        # Decide se quer parar aqui ou ainda tentar o upload
        # return # Descomente para parar se o save local falhar

    # 2. Tentar upload para o GitHub (se configurado)
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            print(f"DEBUG (salvar_historico): Tentando upload de '{HISTORICO_PATH}' para o repo '{GITHUB_REPO}'...")
            # Chama a função importada (síncrona ou assíncrona)
            if is_upload_async:
                 # IMPORTANTE: Usar await se a função for assíncrona
                 status_code, response_data = await upload_to_github(LOCAL_HISTORY_FILE, HISTORICO_PATH)
            else:
                 # Chama diretamente se for síncrona (mas idealmente deveria ser async)
                 # Usamos to_thread para não bloquear o loop principal do bot
                 status_code, response_data = await asyncio.to_thread(upload_to_github, LOCAL_HISTORY_FILE, HISTORICO_PATH)

            if 200 <= status_code < 300:
                print(f"✅ Upload para GitHub bem-sucedido (Status: {status_code}).")
            else:
                print(f"ERRO (salvar_historico): Falha no upload para GitHub (Status: {status_code}): {response_data}")
        except NameError:
             print("ERRO CRÍTICO (salvar_historico): Função 'upload_to_github' não está definida (import falhou?).")
        except Exception as e_upload:
            print(f"ERRO (salvar_historico): Exceção durante o upload para GitHub: {e_upload}")
            traceback.print_exc()
    else:
         print("INFO (salvar_historico): Upload para GitHub pulado (não configurado).")

# --- Geração de conteúdo diário ---
async def gerar_conteudo_com_ia() -> str:
    """Gera palavra e frase estoica, garantindo unicidade e salvando."""
    if not groq_client:
        print("ERRO (gerar_conteudo): Cliente Groq não inicializado.")
        return "⚠️ Serviço de geração de conteúdo indisponível (sem chave Groq)."

    hist = carregar_historico()
    # Cria sets para verificação rápida e case-insensitive
    hist_palavras_lower_set = {p.lower() for p in hist.get("palavras", [])}
    hist_frases_lower_set = {f.lower() for f in hist.get("frases", [])}
    print(f"DEBUG: Histórico carregado para verificação: {len(hist_palavras_lower_set)} palavras únicas, {len(hist_frases_lower_set)} frases únicas.")

    max_tentativas = 10 # Número de tentativas para gerar conteúdo único
    for tentativa in range(max_tentativas):
        print(f"--- Geração IA Tentativa {tentativa + 1}/{max_tentativas} ---")

        # Pega os N mais recentes para incluir no prompt de "evitar"
        N = 5
        palavras_recentes = hist.get("palavras", [])[-N:]
        frases_recentes = hist.get("frases", [])[-N:]
        palavras_evitar_str = ", ".join(f"'{p}'" for p in palavras_recentes) if palavras_recentes else "Nenhuma"
        frases_evitar_str = "\n".join(f"- '{f}'" for f in frases_recentes) if frases_recentes else "Nenhuma"

        # Prompt revisado para maior clareza e ênfase na originalidade e formato
        prompt = f"""
        Sua tarefa é gerar DOIS itens distintos e originais:
        1.  Uma PALAVRA em inglês útil (não trivial, evite palavras muito comuns como 'hello', 'good', 'book').
        2.  Uma FRASE de inspiração estoica concisa e impactante.

        **REGRAS OBRIGATÓRIAS:**
        *   **Originalidade TOTAL:** O conteúdo DEVE ser novo. Verifique cuidadosamente para não repetir palavras ou frases já existentes no histórico implícito.
        *   **Evitar Recentes Explicitamente:** NÃO use as seguintes palavras recentes: [{palavras_evitar_str}]. NÃO use NENHUMA das seguintes frases recentes:\n{frases_evitar_str}
        *   **Formato EXATO:** Siga este formato de saída RIGOROSAMENTE, sem NENHUM texto adicional antes, depois ou entre as seções:

        Palavra: [A Palavra em Inglês]
        Significado: [Explicação concisa do significado em português]
        Exemplo: [Frase curta de exemplo em inglês usando a palavra]
        Tradução: [Tradução da frase de exemplo para o português]

        Frase estoica: "[A Frase Estoica Curta]"
        Autor: [Nome do Autor ou "Desconhecido"]
        Reflexão: [Breve reflexão em português sobre a aplicação prática da frase no dia a dia]

        *   **Qualidade:** Escolha palavras relevantes e frases com profundidade.
        """

        try:
            # Chama a API Groq em uma thread separada
            chat_completion = await asyncio.to_thread(
                groq_client.chat.completions.create,
                messages=[
                    {"role":"system", "content":"Você é um especialista em inglês e estoicismo, extremamente cuidadoso com originalidade e formato de resposta."},
                    {"role":"user", "content":prompt}
                ],
                model=LLAMA_MODEL,
                temperature=0.8, # Um pouco mais de criatividade
                max_tokens=400,
            )
            conteudo_gerado = chat_completion.choices[0].message.content.strip()
            print(f"DEBUG: Conteúdo bruto da IA (Tentativa {tentativa+1}):\n{conteudo_gerado}")

            # Extração mais robusta com regex multiline e case-insensitive
            match_palavra = re.search(r"^Palavra:\s*(.+?)\s*$", conteudo_gerado, re.MULTILINE | re.IGNORECASE)
            match_significado = re.search(r"^Significado:\s*(.+?)\s*$", conteudo_gerado, re.MULTILINE | re.IGNORECASE)
            match_exemplo = re.search(r"^Exemplo:\s*(.+?)\s*$", conteudo_gerado, re.MULTILINE | re.IGNORECASE)
            match_traducao = re.search(r"^Tradução:\s*(.+?)\s*$", conteudo_gerado, re.MULTILINE | re.IGNORECASE)
            match_frase = re.search(r"^Frase estoica:\s*\"?(.+?)\"?\s*$", conteudo_gerado, re.MULTILINE | re.IGNORECASE)
            match_autor = re.search(r"^Autor:\s*(.+?)\s*$", conteudo_gerado, re.MULTILINE | re.IGNORECASE)
            match_reflexao = re.search(r"^Reflexão:\s*(.+?)\s*$", conteudo_gerado, re.MULTILINE | re.IGNORECASE)

            if all([match_palavra, match_significado, match_exemplo, match_traducao, match_frase, match_autor, match_reflexao]):
                palavra_nova = match_palavra.group(1).strip()
                frase_nova = match_frase.group(1).strip()
                palavra_nova_lower = palavra_nova.lower()
                frase_nova_lower = frase_nova.lower()

                print(f"DEBUG: Extraído - Palavra='{palavra_nova}', Frase='{frase_nova}'")

                # **Verificação de unicidade contra o histórico completo**
                if palavra_nova_lower not in hist_palavras_lower_set and frase_nova_lower not in hist_frases_lower_set:
                    print("✅ Conteúdo inédito verificado!")
                    # Adiciona ao histórico em memória (com a capitalização original)
                    hist.setdefault("palavras", []).append(palavra_nova)
                    hist.setdefault("frases", []).append(frase_nova)

                    # Salva o histórico atualizado
                    await salvar_historico(hist) # Chama a função async para salvar

                    # Remonta a string final garantindo o formato
                    conteudo_final_formatado = (
                        f"Palavra: {palavra_nova}\n"
                        f"Significado: {match_significado.group(1).strip()}\n"
                        f"Exemplo: {match_exemplo.group(1).strip()}\n"
                        f"Tradução: {match_traducao.group(1).strip()}\n\n"
                        f"Frase estoica: \"{frase_nova}\"\n"
                        f"Autor: {match_autor.group(1).strip()}\n"
                        f"Reflexão: {match_reflexao.group(1).strip()}"
                    )
                    return conteudo_final_formatado # Retorna o conteúdo único
                else:
                    print(f"⚠️ Conteúdo repetido detectado (Palavra: {palavra_nova_lower in hist_palavras_lower_set}, Frase: {frase_nova_lower in hist_frases_lower_set}). Tentando novamente...")
            else:
                print(f"⚠️ Formato inválido na resposta da IA (Tentativa {tentativa+1}). Não foi possível extrair todos os campos. Tentando novamente...")
                # Log para ajudar a depurar o formato da IA
                missing = [name for name, match in zip(["P", "S", "E", "T", "F", "A", "R"], [match_palavra, match_significado, match_exemplo, match_traducao, match_frase, match_autor, match_reflexao]) if not match]
                print(f"DEBUG: Campos faltando: {missing}")

        except NotFoundError:
             print(f"ERRO (gerar_conteudo): Modelo Groq '{LLAMA_MODEL}' não encontrado. Verifique o nome ou a chave API.")
             return "❌ Modelo de IA não encontrado." # Mensagem específica
        except Exception as e_api:
            print(f"❌ Erro na chamada API Groq ou processamento (Tentativa {tentativa + 1}): {e_api}")
            traceback.print_exc()
            await asyncio.sleep(3) # Pausa maior em caso de erro de API

        await asyncio.sleep(1) # Pequena pausa entre tentativas

    # Se o loop terminar sem sucesso
    print(f"ERRO: Falha ao gerar conteúdo inédito após {max_tentativas} tentativas.")
    return "⚠️ Desculpe, não consegui gerar conteúdo novo hoje após várias tentativas. O histórico pode estar cheio ou a IA está repetitiva."

# --- Task de Envio Diário (com correção de disparo único) ---
@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    """Verifica o horário e envia conteúdo uma vez por dia."""
    agora = datetime.datetime.now()
    # CORREÇÃO: Adiciona flag para garantir execução única por dia
    if agora.hour == 9 and agora.minute == 0:
        # Verifica se a flag existe e se a data é a mesma de hoje
        if hasattr(enviar_conteudo_diario, 'ultima_execucao_hoje') and enviar_conteudo_diario.ultima_execucao_hoje == agora.date():
            # print(f"DEBUG (task): Já executou hoje ({agora.date()}). Pulando.") # Log opcional
            return # Já executou hoje, sai

        # Marca que executou hoje
        enviar_conteudo_diario.ultima_execucao_hoje = agora.date()
        print(f"INFO: Disparando tarefa de conteúdo diário para {agora.date()} às {agora.time()}.")

        if CANAL_DESTINO_ID == 0:
             print("AVISO (task): CANAL_DESTINO_ID não configurado. Saindo.")
             return

        canal_destino = bot.get_channel(CANAL_DESTINO_ID)
        if canal_destino:
            print(f"INFO (task): Gerando conteúdo para canal '{canal_destino.name}' ({CANAL_DESTINO_ID})...")
            try:
                # Gera o conteúdo (com timeout)
                conteudo = await asyncio.wait_for(gerar_conteudo_com_ia(), timeout=180.0) # Timeout de 3 min
                print("INFO (task): Conteúdo gerado. Enviando...")
                await send_long_message(canal_destino, conteudo)
                print(f"✅ Conteúdo diário enviado para canal {CANAL_DESTINO_ID}.")
            except asyncio.TimeoutError:
                 print(f"ERRO (task): Timeout ({180}s) ao gerar conteúdo diário.")
                 try: await canal_destino.send("⚠️ Ocorreu um timeout ao tentar gerar o conteúdo diário.")
                 except Exception as send_e: print(f"ERRO: Falha ao enviar msg de timeout: {send_e}")
            except Exception as e:
                print(f"❌ ERRO (task): Falha ao gerar ou enviar conteúdo diário: {e}")
                traceback.print_exc()
                try: await canal_destino.send("❌ Ocorreu um erro interno ao processar o conteúdo diário.")
                except Exception as send_e: print(f"ERRO: Falha ao enviar msg de erro: {send_e}")
        else:
            print(f"❌ ERRO CRÍTICO (task): Canal de destino {CANAL_DESTINO_ID} não encontrado pelo bot!")
            # Considerar parar a task se o canal for permanentemente inválido?
            # enviar_conteudo_diario.stop()

    # Reseta a flag se o dia mudou (garante que rodará no próximo dia)
    elif hasattr(enviar_conteudo_diario, 'ultima_execucao_hoje') and enviar_conteudo_diario.ultima_execucao_hoje != agora.date():
         print(f"INFO (task): Resetando flag de execução para o novo dia {agora.date()}.")
         delattr(enviar_conteudo_diario, 'ultima_execucao_hoje')

@enviar_conteudo_diario.before_loop
async def before_enviar_conteudo_diario():
    """Espera o bot estar pronto antes de iniciar o loop da task."""
    print("INFO (task): Aguardando bot ficar pronto...")
    await bot.wait_until_ready()
    print("INFO (task): Bot pronto. Iniciando loop de verificação diária.")

# --- Evento on_ready ---
@bot.event
async def on_ready():
    """Executado quando o bot conecta e está pronto."""
    print(f"--- Bot Online ---")
    print(f"Logado como: {bot.user.name} (ID: {bot.user.id})")
    print(f"Versão discord.py: {discord.__version__}")
    print(f"Conectado a {len(bot.guilds)} servidor(es).")
    # Lista os servidores para confirmação
    for guild in bot.guilds:
        print(f" - {guild.name} (ID: {guild.id})")
    print(f"Guild Permitida: {ALLOWED_GUILD_ID}")
    print(f"User Permitido: {ALLOWED_USER_ID}")
    print(f"Canal Posts Diários: {CANAL_DESTINO_ID}")
    print(f"Modelo Groq: {LLAMA_MODEL}")
    print(f"Caminho Histórico Local: {LOCAL_HISTORY_FILE}")
    print(f"--------------------")
    if CANAL_DESTINO_ID != 0:
        if not enviar_conteudo_diario.is_running():
             print("INFO: Iniciando task 'enviar_conteudo_diario'...")
             enviar_conteudo_diario.start()
        else:
             print("INFO: Task 'enviar_conteudo_diario' já estava rodando.")
    else:
         print("AVISO: CANAL_DESTINO_ID não configurado. Task diária não iniciará.")

# --- Comandos ---
@bot.command(aliases=['chat', 'perguntar'])
async def ask(ctx, *, pergunta: str):
    """Responde perguntas usando a API Groq com histórico de conversa."""
    if not groq_client:
        await ctx.send("❌ Serviço de chat indisponível (sem chave API Groq).")
        return
    if not autorizado(ctx):
        await ctx.send("❌ Comando não autorizado neste canal/DM.")
        return

    canal_id = ctx.channel.id
    print(f"\n--- Comando !ask ---")
    print(f"De: {ctx.author} | Canal: {canal_id} | Pergunta: '{pergunta[:100]}...'")
    hist = conversas[canal_id] # Pega/Cria o deque para este canal
    hist.append({"role":"user","content":pergunta})

    # Monta a lista de mensagens (incluindo prompt do sistema)
    mensagens = [{"role": "system", "content": "Você é um assistente prestativo, direto e amigável. Responda sempre em português do Brasil."}] + list(hist)
    print(f"Enviando {len(mensagens)} mensagens para Groq (modelo: {LLAMA_MODEL}).")

    try:
        async with ctx.typing():
            chat_completion = await asyncio.to_thread(
                groq_client.chat.completions.create,
                messages=mensagens,
                model=LLAMA_MODEL,
                temperature=0.7,
                max_tokens=1024,
            )
            texto_resposta = chat_completion.choices[0].message.content

        print(f"Resposta Groq recebida (primeiros 100 chars): '{texto_resposta[:100]}...'")
        hist.append({"role":"assistant","content":texto_resposta}) # Salva resposta no histórico
        print(f"Histórico do canal {canal_id} atualizado. Tamanho: {len(hist)}")
        await send_long_message(ctx, texto_resposta) # Envia resposta (dividida se necessário)

    except Exception as e:
        print(f"❌ Erro na chamada Groq ou processamento (!ask): {e}")
        traceback.print_exc()
        # Remove a última pergunta do usuário do histórico em caso de erro
        if hist and hist[-1]["role"] == "user":
            try: hist.pop()
            except IndexError: pass # Ignora se já estiver vazio
        await ctx.send("❌ Ocorreu um erro ao processar sua pergunta com a IA.")
    print(f"--- Fim !ask ---")


@bot.command(aliases=['buscar', 'web'])
async def search(ctx, *, consulta: str):
    """Busca na web usando SerpApi e resume com Groq."""
    if not SERPAPI_KEY:
        await ctx.send("❌ Serviço de busca web indisponível (sem chave API SerpApi).")
        return
    if not groq_client:
         await ctx.send("❌ Serviço de resumo indisponível (sem chave API Groq).")
         return
    if not autorizado(ctx):
        await ctx.send("❌ Comando não autorizado neste canal/DM.")
        return

    print(f"\n--- Comando !search ---")
    print(f"De: {ctx.author} | Consulta: '{consulta}'")
    msg_status = await ctx.send(f"🔎 Buscando na web sobre: \"{consulta}\"...")

    # Executa a busca síncrona em outra thread
    try:
        search_params = {"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY, "num": 5}
        search_results = await asyncio.to_thread(GoogleSearch(search_params).get_dict)
        organic_results = search_results.get("organic_results", [])
        print(f"DEBUG: SerpApi encontrou {len(organic_results)} resultados orgânicos.")
    except Exception as e:
        print(f"❌ Erro durante a busca SerpApi (!search): {e}")
        traceback.print_exc()
        await msg_status.edit(content=f"❌ Erro ao realizar a busca na web.")
        print(f"--- Fim !search (ERRO BUSCA) ---")
        return

    # Monta o snippet com os 3 primeiros resultados que têm descrição
    snippets = []
    count = 0
    for r in organic_results:
        if count >= 3: break
        title = r.get("title", "Sem título")
        snippet_text = r.get("snippet")
        link = r.get("link")
        if snippet_text: # Só adiciona se houver snippet
            snippets.append(f"**{title}**: {snippet_text}" + (f" ([link]({link}))" if link else ""))
            count += 1

    snippet_final = "\n\n".join(snippets) if snippets else "Nenhum resultado relevante com descrição encontrado."
    print(f"DEBUG: Snippet final para IA:\n{snippet_final[:300]}...") # Loga o início

    if not snippets: # Se não encontrou snippets úteis
         await msg_status.edit(content=snippet_final)
         print(f"--- Fim !search (SEM SNIPPETS) ---")
         return

    await msg_status.edit(content=f"🧠 Analisando resultados para: \"{consulta}\"...")
    # Prompt para o resumo
    prompt_resumo = f"""
Consulta Original: "{consulta}"

Resultados da Busca Web:
---
{snippet_final}
---

Baseado **somente** nos resultados acima, responda de forma concisa à consulta original em português do Brasil. Destaque os pontos principais. Se os resultados não responderem diretamente, indique isso.
"""
    try:
        async with ctx.typing():
            chat_completion = await asyncio.to_thread(
                groq_client.chat.completions.create,
                messages=[
                    {"role":"system", "content":"Você resume resultados de busca web objetivamente, usando apenas as informações fornecidas."},
                    {"role":"user", "content":prompt_resumo}
                ],
                model=LLAMA_MODEL,
                temperature=0.3, # Baixa temperatura para resumo
                max_tokens=1000,
            )
            texto_resumo = chat_completion.choices[0].message.content

        await msg_status.delete() # Remove "Analisando..."
        print("DEBUG: Resumo da IA recebido.")
        await send_long_message(ctx, texto_resumo) # Envia resumo

    except Exception as e:
        print(f"❌ Erro na chamada Groq ou processamento (!search resumo): {e}")
        traceback.print_exc()
        await msg_status.edit(content="❌ Ocorreu um erro ao resumir os resultados da busca com a IA.")
    print(f"--- Fim !search ---")


@bot.command(aliases=['testar'])
async def testar_conteudo(ctx):
    """Gera e envia um exemplo do conteúdo diário."""
    if not autorizado(ctx):
        await ctx.send("❌ Comando não autorizado.")
        return
    await ctx.send("⏳ Gerando conteúdo de teste (pode levar um momento)...")
    async with ctx.typing():
        conteudo = await gerar_conteudo_com_ia()
    await send_long_message(ctx, conteudo)

# --- Keep-alive Flask ---
app = Flask(__name__)
@app.route('/')
def home():
    bot_status = "Online" if bot and bot.is_ready() else "Offline/Iniciando"
    return f"Bot Discord '{bot.user.name if bot.user else 'N/A'}' - Status: {bot_status}"

def run_server():
    port = int(os.getenv('PORT', 10000)) # Porta padrão 10000
    print(f"INFO: Iniciando servidor Flask em 0.0.0.0:{port}")
    try:
        # debug=False e use_reloader=False são importantes para produção
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
         print(f"❌ ERRO CRÍTICO AO INICIAR FLASK: {e}")
         traceback.print_exc()

# --- Main Execution ---
if __name__ == '__main__':
    print("INFO: Iniciando script principal do bot...")
    if not DISCORD_TOKEN:
        print("❌ ERRO CRÍTICO: DISCORD_TOKEN não definido no ambiente. Encerrando.")
    elif not GROQ_API_KEY:
         print("❌ ERRO CRÍTICO: GROQ_API_KEY não definido no ambiente. Funções !ask, !search e conteúdo diário não funcionarão. Encerrando.")
         # Você pode optar por continuar sem Groq, mas muitas funções falharão.
         # exit() # Descomente para parar se Groq for essencial.
    else:
        print("INFO: Iniciando thread do servidor Flask...")
        flask_thread = Thread(target=run_server, daemon=True)
        flask_thread.start()
        print("INFO: Iniciando cliente Discord...")
        try:
            bot.run(DISCORD_TOKEN)
        except discord.LoginFailure:
            print("❌ ERRO CRÍTICO: Falha no login do Discord. Verifique o DISCORD_TOKEN.")
        except Exception as e:
            print(f"❌ ERRO CRÍTICO ao executar o bot Discord: {e}")
            traceback.print_exc()
