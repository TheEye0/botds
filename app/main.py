# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Versão funcional recuperada: integra Groq + SerpApi, persiste histórico via GitHub API,
comandos ask, search, testar_conteudo, e keep-alive HTTP sem lógicas de duplicação avançadas.
"""
import os
import json
import traceback
import re
import base64
import requests
import discord
import asyncio
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
from datetime import time as _time
from discord.ext import commands, tasks
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch

# --- Environment ---
load_dotenv()
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
SERPAPI_KEY      = os.getenv("SERPAPI_KEY")
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN")
GITHUB_REPO      = os.getenv("GITHUB_REPO")
HISTORICO_PATH   = os.getenv("HISTORICO_FILE_PATH", "historico.json")
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER_ID  = int(os.getenv("ALLOWED_USER_ID", "0"))
DEST_CHANNEL_ID  = int(os.getenv("CANAL_DESTINO_ID", "0"))
LLAMA_MODEL      = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
PORT             = int(os.getenv("PORT", "10000"))

# --- HTTP Keep-alive ---
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot online!")
Thread(target=lambda: HTTPServer(("0.0.0.0", PORT), KeepAliveHandler).serve_forever(), daemon=True).start()

# --- Discord Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
conversas = defaultdict(lambda: deque(maxlen=10))
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Sistema de Controle de Duplicação ---
# Usando locks para cada canal em vez de simples flags booleanas
channel_locks = {}

# --- Helpers ---
def autorizado(ctx):
    return ((isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID)
            or (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID))

# --- GitHub Persistence ---
GITHUB_HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def fetch_history():
    """Recupera o histórico do GitHub."""
    try:
        print("Buscando histórico...")
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}",
            headers=GITHUB_HEADERS, timeout=10
        )
        if resp.ok:
            data = resp.json()
            raw = base64.b64decode(data.get("content", ""))
            hist_data = json.loads(raw)
            print(f"Histórico obtido com sucesso: {hist_data}")
            return hist_data, data.get("sha")
        else:
            print(f"Erro ao buscar histórico: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Exceção ao buscar histórico: {e}")
        traceback.print_exc()
    
    # Fallback para histórico vazio
    print("Retornando histórico vazio")
    return {"palavras": [], "frases": []}, None


def push_history(hist, sha=None):
    """Salva o histórico no GitHub com melhor tratamento de erros."""
    try:
        print(f"Salvando histórico: {hist}")
        content_b64 = base64.b64encode(
            json.dumps(hist, ensure_ascii=False).encode()
        ).decode()
        payload = {"message": "Atualiza historico.json pelo bot", "content": content_b64, "branch": "main"}
        if sha:
            payload["sha"] = sha
        
        print(f"Enviando PUT para GitHub com SHA: {sha}")
        put_resp = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}",
            headers=GITHUB_HEADERS, json=payload, timeout=10
        )
        
        if put_resp.status_code == 409:  # Conflict
            print("Conflito detectado no GitHub, buscando versão mais recente")
            new_hist, new_sha = fetch_history()
            
            # Adicionar apenas itens novos
            updated = False
            for palavra in hist.get("palavras", []):
                if palavra.lower() not in [x.lower() for x in new_hist.get("palavras", [])]:
                    new_hist.setdefault("palavras", []).append(palavra)
                    updated = True
                    print(f"Adicionada palavra durante resolução de conflito: {palavra}")
            
            for frase in hist.get("frases", []):
                if frase.lower() not in [x.lower() for x in new_hist.get("frases", [])]:
                    new_hist.setdefault("frases", []).append(frase)
                    updated = True
                    print(f"Adicionada frase durante resolução de conflito: {frase}")
            
            if updated:
                print("Tentando salvar novamente após resolver conflito")
                return push_history(new_hist, new_sha)
            else:
                print("Nenhuma atualização necessária após resolução de conflito")
                return True
        
        elif put_resp.ok:
            print(f"Histórico salvo com sucesso: {put_resp.status_code}")
            return True
        else:
            print(f"Erro ao salvar histórico: {put_resp.status_code} - {put_resp.text}")
            return False
    
    except Exception as e:
        print(f"Exceção ao salvar histórico: {e}")
        traceback.print_exc()
        return False

# --- Content Generation ---
async def gerar_conteudo_com_ia():
    """Gera conteúdo com IA e garante persistência."""
    if not groq_client:
        return "⚠️ Serviço indisponível."
    
    block = None
    
    # Tentar até 3 vezes para garantir salvamento
    for tentativa in range(3):
        try:
            print(f"Gerando conteúdo - tentativa {tentativa+1}")
            hist, sha = fetch_history()
            
            prompt = (
                "Crie uma palavra em inglês (definição em português, exemplo em inglês e tradução).\n"
                "Depois, forneça uma frase estoica em português com explicação.\n"
                "Formato: uma linha por item: Palavra:..., Definição:..., Exemplo:..., Tradução:..., Frase estoica:..., Explicação:..."
            )
            
            resp = groq_client.chat.completions.create(
                model=LLAMA_MODEL,
                messages=[
                    {"role": "system", "content": "Você é um professor de inglês e estoico."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            ).choices[0].message.content.strip()
            
            block = resp  # Guarda o conteúdo gerado
            
            # Extrai palavra/frase
            palavra = re.search(r'(?im)^Palavra: *(.+)$', block)
            frase = re.search(r'(?im)^Frase estoica: *(.+)$', block)
            
            # Flag para verificar se houve atualização
            updated = False
            
            # Processa a palavra se encontrada
            if palavra:
                p = palavra.group(1).strip()
                if p and p.lower() not in [x.lower() for x in hist.get("palavras", [])]:
                    hist.setdefault("palavras", []).append(p)
                    updated = True
                    print(f"Nova palavra adicionada: {p}")
            
            # Processa a frase se encontrada
            if frase:
                f = frase.group(1).strip()
                if f and f.lower() not in [x.lower() for x in hist.get("frases", [])]:
                    hist.setdefault("frases", []).append(f)
                    updated = True
                    print(f"Nova frase adicionada: {f}")

            # Salva apenas se houver algo novo
            if updated:
                print("Tentando salvar histórico atualizado")
                if push_history(hist, sha):
                    print("Histórico atualizado com sucesso!")
                    break  # Sai do loop se salvou com sucesso
                else:
                    print(f"Falha ao salvar na tentativa {tentativa+1}, tentando novamente...")
            else:
                print("Nenhuma atualização necessária no histórico")
                break  # Sai do loop se não há nada para salvar
                
        except Exception as e:
            print(f"Erro na tentativa {tentativa+1}: {str(e)}")
            traceback.print_exc()
            if block is None:  # Se falhou antes de gerar conteúdo
                block = f"⚠️ Erro ao gerar conteúdo: {str(e)}"
            # Continua tentando se falhou
    
    return block  # Retorna o conteúdo gerado (ou mensagem de erro)

async def acquire_lock(channel_id):
    """Adquire lock para o canal de forma segura."""
    channel_id = str(channel_id)  # Garante que seja string
    
    if channel_id not in channel_locks:
        channel_locks[channel_id] = asyncio.Lock()
    
    # Tenta adquirir o lock com timeout
    try:
        await asyncio.wait_for(channel_locks[channel_id].acquire(), timeout=1.0)
        return True
    except asyncio.TimeoutError:
        print(f"Timeout adquirindo lock para canal {channel_id}")
        return False

def release_lock(channel_id):
    """Libera o lock do canal se estiver adquirido."""
    channel_id = str(channel_id)
    if channel_id in channel_locks and channel_locks[channel_id].locked():
        try:
            channel_locks[channel_id].release()
            print(f"Lock liberado para canal {channel_id}")
        except RuntimeError:
            print(f"Erro ao liberar lock para canal {channel_id}")

async def send_content(channel):
    """Envia conteúdo para o canal com proteção contra duplicação."""
    channel_id = str(channel.id)
    
    # Tenta adquirir o lock
    if not await acquire_lock(channel_id):
        await channel.send("⏳ Já estou processando um comando neste canal.")
        return
    
    try:
        print(f"Gerando conteúdo para canal {channel_id}")
        # Indica que está processando
        processing_msg = await channel.send("⌛ Gerando conteúdo...")
        
        # Gera o conteúdo
        content = await gerar_conteudo_com_ia()
        
        # Remove mensagem de processamento
        try:
            await processing_msg.delete()
        except:
            pass
        
        # Envia o conteúdo
        await channel.send(content)
    except Exception as e:
        print(f"Erro em send_content: {e}")
        traceback.print_exc()
        await channel.send(f"❌ Erro ao gerar conteúdo: {str(e)}")
    finally:
        release_lock(channel_id)

# --- Commands ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    """Comando para fazer perguntas ao bot."""
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")
    
    channel_id = str(ctx.channel.id)
    
    # Tenta adquirir o lock
    if not await acquire_lock(channel_id):
        await ctx.send("⏳ Já estou processando um comando neste canal.")
        return
    
    try:
        processing_msg = await ctx.send("⌛ Pensando...")
        
        h = conversas[ctx.channel.id]
        h.append({"role": "user", "content": pergunta})
        
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[{"role": "system", "content": "Você é um assistente prestativo."}] + list(h),
            temperature=0.7
        ).choices[0].message.content
        
        h.append({"role": "assistant", "content": resp})
        
        try:
            await processing_msg.delete()
        except:
            pass
            
        await ctx.send(resp)
    except Exception as e:
        print(f"Erro no comando ask: {e}")
        traceback.print_exc()
        await ctx.send(f"❌ Erro ao processar pergunta: {str(e)}")
    finally:
        release_lock(channel_id)

@bot.command()
async def search(ctx, *, consulta: str):
    """Comando para buscar informações na web."""
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    
    channel_id = str(ctx.channel.id)
    
    # Tenta adquirir o lock
    if not await acquire_lock(channel_id):
        await ctx.send("⏳ Já estou processando um comando neste canal.")
        return
    
    try:
        processing_msg = await ctx.send(f"🔍 Buscando: {consulta}")
        
        results = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY}).get_dict().get("organic_results", [])[:3]
        snippet = "\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
        
        resumo = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[
                {"role": "system", "content": "Resuma resultados."},
                {"role": "user", "content": snippet}
            ],
            temperature=0.3
        ).choices[0].message.content
        
        try:
            await processing_msg.delete()
        except:
            pass
            
        await ctx.send(resumo)
    except Exception as e:
        print(f"Erro no comando search: {e}")
        traceback.print_exc()
        await ctx.send(f"❌ Erro na busca: {str(e)}")
    finally:
        release_lock(channel_id)

@bot.command()
async def testar_conteudo(ctx):
    """Comando para testar a geração de conteúdo."""
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    
    print(f"Comando testar_conteudo recebido no canal {ctx.channel.id}")
    await send_content(ctx.channel)

# --- Scheduled ---
@tasks.loop(time=_time(hour=9, minute=0))
async def daily_send():
    """Tarefa agendada para envio diário."""
    ch = bot.get_channel(DEST_CHANNEL_ID)
    if ch:
        channel_id = str(DEST_CHANNEL_ID)
        
        # Verifica se já está processando
        if channel_id in channel_locks and channel_locks[channel_id].locked():
            print(f"⏳ Já está processando um comando no canal {DEST_CHANNEL_ID}, pulando envio diário.")
            return
            
        print(f"Iniciando envio diário para canal {DEST_CHANNEL_ID}")
        await send_content(ch)

@bot.event
async def on_ready():
    """Evento disparado quando o bot está pronto."""
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")
    daily_send.start()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
