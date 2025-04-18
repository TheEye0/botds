# -*- coding: utf-8 -*- # Adicionado para garantir codificação

# --- Imports ---
import discord
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import json
import re
from collections import defaultdict, deque
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

# API Clients and specific imports
from groq import Groq
from serpapi import GoogleSearch
import google.generativeai as genai
# Removido 'from google.generativeai import types as genai_types' pois usaremos genai.GenerationConfig
import aiohttp # Para baixar imagens
import io      # Para lidar com bytes de imagem
from PIL import Image # Pillow é necessário para processar a imagem de entrada/saída
import traceback # Para logs de erro detalhados

# Módulo local para upload
try:
    from github_uploader import upload_to_github, HISTORICO_FILE_PATH # Garante que HISTORICO_FILE_PATH é importado
except ImportError:
    print("ERRO CRÍTICO: Não foi possível importar 'github_uploader'. Verifique se o arquivo existe e está correto.")
    # Define um valor padrão para evitar erros posteriores, mas o upload falhará
    HISTORICO_FILE_PATH="historico.json"
    async def upload_to_github(): # Função dummy para evitar NameError
        print("ERRO: Função upload_to_github não carregada.")
        return 500, {"message": "Upload function not loaded"}


# --- Load Environment Variables ---
load_dotenv()

# --- Constants and Config ---
# Tenta converter para int, com fallback se não for número válido
try:
    ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", 0))
except ValueError:
    ALLOWED_GUILD_ID = 0
try:
    ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", 0))
except ValueError:
    ALLOWED_USER_ID = 0
try:
    CANAL_DESTINO_ID = int(os.getenv("CANAL_DESTINO_ID", 0))
except ValueError:
    CANAL_DESTINO_ID = 0

print(f"Configuração - Guild ID Permitido: {ALLOWED_GUILD_ID}")
print(f"Configuração - User ID Permitido: {ALLOWED_USER_ID}")
print(f"Configuração - Canal Destino Posts: {CANAL_DESTINO_ID}")


# API Keys from environment
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GOOGLE_AI_API_KEY = os.getenv("GOOGLE_AI_API_KEY") # Para Gemini

# --- API Client Initialization ---
# Groq Client
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    print("✅ Cliente Groq configurado.")
else:
    groq_client = None # Define como None se não houver chave
    print("⚠️ Chave GROQ_API_KEY não encontrada. Comandos Groq desabilitados.")


# Configure Google Generative AI Client
if GOOGLE_AI_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_AI_API_KEY)
        print("✅ Cliente Google Generative AI configurado.")
    except Exception as e:
        print(f"❌ Erro ao configurar Google Generative AI: {e}")
else:
    print("⚠️ Chave GOOGLE_AI_API_KEY não encontrada. Comando !img desabilitado.")


# --- Bot Setup ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True # Necessário para verificar ctx.guild
intents.dm_messages = True # Necessário para DM

bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

# --- Data Structures ---
conversas = defaultdict(lambda: deque(maxlen=10)) # Histórico por canal para !ask
# historico_palavras e historico_frases são carregados do JSON na função gerar_conteudo_com_ia

# --- Helper Functions ---

# Verificação de autorização
def autorizado(ctx):
    # --- DEBUG LOG ---
    print(f"--- Autorizado Check ---")
    print(f"User ID: {ctx.author.id} vs Allowed: {ALLOWED_USER_ID}")

    if isinstance(ctx.channel, discord.DMChannel):
        print(f"Context: DM Channel")
        is_allowed = (ctx.author.id == ALLOWED_USER_ID)
        print(f"DM Check Result: {is_allowed}")
        print(f"--- Fim Autorizado Check ---")
        return is_allowed
    elif ctx.guild:
        print(f"Context: Guild Channel")
        print(f"Guild ID: {ctx.guild.id} vs Allowed: {ALLOWED_GUILD_ID}")
        # <<< CORREÇÃO APLICADA AQUI >>>
        is_allowed = (ctx.guild.id == ALLOWED_GUILD_ID)
        print(f"Guild Check Result: {is_allowed}")
        print(f"--- Fim Autorizado Check ---")
        return is_allowed # Retorna o resultado da comparação
    else:
        # Situação inesperada
        print(f"Context: Unknown (Not DM, Not Guild) - Channel Type: {type(ctx.channel)}")
        print(f"--- Fim Autorizado Check ---")
        return False

# Função de busca na web com SerpApi
def buscar_na_web(consulta):
    if not SERPAPI_KEY:
        print("WARN: SERPAPI_KEY não configurada.")
        return "Erro: A busca na web não está configurada."
    try:
        print(f"DEBUG (buscar_na_web): Buscando por '{consulta}'")
        search = GoogleSearch({
            "q": consulta,
            "hl": "pt-br",
            "gl": "br",
            "api_key": SERPAPI_KEY
        })
        resultados = search.get_dict()

        respostas = []
        organic_results = resultados.get("organic_results", [])
        print(f"DEBUG (buscar_na_web): {len(organic_results)} resultados orgânicos encontrados.")

        for resultado in organic_results[:3]: # Pega top 3
            titulo = resultado.get("title", "Sem título")
            snippet = resultado.get("snippet", "Sem descrição")
            link = resultado.get("link", "")
            respostas.append(f"**{titulo}**: {snippet}" + (f" ([link]({link}))" if link else ""))

        return "\n\n".join(respostas) if respostas else "Nenhum resultado relevante encontrado."
    except Exception as e:
        print(f"❌ Erro ao buscar na web: {e}")
        traceback.print_exc() # Log completo do erro
        return f"Erro interno ao buscar na web."


# --- Bot Events ---
@bot.event
async def on_ready():
    print(f"--- Bot Online ---")
    print(f"Logado como: {bot.user.name} ({bot.user.id})")
    print(f"Py-cord versão: {discord.__version__}")
    print(f"Servidores conectados: {len(bot.guilds)}")
    print(f"--------------------")
    # Inicia a task de conteúdo diário SE o canal estiver configurado
    if CANAL_DESTINO_ID != 0:
        print(f"Iniciando task 'enviar_conteudo_diario' para o canal {CANAL_DESTINO_ID}")
        enviar_conteudo_diario.start()
    else:
        print("WARN: CANAL_DESTINO_ID não definido ou inválido. Task 'enviar_conteudo_diario' não iniciada.")


# --- Bot Commands ---

@bot.command()
async def ask(ctx, *, pergunta):
    # <<< CORREÇÃO 1: Logs Detalhados no !ask >>>
    if not groq_client: # Verifica se o cliente foi inicializado
        return await ctx.send("❌ O serviço de chat não está disponível (sem chave API).")
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado ou DM permitida.")

    canal_id = ctx.channel.id
    # --- LOG 1: Estado do histórico ANTES da pergunta ---
    print(f"\n--- !ask DEBUG ---")
    print(f"Comando recebido de: {ctx.author} ({ctx.author.id}) em Canal ID: {canal_id}")
    # Cuidado ao logar histórico completo se for muito grande ou sensível
    # print(f"Histórico ANTES ({len(conversas[canal_id])} msgs): {list(conversas[canal_id])}")
    print(f"Histórico ANTES tem {len(conversas[canal_id])} mensagens.")
    print(f"Pergunta recebida: '{pergunta}'")

    historico = conversas[canal_id]

    # Adiciona a nova pergunta ao histórico
    historico.append({"role": "user", "content": pergunta})

    # --- LOG 2: Mensagens enviadas para a API ---
    mensagens = [{"role": "system", "content": "Você é um assistente útil, direto e simpático, respondendo em português brasileiro."}] + list(historico)
    # print(f"Mensagens a serem enviadas para Groq ({len(mensagens)} total): {mensagens}") # Pode ser muito verboso
    print(f"Enviando {len(mensagens)} mensagens para Groq (modelo: llama3-8b-8192).")

    try:
        async with ctx.typing(): # Mostra "Bot is typing..."
            response = groq_client.chat.completions.create(
                model="llama3-8b-8192", # Modelo sugerido como alternativa
                messages=mensagens,
                temperature=0.7 # Um pouco de criatividade
            )
            resposta = response.choices[0].message.content

        # --- LOG 3: Resposta recebida da API ---
        print(f"Resposta recebida da Groq (primeiros 100 chars): '{resposta[:100]}...'")

        # Salva a resposta no histórico
        historico.append({"role": "assistant", "content": resposta})

        # --- LOG 4: Estado do histórico DEPOIS da resposta ---
        # print(f"Histórico DEPOIS ({len(conversas[canal_id])} msgs): {list(conversas[canal_id])}")
        print(f"Histórico DEPOIS tem {len(conversas[canal_id])} mensagens.")
        print(f"--- Fim !ask DEBUG ---\n")

        # Envia a resposta (evita duplicar se a resposta for muito longa)
        if len(resposta) > 2000:
            await ctx.send(resposta[:1990] + "\n[...]") # Trunca um pouco antes
        else:
            await ctx.send(resposta)

    except Exception as e:
        # --- LOG 5: Erro na API ---
        print(f"❌ Erro na chamada Groq para !ask: {e}")
        traceback.print_exc() # Imprime o traceback completo no log
        print(f"--- Fim !ask DEBUG (ERRO) ---\n")
        # Tenta remover a última pergunta do histórico se falhou
        if historico and historico[-1]["role"] == "user":
            historico.pop()
            print("DEBUG: Última pergunta do usuário removida do histórico devido a erro.")
        await ctx.send("❌ Ocorreu um erro ao processar sua pergunta com a IA.")
    # <<< FIM DA CORREÇÃO 1 >>>

@bot.command()
async def search(ctx, *, consulta):
    if not groq_client: # Também depende do Groq para resumir
        return await ctx.send("❌ O serviço de busca+resumo não está disponível (sem chave API Groq).")
    if not SERPAPI_KEY:
        return await ctx.send("❌ O serviço de busca web não está disponível (sem chave API SerpApi).")
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado ou DM permitida.")

    await ctx.send(f"🔎 Buscando na web sobre: \"{consulta}\"...")
    dados_busca = buscar_na_web(consulta)

    if "Erro:" in dados_busca:
        await ctx.send(dados_busca) # Informa erro da busca
        return

    if "Nenhum resultado" in dados_busca:
        await ctx.send(dados_busca) # Informa que não achou
        return

    await ctx.send("🧠 Analisando resultados com a IA...")

    canal_id = ctx.channel.id
    historico = conversas[canal_id]

    prompt_contexto = f"""
    Você recebeu a seguinte consulta de um usuário: "{consulta}"

    Aqui estão os principais resultados de uma busca na web sobre isso:
    --- RESULTADOS DA BUSCA ---
    {dados_busca}
    --- FIM DOS RESULTADOS ---

    Com base **apenas** nas informações dos resultados da busca fornecidos acima, responda à consulta original do usuário de forma clara, concisa e objetiva em português brasileiro. Cite os pontos principais encontrados. Não adicione informações externas aos resultados. Se os resultados não responderem diretamente, diga isso.
    """

    # Zera o histórico para focar SÓ na busca atual
    mensagens_busca = [
        {"role": "system", "content": "Você é um assistente que resume informações de busca na web de forma precisa e direta, baseado SOMENTE nos dados fornecidos."},
        {"role": "user", "content": prompt_contexto}
    ]
    print(f"DEBUG (!search): Enviando {len(mensagens_busca)} mensagens para Groq (contexto zerado).")

    try:
        async with ctx.typing():
            response = groq_client.chat.completions.create(
                model="llama3-8b-8192", # Modelo para sumarização
                messages=mensagens_busca, # Usa as mensagens zeradas
                temperature=0.3 # Mais direto para sumarização
            )
            resposta = response.choices[0].message.content

        # NÃO adiciona busca ao histórico principal de conversas
        print("DEBUG (!search): Resposta da IA recebida.")

        if len(resposta) > 2000:
            await ctx.send(resposta[:1990] + "\n[...]")
        else:
            await ctx.send(resposta)

    except Exception as e:
        print(f"❌ Erro na chamada Groq para !search: {e}")
        traceback.print_exc()
        await ctx.send("❌ Ocorreu um erro ao analisar os resultados da busca com a IA.")


@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Comando não autorizado.")
    await ctx.send("⏳ Gerando conteúdo de teste...")
    async with ctx.typing():
        conteudo = await gerar_conteudo_com_ia() # Pode demorar um pouco
    if len(conteudo) > 2000:
         await ctx.send(conteudo[:1990] + "\n[...]")
    else:
         await ctx.send(conteudo)


# --- Comando !img ---
@bot.command()
async def img(ctx, *, prompt: str):
    if not GOOGLE_AI_API_KEY:
        return await ctx.send("❌ A API de imagem não está configurada (sem chave).")
    if not autorizado(ctx):
        return await ctx.send("❌ Comando não autorizado.")

    input_pil_image = None
    input_filename = "input_image"

    # 1. Verificar e processar anexo de imagem
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.content_type and attachment.content_type in ['image/png', 'image/jpeg', 'image/jpg', 'image/webp']:
            await ctx.send(f"⏳ Processando imagem anexada '{attachment.filename}'...")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            image_bytes = await resp.read()
                            input_pil_image = Image.open(io.BytesIO(image_bytes))
                            input_filename = attachment.filename
                            print(f"DEBUG (!img): Imagem '{input_filename}' baixada e carregada ({len(image_bytes)} bytes).")
                        else:
                            await ctx.send(f"❌ Falha ao baixar a imagem anexada (status: {resp.status}).")
                            return
            except Exception as e:
                await ctx.send(f"❌ Erro ao baixar ou processar anexo: {e}")
                print(f"Erro detalhado ao processar anexo (!img): {e}")
                traceback.print_exc()
                return
        else:
            await ctx.send(f"⚠️ O anexo '{attachment.filename}' não é um tipo de imagem suportado (png, jpg, webp). Ignorando anexo.")

    # Mensagem de feedback
    if input_pil_image:
        await ctx.send(f"⏳ Editando imagem '{input_filename}' com o prompt: '{prompt}'...")
    else:
        await ctx.send(f"⏳ Gerando imagem nova com o prompt: '{prompt}'...")

    # 2. Preparar 'contents' e chamar a API Gemini
    try:
        contents_for_api = [prompt, input_pil_image] if input_pil_image else [prompt]

        # <<< CORREÇÃO 2: Usar genai.GenerationConfig >>>
        try:
             # Acessa diretamente do módulo principal genai
             generation_config = genai.GenerationConfig(
                 response_modalities=['TEXT', 'IMAGE']
             )
             print("DEBUG (!img): Usando genai.GenerationConfig")
        except AttributeError as e_config:
             # Fallback muito improvável, mas loga o erro se acontecer
             print(f"ERRO CRÍTICO (!img): Falha ao encontrar genai.GenerationConfig: {e_config}")
             await ctx.send("❌ Erro interno na configuração da API de imagem.")
             return # Aborta se não conseguir configurar
        # <<< FIM DA CORREÇÃO 2 >>>

        gemini_model = genai.GenerativeModel(
            model_name="gemini-2.0-flash-exp-image-generation"
        )

        print(f"DEBUG (!img): Chamando Gemini com contents: {[type(c).__name__ for c in contents_for_api]}") # Mostra nomes dos tipos

        async with ctx.typing():
            response = await gemini_model.generate_content_async(
                contents=contents_for_api
            )
        print("DEBUG (!img): Resposta recebida da API Gemini.")

        # 3. Processar a Resposta
        response_text_parts = []
        generated_image_bytes = None

        if response.candidates:
             if hasattr(response.candidates[0], 'content') and hasattr(response.candidates[0].content, 'parts'):
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_text_parts.append(part.text)
                    elif hasattr(part, 'inline_data') and part.inline_data and generated_image_bytes is None:
                        if hasattr(part.inline_data, 'data'):
                             generated_image_bytes = part.inline_data.data
                             print(f"DEBUG (!img): Imagem inline_data encontrada ({len(generated_image_bytes)} bytes). MimeType: {part.inline_data.mime_type}")
                        else:
                             print("WARN (!img): part.inline_data sem atributo 'data'.")
             else:
                 # Tenta obter informações de erro do candidato
                 candidate_info = response.candidates[0]
                 finish_reason = getattr(candidate_info, 'finish_reason', 'N/A')
                 safety_ratings = getattr(candidate_info, 'safety_ratings', 'N/A')
                 print(f"WARN (!img): Estrutura inesperada (sem content/parts). FinishReason: {finish_reason}, Safety: {safety_ratings}")
                 # Tenta pegar texto alternativo
                 if hasattr(candidate_info, 'text'):
                     response_text_parts.append(candidate_info.text)
                 else:
                      response_text_parts.append(f"⚠️ Resposta da API incompleta ou bloqueada. Razão: {finish_reason}")

        else:
            # Tenta obter feedback do prompt se não houver candidatos
            feedback = "N/A"
            if hasattr(response, 'prompt_feedback'):
                 block_reason = getattr(response.prompt_feedback, 'block_reason', None)
                 if block_reason:
                     feedback = f"Prompt bloqueado. Razão: {block_reason}"
                 else:
                     feedback = str(response.prompt_feedback)

            print(f"WARN (!img): Nenhuma 'candidate' na resposta. Prompt Feedback: {feedback}")
            response_text_parts.append(f"⚠️ A API não retornou um candidato válido. {feedback}")


        # 4. Enviar Resultados para o Discord
        final_response_text = "\n".join(response_text_parts).strip()

        if generated_image_bytes:
            print("DEBUG (!img): Enviando imagem gerada para o Discord.")
            img_file = discord.File(io.BytesIO(generated_image_bytes), filename="gemini_image.png")
            if final_response_text:
                if len(final_response_text) > 1900: # Limite um pouco menor para caber com a imagem
                    final_response_text = final_response_text[:1900] + "..."
                await ctx.send(f"{final_response_text}", file=img_file)
            else:
                await ctx.send(f"🖼️ Imagem para '{prompt}':", file=img_file)
        elif final_response_text:
            print("DEBUG (!img): Nenhuma imagem gerada/encontrada, enviando apenas texto.")
            if len(final_response_text) > 2000:
                final_response_text = final_response_text[:1990] + "\n[...]"
            await ctx.send(f"{final_response_text}")
        else:
            print("ERROR (!img): Nenhuma imagem ou texto na resposta final.")
            await ctx.send("❌ A API não retornou texto ou imagem válidos após o processamento.")

    except Exception as e:
        print(f"❌ Erro durante a chamada/processamento da API Gemini (!img): {e}")
        traceback.print_exc()
        await ctx.send(f"❌ Ocorreu um erro interno ao processar o comando !img.")


# --- Task de Conteúdo Diário ---

@tasks.loop(minutes=1) # Verifica a cada minuto
async def enviar_conteudo_diario():
    agora = datetime.datetime.now()
    # Verifica se são 09:00 (ajuste o fuso horário se necessário no Render)
    if agora.hour == 9 and agora.minute == 0:
        print(f"INFO: Horário de enviar conteúdo diário ({agora}).")
        if CANAL_DESTINO_ID == 0:
            print("WARN: Canal de destino não configurado para conteúdo diário.")
            return # Não faz nada se o canal não estiver definido

        canal = bot.get_channel(CANAL_DESTINO_ID)
        if canal:
            print(f"INFO: Gerando conteúdo para o canal {canal.name} ({CANAL_DESTINO_ID})...")
            try:
                conteudo = await gerar_conteudo_com_ia() # Pode demorar
                print(f"INFO: Conteúdo gerado. Enviando para o canal...")
                if len(conteudo) > 2000:
                    await canal.send(conteudo[:1990] + "\n[...]")
                else:
                    await canal.send(conteudo)
                print(f"INFO: Conteúdo enviado com sucesso.")
                # Dorme por 61 segundos para garantir que não envie duas vezes no mesmo minuto
                await asyncio.sleep(61)
            except Exception as e:
                print(f"❌ Erro ao gerar ou enviar conteúdo diário: {e}")
                traceback.print_exc()
        else:
            print(f"ERRO: Não foi possível encontrar o canal com ID {CANAL_DESTINO_ID}.")

@enviar_conteudo_diario.before_loop
async def before_enviar_conteudo_diario():
    print("INFO: Aguardando o bot ficar pronto antes de iniciar o loop de conteúdo diário...")
    await bot.wait_until_ready()
    print("INFO: Bot pronto. Iniciando loop de conteúdo diário.")


async def gerar_conteudo_com_ia():
    if not groq_client: # Verifica se Groq está disponível
        return "❌ Serviço de geração de conteúdo indisponível (sem chave API Groq)."

    # Determina o nome base do arquivo local
    local_filename = HISTORICO_FILE_PATH.split('/')[-1]
    local_full_path = os.path.abspath(local_filename)
    print(f"DEBUG (gerar_conteudo): Tentando ler histórico local de '{local_full_path}'")

    # Carrega o histórico salvo
    try:
        with open(local_filename, "r", encoding="utf-8") as f:
            historico = json.load(f)
            if not isinstance(historico, dict): raise ValueError("Arquivo não é um dicionário JSON")
            if "palavras" not in historico: historico["palavras"] = []
            if "frases" not in historico: historico["frases"] = []
            if not isinstance(historico["palavras"], list): historico["palavras"] = []
            if not isinstance(historico["frases"], list): historico["frases"] = []
            print(f"DEBUG (gerar_conteudo): Histórico lido com {len(historico['palavras'])} palavras e {len(historico['frases'])} frases.")
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"WARN (gerar_conteudo): Arquivo historico.json não encontrado ou inválido ({e}). Começando do zero.")
        historico = {"palavras": [], "frases": []}

    # Pega últimos N itens para evitar no prompt
    N_ITENS_RECENTES = 5
    palavras_recentes = historico["palavras"][-N_ITENS_RECENTES:]
    frases_recentes = historico["frases"][-N_ITENS_RECENTES:]

    palavras_evitar_str = ", ".join(f"'{p}'" for p in palavras_recentes) if palavras_recentes else "Nenhuma"
    frases_evitar_str = " | ".join(f"'{f}'" for f in frases_recentes) if frases_recentes else "Nenhuma"


    for tentativa in range(15): # Tenta até 15 vezes
        print(f"--- Geração Tentativa {tentativa + 1}/15 ---")
        prompt = f"""
Crie duas coisas originais e variadas para um canal de aprendizado:

1. Uma palavra em inglês útil com:
- Significado claro em português.
- Um exemplo de frase em inglês (com tradução para português).

2. Uma frase estoica inspiradora com:
- Autor (se souber, senão "Desconhecido" ou "Tradição Estoica").
- Pequena explicação/reflexão em português (1-2 frases concisas).

**REGRAS IMPORTANTES:**
- **Seja criativo e evite repetições.** O objetivo é apresentar conteúdo NOVO.
- **NÃO use as seguintes palavras recentes:** {palavras_evitar_str}
- **NÃO use as seguintes frases estoicas recentes:** {frases_evitar_str}
- Siga o formato EXATO abaixo, incluindo as quebras de linha.

Formato:
Palavra: [Palavra em inglês aqui]
Significado: [Significado em português aqui]
Exemplo: [Frase exemplo em inglês aqui]
Tradução: [Tradução da frase exemplo aqui]

Frase estoica: "[Frase estoica aqui]"
Autor: [Autor aqui]
Reflexão: [Reflexão aqui]
"""
        # print(f"DEBUG (gerar_conteudo): Enviando prompt:\n{prompt}") # Muito verboso

        try:
            response = groq_client.chat.completions.create(
                model="llama3-8b-8192", # Usar modelo mais recente e capaz
                messages=[
                    {"role": "system", "content": "Você é um professor de inglês e filosofia estoica, criativo e focado em gerar conteúdo variado e original para um canal no Discord, seguindo estritamente o formato pedido."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.85 # Aumenta mais a aleatoriedade
            )

            conteudo = response.choices[0].message.content
            # print(f"DEBUG (gerar_conteudo): Conteúdo recebido:\n{conteudo}") # Verboso

            # Regex aprimorada
            match_palavra = re.search(r"(?im)^Palavra:\s*\**(.+?)\**\s*$", conteudo) # (?im) para case-insensitive e multiline
            match_frase = re.search(r"(?im)^Frase estoica:\s*\"?(.+)\"?\s*$", conteudo)

            if match_palavra and match_frase:
                palavra = match_palavra.group(1).strip()
                frase = match_frase.group(1).strip()

                print(f"DEBUG (gerar_conteudo): Extraído - Palavra='{palavra}', Frase='{frase}'")

                # Verificação de repetição (case-insensitive)
                palavra_lower = palavra.lower()
                frase_lower = frase.lower()
                historico_palavras_lower = [p.lower() for p in historico["palavras"]]
                historico_frases_lower = [f.lower() for f in historico["frases"]]

                if palavra_lower not in historico_palavras_lower and frase_lower not in historico_frases_lower:
                    print("INFO (gerar_conteudo): Conteúdo inédito encontrado!")
                    historico["palavras"].append(palavra)
                    historico["frases"].append(frase)

                    # --- Bloco de salvar local e fazer upload ---
                    try:
                        with open(local_filename, "w", encoding="utf-8") as f:
                            print(f"DEBUG (gerar_conteudo): Salvando histórico atualizado em '{local_full_path}'")
                            json.dump(historico, f, indent=2, ensure_ascii=False)
                            print(f"✅ Histórico salvo localmente com sucesso.")
                    except Exception as save_err:
                        print(f"❌ Erro ao salvar o arquivo local '{local_filename}': {save_err}")
                        # Não retorna aqui, pois o conteúdo foi gerado, apenas não salvo

                    # Tenta fazer upload mesmo se o save local falhar (o uploader lê o arquivo)
                    try:
                        print(f"INFO (gerar_conteudo): Tentando enviar '{HISTORICO_FILE_PATH}' para o GitHub...")
                        # A função upload_to_github já tem seus próprios logs detalhados
                        status, resp_json = await asyncio.to_thread(upload_to_github) # Executa em outra thread para não bloquear
                        # Log está dentro da função uploader, não precisa repetir aqui
                        if status not in [200, 201]:
                             print(f"WARN (gerar_conteudo): Upload para GitHub falhou ou retornou status {status}.")
                        else:
                             print(f"INFO (gerar_conteudo): Upload para GitHub parece ter funcionado (status {status}).")

                    except Exception as upload_err:
                        print(f"❌ Exceção durante a chamada de upload_to_github: {upload_err}")
                        traceback.print_exc()
                    # --- Fim do bloco de upload ---

                    return conteudo # Retorna o conteúdo gerado e salvo/tentado upload

                else:
                    print(f"⚠️ Conteúdo repetido detectado (Palavra: '{palavra}', Frase: '{frase}'). Tentando novamente...")

            else:
                 print(f"⚠️ Regex falhou! Palavra Match: {match_palavra}, Frase Match: {match_frase}")
                 # print(f"Conteúdo original que causou falha na regex:\n{conteudo}") # Verboso

        except Exception as e:
            print(f"❌ Erro durante a chamada da API Groq ou processamento na tentativa {tentativa+1}: {e}")
            traceback.print_exc()
            # Continua o loop para a próxima tentativa

        # Pequena pausa entre tentativas para não sobrecarregar
        await asyncio.sleep(3)

    # Se o loop terminar sem sucesso
    print("⚠️ Não foi possível gerar um conteúdo inédito após 15 tentativas.")
    return "⚠️ Desculpe, não consegui gerar um conteúdo novo hoje após várias tentativas."


# ------ Servidor Flask (Keep-alive para Render) ------
app = Flask(__name__)

@app.route("/")
def home():
    # Retorna algo mais informativo
    return f"Bot {bot.user.name if bot.user else ''} está online!"

def run_server():
    # O Render define a porta na variável de ambiente PORT
    port = int(os.environ.get("PORT", 10000)) # Render free tier usa 10000 às vezes
    print(f"INFO: Iniciando servidor Flask na porta {port}")
    # CORREÇÃO: Removido 'log_output=False' e 'static_files={}' (não necessário aqui)
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# ------ Início da aplicação ------
if __name__ == "__main__":
    # Verifica se as chaves essenciais estão presentes
    if not DISCORD_TOKEN:
        print("ERRO CRÍTICO: DISCORD_TOKEN não encontrado no ambiente. O bot não pode iniciar.")
    else:
        # Inicia o servidor Flask em uma thread separada
        print("INFO: Iniciando thread do servidor Flask...")
        server_thread = Thread(target=run_server, daemon=True) # Daemon=True permite fechar com o bot
        server_thread.start()

        # Inicia o bot Discord
        print("INFO: Iniciando o bot Discord...")
        try:
            bot.run(DISCORD_TOKEN)
        except discord.LoginFailure:
            print("ERRO CRÍTICO: Falha no login do Discord. Verifique o DISCORD_TOKEN.")
        except Exception as e:
            print(f"ERRO CRÍTICO: Erro inesperado ao rodar o bot: {e}")
            traceback.print_exc()
