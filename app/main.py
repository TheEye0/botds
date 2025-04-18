ALLOWED_GUILD_ID = 692143509259419719
ALLOWED_USER_ID = 367664282990804992
CANAL_DESTINO_ID = 1359260714551873698  # Substitua pelo ID do canal desejado

import discord
from discord.ext import commands
import os
from groq import Groq
from dotenv import load_dotenv
from serpapi import GoogleSearch
from flask import Flask
from threading import Thread
from collections import defaultdict, deque
import datetime
import asyncio
from discord.ext import tasks
import json
import re
from github_uploader import upload_to_github, HISTORICO_FILE_PATH

conversas = defaultdict(lambda: deque(maxlen=10))

load_dotenv()

# Configurações do Discord
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

# Chaves das APIs (deixe no .env)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")  # Lembre de adicionar ao .env

# Inicializa o cliente Groq
groq_client = Groq(api_key=GROQ_API_KEY)

# Função de busca na web com SerpApi
def buscar_na_web(consulta):
    try:
        search = GoogleSearch({
            "q": consulta,
            "hl": "pt-br",
            "gl": "br",
            "api_key": SERPAPI_KEY
        })
        resultados = search.get_dict()

        respostas = []
        if "organic_results" in resultados:
            for resultado in resultados["organic_results"][:3]:
                titulo = resultado.get("title", "")
                snippet = resultado.get("snippet", "")
                respostas.append(f"{titulo}: {snippet}")

        return "\n".join(respostas) if respostas else "Nenhum resultado encontrado."
    except Exception as e:
        return f"Erro ao buscar na web: {e}"


@bot.event
async def on_ready():
    print(f"🤖 Bot conectado como {bot.user}")

# Verificação de autorização
def autorizado(ctx):
    # Permite se for no servidor autorizado
    if ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID:
        return True
    # Permite se for uma DM com o usuário autorizado
    if ctx.guild is None and ctx.author.id == ALLOWED_USER_ID:
        return True
    return False

@bot.command()
async def ask(ctx, *, pergunta):
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado.")

    canal_id = ctx.channel.id
    historico = conversas[canal_id]

    # Adiciona a nova pergunta ao histórico
    historico.append({"role": "user", "content": pergunta})

    mensagens = [{"role": "system", "content": "Você é um assistente útil e simpático."}] + list(historico)

    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=mensagens
        )
        resposta = response.choices[0].message.content

        # Salva a resposta no histórico
        historico.append({"role": "assistant", "content": resposta})

        await ctx.send(resposta)

    except Exception as e:
        print(f"Erro: {e}")
        await ctx.send("❌ Ocorreu um erro ao processar sua pergunta.")

@bot.command()
async def search(ctx, *, consulta):
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado.")

    await ctx.send("🔎 Buscando na internet...")

    dados = buscar_na_web(consulta)

    canal_id = ctx.channel.id
    historico = conversas[canal_id]

    prompt = f"""
    Um usuário fez a seguinte pergunta: \"{consulta}\".
    Aqui estão os resultados da pesquisa online:

    {dados}

    Responda de forma clara, útil e direta com base nesses dados:
    """

    historico.append({"role": "user", "content": prompt})

    mensagens = [{"role": "system", "content": "Você é um assistente inteligente e útil."}] + list(historico)

    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=mensagens
        )
        resposta = response.choices[0].message.content

        historico.append({"role": "assistant", "content": resposta})

        await ctx.send(resposta)

    except Exception as e:
        print(f"Erro: {e}")
        await ctx.send("❌ Ocorreu um erro ao processar sua busca com IA.")


@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado.")

    conteudo = await gerar_conteudo_com_ia()
    await ctx.send(conteudo)


# Armazena histórico para evitar repetições
historico_palavras = set()
historico_frases = set()


@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    agora = datetime.datetime.now()
    if agora.hour == 9 and agora.minute == 0:
        canal = bot.get_channel(CANAL_DESTINO_ID)
        if canal:
            conteudo = await gerar_conteudo_com_ia()
            await canal.send(conteudo)
        await asyncio.sleep(60)  # Evita múltiplos envios às 09:00

@enviar_conteudo_diario.before_loop
async def before():
    await bot.wait_until_ready()


async def gerar_conteudo_com_ia():
    # Carrega o histórico salvo
    try:
        with open(HISTORICO_FILE_PATH.split('/')[-1], "r", encoding="utf-8") as f: # Lê o arquivo local correto
            historico = json.load(f)
            # Garante que as listas existam mesmo que o arquivo esteja mal formatado
            if "palavras" not in historico: historico["palavras"] = []
            if "frases" not in historico: historico["frases"] = []
    except (FileNotFoundError, json.JSONDecodeError):
        print("WARN: Arquivo historico.json não encontrado ou inválido. Começando do zero.")
        historico = {"palavras": [], "frases": []}

    # --- MELHORIA: Pegar últimos N itens para evitar no prompt ---
    N_ITENS_RECENTES = 5 # Quantos itens recentes incluir no prompt (ajuste conforme necessário)
    palavras_recentes = historico["palavras"][-N_ITENS_RECENTES:]
    frases_recentes = historico["frases"][-N_ITENS_RECENTES:]

    palavras_evitar_str = ", ".join(palavras_recentes) if palavras_recentes else "Nenhuma"
    # Junta frases com um separador menos comum para evitar confusão
    frases_evitar_str = " ;; ".join(frases_recentes) if frases_recentes else "Nenhuma"
    # -------------------------------------------------------------

    # Aumenta as tentativas
    for _ in range(15): # Aumentado de 10 para 15 tentativas
        # --- MELHORIA: Prompt mais detalhado com restrições ---
        prompt = f"""
Crie duas coisas originais e variadas para um canal de aprendizado:

1. Uma palavra em inglês útil com:
- Significado claro em português.
- Um exemplo de frase em inglês (com tradução para português).

2. Uma frase estoica inspiradora com:
- Autor (se souber, senão "Desconhecido" ou "Tradição Estoica").
- Pequena explicação/reflexão em português (1-2 frases).

**IMPORTANTE:**
- **Seja criativo e evite repetições.**
- **NÃO use as seguintes palavras recentes:** {palavras_evitar_str}
- **NÃO use as seguintes frases estoicas recentes:** {frases_evitar_str}
- Mantenha o formato EXATO abaixo.

Formato:
Palavra: [Palavra em inglês aqui]
Significado: [Significado em português aqui]
Exemplo: [Frase exemplo em inglês aqui]
Tradução: [Tradução da frase exemplo aqui]

Frase estoica: "[Frase estoica aqui]"
Autor: [Autor aqui]
Reflexão: [Reflexão aqui]
"""
        # ---------------------------------------------------------

        try:
            print(f"--- Tentativa {_ + 1}/15 ---") # Log da tentativa
            print(f"DEBUG: Enviando prompt (sem histórico completo):\n{prompt}") # Log do prompt

            # --- MELHORIA: Adicionar parâmetro 'temperature' ---
            response = groq_client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": "Você é um professor de inglês e filosofia estoica, criativo e focado em variedade, escrevendo para um canal no Discord."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8 # Aumenta a aleatoriedade (valores entre 0.7 e 1.0 são bons para criatividade)
            )
            # ----------------------------------------------------

            conteudo = response.choices[0].message.content
            print(f"DEBUG: Conteúdo recebido da API:\n{conteudo}") # Log da resposta

            # Regex aprimorada (gananciosa para frase)
            match_palavra = re.search(r"(?i)^Palavra:\s*\**(.+?)\**\s*$", conteudo, re.MULTILINE)
            match_frase = re.search(r"(?i)^Frase estoica:\s*\"?(.+)\"?\s*$", conteudo, re.MULTILINE)

            if match_palavra and match_frase:
                palavra = match_palavra.group(1).strip()
                frase = match_frase.group(1).strip()

                print(f"DEBUG: Extraído - Palavra='{palavra}', Frase='{frase}'")

                # Verificação de repetição (considera case-insensitive para robustez)
                palavra_lower = palavra.lower()
                frase_lower = frase.lower()
                historico_palavras_lower = [p.lower() for p in historico["palavras"]]
                historico_frases_lower = [f.lower() for f in historico["frases"]]

                if palavra_lower not in historico_palavras_lower and frase_lower not in historico_frases_lower:
                    print("INFO: Conteúdo inédito encontrado!")
                    historico["palavras"].append(palavra) # Salva a versão original
                    historico["frases"].append(frase)   # Salva a versão original

                    # --- Bloco de salvar local e fazer upload (já corrigido) ---
                    local_filename_to_save = HISTORICO_FILE_PATH.split('/')[-1]
                    local_full_path = os.path.abspath(local_filename_to_save)
                    try:
                        with open(local_filename_to_save, "w", encoding="utf-8") as f:
                            print(f"DEBUG: Salvando no arquivo local '{local_filename_to_save}': {historico}")
                            json.dump(historico, f, indent=2, ensure_ascii=False)
                            print(f"✅ Histórico salvo localmente em: {local_full_path}")
                    except Exception as save_err:
                        print(f"❌ Erro ao salvar o arquivo local '{local_filename_to_save}': {save_err}")
                        # Continua para tentar o upload mesmo se salvar falhar localmente? Ou retorna erro?
                        # return "Erro ao salvar histórico local." # Opção

                    try:
                        print(f"Tentando enviar o arquivo '{HISTORICO_FILE_PATH}' para o GitHub...")
                        status, resp_json = upload_to_github()
                        if status == 201 or status == 200:
                            print(f"✅ Histórico atualizado no GitHub (Status: {status}).")
                        else:
                            print(f"⚠️ Erro ao enviar para o GitHub (Status: {status}). Resposta da API:")
                            if isinstance(resp_json, dict): print(json.dumps(resp_json, indent=2))
                            else: print(resp_json)
                    except Exception as upload_err:
                        print(f"❌ Exceção durante a chamada de upload_to_github: {upload_err}")
                    # --- Fim do bloco de upload ---

                    return conteudo # Retorna o conteúdo gerado e salvo com sucesso
                else:
                    print(f"⚠️ Conteúdo repetido detectado (Palavra: '{palavra}', Frase: '{frase}'). Tentando novamente...")

            else:
                 print(f"⚠️ Regex falhou! Palavra Match: {match_palavra}, Frase Match: {match_frase}")
                 print(f"Conteúdo original que causou falha na regex:\n{conteudo}")

        except Exception as e:
            print(f"❌ Erro durante a chamada da API Groq ou processamento: {e}")
            # Decide se quer retornar o erro ou apenas logar e tentar novamente
            # return f"❌ Erro ao gerar conteúdo diário: {e}" # Opção de parar
            pass # Continua o loop para a próxima tentativa

        # Pequena pausa entre tentativas
        await asyncio.sleep(2)

    # Se o loop terminar sem sucesso
    print("⚠️ Não foi possível gerar um conteúdo inédito após várias tentativas.")
    return "⚠️ Não foi possível gerar um conteúdo inédito após várias tentativas."



# ------ Servidor Flask ------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is online!"

def run_server():
    # O Render define a porta na variável de ambiente PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ------ Início da aplicação ------
if __name__ == "__main__":
    # Inicia o servidor Flask em uma thread
    Thread(target=run_server).start()
    enviar_conteudo_diario.start()


# Roda o bot
bot.run(DISCORD_TOKEN)
