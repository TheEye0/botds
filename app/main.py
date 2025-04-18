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
# <<< CORREÇÃO: Garantir que esta importação esteja ativa >>>
from google.generativeai import types as genai_types
import aiohttp # Para baixar imagens
import io      # Para lidar com bytes de imagem
from PIL import Image # Pillow é necessário para processar a imagem de entrada/saída
import traceback # Para logs de erro detalhados

# <<< ADICIONAR: Verificar versão da biblioteca >>>
try:
    print(f"--- Google Generative AI Version: {genai.__version__} ---")
except Exception as e:
    print(f"--- Não foi possível obter a versão de google-generativeai: {e} ---")


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

# Verificação de autorização (CORRIGIDA)
def autorizado(ctx):
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
        is_allowed = (ctx.guild.id == ALLOWED_GUILD_ID)
        print(f"Guild Check Result: {is_allowed}")
        print(f"--- Fim Autorizado Check ---")
        return is_allowed
    else:
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
        for resultado in organic_results[:3]:
            titulo = resultado.get("title", "Sem título")
            snippet = resultado.get("snippet", "Sem descrição")
            link = resultado.get("link", "")
            respostas.append(f"**{titulo}**: {snippet}" + (f" ([link]({link}))" if link else ""))
        return "\n\n".join(respostas) if respostas else "Nenhum resultado relevante encontrado."
    except Exception as e:
        print(f"❌ Erro ao buscar na web: {e}")
        traceback.print_exc()
        return f"Erro interno ao buscar na web."

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f"--- Bot Online ---")
    print(f"Logado como: {bot.user.name} ({bot.user.id})")
    print(f"Py-cord versão: {discord.__version__}")
    print(f"Servidores conectados: {len(bot.guilds)}")
    print(f"--------------------")
    if CANAL_DESTINO_ID != 0:
        print(f"Iniciando task 'enviar_conteudo_diario' para o canal {CANAL_DESTINO_ID}")
        enviar_conteudo_diario.start()
    else:
        print("WARN: CANAL_DESTINO_ID não definido ou inválido. Task 'enviar_conteudo_diario' não iniciada.")

# --- Bot Commands ---

@bot.command()
async def ask(ctx, *, pergunta):
    if not groq_client:
        return await ctx.send("❌ O serviço de chat não está disponível (sem chave API).")
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado ou DM permitida.")
    canal_id = ctx.channel.id
    print(f"\n--- !ask DEBUG ---")
    print(f"Comando recebido de: {ctx.author} ({ctx.author.id}) em Canal ID: {canal_id}")
    print(f"Histórico ANTES tem {len(conversas[canal_id])} mensagens.")
    print(f"Pergunta recebida: '{pergunta}'")
    historico = conversas[canal_id]
    historico.append({"role": "user", "content": pergunta})
    mensagens = [{"role": "system", "content": "Você é um assistente útil, direto e simpático, respondendo em português brasileiro."}] + list(historico)
    print(f"Enviando {len(mensagens)} mensagens para Groq (modelo: llama3-8b-8192).")
    try:
        async with ctx.typing():
            response = groq_client.chat.completions.create(
                model="llama3-8b-8192", messages=mensagens, temperature=0.7
            )
            resposta = response.choices[0].message.content
        print(f"Resposta recebida da Groq (primeiros 100 chars): '{resposta[:100]}...'")
        historico.append({"role": "assistant", "content": resposta})
        print(f"Histórico DEPOIS tem {len(conversas[canal_id])} mensagens.")
        print(f"--- Fim !ask DEBUG ---\n")
        if len(resposta) > 2000:
            await ctx.send(resposta[:1990] + "\n[...]")
        else:
            await ctx.send(resposta)
    except Exception as e:
        print(f"❌ Erro na chamada Groq para !ask: {e}")
        traceback.print_exc()
        print(f"--- Fim !ask DEBUG (ERRO) ---\n")
        if historico and historico[-1]["role"] == "user":
            historico.pop()
            print("DEBUG: Última pergunta do usuário removida do histórico devido a erro.")
        await ctx.send("❌ Ocorreu um erro ao processar sua pergunta com a IA.")

@bot.command()
async def search(ctx, *, consulta):
    if not groq_client:
        return await ctx.send("❌ O serviço de busca+resumo não está disponível (sem chave API Groq).")
    if not SERPAPI_KEY:
        return await ctx.send("❌ O serviço de busca web não está disponível (sem chave API SerpApi).")
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado ou DM permitida.")
    await ctx.send(f"🔎 Buscando na web sobre: \"{consulta}\"...")
    dados_busca = buscar_na_web(consulta)
    if "Erro:" in dados_busca:
        await ctx.send(dados_busca); return
    if "Nenhum resultado" in dados_busca:
        await ctx.send(dados_busca); return
    await ctx.send("🧠 Analisando resultados com a IA...")
    prompt_contexto = f"""
    Você recebeu a seguinte consulta de um usuário: "{consulta}"
    Aqui estão os principais resultados de uma busca na web sobre isso:
    --- RESULTADOS DA BUSCA ---
    {dados_busca}
    --- FIM DOS RESULTADOS ---
    Com base **apenas** nas informações dos resultados da busca fornecidos acima, responda à consulta original do usuário de forma clara, concisa e objetiva em português brasileiro. Cite os pontos principais encontrados. Não adicione informações externas aos resultados. Se os resultados não responderem diretamente, diga isso.
    """
    mensagens_busca = [
        {"role": "system", "content": "Você é um assistente que resume informações de busca na web de forma precisa e direta, baseado SOMENTE nos dados fornecidos."},
        {"role": "user", "content": prompt_contexto}
    ]
    print(f"DEBUG (!search): Enviando {len(mensagens_busca)} mensagens para Groq (contexto zerado).")
    try:
        async with ctx.typing():
            response = groq_client.chat.completions.create(
                model="llama3-8b-8192", messages=mensagens_busca, temperature=0.3
            )
            resposta = response.choices[0].message.content
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
        conteudo = await gerar_conteudo_com_ia()
    if len(conteudo) > 2000:
         await ctx.send(conteudo[:1990] + "\n[...]")
    else:
         await ctx.send(conteudo)

# --- Comando !img ---
@bot.command()
async def img(ctx, *, prompt: str):
    print(f"\n--- !img START - Ctx ID: {ctx.message.id} ---")
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
                            print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Imagem '{input_filename}' baixada ({len(image_bytes)} bytes).")
                        else:
                            await ctx.send(f"❌ Falha ao baixar anexo (status: {resp.status}).")
                            return
            except Exception as e:
                await ctx.send(f"❌ Erro ao processar anexo: {e}")
                print(f"Erro detalhado (!img): {e}"); traceback.print_exc(); return
        else:
            await ctx.send(f"⚠️ Anexo '{attachment.filename}' não suportado. Ignorando.")

    # Mensagem de feedback
    if input_pil_image: await ctx.send(f"⏳ Editando imagem '{input_filename}'...")
    else: await ctx.send(f"⏳ Gerando imagem nova...")

    # 2. Preparar 'contents' e chamar a API Gemini
    try:
        contents_for_api = [prompt, input_pil_image] if input_pil_image else [prompt]


       gemini_model = genai.GenerativeModel(model_name="gemini-2.0-flash-exp-image-generation")

        print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Chamando Gemini SEM config explícita. Contents: {[type(c).__name__ for c in contents_for_api]}")

        response = None
        async with ctx.typing():
            response = await gemini_model.generate_content_async(contents=contents_for_api)

        print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): RESPOSTA recebida da API Gemini.")
        if response: print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Candidates? { hasattr(response, 'candidates') } | Feedback? { hasattr(response, 'prompt_feedback') }")

        # 3. Processar a Resposta
        response_text_parts = []
        generated_image_bytes = None
        processed_successfully = False
         try:
            if response and response.candidates:
                 # Checa se a resposta foi bloqueada ANTES de tentar acessar partes
                 if hasattr(response.candidates[0], 'finish_reason') and response.candidates[0].finish_reason != 1: # 1 = STOP (normal)
                      reason_num = response.candidates[0].finish_reason
                      safety_ratings_str = str(getattr(response.candidates[0], 'safety_ratings', 'N/A'))
                      error_msg = f"⚠️ Geração interrompida/bloqueada. Razão: {reason_num}. Safety: {safety_ratings_str}"
                      print(f"WARN (!img - Ctx ID: {ctx.message.id}): {error_msg}")
                      response_text_parts.append(error_msg)
                 # Só tenta processar partes se não foi bloqueado e tem conteúdo
                 elif hasattr(response.candidates[0], 'content') and hasattr(response.candidates[0].content, 'parts'):
                    print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Processando {len(response.candidates[0].content.parts)} partes.")
                    for i, part in enumerate(response.candidates[0].content.parts):
                        part_info = f"Part {i}:"
                        if hasattr(part, 'text') and part.text: part_info += " [TEXT]"
                        if hasattr(part, 'inline_data'): part_info += f" [INLINE_DATA - Mime: {getattr(part.inline_data, 'mime_type', 'N/A')}, Data? {hasattr(part.inline_data, 'data')}]"
                        print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): {part_info}")

                        if hasattr(part, 'text') and part.text:
                            response_text_parts.append(part.text)
                            processed_successfully = True # Processou texto
                        elif hasattr(part, 'inline_data') and part.inline_data and generated_image_bytes is None:
                            if hasattr(part.inline_data, 'data'):
                                 generated_image_bytes = part.inline_data.data
                                 print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Imagem inline_data ARMAZENADA ({len(generated_image_bytes)} bytes).")
                                 processed_successfully = True # Processou imagem
                            else: print(f"WARN (!img - Ctx ID: {ctx.message.id}): part.inline_data sem 'data'.")
                 else:
                     print(f"WARN (!img - Ctx ID: {ctx.message.id}): Resposta válida, mas sem 'content' ou 'parts'.")
            else: # Nenhuma candidate ou resposta inválida
                feedback = "N/A"
                if response and hasattr(response, 'prompt_feedback'):
                     # ... (código para obter feedback) ...
                print(f"WARN (!img - Ctx ID: {ctx.message.id}): Nenhuma 'candidate'. Feedback: {feedback}")
                response_text_parts.append(f"⚠️ API não retornou candidato. {feedback}")

        except Exception as proc_err:
             print(f"ERROR (!img - Ctx ID: {ctx.message.id}): Erro ao PROCESSAR resposta da API.")
             print(f"❌ Erro: {proc_err}")
             traceback.print_exc()
             await ctx.send(f"❌ Ocorreu um erro interno ao processar a resposta da API de imagem.")
             print(f"--- !img END (ERRO PROC) - Ctx ID: {ctx.message.id} ---")
             return # Sai da função se o processamento falhar

        # 4. Enviar Resultados para o Discord
        final_response_text = "\n".join(response_text_parts).strip()
        print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Texto final: '{final_response_text[:100]}...' | Imagem? {'Sim' if generated_image_bytes else 'Não'}")
        if generated_image_bytes:
            print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Enviando imagem...")
            img_file = discord.File(io.BytesIO(generated_image_bytes), filename="gemini_image.png")
            if final_response_text:
                if len(final_response_text) > 1900: final_response_text = final_response_text[:1900] + "..."
                await ctx.send(f"{final_response_text}", file=img_file)
            else:
                await ctx.send(f"🖼️ Imagem para '{prompt}':", file=img_file)
            print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Imagem enviada.")
        elif final_response_text:
            print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Enviando APENAS texto...")
            if len(final_response_text) > 2000: final_response_text = final_response_text[:1990] + "\n[...]"
            await ctx.send(f"{final_response_text}")
            print(f"DEBUG (!img - Ctx ID: {ctx.message.id}): Texto enviado.")
        else:
            print(f"ERROR (!img - Ctx ID: {ctx.message.id}): Nenhuma imagem ou texto após processamento.")
            await ctx.send("❌ API não retornou conteúdo válido.")

    except Exception as e:
        print(f"ERROR (!img - Ctx ID: {ctx.message.id}): Entrou no bloco EXCEPT GERAL.")
        print(f"❌ Erro: {e}")
        traceback.print_exc()
        await ctx.send(f"❌ Ocorreu um erro interno ao processar o comando !img.")

    print(f"--- !img END - Ctx ID: {ctx.message.id} ---")


# --- Task de Conteúdo Diário ---

@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    agora = datetime.datetime.now()
    if agora.hour == 9 and agora.minute == 0: # Ajuste para seu fuso horário se necessário
        print(f"INFO: Horário de enviar conteúdo diário ({agora}).")
        if CANAL_DESTINO_ID == 0: print("WARN: Canal de destino não configurado."); return
        canal = bot.get_channel(CANAL_DESTINO_ID)
        if canal:
            print(f"INFO: Gerando conteúdo para o canal {canal.name} ({CANAL_DESTINO_ID})...")
            try:
                conteudo = await gerar_conteudo_com_ia()
                print(f"INFO: Conteúdo gerado. Enviando...")
                if len(conteudo) > 2000: await canal.send(conteudo[:1990] + "\n[...]")
                else: await canal.send(conteudo)
                print(f"INFO: Conteúdo enviado.")
                await asyncio.sleep(61)
            except Exception as e: print(f"❌ Erro ao gerar/enviar conteúdo diário: {e}"); traceback.print_exc()
        else: print(f"ERRO: Canal {CANAL_DESTINO_ID} não encontrado.")

@enviar_conteudo_diario.before_loop
async def before_enviar_conteudo_diario():
    print("INFO: Aguardando bot ficar pronto para loop diário...")
    await bot.wait_until_ready()
    print("INFO: Bot pronto. Iniciando loop diário.")

async def gerar_conteudo_com_ia():
    if not groq_client: return "❌ Serviço de geração indisponível (sem chave Groq)."
    local_filename = HISTORICO_FILE_PATH.split('/')[-1]
    local_full_path = os.path.abspath(local_filename)
    print(f"DEBUG (gerar_conteudo): Lendo histórico de '{local_full_path}'")
    try:
        with open(local_filename, "r", encoding="utf-8") as f:
            historico = json.load(f); assert isinstance(historico, dict)
            historico.setdefault("palavras", []); historico.setdefault("frases", [])
            assert isinstance(historico["palavras"], list); assert isinstance(historico["frases"], list)
            print(f"DEBUG: Histórico lido: {len(historico['palavras'])} palavras, {len(historico['frases'])} frases.")
    except Exception as e:
        print(f"WARN (gerar_conteudo): Histórico não encontrado/inválido ({e}). Começando do zero.")
        historico = {"palavras": [], "frases": []}
    N=5; palavras_recentes=historico["palavras"][-N:]; frases_recentes=historico["frases"][-N:]
    palavras_evitar = ", ".join(f"'{p}'" for p in palavras_recentes) or "Nenhuma"
    frases_evitar = " | ".join(f"'{f}'" for f in frases_recentes) or "Nenhuma"

    for tentativa in range(15):
        print(f"--- Geração Tentativa {tentativa + 1}/15 ---")
        prompt = f"""Crie uma palavra em inglês útil (com significado, exemplo, tradução) E uma frase estoica inspiradora (com autor, reflexão). **REGRAS:** Seja criativo, evite repetições. NÃO use palavras recentes: {palavras_evitar}. NÃO use frases recentes: {frases_evitar}. Siga o formato EXATO:\n\nPalavra: [Palavra]\nSignificado: [Significado]\nExemplo: [Exemplo]\nTradução: [Tradução]\n\nFrase estoica: "[Frase]"\nAutor: [Autor]\nReflexão: [Reflexão]"""
        try:
            response = groq_client.chat.completions.create(model="llama3-8b-8192", messages=[{"role": "system", "content": "Você é um professor de inglês/filosofia estoica focado em originalidade e formato."}, {"role": "user", "content": prompt}], temperature=0.85)
            conteudo = response.choices[0].message.content
            match_palavra = re.search(r"(?im)^Palavra:\s*\**(.+?)\**\s*$", conteudo)
            match_frase = re.search(r"(?im)^Frase estoica:\s*\"?(.+)\"?\s*$", conteudo)
            if match_palavra and match_frase:
                palavra = match_palavra.group(1).strip(); frase = match_frase.group(1).strip()
                print(f"DEBUG: Extraído - P='{palavra}', F='{frase}'")
                palavra_lower=palavra.lower(); frase_lower=frase.lower()
                hist_palavras_lower=[p.lower() for p in historico["palavras"]]; hist_frases_lower=[f.lower() for f in historico["frases"]]
                if palavra_lower not in hist_palavras_lower and frase_lower not in hist_frases_lower:
                    print("INFO: Conteúdo inédito!"); historico["palavras"].append(palavra); historico["frases"].append(frase)
                    try:
                        with open(local_filename,"w",encoding="utf-8") as f: json.dump(historico,f,indent=2,ensure_ascii=False)
                        print(f"✅ Histórico salvo localmente.")
                    except Exception as e: print(f"❌ Erro ao salvar local: {e}")
                    try:
                        print(f"INFO: Enviando '{HISTORICO_FILE_PATH}' para GitHub...")
                        status, resp = await asyncio.to_thread(upload_to_github)
                        print(f"INFO: Upload GitHub status: {status}")
                    except Exception as e: print(f"❌ Exceção no upload: {e}"); traceback.print_exc()
                    return conteudo
                else: print(f"⚠️ Conteúdo repetido detectado. Tentando novamente...")
            else: print(f"⚠️ Regex falhou! P:{match_palavra}, F:{match_frase}")
        except Exception as e: print(f"❌ Erro API Groq/proc. tentativa {tentativa+1}: {e}"); traceback.print_exc()
        await asyncio.sleep(3)
    print("⚠️ Não foi possível gerar conteúdo inédito após 15 tentativas.")
    return "⚠️ Desculpe, não consegui gerar conteúdo novo hoje."

# ------ Servidor Flask (Keep-alive para Render) ------
app = Flask(__name__)
@app.route("/")
def home(): return f"Bot {bot.user.name if bot.user else ''} está online!"
def run_server():
    port = int(os.environ.get("PORT", 10000))
    print(f"INFO: Iniciando servidor Flask na porta {port}")
    try: app.run(host="0.0.0.0", port=port, use_reloader=False)
    except Exception as e: print(f"ERRO CRÍTICO no Flask: {e}"); traceback.print_exc()

# ------ Início da aplicação ------
if __name__ == "__main__":
    if not DISCORD_TOKEN: print("ERRO CRÍTICO: DISCORD_TOKEN não encontrado.")
    else:
        print("INFO: Iniciando thread Flask...")
        server_thread = Thread(target=run_server, daemon=True); server_thread.start()
        print("INFO: Iniciando bot Discord...")
        try: bot.run(DISCORD_TOKEN)
        except discord.LoginFailure: print("ERRO CRÍTICO: Falha login Discord. Verifique DISCORD_TOKEN.")
        except Exception as e: print(f"ERRO CRÍTICO ao rodar bot: {e}"); traceback.print_exc()
